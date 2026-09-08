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
