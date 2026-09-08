import assert from "node:assert/strict";
import test from "node:test";

function draftsCacheKey(userId, sessionId) {
  const owner = String(userId || "").trim() || "lan-local";
  return `enprato.drafts.${owner}.${String(sessionId || "").trim()}`;
}

function fullDraftSnapshot(drafts, sentenceCount) {
  const n = Math.max(0, Math.floor(sentenceCount) || 0);
  const src = drafts || {};
  const out = {};
  for (let i = 0; i < n; i += 1) out[i] = String(src[i] ?? "");
  return out;
}

function createLoadGate() {
  let gen = 0;
  return {
    bump() {
      gen += 1;
      return gen;
    },
    isCurrent(token) {
      return token === gen;
    },
  };
}

test("cache key isolates user and session", () => {
  assert.equal(draftsCacheKey("user-a", "sess-x"), "enprato.drafts.user-a.sess-x");
  assert.equal(draftsCacheKey("", "sess-x"), "enprato.drafts.lan-local.sess-x");
  assert.notEqual(draftsCacheKey("user-a", "sess-x"), draftsCacheKey("user-b", "sess-x"));
});

test("new course snapshot is empty padded slots not inherited text", () => {
  const fromA = { 0: "typed in A", 1: "also A" };
  const forB = fullDraftSnapshot({}, 2);
  assert.deepEqual(forB, { 0: "", 1: "" });
  assert.notEqual(forB[0], fromA[0]);
});

test("load gate ignores stale generation", () => {
  const gate = createLoadGate();
  const tokenA = gate.bump();
  const tokenB = gate.bump();
  assert.equal(gate.isCurrent(tokenA), false);
  assert.equal(gate.isCurrent(tokenB), true);
});
