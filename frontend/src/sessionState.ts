export const LEGACY_DRAFTS_CACHE_PREFIX = "enprato.drafts.";

export function draftsOwnerId(userId: string | null | undefined): string {
  const owner = String(userId || "").trim();
  return owner || "lan-local";
}

export function draftsCacheKey(userId: string | null | undefined, sessionId: string): string {
  return `enprato.drafts.${draftsOwnerId(userId)}.${String(sessionId || "").trim()}`;
}

export function fullDraftSnapshot(
  drafts: Record<number, string> | undefined,
  sentenceCount: number,
): Record<number, string> {
  const n = Math.max(0, Math.floor(sentenceCount) || 0);
  const src = drafts || {};
  const out: Record<number, string> = {};
  for (let i = 0; i < n; i += 1) {
    out[i] = String(src[i] ?? "");
  }
  return out;
}

export type LearningSnapshot = {
  sessionId: string;
  drafts: Record<number, string>;
  index: number;
  sentenceCount: number;
  draftCount: number;
  completedCount: number;
};

export function captureLearningSnapshot(state: {
  sessionId: string;
  drafts: Record<number, string> | undefined;
  index: number;
  sentenceCount: number;
}): LearningSnapshot {
  const drafts = fullDraftSnapshot(state.drafts, state.sentenceCount);
  let completedCount = 0;
  for (const value of Object.values(drafts)) {
    if (value.trim()) completedCount += 1;
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

export function createLoadGate() {
  let gen = 0;
  return {
    bump(): number {
      gen += 1;
      return gen;
    },
    isCurrent(token: number): boolean {
      return token === gen;
    },
  };
}
