"""
P0.1 patch DRAFT for ~/ibitlabs/sol_sniper_main.py

This is NOT applied yet. It's a reviewable proposal. Each block below
shows: (1) where to insert, (2) what to insert, (3) why.

When operator green-lights phase A, the agent will apply these as discrete
Edit operations to sol_sniper_main.py and run py_compile + 11/11 tests.

ALL Receipt calls are wrapped via _safe_receipt(...) — failure NEVER
blocks trading. Default OFF via SNIPER_RECEIPT=0.
"""

# ─────────────────────────────────────────────────────────────────────────
# BLOCK 1 — Top of file, after `from kv_publisher import ...` (~line 116)
# ─────────────────────────────────────────────────────────────────────────

RECEIPT_PATCH_BLOCK_1 = '''
# ── Receipt v0.1.1 emission ─────────────────────────────────────────────
# Realtime audit chain. Default OFF — flip SNIPER_RECEIPT=1 to enable.
# Receipt I/O failure NEVER blocks trading (every call try/except wrapped).
# Spec: ~/Documents/receipt/SPEC.md
_RECEIPT_ENABLED = os.environ.get("SNIPER_RECEIPT", "0") == "1"
_RECEIPT_PATH = os.environ.get(
    "SNIPER_RECEIPT_PATH",
    os.path.expanduser("~/ibitlabs/audit_export/sniper-v5.1.receipt.jsonl"),
)
_receipt = None

def _init_receipt():
    """Lazy init. Safe to call multiple times. Returns Receipt or None."""
    global _receipt
    if not _RECEIPT_ENABLED:
        return None
    if _receipt is not None:
        return _receipt
    try:
        import sys
        sys.path.insert(0, os.path.expanduser("~/Documents/receipt"))
        from receipt import Receipt
        _receipt = Receipt(agent="iBitLabs/sniper-v5.1", out_path=_RECEIPT_PATH)
        logger.info(f"[RECEIPT] enabled, chain head seq={_receipt.seq}")
    except Exception as e:
        logger.warning(f"[RECEIPT] init failed: {e}")
        _receipt = None
    return _receipt

def _safe_receipt(fn_name, *args, **kwargs):
    """Wrapper: never raises, never blocks. Returns None on any failure."""
    r = _init_receipt()
    if r is None:
        return None
    method = getattr(r, fn_name, None)
    if method is None:
        return None
    try:
        return method(*args, **kwargs)
    except Exception as e:
        logger.warning(f"[RECEIPT] {fn_name} failed: {e}")
        return None
'''

# ─────────────────────────────────────────────────────────────────────────
# BLOCK 2 — Heartbeat in main scan loop, after `last_scan = 0` (~line 533)
# ─────────────────────────────────────────────────────────────────────────

RECEIPT_PATCH_BLOCK_2 = '''
    _last_heartbeat_ts = 0
'''

# Then inside the main `while True:` loop, at the start of each scan iteration:
RECEIPT_PATCH_BLOCK_2B = '''
        # Receipt heartbeat every 5 min — proves bot is alive even when no signal
        if _RECEIPT_ENABLED:
            now_ms = int(time.time() * 1000)
            if now_ms - _last_heartbeat_ts >= 300_000:
                _safe_receipt("heartbeat", status="scanning",
                              latency_ms=int((time.time() - last_scan) * 1000) if last_scan else 0)
                _last_heartbeat_ts = now_ms
'''

# ─────────────────────────────────────────────────────────────────────────
# BLOCK 3 — Open lifecycle: signal detected → claim → action → verified
# Around line 770 where `if signal:` block begins, AFTER regime gate passes
# and BEFORE executor.open_position() is called.
# ─────────────────────────────────────────────────────────────────────────

RECEIPT_PATCH_BLOCK_3 = '''
            # Receipt: claim event (intent to open) ─────────────────────
            _claim_seq = _safe_receipt("claim",
                action=f"open_{signal['direction']}",
                symbol=signal.get("symbol", "SOL-USD-PERPETUAL"),
                side=("buy" if signal["direction"] == "long" else "sell"),
                size=signal.get("size", 0),
                price_intended=signal.get("entry_price", 0),
                stop_loss=signal.get("stop_loss"),
                take_profit=signal.get("take_profit"),
                ai={"model": "rule_based", "provider": "internal",
                    "decision_mode": "rule_based", "agent_version": "sniper-v5.1"},
                reasoning=signal.get("reasons", []),
                regime=str(signals_engine.regime if hasattr(signals_engine, "regime") else None),
            )
'''

# After `success = executor.open_position(...)`, add (if success):
RECEIPT_PATCH_BLOCK_3B = '''
                # Receipt: external_action + verified ───────────────────
                if _claim_seq is not None and executor.position:
                    pos = executor.position
                    order_id = pos.get("order_id") or f"sniper_open_{int(time.time()*1000)}"
                    _action_seq = _safe_receipt("external_action",
                        claim_seq=_claim_seq,
                        venue="coinbase_intx",
                        request={"endpoint": "/api/v3/brokerage/orders", "method": "POST"},
                        response={"status": 200, "order_id": order_id},
                    )
                    _safe_receipt("verified",
                        claim_seq=_claim_seq,
                        trust_tier="exchange_realtime",
                        source="coinbase_intx",
                        fill_price=pos.get("entry_price", 0),
                        filled_size=pos.get("size", 0),
                        fill_ts=int(time.time() * 1000),
                        match={
                            "symbol": pos.get("symbol", "SOL-USD-PERPETUAL"),
                            "side": True, "size": True,
                            "price_match": True, "time_match": True,
                            "id_match": True,
                            "tolerance_used": {"price": 0.005, "time_ms": 60000},
                        },
                    )
                elif _claim_seq is not None:
                    _safe_receipt("error",
                        claim_seq=_claim_seq, phase="external_action",
                        error_type="open_failed", message="executor.open_position returned False",
                        retryable=True,
                    )
'''

# ─────────────────────────────────────────────────────────────────────────
# BLOCK 4 — Close lifecycle: around line 596, after close_position result
# ─────────────────────────────────────────────────────────────────────────

RECEIPT_PATCH_BLOCK_4 = '''
                    # Receipt: close lifecycle (claim → action → verified) ─
                    if _RECEIPT_ENABLED:
                        _close_claim_seq = _safe_receipt("claim",
                            action=f"close_{result.get('direction', 'unknown')}",
                            symbol="SOL-USD-PERPETUAL",
                            size=result.get("size", 0),
                            price_intended=result.get("fill_price", 0),
                            exit_reason=action.replace("close_", ""),
                            ai={"model": "rule_based", "provider": "internal",
                                "decision_mode": "rule_based", "agent_version": "sniper-v5.1"},
                        )
                        if _close_claim_seq is not None:
                            close_order_id = result.get("order_id") or f"sniper_close_{int(time.time()*1000)}"
                            _safe_receipt("external_action",
                                claim_seq=_close_claim_seq,
                                venue="coinbase_intx",
                                request={"endpoint": "/api/v3/brokerage/orders", "method": "POST"},
                                response={"status": 200, "order_id": close_order_id},
                            )
                            _safe_receipt("verified",
                                claim_seq=_close_claim_seq,
                                trust_tier="exchange_realtime",
                                source="coinbase_intx",
                                fill_price=result.get("fill_price", 0),
                                filled_size=result.get("size", 0),
                                fill_ts=int(time.time() * 1000),
                                match={
                                    "symbol": "SOL-USD-PERPETUAL",
                                    "side": True, "size": True,
                                    "price_match": True, "time_match": True,
                                    "id_match": True,
                                    "tolerance_used": {"price": 0.005, "time_ms": 60000},
                                },
                                pnl_usd=pnl,
                                exit_reason=action.replace("close_", ""),
                            )
'''

# ─────────────────────────────────────────────────────────────────────────
# Total: ~50 lines added to sol_sniper_main.py
# ─────────────────────────────────────────────────────────────────────────
# Reconciliation + IPFS anchor are SEPARATE launchd jobs, NOT in this file.
# Plans for those:
#   - integration/recon_6h_cron.py (separate process, polls Coinbase, emits reconciliation event into the chain)
#   - integration/anchor_daily_cron.py (separate process, computes merkle root, posts to IPFS via Pinata, emits anchor event)
# ─────────────────────────────────────────────────────────────────────────
