// Node runner — read a JSONL chain from arg, emit JSON score to stdout.
// Used by tests/cross_impl_test.py to validate Python ↔ JS output parity.
//
// Usage:  node cross_impl_runner.js <path/to/chain.jsonl>

const fs = require("fs");
const path = require("path");
const { verifyChain, computeScore } = require(path.join(__dirname, "..", "viewer", "score.js"));

(async function main() {
  const file = process.argv[2];
  if (!file) {
    console.error("usage: node cross_impl_runner.js <chain.jsonl>");
    process.exit(2);
  }
  const text = fs.readFileSync(file, "utf8");
  const events = text.split("\n").filter(l => l.trim()).map(l => JSON.parse(l));
  const report = await verifyChain(events);
  const score = computeScore(events, report);
  process.stdout.write(JSON.stringify(score, null, 2));
})();
