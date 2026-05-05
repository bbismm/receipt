"""Adapter contract — every venue adapter implements this protocol."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class MatchResult:
    size: bool = False
    side: bool = False
    price_within_tolerance: bool = False
    timing_within_tolerance: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def all_match(self) -> bool:
        return self.size and self.side and self.price_within_tolerance and self.timing_within_tolerance

    def to_dict(self) -> dict:
        return {
            "size": self.size,
            "side": self.side,
            "price_within_tolerance": self.price_within_tolerance,
            "timing_within_tolerance": self.timing_within_tolerance,
            "notes": self.notes,
        }


class ReconciliationAdapter(Protocol):
    """Protocol for venue adapters. Implement these four methods."""

    venue: str

    def fetch_order(self, order_id: str) -> dict:
        """Return normalized order dict for a single exchange order_id.

        Required keys: order_id, side ('buy'|'sell'), size (float), price (float),
                       status ('filled'|'open'|'cancelled'), filled_size (float),
                       average_fill_price (float|None), filled_at (ISO Z string|None).
        """
        ...

    def fetch_position(self, symbol: str) -> dict:
        """Return current position for `symbol`.

        Required keys: symbol, side ('long'|'short'|'flat'), size (float),
                       entry_price (float|None), unrealized_pnl_usd (float).
        """
        ...

    def fetch_balance(self) -> dict:
        """Return account balance.

        Required keys: balance_usd (float), available_usd (float).
        """
        ...

    def reconcile_claim(
        self,
        claim_data: dict,
        external_action_data: dict,
        price_tolerance_pct: float = 0.5,
        timing_tolerance_sec: int = 60,
    ) -> MatchResult:
        """Compare a claim+action pair against re-fetched truth and return MatchResult."""
        ...
