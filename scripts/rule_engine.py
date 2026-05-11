#!/usr/bin/env python3
"""rule_engine.py — Reactive layer for the Receipt protocol.

Tails one or more receipt JSONL chains and fires actions (ntfy, shell,
iMessage) when events match declarative rules. Each rule firing optionally
emits a meta-receipt event back to its own chain, so the alert layer is
itself auditable via the same protocol it operates on.

Design (v0.1):
  - Event-level matching only (kind, data.X field equality / list membership)
  - Per-rule debounce (in seconds; in-memory, not persisted)
  - Multi-chain support via `chains: [name, name, ...]` per rule (default ["*"])
  - Actions: ntfy, shell, imessage
  - Optional meta-receipt: every rule firing emits claim → external_action →
    verified events to a separate chain
  - Stateless across restarts: marks current chain heads as "seen" on boot
    so a restart never re-fires all prior events

Not included in v0.1 (potential v0.2):
  - Stateful patterns (no heartbeat for X min, claim without verified in Y sec)
  - External truth cross-check (compare receipt-state to exchange API)
  - Persistent debounce state across restarts

Usage:
    python3 rule_engine.py --rules /path/to/rules.py \\
                           --meta-receipt /path/to/rule-engine.receipt.jsonl \\
                           --meta-agent iBitLabs/rule-engine-v0.1 \\
                           --log-file /path/to/engine.log

The rules file is a Python module exposing two top-level values:

    CHAINS = {
        "live":   "/path/to/live.receipt.jsonl",
        "shadow": "/path/to/shadow.receipt.jsonl",
    }

    RULES = [
        {
            "name": "alert_on_open",
            "chains": ["live"],            # optional, default ["*"]
            "match": {                     # all keys must match
                "kind": "claim",
                "data.action": ["open_long", "open_short"],
            },
            "do": [                        # actions run in order
                {"type": "ntfy", "topic": "...",
                 "body": "Opened {data.symbol} @ ${data.price_intended}"},
            ],
            "debounce_seconds": 30,        # optional, default 0
        },
        ...
    ]

Match values can be a scalar (equality), a list (membership), or a bool
(truthiness comparison). Field paths use dot notation: `data.action`.
Template placeholders in action strings use `{data.x}` form — same dot
notation, missing fields render as empty string.

License: MIT (matches receipt repo).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Receipt SDK for the meta-receipt chain (optional).
# Look in user site-packages first (the launchd-safe install location).
try:
    from receipt import Receipt as _Receipt
except ImportError:
    # Fallback: try the source tree at ~/Documents/receipt/
    _src = os.path.expanduser("~/Documents/receipt")
    if os.path.isdir(_src):
        sys.path.insert(0, _src)
    try:
        from receipt import Receipt as _Receipt
    except ImportError:
        _Receipt = None  # type: ignore

POLL_INTERVAL_SECONDS = 2
TEMPLATE_RE = re.compile(r"\{([^}]+)\}")


def deep_get(obj: Any, dotted: str) -> Any:
    """`data.action` → obj['data']['action']. Returns None on missing path."""
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def render_template(s: str, event: dict) -> str:
    """`Opened {data.symbol}` → `Opened SOL`. Missing fields render as empty."""
    def repl(m: re.Match) -> str:
        val = deep_get(event, m.group(1))
        return "" if val is None else str(val)
    return TEMPLATE_RE.sub(repl, s)


def match_rule(rule: dict, event: dict) -> bool:
    """All match keys must satisfy. Bool / list / scalar value semantics."""
    for key, expected in rule.get("match", {}).items():
        actual = deep_get(event, key) if "." in key else event.get(key)
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif isinstance(expected, bool):
            if bool(actual) != expected:
                return False
        else:
            if actual != expected:
                return False
    return True


def execute_action(action: dict, event: dict, chain: str, dry_run: bool, log: logging.Logger) -> dict:
    """Run one action. Returns dict with {action, ok, ...details}. Never raises."""
    t = action.get("type", "?")
    try:
        if t == "ntfy":
            topic = action.get("topic", "default")
            body = render_template(action.get("body", ""), event)
            headers: list[str] = []
            for key in ("priority", "title", "tags"):
                if key in action:
                    val = render_template(str(action[key]), event)
                    headers.extend(["-H", f"{key.capitalize()}: {val}"])
            cmd = ["curl", "-fsS", "--max-time", "10", "--data", body,
                   f"https://ntfy.sh/{topic}", *headers]
            if dry_run:
                return {"action": "ntfy", "topic": topic, "body": body, "dry_run": True}
            r = subprocess.run(cmd, capture_output=True, timeout=15)
            return {"action": "ntfy", "topic": topic, "body": body,
                    "ok": r.returncode == 0,
                    "stderr": r.stderr.decode()[-160:] if r.returncode != 0 else ""}

        elif t == "shell":
            cmd_str = render_template(action["cmd"], event)
            if dry_run:
                return {"action": "shell", "cmd": cmd_str, "dry_run": True}
            r = subprocess.run(["/bin/zsh", "-c", cmd_str],
                               capture_output=True, timeout=60)
            return {"action": "shell", "cmd": cmd_str,
                    "ok": r.returncode == 0,
                    "stdout_tail": r.stdout.decode()[-160:],
                    "stderr_tail": r.stderr.decode()[-160:] if r.returncode != 0 else ""}

        elif t == "imessage":
            to = action.get("to", "")
            body = render_template(action.get("body", ""), event)
            if dry_run:
                return {"action": "imessage", "to": to, "body": body, "dry_run": True}
            # Escape double quotes in body for AppleScript
            body_esc = body.replace('"', '\\"')
            ascr = f'tell application "Messages" to send "{body_esc}" to buddy "{to}"'
            r = subprocess.run(["osascript", "-e", ascr],
                               capture_output=True, timeout=15)
            return {"action": "imessage", "to": to, "body": body,
                    "ok": r.returncode == 0}

        else:
            return {"action": t, "ok": False, "error": f"unknown action type: {t}"}
    except Exception as e:
        return {"action": t, "ok": False, "error": str(e)}


def load_rules_module(path: str) -> tuple[dict, list]:
    spec = importlib.util.spec_from_file_location("operator_rules", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load rules module from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    chains = getattr(mod, "CHAINS", None)
    rules = getattr(mod, "RULES", None)
    if not isinstance(chains, dict):
        raise RuntimeError("rules module must define CHAINS = {name: path}")
    if not isinstance(rules, list):
        raise RuntimeError("rules module must define RULES = [...]")
    return chains, rules


def initial_scan(chain_path: str, log: logging.Logger) -> tuple[int, int]:
    """Return (file_size, last_seq) for a chain — used to mark existing
    events as 'already seen' so engine restart doesn't fire on backfill."""
    p = Path(chain_path)
    if not p.exists():
        return 0, -1
    try:
        size = p.stat().st_size
        with open(p, "r") as f:
            lines = f.readlines()
        last_seq = -1
        for line in reversed(lines):
            line = line.strip()
            if line:
                try:
                    last_seq = json.loads(line).get("seq", -1)
                    break
                except json.JSONDecodeError:
                    continue
        return size, last_seq
    except Exception as e:
        log.warning(f"initial_scan failed for {chain_path}: {e}")
        return 0, -1


def read_new_events(chain_path: str, prev_size: int, log: logging.Logger) -> tuple[int, list[dict]]:
    """Read bytes [prev_size, EOF) and parse JSONL. Returns (new_size, events)."""
    p = Path(chain_path)
    new_size = p.stat().st_size
    if new_size <= prev_size:
        return prev_size, []
    events = []
    with open(p, "r") as f:
        f.seek(prev_size)
        for line in f.read().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                log.warning(f"{chain_path}: bad jsonl line: {e}")
    return new_size, events


def emit_meta_receipt(meta: Any, rule_name: str, chain_name: str, event: dict,
                      results: list[dict], log: logging.Logger) -> None:
    """Emit claim/external_action/verified triple for one rule firing.
    Best-effort — failures are logged but don't propagate."""
    if meta is None:
        return
    try:
        claim_seq = meta.claim(
            action="rule_fired",
            rule_name=rule_name,
            source_chain=chain_name,
            source_seq=event.get("seq"),
            source_kind=event.get("kind"),
            source_agent=event.get("agent"),
            fired_at_ms=int(time.time() * 1000),
        )
        for i, res in enumerate(results):
            meta.external_action(
                claim_seq,
                venue=res.get("action", "?"),
                request={"action_index": i},
                response=res,
            )
        all_ok = all(r.get("ok", True) for r in results if "ok" in r)
        meta.verified(
            claim_seq,
            trust_tier="api_verified",
            source="rule_engine_self_attest",
            summary=f"{len(results)} action(s); {'all_ok' if all_ok else 'some_failed'}",
            match={
                "symbol": rule_name,
                "side": "rule_fire",
                "size": True,
                "price_match": True,
                "time_match": True,
                "id_match": all_ok,
                "tolerance_used": "exact",
            },
        )
    except Exception as e:
        log.warning(f"meta-receipt write failed: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", required=True, help="Path to rules.py module")
    ap.add_argument("--meta-receipt", default=None,
                    help="Path to meta-receipt JSONL output (optional)")
    ap.add_argument("--meta-agent", default="iBitLabs/rule-engine-v0.1")
    ap.add_argument("--log-file", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="Match rules but don't execute actions; useful for testing")
    ap.add_argument("--poll-seconds", type=float, default=POLL_INTERVAL_SECONDS)
    args = ap.parse_args()

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
    log = logging.getLogger("rule_engine")
    log.info("== rule_engine v0.1 starting ==")
    log.info(f"  rules:        {args.rules}")
    log.info(f"  meta-receipt: {args.meta_receipt or 'disabled'}")
    log.info(f"  dry-run:      {args.dry_run}")

    try:
        CHAINS, RULES = load_rules_module(args.rules)
    except Exception as e:
        log.error(f"failed to load rules: {e}")
        return 1
    log.info(f"  chains:       {list(CHAINS.keys())}")
    log.info(f"  rules loaded: {len(RULES)}")

    meta = None
    if args.meta_receipt and _Receipt and not args.dry_run:
        try:
            meta = _Receipt(agent=args.meta_agent, out_path=args.meta_receipt)
            log.info(f"  meta-chain head seq={meta.seq}")
        except Exception as e:
            log.warning(f"  meta-receipt init failed: {e}")

    chain_state: dict[str, dict] = {}
    for name, path in CHAINS.items():
        size, last_seq = initial_scan(path, log)
        chain_state[name] = {"path": path, "size": size, "last_seq": last_seq}
        log.info(f"  init {name}: size={size} last_seq={last_seq}")

    debounce: dict[str, float] = {}

    # SIGTERM / SIGINT cleanly stop the loop
    running = {"flag": True}
    def _stop(signum, frame):
        log.info(f"received signal {signum}, exiting")
        running["flag"] = False
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    log.info("watching for new events...")
    while running["flag"]:
        time.sleep(args.poll_seconds)
        for chain_name, st in chain_state.items():
            try:
                if not Path(st["path"]).exists():
                    continue
                new_size, events = read_new_events(st["path"], st["size"], log)
                st["size"] = new_size
            except Exception as e:
                log.warning(f"{chain_name}: read failed: {e}")
                continue
            for ev in events:
                seq = ev.get("seq", -1)
                if seq <= st["last_seq"]:
                    continue
                st["last_seq"] = seq
                for rule in RULES:
                    rule_chains = rule.get("chains", ["*"])
                    if "*" not in rule_chains and chain_name not in rule_chains:
                        continue
                    if not match_rule(rule, ev):
                        continue
                    debounce_sec = rule.get("debounce_seconds", 0)
                    now = time.time()
                    last_fire = debounce.get(rule["name"], 0)
                    if debounce_sec > 0 and (now - last_fire) < debounce_sec:
                        log.info(f"DEBOUNCED rule='{rule['name']}' chain={chain_name} "
                                 f"seq={seq} kind={ev.get('kind')}")
                        continue
                    debounce[rule["name"]] = now
                    log.info(f"FIRE rule='{rule['name']}' chain={chain_name} "
                             f"seq={seq} kind={ev.get('kind')}")
                    results = []
                    for action in rule.get("do", []):
                        res = execute_action(action, ev, chain_name, args.dry_run, log)
                        results.append(res)
                        log.info(f"  -> {json.dumps(res)[:200]}")
                    emit_meta_receipt(meta, rule["name"], chain_name, ev, results, log)
    log.info("== rule_engine stopped ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
