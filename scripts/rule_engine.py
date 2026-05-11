#!/usr/bin/env python3
"""rule_engine.py — Reactive + self-healing layer for the Receipt protocol.

Tails one or more receipt JSONL chains and fires actions (ntfy, shell,
iMessage) when events match declarative rules. Each rule firing emits a
meta-receipt event to its own chain — the alert / auto-heal layer is
itself auditable via the same protocol it operates on.

v0.2 additions over v0.1:
  - Stateful "absent" pattern: fires when no event of kind X has occurred
    on a chain in the last Y seconds (poll-driven, not event-driven)
  - `auto: true/false` flag gating shell-action execution. Default false:
    shell actions are blocked unless the rule explicitly opts in. ntfy /
    iMessage actions are not gated (low blast radius). Logs "BLOCKED"
    when a shell action is skipped due to missing `auto: true`
  - Engine emits its own heartbeat events to the meta chain every 60s,
    so external watchers can detect "engine alive but silent" conditions

What v0.2 still does NOT do (deferred to v0.3):
  - External truth cross-check (compare receipt-state to exchange API)
  - Persistent debounce state across engine restarts
  - Pattern matching on event sequences ("claim without verified in N sec")

Tier discipline (operator policy, not enforced by code):
  - Tier 1 (idempotent, reversible) → safe to auto: anchor, reconcile, sync
  - Tier 2 (state-fixing, reversible w/ effort) → auto + circuit breaker
  - Tier 3 (irreversible, live money) → ntfy only, NEVER set auto: true
    Examples: bootout sniper, kill positions, modify chains

Usage:
    python3 rule_engine.py --rules /path/to/rules.py \\
                           --meta-receipt /path/to/rule-engine.receipt.jsonl \\
                           --meta-agent iBitLabs/rule-engine-v0.2 \\
                           --log-file /path/to/engine.log

Rules file schema (Python module):

    CHAINS = {"live": "/path/to/live.jsonl", "shadow": "/path/to/shadow.jsonl"}

    RULES = [
        # event-driven (v0.1):
        {
            "name": "alert_on_open",
            "chains": ["live"],
            "match": {"kind": "claim", "data.action": ["open_long","open_short"]},
            "do": [{"type": "ntfy", "topic": "...", "body": "..."}],
            "debounce_seconds": 30,
        },
        # stateful absence (v0.2):
        {
            "name": "auto_anchor_stale",
            "chains": ["live"],
            "match": {"absent": {"kind": "anchor", "for_seconds": 86400}},
            "auto": True,  # required for shell actions to actually execute
            "do": [
                {"type": "shell", "cmd": "python3 /path/to/anchor_daily.py --chain /path/to/live.jsonl"},
                {"type": "ntfy", "topic": "...", "body": "Auto-anchored stale chain"},
            ],
            "debounce_seconds": 3600,
        },
    ]

License: MIT.
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
try:
    from receipt import Receipt as _Receipt
except ImportError:
    _src = os.path.expanduser("~/Documents/receipt")
    if os.path.isdir(_src):
        sys.path.insert(0, _src)
    try:
        from receipt import Receipt as _Receipt
    except ImportError:
        _Receipt = None  # type: ignore

POLL_INTERVAL_SECONDS = 2
SELF_HEARTBEAT_SECONDS = 60
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


def is_stateful(rule: dict) -> bool:
    """Rule uses stateful absence pattern (matched per-poll, not per-event)."""
    return isinstance(rule.get("match"), dict) and "absent" in rule["match"]


def match_rule_event(rule: dict, event: dict) -> bool:
    """Event-driven match (v0.1 semantics). Stateful rules return False here."""
    if is_stateful(rule):
        return False
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


def match_rule_stateful(rule: dict, chain_st: dict, now_ms: int) -> bool:
    """Stateful match: `absent` clause fires when no event of given kind
    has happened in the last `for_seconds`. Requires the chain to have
    at least one event already (so we have a meaningful "for X seconds"
    reference point). Returns False on empty chains."""
    if not is_stateful(rule):
        return False
    spec = rule["match"]["absent"]
    kind = spec.get("kind")
    for_seconds = spec.get("for_seconds", 0)
    if not kind or for_seconds <= 0:
        return False
    if chain_st.get("first_ts") is None:
        return False  # chain has no events yet
    last_kind_ts = chain_st.get("last_kind_ts", {}).get(kind)
    reference_ts = last_kind_ts if last_kind_ts is not None else chain_st["first_ts"]
    return (now_ms - reference_ts) > (for_seconds * 1000)


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
                               capture_output=True, timeout=120)
            return {"action": "shell", "cmd": cmd_str,
                    "ok": r.returncode == 0,
                    "stdout_tail": r.stdout.decode()[-160:],
                    "stderr_tail": r.stderr.decode()[-160:] if r.returncode != 0 else ""}

        elif t == "imessage":
            to = action.get("to", "")
            body = render_template(action.get("body", ""), event)
            if dry_run:
                return {"action": "imessage", "to": to, "body": body, "dry_run": True}
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


def initial_scan(chain_path: str, log: logging.Logger) -> dict:
    """Inspect a chain file and return its current state:
        {size, last_seq, first_ts, last_kind_ts: {kind: ts_ms}}
    Used at boot to mark existing events as 'already seen' (so engine
    restart never fires backfilled events) and to seed the kind→last_ts
    map used by stateful absence matching.
    """
    state = {"size": 0, "last_seq": -1, "first_ts": None, "last_kind_ts": {}}
    p = Path(chain_path)
    if not p.exists():
        return state
    try:
        state["size"] = p.stat().st_size
        with open(p, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if state["first_ts"] is None:
                    state["first_ts"] = ev.get("ts")
                seq = ev.get("seq")
                if isinstance(seq, int) and seq > state["last_seq"]:
                    state["last_seq"] = seq
                k = ev.get("kind")
                t = ev.get("ts")
                if k and t is not None:
                    state["last_kind_ts"][k] = t
    except Exception as e:
        log.warning(f"initial_scan failed for {chain_path}: {e}")
    return state


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


def fire_rule(rule: dict, event: dict, chain_name: str, dry_run: bool,
              meta: Any, log: logging.Logger) -> list[dict]:
    """Execute a rule's actions and return results. Gates shell actions on
    rule's `auto: true` flag — without it, shell actions are skipped with
    a 'BLOCKED' log entry."""
    is_auto = rule.get("auto", False)
    results: list[dict] = []
    for action in rule.get("do", []):
        if action.get("type") == "shell" and not is_auto:
            log.info(f"  -> BLOCKED shell action (rule lacks auto: true): "
                     f"{render_template(action.get('cmd', ''), event)[:120]}")
            results.append({"action": "shell", "blocked": "no_auto_flag",
                            "cmd": render_template(action.get("cmd", ""), event)})
            continue
        res = execute_action(action, event, chain_name, dry_run, log)
        results.append(res)
        log.info(f"  -> {json.dumps(res)[:240]}")
    emit_meta_receipt(meta, rule["name"], chain_name, event, results, log)
    return results


def emit_meta_receipt(meta: Any, rule_name: str, chain_name: str, event: dict,
                      results: list[dict], log: logging.Logger) -> None:
    """Emit claim/external_action/verified triple for one rule firing."""
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
            summary=f"{len(results)} action(s); {'all_ok' if all_ok else 'some_failed_or_blocked'}",
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


def synthetic_stateful_event(rule: dict, chain_name: str, chain_st: dict, now_ms: int) -> dict:
    """For stateful rules, fabricate a minimal event-shaped dict so the
    meta-receipt emission has something to reference. seq is the chain's
    current last_seq (the event we'd be "responding to"), agent is the
    chain name, kind is a sentinel."""
    return {
        "seq": chain_st.get("last_seq", -1),
        "agent": chain_name,
        "kind": "stateful_match",
        "ts": now_ms,
        "data": {
            "absent_kind": rule["match"]["absent"]["kind"],
            "absent_for_seconds": rule["match"]["absent"]["for_seconds"],
            "chain": chain_name,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", required=True, help="Path to rules.py module")
    ap.add_argument("--meta-receipt", default=None,
                    help="Path to meta-receipt JSONL output (optional)")
    ap.add_argument("--meta-agent", default="iBitLabs/rule-engine-v0.2")
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
    log.info("== rule_engine v0.2 starting ==")
    log.info(f"  rules:        {args.rules}")
    log.info(f"  meta-receipt: {args.meta_receipt or 'disabled'}")
    log.info(f"  dry-run:      {args.dry_run}")

    try:
        CHAINS, RULES = load_rules_module(args.rules)
    except Exception as e:
        log.error(f"failed to load rules: {e}")
        return 1
    log.info(f"  chains:       {list(CHAINS.keys())}")
    stateful_count = sum(1 for r in RULES if is_stateful(r))
    auto_count = sum(1 for r in RULES if r.get("auto"))
    log.info(f"  rules loaded: {len(RULES)} ({stateful_count} stateful, {auto_count} auto)")

    meta = None
    if args.meta_receipt and _Receipt and not args.dry_run:
        try:
            meta = _Receipt(agent=args.meta_agent, out_path=args.meta_receipt)
            log.info(f"  meta-chain head seq={meta.seq}")
            # Emit a startup heartbeat so the meta chain is non-empty + monitor.html
            # has something to render.
            meta.heartbeat(status="started")
        except Exception as e:
            log.warning(f"  meta-receipt init failed: {e}")

    chain_state: dict[str, dict] = {}
    for name, path in CHAINS.items():
        st = initial_scan(path, log)
        st["path"] = path
        chain_state[name] = st
        log.info(f"  init {name}: size={st['size']} last_seq={st['last_seq']} "
                 f"first_ts={st['first_ts']} kinds={list(st['last_kind_ts'].keys())}")

    debounce: dict[str, float] = {}
    last_self_hb = time.time()

    running = {"flag": True}
    def _stop(signum, frame):
        log.info(f"received signal {signum}, exiting")
        running["flag"] = False
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    log.info("watching for new events + stateful absences...")
    while running["flag"]:
        time.sleep(args.poll_seconds)
        now_ms = int(time.time() * 1000)

        # Phase 1: read new events from each chain, fire event-driven rules
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
                # Update kind tracking for stateful matching
                if st.get("first_ts") is None:
                    st["first_ts"] = ev.get("ts")
                k, t = ev.get("kind"), ev.get("ts")
                if k and t is not None:
                    st["last_kind_ts"][k] = t
                # Evaluate event-driven rules
                for rule in RULES:
                    if is_stateful(rule):
                        continue
                    rule_chains = rule.get("chains", ["*"])
                    if "*" not in rule_chains and chain_name not in rule_chains:
                        continue
                    if not match_rule_event(rule, ev):
                        continue
                    debounce_sec = rule.get("debounce_seconds", 0)
                    now = time.time()
                    if debounce_sec > 0 and (now - debounce.get(rule["name"], 0)) < debounce_sec:
                        log.info(f"DEBOUNCED rule='{rule['name']}' chain={chain_name} seq={seq}")
                        continue
                    debounce[rule["name"]] = now
                    log.info(f"FIRE rule='{rule['name']}' chain={chain_name} "
                             f"seq={seq} kind={ev.get('kind')}")
                    fire_rule(rule, ev, chain_name, args.dry_run, meta, log)

        # Phase 2: evaluate stateful absence rules per chain
        for chain_name, st in chain_state.items():
            for rule in RULES:
                if not is_stateful(rule):
                    continue
                rule_chains = rule.get("chains", ["*"])
                if "*" not in rule_chains and chain_name not in rule_chains:
                    continue
                if not match_rule_stateful(rule, st, now_ms):
                    continue
                debounce_sec = rule.get("debounce_seconds", 0)
                now = time.time()
                if debounce_sec > 0 and (now - debounce.get(rule["name"], 0)) < debounce_sec:
                    continue
                debounce[rule["name"]] = now
                synth = synthetic_stateful_event(rule, chain_name, st, now_ms)
                log.info(f"FIRE stateful rule='{rule['name']}' chain={chain_name} "
                         f"absent_kind={synth['data']['absent_kind']} "
                         f"absent_for={synth['data']['absent_for_seconds']}s")
                fire_rule(rule, synth, chain_name, args.dry_run, meta, log)

        # Phase 3: engine self-heartbeat
        if meta is not None and (time.time() - last_self_hb) >= SELF_HEARTBEAT_SECONDS:
            try:
                meta.heartbeat(status="alive",
                               watched_chains=len(chain_state),
                               rules=len(RULES))
                last_self_hb = time.time()
            except Exception as e:
                log.warning(f"self-heartbeat write failed: {e}")

    log.info("== rule_engine stopped ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
