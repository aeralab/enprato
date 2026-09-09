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

function captureLearningSnapshot(state) {
  const drafts = fullDraftSnapshot(state.drafts, state.sentenceCount);
  let completedCount = 0;
  for (const value of Object.values(drafts)) {
    if (String(value).trim()) completedCount += 1;
  }
  return {
    sessionId: state.sessionId,
    drafts,
    index: state.index,
    sentenceCount: state.sentenceCount,
    draftCount: Object.keys(drafts).length,
    completedCount,
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

test("live sessionIdRef persist after render writes A drafts onto C", () => {
  const sessionIdRef = { current: "sess-a" };
  const drafts = {};
  for (let i = 0; i < 20; i += 1) drafts[i] = `typed A ${i}`;
  const cache = {};
  function persistUsingLiveRef() {
    cache[sessionIdRef.current] = { ...drafts };
  }
  persistUsingLiveRef();
  sessionIdRef.current = "sess-c";
  persistUsingLiveRef();
  assert.equal(Object.keys(cache["sess-c"]).length, 20);
  assert.equal(cache["sess-c"][0], "typed A 0");
});

test("bound snapshot persist after render still patches A not C", () => {
  const bound = { current: "sess-a" };
  const sessionIdRef = { current: "sess-a" };
  const drafts = {};
  for (let i = 0; i < 20; i += 1) drafts[i] = `typed A ${i}`;
  const cache = { "sess-c": { 0: "typed C 0", 1: "typed C 1", 2: "typed C 2" } };
  function persistBound() {
    const snap = captureLearningSnapshot({
      sessionId: bound.current,
      drafts,
      index: 19,
      sentenceCount: 24,
    });
    cache[snap.sessionId] = snap.drafts;
  }
  persistBound();
  sessionIdRef.current = "sess-c";
  persistBound();
  assert.equal(Object.keys(cache["sess-c"]).filter((k) => cache["sess-c"][k]).length, 3);
  assert.equal(cache["sess-c"][0], "typed C 0");
  assert.equal(cache["sess-a"][0], "typed A 0");
  assert.equal(Object.keys(cache["sess-a"]).filter((k) => String(cache["sess-a"][k]).trim()).length, 20);
});

test("atomic snapshot does not pick a later sessionId", () => {
  const live = { sessionId: "sess-a", drafts: { 0: "from A" }, index: 1, sentenceCount: 3 };
  const snap = captureLearningSnapshot(live);
  live.sessionId = "sess-c";
  live.drafts[0] = "CHANGED";
  assert.equal(snap.sessionId, "sess-a");
  assert.equal(snap.drafts[0], "from A");
  assert.equal(snap.completedCount, 1);
});
