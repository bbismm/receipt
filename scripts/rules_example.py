"""rules_example.py — Example rules file for `rule_engine.py`.

Copy this to a private location (e.g., ~/myproject/receipt-rule-engine/rules.py)
and edit. Then run:

    python3 rule_engine.py --rules /path/to/your/rules.py \\
                           --meta-receipt /path/to/meta.receipt.jsonl

The rules file must define two top-level values: CHAINS and RULES.

CHAINS maps short names (used in `chains:` lists) to JSONL paths on disk.
RULES is a list of dicts, each with the shape:

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
            {"type": "shell", "cmd": "..."},
            {"type": "imessage", "to": "+1...", "body": "..."},
        ],
        "debounce_seconds": 30,                # optional, default 0
    }
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
]
