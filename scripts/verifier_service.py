#!/usr/bin/env python3
"""verifier_service.py — minimal stateless HTTP verifier for Receipt v0.1.1.

Single-file, stdlib-only. Designed to run on any $5/mo VPS.
Implements the API surface from docs/verifier_api.md (subset).

Endpoints
---------
GET  /                          — HTML landing (links to viewer)
GET  /health                    — liveness probe
POST /v0/verify                 — body: JSONL or JSON array → verdict + score
GET  /v0/verify?url=<chain>     — fetch JSONL from URL, verify
GET  /v0/leaderboard            — registered agents ranked by score
POST /v0/agent/register         — body: {agent, chain_url} → 200/400

Storage
-------
Agent registry: receipt/data/agents.json (created on first register).

Run
---
    PYTHONPATH=~/Documents/receipt python3 scripts/verifier_service.py
    # then: curl http://localhost:8088/health
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.expanduser("~/Documents/receipt"))

from receipt import compute_score, verify_chain

REGISTRY = Path(os.path.expanduser("~/.receipt-verifier/agents.json"))
REGISTRY.parent.mkdir(parents=True, exist_ok=True)
_lock = threading.Lock()
MAX_BODY = 100 * 1024 * 1024  # 100MB


def _load_registry() -> dict:
    if not REGISTRY.exists():
        return {}
    try:
        return json.loads(REGISTRY.read_text())
    except Exception:
        return {}


def _save_registry(d: dict) -> None:
    REGISTRY.write_text(json.dumps(d, indent=2))


def _parse_chain(body: bytes) -> list[dict]:
    text = body.decode("utf-8")
    text = text.strip()
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(l) for l in text.splitlines() if l.strip()]


def _verify_payload(events: list[dict]) -> dict:
    report = verify_chain(events)
    score = compute_score(events, report)
    return {
        "chain_status": report.status.value,
        **score,
        "issues": report.issues,
    }


def _fetch_chain(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "receipt-verifier/0.1.1"})
    with urllib.request.urlopen(req, timeout=15) as r:
        if int(r.headers.get("Content-Length", 0) or 0) > MAX_BODY:
            raise ValueError("chain too large")
        body = r.read(MAX_BODY + 1)
        if len(body) > MAX_BODY:
            raise ValueError("chain exceeds 100MB limit")
    return _parse_chain(body)


LANDING_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>Receipt Verifier v0.1.1</title>
<style>body{font:16px/1.5 system-ui;max-width:680px;margin:48px auto;padding:0 20px;color:#1a1a1a}
code{background:#f4f4f4;padding:2px 6px;border-radius:3px}h1{margin-bottom:0.2em}
.tag{color:#666;font-style:italic}</style></head><body>
<h1>Receipt</h1>
<div class="tag">A minimal standard for verifiable actions.<br>If an agent claims it acted, it should produce a receipt.</div>
<h2>API</h2>
<ul>
<li><code>POST /v0/verify</code> — body: JSONL chain → verdict + trust score</li>
<li><code>GET /v0/verify?url=&lt;chain.jsonl&gt;</code> — fetch + verify</li>
<li><code>GET /v0/leaderboard</code> — registered agents ranked by score</li>
<li><code>POST /v0/agent/register</code> — <code>{agent, chain_url}</code></li>
</ul>
<h2>Browser viewer</h2>
<p>For an interactive UI that runs the verifier client-side in your
browser (no server, no API call), see the <a href="/viewer">viewer</a>.</p>
<h2>Spec & source</h2>
<p><a href="https://github.com/bbismm/receipt">github.com/bbismm/receipt</a></p>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write(f"[verifier] {self.address_string()} - {fmt % args}\n")

    # ── helpers ──
    def _send(self, code: int, body: dict | str, content_type: str = "application/json") -> None:
        if isinstance(body, dict):
            body_bytes = json.dumps(body, indent=2, ensure_ascii=False).encode("utf-8")
        else:
            body_bytes = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def _read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n > MAX_BODY:
            raise ValueError("body too large")
        return self.rfile.read(n) if n else b""

    # ── routes ──
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            return self._send(200, LANDING_HTML, "text/html")
        if u.path == "/health":
            return self._send(200, {"status": "ok", "schema_version": "0.1.1"})
        if u.path == "/v0/leaderboard":
            return self._handle_leaderboard()
        if u.path == "/v0/verify":
            qs = parse_qs(u.query)
            url = (qs.get("url") or [None])[0]
            if not url:
                return self._send(400, {"error": "missing ?url= query parameter"})
            return self._handle_verify_url(url)
        if u.path.startswith("/v0/verify/"):
            agent = u.path[len("/v0/verify/"):]
            return self._handle_verify_agent(agent)
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/v0/verify":
            return self._handle_verify_post()
        if u.path == "/v0/agent/register":
            return self._handle_register()
        return self._send(404, {"error": "not found"})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── handlers ──
    def _handle_verify_post(self):
        try:
            body = self._read_body()
            events = _parse_chain(body)
        except Exception as e:
            return self._send(400, {"error": f"parse failed: {e}"})
        return self._send(200, _verify_payload(events))

    def _handle_verify_url(self, url: str):
        try:
            events = _fetch_chain(url)
        except Exception as e:
            return self._send(400, {"error": f"fetch failed: {e}"})
        return self._send(200, _verify_payload(events))

    def _handle_verify_agent(self, agent: str):
        with _lock:
            reg = _load_registry()
        meta = reg.get(agent)
        if not meta:
            return self._send(404, {"error": f"agent not registered: {agent}"})
        try:
            events = _fetch_chain(meta["chain_url"])
        except Exception as e:
            return self._send(502, {"error": f"chain fetch failed: {e}",
                                     "agent": agent, "chain_url": meta["chain_url"]})
        return self._send(200, {"agent": agent, "chain_url": meta["chain_url"],
                                 **_verify_payload(events)})

    def _handle_leaderboard(self):
        with _lock:
            reg = _load_registry()
        rows = []
        for agent, meta in reg.items():
            try:
                events = _fetch_chain(meta["chain_url"])
                v = _verify_payload(events)
                rows.append({"agent": agent, "score": v.get("score"),
                             "verdict": v.get("verdict"), "n_events": v.get("summary", {}).get("total_claims", 0),
                             "chain_url": meta["chain_url"]})
            except Exception as e:
                rows.append({"agent": agent, "error": str(e), "chain_url": meta["chain_url"]})
        rows.sort(key=lambda r: r.get("score") or 0, reverse=True)
        return self._send(200, {"agents": rows, "count": len(rows)})

    def _handle_register(self):
        try:
            data = json.loads(self._read_body() or b"{}")
        except Exception:
            return self._send(400, {"error": "invalid JSON body"})
        agent = (data.get("agent") or "").strip()
        chain_url = (data.get("chain_url") or "").strip()
        if not agent or not chain_url:
            return self._send(400, {"error": "agent and chain_url required"})
        # smoke-test: try to fetch + parse
        try:
            events = _fetch_chain(chain_url)
            _ = _verify_payload(events)
        except Exception as e:
            return self._send(400, {"error": f"chain unreachable or invalid: {e}"})
        with _lock:
            reg = _load_registry()
            reg[agent] = {"chain_url": chain_url, "contact": data.get("contact", "")}
            _save_registry(reg)
        return self._send(200, {"agent": agent, "chain_url": chain_url, "registered": True})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8088)
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Receipt verifier v0.1.1 listening on http://{args.host}:{args.port}")
    print(f"  registry: {REGISTRY}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
