export type DiffToken = {
  text: string;
  status: "match" | "missing" | "extra" | "gap";
};

function tokens(input: string): string[] {
  return input
    .toLowerCase()
    .replace(/[^a-z'\s]/g, " ")
    .split(/\s+/)
    .filter(Boolean);
}

export function diffWords(official: string, draft: string): DiffToken[] {
  const a = tokens(official);
  const b = tokens(draft);
  const n = a.length;
  const m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const out: DiffToken[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ text: b[j], status: "match" });
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ text: a[i], status: "missing" });
      i += 1;
    } else {
      out.push({ text: b[j], status: "extra" });
      j += 1;
    }
  }
  while (i < n) {
    out.push({ text: a[i], status: "missing" });
    i += 1;
  }
  while (j < m) {
    out.push({ text: b[j], status: "extra" });
    j += 1;
  }
  return out;
}

export function splitWords(text: string): { raw: string; word: string }[] {
  return text.split(/(\s+)/).map((raw) => ({
    raw,
    word: raw.replace(/[^A-Za-z'-]/g, ""),
  }));
}

function wordTokens(input: string): string[] {
  return input
    .toLowerCase()
    .replace(/[^a-z'\s]/g, " ")
    .split(/\s+/)
    .filter(Boolean);
}

/** 听写稿已覆盖目标句开头多少个词（用于从半句续播） */
export function coveredPrefixCount(target: string, draft: string): number {
  const a = wordTokens(target);
  const b = wordTokens(draft);
  if (!a.length || !b.length) return 0;
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    const tw = a[i];
    const dw = b[j];
    if (tw === dw || tw.startsWith(dw) || dw.startsWith(tw)) {
      i += 1;
      j += 1;
      continue;
    }
    // 跳过听写里多出来的一个词，再试对齐
    if (j + 1 < b.length && (a[i] === b[j + 1] || a[i].startsWith(b[j + 1]))) {
      j += 1;
      continue;
    }
    break;
  }
  return i;
}

/** 根据听写进度（和可选暂停点）估算应从本句何处续播 */
export function resumeTimeInSentence(
  sentence: { start: number; end: number; text: string },
  draft: string,
  pausedAt?: number,
): number {
  const words = wordTokens(sentence.text);
  const covered = coveredPrefixCount(sentence.text, draft);
  const dur = Math.max(0.05, sentence.end - sentence.start);
  let t = sentence.start;

  if (words.length >= 2 && covered > 0 && covered < words.length) {
    // 从第一个未写完的词附近开始，略回退一点方便接上
    t = sentence.start + (covered / words.length) * dur - 0.4;
  } else if (
    pausedAt != null &&
    pausedAt > sentence.start + 0.45 &&
    pausedAt < sentence.end - 0.25
  ) {
    // 还没写上字，但停在本句中间 → 从暂停处再听
    t = pausedAt - 0.2;
  }

  return Math.max(sentence.start, Math.min(t, sentence.end - 0.45));
}
