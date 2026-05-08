# Verifier API v0.1

**Status**: draft. License: CC-BY-4.0.
**Goal**: a stateless HTTP API anyone can self-host that produces verdicts
on Receipt chains. Reference deployment planned at `receipt.ibitlabs.com`
(not yet live).

## Design principles

1. **Stateless**: server doesn't store chains long-term. POST in JSONL,
   GET back verdict. (Caching by content-hash for performance only.)
2. **Open & forkable**: same API spec, anyone can run their own. Reference
   deployment is canonical but not exclusive.
3. **No CORS gating**: every endpoint is public read.
4. **No auth on reads**: trust scores must be quotable by anyone, no key.

## Endpoints

### `POST /v0/verify`

Verify a chain. Body is JSONL (one event per line) or JSON array.

**Request**
```http
POST /v0/verify
Content-Type: application/x-ndjson  (or application/json)

{"v":"0.1","schema_version":"1","ts":...,"seq":0,"agent":"X",...}
{"v":"0.1","schema_version":"1","ts":...,"seq":1,"agent":"X",...}
...
```

**Response**
```json
{
  "chain_status": "VERIFIED",
  "n_events": 419,
  "n_claims": 139,
  "n_verified": 139,
  "n_errors": 0,
  "issues": [],
  "trust_score": { ... full trust_score object per docs/trust_score.md ... },
  "head_hash": "sha256:e204c8...",
  "computed_at": 1714912321847
}
```

Status codes:
- `200 OK` — verdict computed (regardless of VERIFIED vs INVALID)
- `400 Bad Request` — body not valid JSONL/JSON
- `413 Payload Too Large` — chain exceeds size limit (default 100MB)

### `GET /v0/verify/<agent>`

Pull and verify the chain published by `<agent>`. Server resolves `<agent>`
via the **agent registry** (see below) and dereferences their public chain
URL.

```
GET /v0/verify/iBitLabs%2Fsniper-v5.1
→ same response shape as POST /v0/verify
```

### `GET /v0/agent/<agent>`

Agent profile metadata.

**Response**
```json
{
  "agent": "iBitLabs/sniper-v5.1",
  "chain_url": "https://ibitlabs.com/data/sniper-v5.1.receipt.jsonl",
  "viewer_url": "https://receipt.ibitlabs.com/v/iBitLabs%2Fsniper-v5.1",
  "registered_at": 1714000000000,
  "last_anchor": {
    "ts": 1714900000000,
    "anchor_kind": "ipfs",
    "anchor_uri": "ipfs://Qm..."
  },
  "score_history": [
    {"date": "2026-05-04", "score": 78},
    {"date": "2026-05-03", "score": 76}
  ]
}
```

### `POST /v0/agent/register`

Register an agent's chain URL. Self-attested; anyone can register, but
registration alone confers no credibility — only the chain itself does.

```json
{
  "agent": "iBitLabs/sniper-v5.1",
  "chain_url": "https://ibitlabs.com/data/sniper-v5.1.receipt.jsonl",
  "contact": "hello@ibitlabs.com"
}
```

The server tries to fetch the chain URL. If reachable + valid Receipt
schema, registration succeeds. **No KYC, no review** — the trust comes
from the chain's content, not from registration.

### `POST /v0/anchor/check`

Independently re-verify that an anchor URI contains the claimed
merkle_root.

```json
{
  "anchor_uri": "ipfs://Qm.../receipt_2026-05-05.jsonl",
  "anchor_kind": "ipfs",
  "expected_merkle_root": "sha256:..."
}
```

Returns `{"verified": true/false, "fetched_root": "sha256:...", "notes": []}`.

### `GET /v0/leaderboard`

Public-good ranked list of all registered agents by trust score.

```json
{
  "as_of": 1714912321847,
  "agents": [
    {"agent": "iBitLabs/sniper-v5.1", "score": 78, "n_verified": 139},
    ...
  ]
}
```

This is the **distribution flywheel**. As more agents register, the
leaderboard becomes the canonical "who's actually publishing receipts"
list. Refusing to register or scoring low becomes its own signal.

## Reconciliation adapter contract (server-side)

Verifier server MAY optionally run live reconciliation against venues if
configured with read-only API keys (operator-provided per their own
exchange account, never the operator-being-verified).

For v0.1, default is **static verification only** (no live re-fetch).
Live reconciliation = v0.2 deployment feature.

## Rate limits

- Anonymous reads: 100 req/min/IP
- POST /verify: 10 req/min/IP (chain hashing is the expensive op)
- POST /agent/register: 5 req/day/IP

## Anti-abuse

- POST /verify body capped at 100MB
- /agent/register chain_url MUST resolve to ≤100MB JSONL
- Verifier MAY refuse chains older than 5 years (storage cost)
- Verifier MUST NOT re-publish chain content; only verdicts. Operators
  retain ownership of their chain bytes.

## Implementation outline (FastAPI / Python)

```python
@app.post("/v0/verify")
async def verify(body: bytes) -> VerifyResponse:
    events = parse_jsonl(body)
    report = verify_chain(events)            # from receipt.chain
    score = compute_trust_score(events, report)  # from receipt.score
    return VerifyResponse(
        chain_status=report.status,
        ...
        trust_score=score.to_dict(),
    )
```

Single file, ~200 lines, can be deployed on Cloudflare Workers / Vercel
Functions / a $5 VPS. The simplicity is the point.
