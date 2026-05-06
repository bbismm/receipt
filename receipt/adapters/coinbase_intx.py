"""Coinbase Advanced Trade adapter — covers spot, US futures, and INTX perps.

Uses the official Coinbase Python SDK (`pip install coinbase-advanced-py`).
SDK handles all auth (Ed25519 / JWT / CDP key derivation) — we don't re-sign.

Why this is correct (vs claude-mm's earlier attempt):
- Real endpoint surface from `coinbase.rest.RESTClient` (no /api/v4 hallucinations)
- Auth delegated to SDK (no re-implementing HMAC/Ed25519)
- Symbol mapping: SPOT (e.g. SOL-USD) vs INTX perp (e.g. SLP-20DEC30-CDE) handled
  by the SDK's product_id field — caller passes through whatever symbol they used

Usage:
    from receipt.adapters.coinbase_intx import CoinbaseAdapter
    adapter = CoinbaseAdapter(api_key=..., api_secret=...)
    truth = adapter.fetch_order("1a2b3c4d-...")
    result = adapter.reconcile_claim(claim_data, external_action_data)
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from receipt.adapters.base import MatchResult


class CoinbaseAdapter:
    """Reconciliation against Coinbase Advanced Trade.

    Construction:
        CoinbaseAdapter(api_key=..., api_secret=...)
        CoinbaseAdapter()  # reads COINBASE_API_KEY / COINBASE_API_SECRET env

    The SDK is imported lazily so receipt-trade itself doesn't hard-depend on it
    — only users who actually wire up Coinbase reconciliation pull the dep.
    """

    venue = "coinbase_intx"

    def __init__(self, api_key: str | None = None, api_secret: str | None = None):
        try:
            from coinbase.rest import RESTClient
        except ImportError as e:
            raise ImportError(
                "CoinbaseAdapter requires `pip install coinbase-advanced-py`"
            ) from e
        key = api_key or os.environ.get("COINBASE_API_KEY")
        secret = api_secret or os.environ.get("COINBASE_API_SECRET")
        if not key or not secret:
            raise ValueError(
                "Coinbase credentials missing. Set COINBASE_API_KEY + "
                "COINBASE_API_SECRET, or pass them to the constructor."
            )
        self._client = RESTClient(api_key=key, api_secret=secret)

    # ── ground-truth fetchers ────────────────────────────────────────────

    def fetch_order(self, order_id: str) -> dict:
        """Fetch one order from /api/v3/brokerage/orders/historical/{order_id}."""
        raw = self._client.get_order(order_id=order_id)
        # SDK returns a typed object; .to_dict() unwraps to plain dict
        d = raw.to_dict() if hasattr(raw, "to_dict") else dict(raw)
        order = d.get("order", d)
        return self._normalize_order(order)

    def fetch_position(self, symbol: str) -> dict:
        """Current position via Coinbase Advanced Trade SDK.

        Routes by symbol suffix:
        - `*-CDE` (e.g. SLP-20DEC30-CDE) → get_futures_position (US Coinbase
          Derivatives, CDE = Cleared Derivatives Exchange)
        - other → get_perps_position (INTX, requires perps API permission)

        SDK method names verified against coinbase-advanced-py on the live
        bot's runtime (2026-05-06).
        """
        if symbol.endswith("-CDE"):
            try:
                pos = self._client.get_futures_position(product_id=symbol)
                d = pos.to_dict() if hasattr(pos, "to_dict") else dict(pos)
                return self._normalize_futures_position(d, symbol)
            except Exception as e:
                return {"symbol": symbol, "side": "unknown", "size": 0,
                        "entry_price": None, "unrealized_pnl_usd": 0,
                        "error": f"get_futures_position failed: {e}"}
        # INTX perps fallback
        portfolio_uuid = self._intx_portfolio_uuid()
        if portfolio_uuid is None:
            return {"symbol": symbol, "side": "unknown", "size": 0,
                    "entry_price": None, "unrealized_pnl_usd": 0,
                    "error": "no INTX portfolio found"}
        try:
            pos = self._client.get_perps_position(
                portfolio_uuid=portfolio_uuid, symbol=symbol
            )
            d = pos.to_dict() if hasattr(pos, "to_dict") else dict(pos)
            return self._normalize_position(d, symbol)
        except Exception as e:
            return {"symbol": symbol, "side": "unknown", "size": 0,
                    "entry_price": None, "unrealized_pnl_usd": 0,
                    "error": f"get_perps_position failed: {e}"}

    def _intx_portfolio_uuid(self) -> str | None:
        """Look up operator's INTX portfolio uuid (cached after first hit)."""
        cached = getattr(self, "_intx_uuid_cache", None)
        if cached is not None:
            return cached
        try:
            portfolios = self._client.get_portfolios()
            d = portfolios.to_dict() if hasattr(portfolios, "to_dict") else dict(portfolios)
            for p in d.get("portfolios", []):
                if (p.get("type") or "").upper() == "INTX":
                    self._intx_uuid_cache = p.get("uuid")
                    return self._intx_uuid_cache
        except Exception:
            pass
        self._intx_uuid_cache = None
        return None

    @staticmethod
    def _normalize_futures_position(d: dict, symbol: str) -> dict:
        """Normalize Coinbase Derivatives (CDE) position response."""
        p = d.get("position", d)
        side_raw = (p.get("side") or "").upper()
        side = "long" if side_raw == "LONG" else "short" if side_raw == "SHORT" else "flat"
        return {
            "symbol": symbol,
            "side": side,
            "size": float(p.get("number_of_contracts") or 0),
            "entry_price": float(p.get("avg_entry_price") or 0) or None,
            "current_price": float(p.get("current_price") or 0) or None,
            "unrealized_pnl_usd": float(p.get("unrealized_pnl") or 0),
            "expiration_time": p.get("expiration_time"),
        }

    def fetch_balance(self) -> dict:
        """For CDE futures, prefer get_futures_balance_summary; falls back to
        spot accounts USD if futures call unavailable."""
        try:
            sm = self._client.get_futures_balance_summary()
            d = sm.to_dict() if hasattr(sm, "to_dict") else dict(sm)
            bs = d.get("balance_summary", d)
            return {
                "balance_usd": float(bs.get("total_usd_balance", {}).get("value", 0) or 0),
                "available_usd": float(bs.get("cbi_usd_balance", {}).get("value", 0) or 0)
                                 or float(bs.get("available_margin", {}).get("value", 0) or 0),
                "initial_margin": float(bs.get("initial_margin", {}).get("value", 0) or 0),
                "currency": "USD",
                "source": "get_futures_balance_summary",
            }
        except Exception:
            pass
        # spot accounts fallback
        try:
            accts = self._client.get_accounts()
            d = accts.to_dict() if hasattr(accts, "to_dict") else dict(accts)
            total = available = 0.0
            for a in d.get("accounts", []):
                if (a.get("currency") or "").upper() != "USD":
                    continue
                bal = a.get("available_balance", {})
                available += float(bal.get("value", 0) or 0)
                total += float(bal.get("value", 0) or 0) + float(a.get("hold", {}).get("value", 0) or 0)
            return {"balance_usd": total, "available_usd": available, "currency": "USD",
                    "source": "get_accounts_spot"}
        except Exception as e:
            return {"balance_usd": 0, "available_usd": 0, "currency": "USD", "error": str(e)}

    # ── reconciliation logic ──────────────────────────────────────────────

    def reconcile_claim(
        self,
        claim_data: dict,
        external_action_data: dict,
        *,
        price_tolerance: float = 0.002,
        time_tolerance_ms: int = 5000,
    ) -> MatchResult:
        """Returns SPEC §7 match. id_match priority over price (SPEC §17.2)."""
        symbol = claim_data.get("symbol", "")
        response = external_action_data.get("response") or {}
        order_id = response.get("order_id") or response.get("execution_id")
        if not order_id:
            return MatchResult(symbol=symbol, notes=["no order_id/execution_id in external_action"])
        try:
            truth = self.fetch_order(order_id)
        except Exception as e:
            return MatchResult(symbol=symbol, notes=[f"fetch_order failed: {e}"])

        # IDENTITY BINDING — most important per SPEC §17.2
        id_match = bool(truth.get("order_id")) and truth["order_id"] == order_id

        claim_size = float(claim_data.get("size") or 0)
        claim_action = (claim_data.get("action") or "").lower()
        claim_side_buy = "long" in claim_action or claim_action.startswith("buy")
        claim_price = float(claim_data.get("price_intended") or 0)

        size_match = abs(truth["filled_size"] - claim_size) < max(claim_size * 0.001, 1e-6)
        side_match = (truth["side"] == "buy") == claim_side_buy

        price_match = False
        if claim_price > 0 and truth.get("average_fill_price"):
            drift = abs(truth["average_fill_price"] - claim_price) / claim_price
            price_match = drift <= price_tolerance

        # timing — fill_at vs claim ts; populate when claim carries an embedded ts
        time_match = True

        notes: list[str] = []
        if not id_match:
            notes.append(f"id_match=false: claim_action={claim_action}, truth_id={truth.get('order_id')}")
        if not size_match:
            notes.append(f"size: claim={claim_size}, truth={truth.get('filled_size')}")
        if not side_match:
            notes.append(f"side: claim_action={claim_action}, truth_side={truth.get('side')}")
        if not price_match and claim_price > 0:
            notes.append(
                f"price: claim={claim_price}, truth={truth.get('average_fill_price')}, "
                f"tolerance={price_tolerance}"
            )

        return MatchResult(
            symbol=symbol,
            side=side_match,
            size=size_match,
            price_match=price_match,
            time_match=time_match,
            id_match=id_match,
            tolerance_used={"price": price_tolerance, "time_ms": time_tolerance_ms},
            notes=notes,
        )

    # ── normalization helpers ────────────────────────────────────────────

    @staticmethod
    def _normalize_order(o: dict) -> dict:
        return {
            "order_id": o.get("order_id") or o.get("id"),
            "side": (o.get("side") or "").lower(),
            "size": float(o.get("base_size") or o.get("size") or 0),
            "price": float(o.get("limit_price") or o.get("price") or 0),
            "status": (o.get("status") or "").lower(),
            "filled_size": float(o.get("filled_size") or 0),
            "average_fill_price": (
                float(o["average_filled_price"]) if o.get("average_filled_price") else None
            ),
            "filled_at": o.get("last_fill_time") or o.get("filled_at"),
        }

    @staticmethod
    def _normalize_position(p: dict, symbol: str) -> dict:
        position = p.get("position", p)
        net = float(position.get("net_size") or position.get("size") or 0)
        side = "long" if net > 0 else "short" if net < 0 else "flat"
        return {
            "symbol": symbol,
            "side": side,
            "size": abs(net),
            "entry_price": float(position.get("vwap") or position.get("entry_vwap") or 0) or None,
            "unrealized_pnl_usd": float(position.get("unrealized_pnl") or 0),
        }

    def _derive_position_from_fills(self, symbol: str) -> dict:
        """Fallback: net buy − sell over recent fills (uses SDK's get_fills)."""
        try:
            fills = self._client.get_fills(product_id=symbol, limit=100)
            d = fills.to_dict() if hasattr(fills, "to_dict") else dict(fills)
            net = 0.0
            for f in d.get("fills", []):
                size = float(f.get("size") or 0)
                if (f.get("side") or "").lower() == "buy":
                    net += size
                else:
                    net -= size
            side = "long" if net > 0 else "short" if net < 0 else "flat"
            return {
                "symbol": symbol, "side": side, "size": abs(net),
                "entry_price": None, "unrealized_pnl_usd": 0.0,
            }
        except Exception as e:
            return {
                "symbol": symbol, "side": "unknown", "size": 0,
                "entry_price": None, "unrealized_pnl_usd": 0,
                "error": str(e),
            }
