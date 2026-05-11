"""rules_example.py — Example rules file for `rule_engine.py`.

Copy this to a private location (e.g., ~/myproject/receipt-rule-engine/rules.py)
and edit. Then run:

    python3 rule_engine.py --rules /path/to/your/rules.py \\
                           --meta-receipt /path/to/meta.receipt.jsonl

The rules file must define two top-level values: CHAINS and RULES.

CHAINS maps short names (used in `chains:` lists) to JSONL paths on disk.
RULES is a list of dicts. Two rule types are supported:

EVENT-DRIVEN rule shape:

    {
        "name": "...",                         # required, debounce key
        "chains": ["live", "shadow"] or ["*"], # optional, default ["*"]
        "match": {                             # required
            "kind": "...",                     # event kind
            "data.field.path": value-or-list,  # equality or membership
            "data.bool_field": True/False,     # bool comparison
        },
        "do": [                                # required, list of actions
            {"type": "ntfy", "topic": "...",
             "body": "Free-form with {data.x} placeholders"},
            {"type": "shell", "cmd": "..."},   # requires auto: True (see below)
            {"type": "imessage", "to": "+1...", "body": "..."},
        ],
        "debounce_seconds": 30,                # optional, default 0
    }

STATEFUL ABSENCE rule shape (v0.2):

    {
        "name": "...",
        "chains": ["live"],
        "match": {                             # ABSENCE pattern, mutually
            "absent": {                        # exclusive with event match
                "kind": "anchor",              # event kind that's missing
                "for_seconds": 86400,          # for at least this many sec
            },
        },
        "auto": True,                          # required for shell actions
        "do": [...],
        "debounce_seconds": 1800,
    }

Stateful rules fire on every poll tick (not on event arrival). They check
whether the chain has had any event of `kind` in the last `for_seconds`.
If not, the rule fires. Stateful rules need the chain to have at least
ONE event already (so there's a meaningful "for X seconds" reference);
they never fire on empty chains.

AUTO-EXECUTION SAFETY (v0.2):

Shell actions ONLY run if the rule has `auto: True`. Without it, shell
actions are BLOCKED (logged but skipped). ntfy / iMessage are never gated
because they're low-blast-radius (just notify, can't break state).

Tier discipline — operator policy:
  Tier 1 (idempotent, reversible):   auto: True is safe.
    Examples: anchor / reconcile / sync / kickstart-daemon
  Tier 2 (state-fixing, recoverable): auto: True with tight debounce.
    Examples: bot restart, switch-to-backup-signal
  Tier 3 (irreversible, live money):  NEVER set auto: True.
    Examples: bootout sniper, kill positions, modify chains by hand
    Use ntfy only — humans must consciously act.
"""

CHAINS = {
    "live":   "/path/to/your/agent-live.realtime.receipt.jsonl",
    "shadow": "/path/to/your/agent-shadow.realtime.receipt.jsonl",
}

RULES = [
    # ---- Trading-bot example ----
    {
        "name": "live_position_opened",
        "chains": ["live"],
        "match": {
            "kind": "claim",
            "data.action": ["open_long", "open_short"],
        },
        "do": [
            {
                "type": "ntfy",
                "topic": "your-ntfy-topic",
                "title": "Position opened",
                "tags": "bell",
                "body": "{data.action} {data.symbol} size={data.size} @ ${data.price_intended}",
            },
        ],
        "debounce_seconds": 10,
    },
    {
        "name": "reconciliation_failed",
        "chains": ["*"],
        "match": {
            "kind": "reconciliation",
            "data.match": False,
        },
        "do": [
            {
                "type": "ntfy",
                "topic": "your-ntfy-topic",
                "priority": "urgent",
                "tags": "warning,rotating_light",
                "title": "Reconciliation FAILED",
                "body": "agent={agent} seq={seq}",
            },
            # Optional: also run a shell command to bootout the agent
            # {"type": "shell", "cmd": "/path/to/your/bootout-script.sh"},
        ],
        "debounce_seconds": 60,
    },

    # ---- Code-writing-agent example ----
    # When a code-writing AI claims to edit a file, ntfy with the file path
    # and the diff hash so a human reviewer can spot-check.
    {
        "name": "code_edit_claimed",
        "chains": ["code_agent"],
        "match": {
            "kind": "claim",
            "data.action": "edit_file",
        },
        "do": [
            {
                "type": "ntfy",
                "topic": "your-ntfy-topic",
                "title": "Code edit claimed",
                "body": "AI says it edited {data.target_file}: {data.intent}",
            },
        ],
        "debounce_seconds": 5,
    },

    # ---- Generic: alert on any error event ----
    {
        "name": "any_error",
        "match": {
            "kind": "error",
        },
        "do": [
            {
                "type": "ntfy",
                "topic": "your-ntfy-topic",
                "priority": "high",
                "title": "Error in {agent}",
                "body": "{data.phase} phase: {data.message}",
            },
        ],
        "debounce_seconds": 30,
    },

    # ---- v0.2 stateful example: auto-anchor when stale ----
    # Fires when the live chain has had NO `anchor` event in 24h+. Runs
    # anchor_daily.py automatically. `auto: True` is the explicit opt-in
    # required for the shell action to actually execute (Tier 1 safety).
    #
    # SDK uses fcntl.flock since v0.2, so concurrent writes from the bot
    # and the anchor script don't corrupt the chain.
    {
        "name": "auto_anchor_stale",
        "chains": ["live"],
        "match": {"absent": {"kind": "anchor", "for_seconds": 86400}},
        "auto": True,
        "do": [
            {"type": "shell",
             "cmd": "/usr/bin/python3 /path/to/anchor_daily.py --chain /path/to/live.jsonl"},
            {"type": "ntfy", "topic": "your-ntfy-topic", "priority": "low",
             "title": "Auto-anchored stale chain",
             "body": "Chain had no anchor in 24h+; rule engine fixed it"},
        ],
        "debounce_seconds": 1800,
    },
]
