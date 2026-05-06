#!/usr/bin/env python3
"""anchor_daily.py — emit a daily anchor event linking chain head to IPFS.

Computes the Merkle root over the chain's seq range, uploads the chain
JSONL to a permanent storage layer, and appends an `anchor` event with
the resulting URI. SPEC §10.

Backends, tried in order; first one with credentials configured wins:
    1. Pinata        (env: PINATA_JWT)
    2. Web3.Storage  (env: W3S_TOKEN)
    3. Local IPFS    (`ipfs` on PATH, daemon running on :5001)
    4. Dry-run       (no upload; emits anchor with placeholder URI)

Usage:
    PYTHONPATH=~/Documents/receipt python3 scripts/anchor_daily.py
    PYTHONPATH=~/Documents/receipt python3 scripts/anchor_daily.py --chain ~/path/to/x.jsonl --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/Documents/receipt"))

from receipt import Receipt
from receipt.chain import compute_hash

DEFAULT_CHAIN = Path("~/ibitlabs/audit_export/sniper-v5.1.realtime.receipt.jsonl").expanduser()


# ── Merkle root over chain hashes (binary tree, hash duplicated for odd) ──
def _merkle_root(hashes: list[str]) -> str:
    """SHA-256 binary Merkle tree. Duplicates last hash for odd levels."""
    if not hashes:
        return "sha256:" + "0" * 64
    layer = [bytes.fromhex(h.split(":", 1)[1]) for h in hashes]
    while len(layer) > 1:
        if len(layer) % 2:
            layer.append(layer[-1])
        nxt = []
        for i in range(0, len(layer), 2):
            nxt.append(hashlib.sha256(layer[i] + layer[i + 1]).digest())
        layer = nxt
    return "sha256:" + layer[0].hex()


# ── Backends ──
def _upload_pinata(jsonl_bytes: bytes, name: str) -> str | None:
    jwt = os.environ.get("PINATA_JWT")
    if not jwt:
        return None
    import urllib.request, uuid
    boundary = "----receipt" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f"Content-Type: application/x-ndjson\r\n\r\n"
    ).encode("utf-8") + jsonl_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        "https://api.pinata.cloud/pinning/pinFileToIPFS",
        data=body,
        headers={
            "Authorization": f"Bearer {jwt}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read())
            cid = d.get("IpfsHash")
            return f"ipfs://{cid}" if cid else None
    except Exception as e:
        print(f"  pinata upload failed: {e}", file=sys.stderr)
        return None


def _upload_w3s(jsonl_bytes: bytes, name: str) -> str | None:
    token = os.environ.get("W3S_TOKEN")
    if not token:
        return None
    import urllib.request
    req = urllib.request.Request(
        "https://api.web3.storage/upload",
        data=jsonl_bytes,
        headers={"Authorization": f"Bearer {token}",
                 "X-NAME": name,
                 "Content-Type": "application/x-ndjson"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read())
            cid = d.get("cid")
            return f"ipfs://{cid}" if cid else None
    except Exception as e:
        print(f"  w3s upload failed: {e}", file=sys.stderr)
        return None


def _upload_local_ipfs(jsonl_bytes: bytes, name: str) -> str | None:
    if not shutil.which("ipfs"):
        return None
    try:
        out = subprocess.run(
            ["ipfs", "add", "-Q", "--cid-version=1", "-"],
            input=jsonl_bytes, capture_output=True, timeout=30, check=True,
        )
        cid = out.stdout.decode().strip()
        return f"ipfs://{cid}" if cid else None
    except Exception as e:
        print(f"  local ipfs failed: {e}", file=sys.stderr)
        return None


def _upload(jsonl_bytes: bytes, name: str, dry_run: bool) -> tuple[str, str]:
    """Returns (anchor_uri, backend_name)."""
    if dry_run:
        return "ipfs://pending-dry-run", "dry_run"
    for fn, label in [
        (_upload_pinata, "pinata"),
        (_upload_w3s, "web3.storage"),
        (_upload_local_ipfs, "local_ipfs"),
    ]:
        uri = fn(jsonl_bytes, name)
        if uri:
            return uri, label
    return "ipfs://pending-no-backend-configured", "unconfigured"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chain", default=str(DEFAULT_CHAIN),
                    help="JSONL chain to anchor")
    ap.add_argument("--dry-run", action="store_true",
                    help="compute Merkle root + emit anchor event, skip upload")
    args = ap.parse_args()

    chain = Path(os.path.expanduser(args.chain))
    if not chain.exists():
        print(f"chain not found: {chain}", file=sys.stderr)
        return 2

    raw = chain.read_bytes()
    events = [json.loads(l) for l in raw.decode().splitlines() if l.strip()]
    if not events:
        print("chain is empty", file=sys.stderr)
        return 2

    seq_lo, seq_hi = events[0]["seq"], events[-1]["seq"]
    hashes = [e["hash"] for e in events if e.get("kind") != "anchor"]
    root = _merkle_root(hashes)
    print(f"chain:        {chain}")
    print(f"events:       {len(events)} (seq {seq_lo}..{seq_hi})")
    print(f"merkle_root:  {root}")

    name = f"{chain.stem}_seq{seq_lo}-{seq_hi}.jsonl"
    anchor_uri, backend = _upload(raw, name, dry_run=args.dry_run)
    print(f"backend:      {backend}")
    print(f"anchor_uri:   {anchor_uri}")

    # append anchor event to the same chain
    agent = events[0].get("agent")
    r = Receipt(agent=agent, out_path=chain)
    seq = r.anchor(merkle_root=root, anchor_uri=anchor_uri, anchor_kind="ipfs")
    print(f"appended anchor event seq={seq}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
