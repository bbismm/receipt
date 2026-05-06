// Receipt v0.1.1 — verifier algorithm (browser + Node compatible).
//
// Single source of truth for the JS implementation. Used by:
//   - viewer/index.html (browser via <script src>)
//   - tests/cross_impl_test.py (Node, via subprocess)
//
// Cross-impl invariant: this file + receipt/score.py + receipt/chain.py
// MUST produce byte-identical outputs for any input chain. Tests/cross_impl
// enforces this on fixtures.
//
// Hashing: detects environment.
//   - Browser: globalThis.crypto.subtle.digest (async)
//   - Node 16+: node:crypto.createHash (sync)

(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory(require("crypto"));  // Node
  } else {
    root.ReceiptScore = factory(null);            // Browser
  }
}(typeof self !== "undefined" ? self : this, function (nodeCrypto) {

  const SCORE_SCHEMA_VERSION = "0.1.1";
  const HASH_PREFIX = "sha256:";
  const GENESIS_PREV = HASH_PREFIX + "0".repeat(64);
  const CANONICAL_KEYS = ["v","schema_version","ts","seq","agent","kind","data","prev_hash"];
  const PERMANENT_ANCHORS_TIER = new Set(["btc_op_return","ethereum","arweave"]);
  const SEMI_PERM_ANCHORS_TIER = new Set(["ipfs"]);
  const SOCIAL_ANCHORS_TIER = new Set(["twitter","moltbook","github_commit"]);
  const REALTIME_TIERS = new Set(["exchange_realtime","exchange_delayed","api_verified"]);
  const HEARTBEAT_BUCKET_MS = 5*60*1000;
  const DEFAULT_WINDOW_MS = 7*24*3600*1000;

  function anchorTier(kind) {
    if (PERMANENT_ANCHORS_TIER.has(kind)) return "permanent";
    if (SEMI_PERM_ANCHORS_TIER.has(kind)) return "semi_permanent";
    if (SOCIAL_ANCHORS_TIER.has(kind)) return "social";
    return "unknown";
  }

  // ── Hashing (env-detected) ───────────────────────────────────────────
  async function sha256hex(text) {
    if (nodeCrypto) {
      return nodeCrypto.createHash("sha256").update(text, "utf8").digest("hex");
    }
    const buf = new TextEncoder().encode(text);
    const hash = await globalThis.crypto.subtle.digest("SHA-256", buf);
    return [...new Uint8Array(hash)].map(b => b.toString(16).padStart(2,"0")).join("");
  }

  function jsonCanonical(v) {
    if (v === null) return "null";
    if (typeof v === "boolean") return v ? "true" : "false";
    if (typeof v === "number") return JSON.stringify(v);
    if (typeof v === "string") return JSON.stringify(v);
    if (Array.isArray(v)) return "[" + v.map(jsonCanonical).join(",") + "]";
    return "{" + Object.keys(v).sort().map(k => JSON.stringify(k)+":"+jsonCanonical(v[k])).join(",") + "}";
  }

  function canonical(ev) {
    const body = {};
    for (const k of CANONICAL_KEYS) if (k in ev) body[k] = ev[k];
    return jsonCanonical(body);
  }

  // ── verifyChain ───────────────────────────────────────────────────────
  async function verifyChain(events) {
    if (events.length === 0) return {status:"EMPTY", n_events:0, n_claims:0, n_verified:0, n_errors:0, n_rejected:0, issues:[]};
    let prev = GENESIS_PREV, expectedSeq = 0;
    const claims=new Set(), verified=new Set(), errors=new Set(), rejected=new Set(), suspect=new Set();
    let lastReconTs=null, lastEventTs=null;
    const issues = [];
    for (const ev of events) {
      if (ev.seq !== expectedSeq) { issues.push("seq gap at " + expectedSeq); return fail(); }
      if (ev.prev_hash !== prev) { issues.push("prev_hash mismatch at seq " + expectedSeq); return fail(); }
      const recomputed = HASH_PREFIX + (await sha256hex(canonical(ev)));
      if (ev.hash !== recomputed) { issues.push("hash mismatch at seq " + expectedSeq); return fail(); }
      const d = ev.data || {};
      if (ev.kind === "claim") claims.add(ev.seq);
      else if (ev.kind === "verified") {
        if (d.claim_seq != null) verified.add(d.claim_seq);
        if (!(d.match || {}).id_match) suspect.add(ev.seq);
      } else if (ev.kind === "error") { if (d.claim_seq != null) errors.add(d.claim_seq); }
      else if (ev.kind === "signal_rejected") rejected.add(ev.seq);
      else if (ev.kind === "reconciliation") lastReconTs = ev.ts;
      lastEventTs = ev.ts; prev = ev.hash; expectedSeq++;
    }
    function fail() { return {status:"INVALID_CHAIN", n_events:events.length, n_claims:0, n_verified:0, n_errors:0, n_rejected:0, issues}; }
    return {
      status: "OK",
      n_events: events.length, n_claims: claims.size,
      n_verified: [...claims].filter(c => verified.has(c)).length,
      n_errors: errors.size, n_rejected: rejected.size, issues,
    };
  }

  function trustTierMajority(events) {
    const counts = {};
    for (const e of events) {
      if (e.kind === "verified" || e.kind === "reconciliation") {
        const t = (e.data||{}).trust_tier;
        if (t) counts[t] = (counts[t]||0)+1;
      }
    }
    if (Object.keys(counts).length === 0) return "manual";
    return Object.entries(counts).sort((a,b)=>b[1]-a[1])[0][0];
  }

  // ── computeScore ──────────────────────────────────────────────────────
  function computeScore(events, report) {
    if (events.length === 0) return {schema_version: SCORE_SCHEMA_VERSION, verdict:"Unverified", verdict_color:"red", score:0, dimensions:{}, summary:{}, rule_breaks:["empty"], suspicion_flags:[], examples:{mismatch:[],missing_receipts:[]}};
    if (report.status === "INVALID_CHAIN") return {schema_version: SCORE_SCHEMA_VERSION, verdict:"Unverified", verdict_color:"red", score:0, dimensions:{Integrity:{score:0,detail:"hash chain broken"}}, summary:{}, rule_breaks:["invalid_chain"], suspicion_flags:[], examples:{mismatch:[],missing_receipts:[]}};

    const claims = new Set(events.filter(e=>e.kind==="claim").map(e=>e.seq));
    const terminated = new Set();
    for (const e of events) {
      const cs = (e.data||{}).claim_seq;
      if ((e.kind==="verified"||e.kind==="error") && cs!=null) terminated.add(cs);
      else if (e.kind==="signal_rejected") terminated.add(e.seq);
    }
    const covered = [...claims].filter(c=>terminated.has(c)).length;
    const coverage = claims.size === 0 ? 1 : covered / claims.size;
    const cov_score = +(coverage * 100).toFixed(1);
    const cov_detail = `${covered}/${claims.size||0} claims have receipts`;

    const verifs = events.filter(e=>e.kind==="verified");
    const verified_ok = verifs.filter(e => (e.data.match||{}).id_match).length;
    const receipts = verifs.length;
    const mismatches = receipts - verified_ok;
    const mismatch_rate = receipts ? mismatches / receipts : 0;
    const accuracy_raw = receipts ? verified_ok / receipts : 1;
    const acc_score = +Math.max(0, Math.min(100, (accuracy_raw - mismatch_rate*2)*100)).toFixed(1);
    const acc_detail = receipts ? `${verified_ok}/${receipts} verified, ${mismatches} mismatch${mismatches!==1?'es':''}` : "no verified events";

    const last = events[events.length-1].ts;
    const windowEnd = last;
    const windowStart = windowEnd - DEFAULT_WINDOW_MS;
    const span = Math.max(windowEnd - windowStart, HEARTBEAT_BUCKET_MS);
    const totalBuckets = Math.max(1, Math.floor(span/HEARTBEAT_BUCKET_MS));
    const buckets = new Set();
    for (const e of events) {
      const b = Math.floor((e.ts - windowStart) / HEARTBEAT_BUCKET_MS);
      if (b >= 0 && b < totalBuckets) buckets.add(b);
    }
    const cons_ratio = buckets.size / totalBuckets;
    const cons_score = +Math.min(cons_ratio*100, 100).toFixed(1);
    const cons_detail = `covered ${buckets.size}/${totalBuckets} 5-min buckets in ${(span/3600000).toFixed(0)}h window`;

    let trans_score = 100;
    const trans_notes = [];
    if (!events.some(e=>e.kind==="error")) { trans_score -= 20; trans_notes.push("−20 no error events (real systems fail)"); }
    if (!events.some(e=>e.kind==="reconciliation")) { trans_score -= 30; trans_notes.push("−30 no reconciliation event"); }
    const allHaveTol = verifs.every(e => ((e.data.match||{}).tolerance_used) != null);
    if (!allHaveTol) { trans_score -= 20; trans_notes.push("−20 missing match.tolerance_used"); }
    trans_score = Math.max(0, trans_score);
    const trans_detail = trans_notes.length ? trans_notes.join("; ") : "all transparency criteria met";

    const anchors = events.filter(e=>e.kind==="anchor");
    let int_score = 100, int_detail = "chain valid";
    if (anchors.length === 0) { int_score = 50; int_detail = "no anchor event"; }
    else {
      const a = anchors[anchors.length-1];
      const kind = a.data.anchor_kind || "";
      const tier = anchorTier(kind);
      const ageH = (last - a.ts)/3600000;
      const notes = [];
      if (tier === "social") { int_score = Math.min(int_score, 90); notes.push(`social tier (${kind}) capped 90`); }
      else if (tier === "semi_permanent") { int_score = Math.min(int_score, 95); notes.push(`semi-permanent (${kind}) capped 95`); }
      else if (tier === "unknown") { int_score = Math.min(int_score, 80); notes.push(`unknown anchor (${kind}) capped 80`); }
      if (ageH > 24) { int_score -= 30; notes.push(`anchor ${ageH.toFixed(1)}h old (−30)`); }
      if (ageH > 168) { int_score = Math.min(int_score, 30); notes.push("anchor > 7d cap 30"); }
      int_score = Math.max(0, Math.min(100, int_score));
      int_detail = `anchor ${ageH.toFixed(1)}h ago on ${kind} [tier=${tier}]` + (notes.length ? "; " + notes.join("; ") : "");
    }

    const dims = {
      Coverage:     {score: cov_score,   detail: cov_detail},
      Accuracy:     {score: acc_score,   detail: acc_detail},
      Consistency:  {score: cons_score,  detail: cons_detail},
      Transparency: {score: trans_score, detail: trans_detail},
      Integrity:    {score: int_score,   detail: int_detail},
    };
    const raw = Math.round((cov_score+acc_score+cons_score+trans_score+int_score)/5);

    const tierMaj = trustTierMajority(events);
    const hasAnchor = anchors.length > 0;
    const hasRecon = events.some(e=>e.kind==="reconciliation");
    const anchorRecent = hasAnchor && (last - anchors[anchors.length-1].ts) <= 24*3600000;
    let verdict, color;
    if (coverage >= 0.9 && accuracy_raw >= 0.8 && mismatch_rate <= 0.05
        && hasAnchor && hasRecon && anchorRecent && REALTIME_TIERS.has(tierMaj)) {
      verdict = "Verified"; color = "green";
    } else if (coverage < 0.5 || mismatch_rate > 0.20 || !hasAnchor || cons_score < 30 || tierMaj === "manual") {
      verdict = "Unverified"; color = "red";
    } else {
      verdict = "Mixed"; color = "yellow";
    }

    let score = raw;
    if (verdict === "Verified" && score < 75) score = 75;
    if (verdict === "Unverified" && score >= 40) score = 39;
    if (verdict === "Mixed") score = Math.max(40, Math.min(74, score));

    const errCount = events.filter(e=>e.kind==="error").length;
    const flags = [];
    if (mismatches > 0) flags.push(`${mismatches}_mismatches`);
    if (errCount) flags.push(`${errCount}_error_events`);
    if (cons_score < 60) flags.push("missing_heartbeat_periods");
    if (anchors.length === 0) flags.push("no_anchor");
    else if ((last - anchors[anchors.length-1].ts)/3600000 > 24) flags.push("anchor_stale");
    if (tierMaj === "backfill_local") flags.push("backfill_only_evidence");
    if (tierMaj === "manual") flags.push("manual_attested_only");

    const exMismatch = [];
    for (const e of verifs) {
      const m = e.data.match || {};
      if (!m.id_match) exMismatch.push({seq:e.seq, reason:"id_not_matched"});
      else if (m.price_match === false) exMismatch.push({seq:e.seq, reason:"price_out_of_tolerance"});
      else if (m.size === false) exMismatch.push({seq:e.seq, reason:"size_mismatch"});
    }
    const exMissing = [];
    for (const c of [...claims].filter(c=>!terminated.has(c)).slice(0,5)) exMissing.push({seq:c});

    const suspicion = [];
    if (claims.size >= 20 && errCount === 0) suspicion.push("no_error_events");
    if (DEFAULT_WINDOW_MS > 24*3600000) {
      let maxGap = 0;
      for (let i=1; i<events.length; i++) maxGap = Math.max(maxGap, (events[i].ts - events[i-1].ts)/3600000);
      if (maxGap > 24) suspicion.push("sudden_activity_gap");
    }
    if (verifs.length >= 10) {
      for (let i=0; i<=verifs.length-10; i++) {
        const win = verifs.slice(i, i+10);
        const mm = win.filter(e => !((e.data.match||{}).id_match)).length;
        if (mm >= 3) { suspicion.push("mismatch_cluster"); break; }
      }
    }
    const tierCounts = {};
    for (const e of events) {
      const t = (e.data||{}).trust_tier;
      if (t) tierCounts[t] = (tierCounts[t]||0)+1;
    }
    const bf = tierCounts.backfill_local||0, rt = tierCounts.exchange_realtime||0;
    if (bf > 0 && rt > 0 && bf > rt*5) suspicion.push("backfill_dominant_pretending_realtime");
    if (anchors.length > 0 && anchors.every(a => anchorTier(a.data.anchor_kind||"") === "social")) suspicion.push("only_social_anchor");
    if (DEFAULT_WINDOW_MS > 24*3600000 && !events.some(e=>e.kind==="heartbeat")) suspicion.push("heartbeat_silence");

    return {
      schema_version: SCORE_SCHEMA_VERSION,
      window: {from_ms: windowStart, to_ms: windowEnd, source: "default_last_7d"},
      verdict, verdict_color: color, score, raw_score_pre_clamp: raw,
      trust_tier_majority: tierMaj,
      dimensions: dims,
      summary: {total_claims: claims.size, receipts, verified: verified_ok, mismatch: mismatches, errors: errCount},
      rule_breaks: flags,
      suspicion_flags: suspicion,
      examples: {mismatch: exMismatch.slice(0,5), missing_receipts: exMissing},
    };
  }

  return { verifyChain, computeScore, anchorTier, SCORE_SCHEMA_VERSION };
}));
