import type { CurrentUser, CuratedLesson, LicenseStatus, Order, ProgressSummary, SessionDetail, SessionSummary, ShadowScore, UpdateInfo, WordSense } from "./types";

let progressSaveChain: Promise<void> = Promise.resolve();

async function readError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    const detail = data.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((item) => item.msg || item).join("; ");
    return JSON.stringify(data);
  } catch {
    const status = res.statusText || "";
    if (res.status >= 500 || /internal server error/i.test(status)) {
      return "\u540e\u7aef\u6682\u65f6\u65e0\u6cd5\u8fde\u63a5\uff0c\u8bf7\u7a0d\u540e\u5237\u65b0\u91cd\u8bd5\u3002";
    }
    return status || `请求失败（${res.status}）`;
  }
}

function isAbortError(err: unknown): boolean {
  return (
    (typeof DOMException !== "undefined" && err instanceof DOMException && err.name === "AbortError") ||
    (err instanceof Error && err.name === "AbortError")
  );
}

const URL_IMPORT_TIMEOUT_MS = 15 * 60 * 1000;

function compareVersions(a: string, b: string): number {
  const pa = a.split(/[.-]/).map((part) => Number(part) || 0);
  const pb = b.split(/[.-]/).map((part) => Number(part) || 0);
  const len = Math.max(pa.length, pb.length);
  for (let i = 0; i < len; i += 1) {
    const diff = (pa[i] || 0) - (pb[i] || 0);
    if (diff) return diff;
  }
  return 0;
}

export async function checkUpdate(): Promise<UpdateInfo | null> {
  const url = import.meta.env.VITE_UPDATE_MANIFEST_URL as string | undefined;
  if (!url) return null;
  const res = await fetch(`${url}${url.includes("?") ? "&" : "?"}t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) return null;
  const data = (await res.json()) as UpdateInfo;
  if (!data.version || !data.download_url) return null;
  return compareVersions(data.version, __APP_VERSION__) > 0 ? data : null;
}

export async function prepareSession(
  video: File,
  captions?: File | null,
): Promise<SessionDetail> {
  const body = new FormData();
  body.append("video", video);
  if (captions) body.append("captions", captions);
  const res = await fetch("/api/prepare", { method: "POST", body, credentials: "same-origin" });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function prepareSessionFromUrl(url: string, createNewSession = false): Promise<SessionDetail> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), URL_IMPORT_TIMEOUT_MS);
  try {
    const res = await fetch("/api/prepare-url", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, create_new_session: createNewSession }),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(await readError(res));
    return res.json();
  } catch (err) {
    if (isAbortError(err)) {
      throw new Error("导入时间过长，已停止等待。请换一条较短的链接，或先下载到本地再上传。");
    }
    throw err;
  } finally {
    window.clearTimeout(timer);
  }
}

export async function fetchLicense(): Promise<LicenseStatus> {
  let res: Response;
  try {
    res = await fetch("/api/license");
  } catch {
    throw new Error("\u540e\u7aef\u6682\u65f6\u65e0\u6cd5\u8fde\u63a5\uff0c\u8bf7\u7a0d\u540e\u5237\u65b0\u91cd\u8bd5\u3002");
  }
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function activateLicense(key: string): Promise<LicenseStatus> {
  const res = await fetch("/api/license/activate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function checkoutLicense(plan: "monthly" | "lifetime"): Promise<LicenseStatus> {
  let res: Response;
  try {
    res = await fetch("/api/license/checkout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan }),
    });
  } catch {
    throw new Error("\u540e\u7aef\u6682\u65f6\u65e0\u6cd5\u8fde\u63a5\uff0c\u8bf7\u7a0d\u540e\u5237\u65b0\u91cd\u8bd5\u3002");
  }
  if (!res.ok) {
    if (res.status === 404) {
      throw new Error("\u540e\u7aef\u6682\u65f6\u65e0\u6cd5\u8fde\u63a5\uff0c\u8bf7\u7a0d\u540e\u5237\u65b0\u91cd\u8bd5\u3002");
    }
    throw new Error(await readError(res));
  }
  return res.json();
}

export async function transcribeUtterance(
  blob: Blob,
  context: string,
  target = "",
): Promise<string> {
  const makeBody = () => {
    const body = new FormData();
    body.append("audio", blob, "dictation.webm");
    body.append("context", context);
    body.append("target", target);
    return body;
  };
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 150_000);
  const post = () =>
    fetch("/api/stt", {
      method: "POST",
      body: makeBody(),
      signal: controller.signal,
      cache: "no-store",
      credentials: "same-origin",
    });
  let res: Response;
  const startedAt = Date.now();
  try {
    try {
      res = await post();
    } catch (error) {
      const quickNet =
        Date.now() - startedAt < 2500 &&
        !(error instanceof DOMException && error.name === "AbortError") &&
        (error instanceof TypeError || String(error).includes("Failed to fetch"));
      if (!quickNet) throw error;
      res = await post();
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("ASR_TIMEOUT: 这段语音较长，服务器识别超时，请重试。");
    }
    throw new Error("NETWORK_ERROR: 录音没有完整送到服务器，请检查网络后重试。");
  } finally {
    window.clearTimeout(timeout);
  }
  if (!res.ok) throw new Error(await readError(res));
  const data = await res.json();
  return data.text as string;
}

export async function defineWord(word: string): Promise<WordSense> {
  const res = await fetch(`/api/define?word=${encodeURIComponent(word)}`);
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function translateSentence(text: string): Promise<string> {
  const res = await fetch(`/api/translate?text=${encodeURIComponent(text)}`);
  if (!res.ok) throw new Error(await readError(res));
  const data = await res.json();
  return (data.zh as string) || "";
}

export async function scoreShadow(sessionId: string, blob: Blob): Promise<ShadowScore> {
  const body = new FormData();
  body.append("audio", blob, "shadow.webm");
  body.append("session_id", sessionId);
  const res = await fetch("/api/score", { method: "POST", body });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function warmupAsr(): Promise<void> {
  await fetch("/api/warmup", { method: "POST" }).catch(() => undefined);
}

export async function listSessions(): Promise<SessionSummary[]> {
  const res = await fetch("/api/sessions", { credentials: "same-origin" });
  if (!res.ok) throw new Error(await readError(res));
  const data = await res.json();
  return data.sessions as SessionSummary[];
}

export async function fetchProgress(days?: 7 | 30): Promise<ProgressSummary> {
  const res = await fetch(`/api/progress${days ? `?days=${days}` : ""}`, { credentials: "same-origin" });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function completeLearningRecord(sessionId: string, durationSeconds: number): Promise<ProgressSummary> {
  const res = await fetch(`/api/progress/complete/${sessionId}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ duration_seconds: durationSeconds }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function loadSession(sessionId: string): Promise<SessionDetail> {
  const res = await fetch(`/api/session/${sessionId}`);
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function saveProgress(
  sessionId: string,
  payload: {
    phase: string;
    index?: number;
    drafts?: Record<number, string>;
    highlights: { sentenceId: number; word: string }[];
    score: ShadowScore | null;
    orientation: string;
  },
  options?: { keepalive?: boolean },
): Promise<void> {
  const request = async () => {
    await fetch(`/api/session/${sessionId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      keepalive: options?.keepalive ?? false,
      body: JSON.stringify({
        phase: payload.phase,
        index: payload.index,
        highlights: payload.highlights,
        score: payload.score,
        orientation: payload.orientation,
        ...(payload.drafts
          ? { drafts: Object.fromEntries(Object.entries(payload.drafts).map(([k, v]) => [String(k), v])) }
          : {}),
      }),
    }).catch(() => undefined);
  };
  progressSaveChain = progressSaveChain.then(request, request);
  await progressSaveChain;
}

export async function deleteSession(sessionId: string): Promise<void> {
  const res = await fetch(`/api/session/${sessionId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await readError(res));
}

/** 走本机默认音箱（ffplay），不依赖浏览器音轨 —— 解决 Chrome 无声 */
export async function speakerPlay(
  sessionId: string,
  start: number,
  end: number,
  volume = 1,
): Promise<void> {
  const res = await fetch(`/api/session/${sessionId}/speaker-play`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ start, end, volume }),
  });
  if (!res.ok) throw new Error(await readError(res));
}

export async function speakerStop(sessionId: string): Promise<void> {
  await fetch(`/api/session/${sessionId}/speaker-stop`, { method: "POST" }).catch(() => undefined);
}

export async function fetchLanLinks(): Promise<{
  ips: string[];
  links: string[];
  ipad_links?: string[];
  ipad_home?: string[];
  ipad_build?: string;
  port: number;
}> {
  const res = await fetch("/api/lan");
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

/** 声明电脑当前课，手机麦页会自动跟过来；关掉手机麦时传 null */
export async function claimRemoteSession(sessionId: string | null): Promise<void> {
  const res = await fetch("/api/remote-claim", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId || "" }),
  });
  if (!res.ok) throw new Error(await readError(res));
}

export async function fetchRemoteInbox(
  sessionId: string,
  after: number,
): Promise<{ items: { id: number; index: number; text: string }[]; connected?: boolean }> {
  const res = await fetch(`/api/session/${sessionId}/remote-inbox?after=${after}`);
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function fetchRemoteState(sessionId: string): Promise<{
  index: number;
  total: number;
  draft: string;
  drafts: Record<string, string>;
  phase: string;
}> {
  const res = await fetch(`/api/session/${sessionId}/remote-state`);
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}


export async function fetchCatalog(): Promise<CuratedLesson[]> {
  const res = await fetch("/api/catalog", { credentials: "same-origin" });
  if (!res.ok) throw new Error(await readError(res));
  const data = (await res.json()) as { lessons?: CuratedLesson[] };
  return Array.isArray(data.lessons) ? data.lessons : [];
}

export async function fetchAuthState(): Promise<{ user: CurrentUser | null; requireAuth: boolean }> {
  const res = await fetch("/api/auth/me", { credentials: "same-origin" });
  if (res.status === 401) return { user: null, requireAuth: true };
  if (!res.ok) throw new Error(await readError(res));
  const data = (await res.json()) as { user: CurrentUser | null; require_auth?: boolean };
  return { user: data.user, requireAuth: Boolean(data.require_auth) };
}

export async function fetchCurrentUser(): Promise<CurrentUser | null> {
  return (await fetchAuthState()).user;
}

export async function registerAccount(email: string, password: string): Promise<CurrentUser> {
  const res = await fetch("/api/auth/register", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }) });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function loginAccount(email: string, password: string): Promise<CurrentUser> {
  const res = await fetch("/api/auth/login", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }) });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function logoutAccount(): Promise<void> {
  const res = await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
  if (!res.ok) throw new Error(await readError(res));
}

export async function fetchAuthMethods(): Promise<{ wechat: { available: boolean }; phone: { available: boolean } }> {
  const res = await fetch("/api/auth/methods", { credentials: "same-origin" });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function sendPhoneCode(phone: string): Promise<{ challenge_id: string; expires_in: number }> {
  const res = await fetch("/api/auth/phone/send", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ phone }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function verifyPhoneLogin(phone: string, challengeId: string, code: string): Promise<CurrentUser> {
  const res = await fetch("/api/auth/phone/verify", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ phone, challenge_id: challengeId, code }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function grantDevMembership(): Promise<CurrentUser["membership"]> {
  const res = await fetch("/api/dev/membership", { method: "POST", credentials: "same-origin" });
  if (!res.ok) throw new Error(await readError(res));
  const data = (await res.json()) as { membership: CurrentUser["membership"] };
  return data.membership;
}

export async function createOrder(): Promise<Order> {
  const res = await fetch("/api/payments/wechat/native", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ plan: "monthly_30d", provider: "wechat" }) });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function fetchOrder(orderNo: string): Promise<Order> {
  const res = await fetch("/api/payments/orders/" + encodeURIComponent(orderNo), { credentials: "same-origin" });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function createRemoteToken(sessionId: string): Promise<string> {
  const res = await fetch("/api/remote-token/" + encodeURIComponent(sessionId), { method: "POST", credentials: "same-origin" });
  if (!res.ok) throw new Error(await readError(res));
  const data = (await res.json()) as { token: string };
  return data.token;
}
