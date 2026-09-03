import { useEffect, useRef, useState } from "react";
import {
  activateLicense,
  fetchCatalog,
  fetchCurrentUser,
  logoutAccount,
  checkUpdate,
  defineWord,
  deleteSession,
  fetchLicense,
  listSessions,
  loadSession,
  prepareSession,
  prepareSessionFromUrl,
  saveProgress,
  scoreShadow,
  speakerPlay,
  speakerStop,
  claimRemoteSession,
  fetchLanLinks,
  fetchRemoteInbox,
  fetchRemoteState,
  translateSentence,
  transcribeUtterance,
  warmupAsr,
} from "./api";
import { resumeTimeInSentence, splitWords } from "./diffWords";
import { applyBurnWipeLayout } from "./videoBurnLayout";
import type {
  CaptionMode,
  CurrentUser,
  CuratedLesson,
  Highlight,
  LicenseStatus,
  Orientation,
  Phase,
  Sentence,
  SessionDetail,
  SessionSummary,
  ShadowScore,
  UpdateInfo,
  WordSense,
} from "./types";

const LAST_SESSION_KEY = "enprato.lastSession";
const DRAFTS_CACHE_PREFIX = "enprato.drafts.";
const ENABLE_SERVER_SPEAKER = import.meta.env.VITE_ENABLE_SERVER_SPEAKER === "1";


function backendHint(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err);
  if (/failed to fetch|network|500|502|503|504|internal server error/i.test(msg)) {
    return "\u540e\u7aef\u6682\u65f6\u65e0\u6cd5\u8fde\u63a5\uff0c\u8bf7\u7a0d\u540e\u5237\u65b0\u91cd\u8bd5\u3002";
  }
  return msg || "无法连接后端";
}

function readDraftsCache(sessionId: string): { drafts: Record<number, string>; savedAt: number } {
  try {
    const raw = localStorage.getItem(`${DRAFTS_CACHE_PREFIX}${sessionId}`);
    if (!raw) return { drafts: {}, savedAt: 0 };
    const data = JSON.parse(raw) as { drafts?: Record<string, string>; savedAt?: number };
    return { drafts: draftsMap(data?.drafts), savedAt: data.savedAt || 0 };
  } catch {
    return { drafts: {}, savedAt: 0 };
  }
}

function writeDraftsCache(sessionId: string, drafts: Record<number, string>, index: number) {
  try {
    const payload = {
      drafts: Object.fromEntries(Object.entries(drafts).map(([k, v]) => [String(k), v])),
      index,
      savedAt: Date.now(),
    };
    localStorage.setItem(`${DRAFTS_CACHE_PREFIX}${sessionId}`, JSON.stringify(payload));
  } catch {
    /* ignore quota */
  }
}

function mergeDraftMaps(
  base: Record<number, string>,
  incoming: Record<number, string>,
): Record<number, string> {
  const next = { ...base };
  for (const [k, v] of Object.entries(incoming)) {
    const i = Number(k);
    if (!Number.isFinite(i)) continue;
    next[i] = preferCleanerDraft(String(next[i] || ""), String(v || ""));
  }
  return next;
}

/** 相同听写内容只保留最早一句 */
function collapseIdenticalDrafts(drafts: Record<number, string>): Record<number, string> {
  const keys = Object.keys(drafts)
    .map(Number)
    .filter(Number.isFinite)
    .sort((a, b) => a - b);
  const seen = new Map<string, number>();
  const next = { ...drafts };
  for (const k of keys) {
    const text = dedupeRepeatedClauses(next[k] || "").trim();
    next[k] = text;
    if (!text) continue;
    if (seen.has(text)) next[k] = "";
    else seen.set(text, k);
  }
  return next;
}

function draftsFromServerMap(raw: Record<string, string> | undefined): Record<number, string> {
  const map: Record<number, string> = {};
  if (!raw) return map;
  for (const [k, v] of Object.entries(raw)) {
    const i = Number(k);
    if (Number.isFinite(i)) map[i] = String(v || "");
  }
  return collapseIdenticalDrafts(map);
}

function loadDraftsForSession(detail: SessionDetail): Record<number, string> {
  const server = draftsFromServerMap(detail.drafts);
  const cached = readDraftsCache(detail.session_id);
  const merged = collapseIdenticalDrafts(mergeDraftMaps(server, cached.drafts));
  writeDraftsCache(detail.session_id, merged, detail.index || 0);
  return merged;
}

function dedupeParagraphs(parts: string[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const raw of parts) {
    const p = (raw || "").trim();
    if (!p) {
      out.push("");
      continue;
    }
    if (seen.has(p)) continue;
    seen.add(p);
    out.push(p);
  }
  return out;
}

function normDictationWords(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[^\w\s']/g, " ")
    .split(/\s+/)
    .filter(Boolean);
}

function mergeDictationPiece(cur: string, piece: string, target: string): string {
  const c = dedupeRepeatedClauses(cur.trim());
  const p = dedupeRepeatedClauses(piece.trim());
  if (!p) return c;
  if (!c) return p;
  const cw = normDictationWords(c);
  const pw = normDictationWords(p);
  if (cw.length && pw.length) {
    const cj = cw.join(" ");
    const pj = pw.join(" ");
    if (cj === pj) return c;
    if (cj.includes(pj)) return c;
    if (pj.includes(cj)) return p;
    const tn = normDictationWords(target).join(" ");
    if (tn && pj === tn) return p;
    if (tn && cj === tn && pj === tn) return p;
    const maxOverlap = Math.min(cw.length, pw.length, 24);
    for (let k = maxOverlap; k >= 3; k -= 1) {
      if (cw.slice(-k).join(" ") === pw.slice(0, k).join(" ")) {
        return dedupeRepeatedClauses(`${c} ${p.split(/\s+/).slice(k).join(" ")}`.trim());
      }
    }
  }
  return dedupeRepeatedClauses(`${c} ${p}`);
}

function dedupeRepeatedClauses(text: string): string {
  let out = text.trim().replace(/\s+/g, " ");
  if (out.length < 20) return out;

  const parts = out.split(/(?<=[.!?])\s+/);
  if (parts.length >= 2) {
    const kept: string[] = [];
    for (const part of parts) {
      const p = part.trim();
      if (!p) continue;
      if (kept.length) {
        const a = normDictationWords(kept[kept.length - 1]).join(" ");
        const b = normDictationWords(p).join(" ");
        if (a && b && (a === b || (a.length >= 12 && (a.includes(b) || b.includes(a))))) continue;
      }
      kept.push(p);
    }
    out = kept.join(" ");
  }

  let words = out.split(/\s+/).filter(Boolean);
  if (words.length < 6) return out;

  for (let round = 0; round < 10; round += 1) {
    let changed = false;
    for (let n = Math.min(20, Math.floor(words.length / 2)); n >= 2; n -= 1) {
      const next: string[] = [];
      let i = 0;
      while (i < words.length) {
        if (i + 2 * n <= words.length) {
          const a = normDictationWords(words.slice(i, i + n).join(" ")).join(" ");
          const b = normDictationWords(words.slice(i + n, i + 2 * n).join(" ")).join(" ");
          if (a && a === b) {
            next.push(...words.slice(i, i + n));
            i += n;
            while (i + n <= words.length) {
              const c = normDictationWords(words.slice(i, i + n).join(" ")).join(" ");
              if (c !== a) break;
              i += n;
              changed = true;
            }
            continue;
          }
        }
        next.push(words[i]);
        i += 1;
      }
      words = next;
      if (changed) break;
    }
    if (!changed) break;
  }
  return words.join(" ");
}

function preferCleanerDraft(cur: string, incoming: string): string {
  const ct = String(cur || "").trim();
  const nt = String(incoming || "").trim();
  if (!nt) return dedupeRepeatedClauses(ct);
  if (!ct) return dedupeRepeatedClauses(nt);
  const cc = dedupeRepeatedClauses(ct);
  const nn = dedupeRepeatedClauses(nt);
  const cBloated = ct.length > Math.max(48, Math.floor(cc.length * 1.35));
  const nBloated = nt.length > Math.max(48, Math.floor(nn.length * 1.35));
  if (cBloated && !nBloated) return nn;
  if (nBloated && !cBloated) return cc;
  if (cBloated && nBloated) return nn.length >= cc.length ? nn : cc;
  return nn.length >= cc.length ? nn : cc;
}

type DraftTextRow = {
  sentenceIndex: number;
  start: number;
  end: number;
  text: string;
};

function draftTextRows(drafts: Record<number, string>, index: number): { text: string; rows: DraftTextRow[] } {
  let end = index;
  for (const k of Object.keys(drafts)) {
    const i = Number(k);
    if (Number.isFinite(i)) end = Math.max(end, i);
  }
  const lines = Array.from({ length: end + 1 }, (_, i) => dedupeRepeatedClauses(String(drafts[i] ?? "")));
  let lastNonEmpty = -1;
  for (let i = lines.length - 1; i >= 0; i--) {
    if (lines[i].trim()) {
      lastNonEmpty = i;
      break;
    }
  }
  if (lastNonEmpty < 0) return { text: "", rows: [] };

  const kept = lines.slice(0, lastNonEmpty + 1);
  const rows: DraftTextRow[] = [];
  let pos = 0;
  for (let i = 0; i < kept.length; i += 1) {
    const text = kept[i] || "";
    rows.push({ sentenceIndex: i, start: pos, end: pos + text.length, text });
    pos += text.length;
    if (i < kept.length - 1) pos += 2;
  }
  return { text: kept.join("\n\n"), rows };
}

function draftsToText(drafts: Record<number, string>, index: number): string {
  return draftTextRows(drafts, index).text;
}

type DraftBeforeInput = {
  value: string;
  selectionStart: number;
  selectionEnd: number;
  inputType: string;
  rows: DraftTextRow[];
};

function isDraftDeleteInput(inputType: string): boolean {
  return inputType.startsWith("delete");
}

function selectedDraftIndexes(before: DraftBeforeInput): number[] {
  const start = Math.min(before.selectionStart, before.selectionEnd);
  const end = Math.max(before.selectionStart, before.selectionEnd);
  if (end <= start) return [];

  const touched: { index: number; overlap: number; length: number; fullySelected: boolean }[] = [];
  for (const row of before.rows) {
    const overlap = Math.min(end, row.end) - Math.max(start, row.start);
    if (row.text.trim() && overlap > 0) {
      touched.push({
        index: row.sentenceIndex,
        overlap,
        length: Math.max(1, row.end - row.start),
        fullySelected: start <= row.start && end >= row.end,
      });
    }
  }
  if (touched.length > 1) return touched.map((item) => item.index);
  return touched
    .filter((item) => item.fullySelected || item.overlap / item.length >= 0.65)
    .map((item) => item.index);
}

function mediaSrc(path: string): string {
  if (!path || path.startsWith("blob:") || path.startsWith("http")) return path;
  // 走当前页同源（Vite 代理），避免 https 自签证书把 <video> 拦成黑屏
  return path;
}

function ipadHomeUrl(base: string): string {
  try {
    const u = new URL(base);
    u.pathname = u.pathname.replace(/\/ipad(?:\/[^/]+)?\/?$/, "") + "/ipad";
    u.search = "";
    u.hash = "";
    return u.toString().replace(/\/$/, "");
  } catch {
    return base.replace(/\/ipad(?:\/[^/]+)?\/?$/, "/ipad").split("?")[0];
  }
}

function orientationFromSize(width: number, height: number): Orientation {
  return height > width * 1.05 ? "portrait" : "landscape";
}

function probeVideoOrientation(file: File): Promise<Orientation> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const probe = document.createElement("video");
    probe.preload = "metadata";
    probe.src = url;
    const finish = (value: Orientation) => {
      URL.revokeObjectURL(url);
      resolve(value);
    };
    probe.onloadedmetadata = () => finish(orientationFromSize(probe.videoWidth, probe.videoHeight));
    probe.onerror = () => finish("landscape");
  });
}

function recorderOptions(): MediaRecorderOptions {
  const types = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
  const found = types.find((type) => typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(type));
  return found ? { mimeType: found } : {};
}

function micMessage(err: unknown): string {
  const name = err instanceof DOMException ? err.name : "";
  if (name === "NotAllowedError" || name === "PermissionDeniedError") {
    return "浏览器拦住了麦克风。请允许本页使用麦克风，然后刷新再试。";
  }
  if (name === "NotFoundError") return "没有找到麦克风，请检查是否已插入或已在系统里启用。";
  if (name === "NotReadableError") return "麦克风被其他程序占用，请关闭会议软件后再试。";
  if (name === "SecurityError") return "请用 http://127.0.0.1:5173 打开本页后再用语音输入。";
  return err instanceof Error ? err.message : "无法开始录音";
}

function draftsMap(raw: Record<string, string> | undefined): Record<number, string> {
  const out: Record<number, string> = {};
  for (const [key, value] of Object.entries(raw || {})) {
    const id = Number(key);
    if (!Number.isNaN(id)) out[id] = value;
  }
  return out;
}

export default function App() {
  const [phase, setPhase] = useState<Phase>("import");
  const [error, setError] = useState("");
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [sourceUrl, setSourceUrl] = useState("");
  const [videoUrl, setVideoUrl] = useState("");
  const [audioUrl, setAudioUrl] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [sentences, setSentences] = useState<Sentence[]>([]);
  const [orientation, setOrientation] = useState<Orientation>("landscape");
  const [history, setHistory] = useState<SessionSummary[]>([]);
  const [resumeIndex, setResumeIndex] = useState(0);
  const [resumeDrafts, setResumeDrafts] = useState<Record<number, string>>({});
  const [resumeHighlights, setResumeHighlights] = useState<Highlight[]>([]);
  const [resumeScore, setResumeScore] = useState<ShadowScore | null>(null);
  const [resumeHasVideo, setResumeHasVideo] = useState(true);
  const [license, setLicense] = useState<LicenseStatus | null>(null);
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [licenseBusy, setLicenseBusy] = useState(false);
  const [licenseLoadError, setLicenseLoadError] = useState("");
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null);
  const [catalog, setCatalog] = useState<CuratedLesson[]>([]);

  useEffect(() => {
    const root = document.documentElement;
    const setKeyboardInset = () => {
      const vv = window.visualViewport;
      const inset = vv ? Math.max(0, window.innerHeight - vv.height - vv.offsetTop) : 0;
      root.style.setProperty("--keyboard-inset", `${Math.round(inset)}px`);
    };
    setKeyboardInset();
    window.addEventListener("resize", setKeyboardInset);
    window.addEventListener("orientationchange", setKeyboardInset);
    window.visualViewport?.addEventListener("resize", setKeyboardInset);
    window.visualViewport?.addEventListener("scroll", setKeyboardInset);
    return () => {
      window.removeEventListener("resize", setKeyboardInset);
      window.removeEventListener("orientationchange", setKeyboardInset);
      window.visualViewport?.removeEventListener("resize", setKeyboardInset);
      window.visualViewport?.removeEventListener("scroll", setKeyboardInset);
      root.style.removeProperty("--keyboard-inset");
    };
  }, []);

  useEffect(() => {
    warmupAsr();
    let cancelled = false;
    (async () => {
      try {
        const rows = await listSessions();
        if (cancelled) return;
        setHistory(rows);
        const last = localStorage.getItem(LAST_SESSION_KEY);
        if (last && rows.some((row) => row.session_id === last)) {
          openDetail(await loadSession(last));
        }
      } catch (err) {
        if (!cancelled) {
          setHistory([]);
          setError(backendHint(err));
        }
      }
    })();
    void fetchCatalog()
      .then((lessons) => {
        if (!cancelled) setCatalog(lessons);
      })
      .catch(() => {
        if (!cancelled) setCatalog([]);
      });
    return () => {
      cancelled = true;
    };
    // 仅启动时恢复上次课
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void fetchCurrentUser().then(setUser).catch(() => setUser(null));
    void refreshLicense();
    void checkUpdate().then(setUpdateInfo).catch(() => undefined);
  }, []);

  useEffect(() => {
    return () => {
      if (videoUrl.startsWith("blob:")) URL.revokeObjectURL(videoUrl);
    };
  }, [videoUrl]);

  async function refreshHistory() {
    try {
      setHistory(await listSessions());
    } catch (err) {
      setHistory([]);
      setError(backendHint(err));
    }
  }

  async function refreshLicense() {
    try {
      setLicense(await fetchLicense());
      setLicenseLoadError("");
    } catch (err) {
      setLicense(null);
      setLicenseLoadError(err instanceof Error ? err.message : "授权状态读取失败");
    }
  }

  async function onActivateLicense(key: string) {
    setLicenseBusy(true);
    setError("");
    try {
      setLicense(await activateLicense(key));
      setLicenseLoadError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "授权码无效");
    } finally {
      setLicenseBusy(false);
    }
  }

  function openDetail(detail: SessionDetail) {
    const restored = (detail.phase === "import" || detail.phase === "preparing" ? "listen" : detail.phase) as Phase;
    setSessionId(detail.session_id);
    setSentences(detail.sentences);
    setVideoUrl(`${mediaSrc(detail.video_url)}?v=playable`);
    setAudioUrl(`${mediaSrc(detail.audio_url || `/api/session/${detail.session_id}/audio`)}?v=1`);
    setOrientation(detail.orientation === "portrait" ? "portrait" : "landscape");
    setResumeIndex(detail.index || 0);
    setResumeDrafts(loadDraftsForSession(detail));
    setResumeHighlights(detail.highlights || []);
    setResumeScore(detail.score || null);
    setResumeHasVideo(detail.has_video ?? true);
    localStorage.setItem(LAST_SESSION_KEY, detail.session_id);
    setPhase(restored);
  }

  async function start() {
    setError("");
    const file = videoFile;
    const url = sourceUrl.trim();
    // 有本地文件优先用文件，避免错误链接挡住进入
    if (!file && !url) return;
    setPhase("preparing");
    try {
      const prepared = file
        ? await prepareSession(file, null)
        : await prepareSessionFromUrl(url);
      openDetail({ ...prepared, phase: prepared.phase === "listen" ? "listen" : prepared.phase });
      await refreshHistory();
      await refreshLicense();
    } catch (err) {
      setPhase("import");
      const msg = err instanceof Error ? err.message : "准备失败";
      setError(msg.replace(/^链接无法用于学习[:：]\s*/i, ""));
      if (!file) {
        setVideoUrl("");
        setAudioUrl("");
      }
    }
  }

  async function startCurated(url: string) {
    const clean = url.trim();
    if (!clean) return;
    setVideoFile(null);
    setSourceUrl(clean);
    setError("");
    setPhase("preparing");
    try {
      const prepared = await prepareSessionFromUrl(clean);
      openDetail({ ...prepared, phase: prepared.phase === "listen" ? "listen" : prepared.phase });
      await refreshHistory();
      await refreshLicense();
    } catch (err) {
      setPhase("import");
      const msg = err instanceof Error ? err.message : "准备失败";
      setError(msg.replace(/^链接无法用于学习[:：]\s*/i, ""));
      setVideoUrl("");
      setAudioUrl("");
    }
  }

  async function resume(id: string) {
    setError("");
    try {
      openDetail(await loadSession(id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法打开历史课");
      localStorage.removeItem(LAST_SESSION_KEY);
      await refreshHistory();
    }
  }

  async function removeHistory(id: string) {
    await deleteSession(id);
    if (localStorage.getItem(LAST_SESSION_KEY) === id) localStorage.removeItem(LAST_SESSION_KEY);
    await refreshHistory();
  }

  const isHome = phase === "import" || phase === "preparing";

  return (
    <div className={`shell${isHome ? " shell-home" : ""}`}>
      <div className={`stage ${isHome ? "stage-home" : orientation}`}>
        {phase === "import" || phase === "preparing" ? (
          <ImportScreen
            phase={phase}
            error={error}
            videoFile={videoFile}
            sourceUrl={sourceUrl}
            history={history}
            catalog={catalog}
            onVideo={async (file) => {
              setVideoFile(file);
              setSourceUrl("");
              setOrientation(await probeVideoOrientation(file));
            }}
            onSourceUrl={(value) => {
              setSourceUrl(value);
              if (value.trim()) setVideoFile(null);
              const low = value.toLowerCase();
              if (
                low.includes("weixin.qq.com") ||
                low.includes("channels.weixin.qq.com") ||
                low.includes("/sph/")
              ) {
                setError("微信视频号链接无法在线拉取，请先下载到本机，再拖入或点击上传");
              } else {
                setError("");
              }
            }}
            onStart={start}
            onStartCurated={startCurated}
            onResume={resume}
            onDelete={removeHistory}
            user={user}
            onAuth={setUser}
            license={license}
            licenseBusy={licenseBusy}
            licenseLoadError={licenseLoadError}
            onActivateLicense={onActivateLicense}
            onRetryLicense={refreshLicense}
            updateInfo={updateInfo}
          />
        ) : (
          <Studio
            key={sessionId}
            phase={phase}
            setPhase={setPhase}
            videoUrl={videoUrl}
            audioUrl={audioUrl}
            sessionId={sessionId}
            sentences={sentences}
            orientation={orientation}
            initialIndex={resumeIndex}
            initialDrafts={resumeDrafts}
            initialHighlights={resumeHighlights}
            initialScore={resumeScore}
            audioOnly={!resumeHasVideo}
            history={history}
            onOrientation={setOrientation}
            onRefreshHistory={refreshHistory}
            onSwitchSession={resume}
            onReset={() => {
              setPhase("import");
              setSentences([]);
              setSessionId("");
              setOrientation("landscape");
              setSourceUrl("");
              setVideoFile(null);
              setVideoUrl("");
              setAudioUrl("");
              setResumeHasVideo(true);
              void refreshHistory();
            }}
          />
        )}
      </div>
    </div>
  );
}

function ImportScreen({
  phase,
  error,
  videoFile,
  sourceUrl,
  history,
  catalog,
  user,
  onAuth,
  onVideo,
  onSourceUrl,
  onStart,
  onStartCurated,
  onResume,
  onDelete,
  license,
  licenseBusy,
  licenseLoadError,
  onActivateLicense,
  onRetryLicense,
  updateInfo,
}: {
  phase: Phase;
  error: string;
  videoFile: File | null;
  sourceUrl: string;
  history: SessionSummary[];
  catalog: CuratedLesson[];
  user: CurrentUser | null;
  onAuth: (user: CurrentUser | null) => void;
  onVideo: (file: File) => void | Promise<void>;
  onSourceUrl: (value: string) => void;
  onStart: () => void;
  onStartCurated: (url: string) => void;
  onResume: (id: string) => void;
  onDelete: (id: string) => void;
  license: LicenseStatus | null;
  licenseBusy: boolean;
  licenseLoadError: string;
  onActivateLicense: (key: string) => void | Promise<void>;
  onRetryLicense: () => void | Promise<void>;
  updateInfo: UpdateInfo | null;
}) {
  if (phase === "preparing") {
    return (
      <div className="preparing">
        <h1>抽音、分句</h1>
        <p>
          {sourceUrl.trim()
            ? "正在下载视频 → 抽出音轨 → 按句切开。B站多数没有英文字幕，需要整段语音识别，视频越长越慢（约 1 小时片源通常要 1–3 分钟）。"
            : "第一遍不会显示字幕。有现成英文字幕会快很多；没有则用语音模型整段识别后再按句切开，片源越长越慢。"}
        </p>
        <div className="pulse" />
      </div>
    );
  }
  const weixinLink = /weixin\.qq\.com|channels\.weixin\.qq\.com|\/sph\//i.test(sourceUrl);
  const canStart = Boolean(videoFile || (sourceUrl.trim() && !weixinLink));
  const [historyMenu, setHistoryMenu] = useState<string | null>(null);
  return (
    <>
      <header className="topbar">
        <div className="brand">
          <strong>ENPRATO</strong>
          <span>dictation booth</span>
        </div>
        <AuthPanel user={user} onAuth={onAuth} />
      </header>
      <div className="import">
        <aside className="import-history">
          <h2>历史</h2>
          {history.length ? (
            history.map((item) => (
              <div className="history-row" key={item.session_id}>
                <button type="button" className="history-open" onClick={() => onResume(item.session_id)}>
                  <SessionThumb
                    sessionId={item.session_id}
                    label={sessionDisplayTitle(item)}
                    version={item.updated_at}
                    coverUrl={item.cover_url}
                  />
                  <span className="history-open-text">
                    <b>{sessionDisplayTitle(item)}</b>
                    <span>
                      {item.done}/{item.count} 句 · {phaseLabel(item.phase)}
                    </span>
                  </span>
                </button>
                <div className="history-actions">
                  <button type="button" className="icon-button" title="课程操作" aria-label="课程操作" onClick={() => setHistoryMenu(current => current === item.session_id ? null : item.session_id)}>...</button>
                  {historyMenu === item.session_id ? <div className="history-menu"><button type="button" onClick={() => { if (window.confirm("确定删除这门课程吗？")) onDelete(item.session_id); setHistoryMenu(null); }}>删除课程</button></div> : null}
                </div>
              </div>
            ))
          ) : (
            <p className="meta">还没有课，从右边导入开始</p>
          )}
        </aside>
        <section className="import-source">
          {updateInfo ? <UpdateBanner info={updateInfo} /> : null}
          {user ? (
            <section className="license-panel license-compat" aria-label="本机兼容授权">
              <div className="license-summary">
                <div>
                  <strong>本机兼容授权</strong>
                  <span>账号会员与免费次数以右上角「我的账号」为准；下方授权码仅影响本机/LAN 兼容层，不改变账号会员。</span>
                </div>
              </div>
              <details className="license-compat-details">
                <summary>展开本机授权码入口</summary>
                <LicensePanel
                  status={license}
                  busy={licenseBusy}
                  loadError={licenseLoadError}
                  onActivate={onActivateLicense}
                  onRetry={onRetryLicense}
                  localCompat
                />
              </details>
            </section>
          ) : (
            <LicensePanel
              status={license}
              busy={licenseBusy}
              loadError={licenseLoadError}
              onActivate={onActivateLicense}
              onRetry={onRetryLicense}
            />
          )}
          <div
            className="intake"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              const file = [...e.dataTransfer.files].find((item) => item.type.startsWith("video/"));
              if (file) void onVideo(file);
            }}
          >
            <div className="intake-header"><span>学习入口</span><strong>粘贴链接或上传本地视频</strong></div>
            <label className="intake-option"><span>粘贴视频链接</span><input
              className="intake-url"
              type="url"
              placeholder="粘贴 YouTube / B站 / mp4 链接"
              value={sourceUrl}
              onChange={(e) => onSourceUrl(e.target.value)}
              onClick={(e) => e.stopPropagation()}
            /></label>
            <label className="intake-file">
              <input
                type="file"
                accept="video/*"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) void onVideo(file);
                }}
              />
              <strong>{videoFile ? videoFile.name : "点击选择视频，或拖入此框"}</strong>
              <span>mp4 / mkv / webm · 微信视频请先下载再上传</span>
            </label>
          </div>
          {error ? <p className="err">{error}</p> : null}
          <button className="primary" disabled={!canStart} onClick={onStart}>
            进入听写室
          </button>
          {catalog.length ? (
            <section className="curated-lessons" aria-label="推荐课程">
              <div className="curated-head">
                <strong>推荐课 · VOA Level 2</strong>
                <span>公开学习素材，点击即可进入听写室</span>
              </div>
              <div className="curated-list">
                {catalog.map((lesson) => {
                  const existing = history.find((item) => item.source_url === lesson.source_url);
                  return (
                    <button
                      key={lesson.id}
                      type="button"
                      className="curated-item"
                      onClick={() => {
                        if (existing) onResume(existing.session_id);
                        else onStartCurated(lesson.source_url);
                      }}
                    >
                      <b>Lesson {lesson.lesson}</b>
                      <span>{lesson.title.replace(/^Let's Learn English Level 2 ·\s*/i, "")}</span>
                      <em>{existing ? "继续" : "开始"}</em>
                    </button>
                  );
                })}
              </div>
            </section>
          ) : null}
          <aside className="home-notice" aria-label="产品说明">
            <p>
              Enprato 是一款英语学习工具，你可以使用自己选择的英语素材进行听写、跟读与练习。
            </p>
            <p>
              英语素材推荐：BBC News、Bilibili、TED、YouTube 及各类英文 Podcast。
            </p>
            <p>
              第三方内容的版权及使用规则归相应内容提供方所有，请遵守原平台及权利人的相关规定。
            </p>
          </aside>
        </section>
      </div>
    </>
  );
}

function UpdateBanner({ info }: { info: UpdateInfo }) {
  return (
    <section className="update-banner">
      <div>
        <strong>发现新版本 {info.version}</strong>
        <span>{info.notes || "建议更新到最新版后继续使用"}</span>
      </div>
      <a href={info.download_url} target="_blank" rel="noreferrer">
        下载更新
      </a>
    </section>
  );
}

function AuthPanel({ user, onAuth }: { user: CurrentUser | null; onAuth: (user: CurrentUser | null) => void }) {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  async function logout() { await logoutAccount(); onAuth(null); }
  if (user) return <div className="account-compact"><button type="button" className="account-status" onClick={() => setOpen(!open)}><strong>{user.membership.status === "active" ? "Enprato Pro" : "免费版"}</strong><span>{user.membership.status === "active" ? "" : "剩余 " + user.trial.remaining + " 次"}</span><b>我的账号</b></button>{open ? <div className="account-popover"><p>{user.email}</p><p>会员状态：{user.membership.status === "active" ? "Enprato Pro" : "免费版"}</p><p>免费额度：{user.trial.remaining}/{user.trial.limit} 次</p>{user.membership.status === "active" ? <p>到期时间：{formatLicenseDate(user.membership.expires_at)}</p> : null}<button type="button" className="ghost" onClick={() => void logout()}>退出登录</button></div> : null}</div>;
  return <div className="account-compact"><button type="button" className="account-link" onClick={() => { setMessage(""); setOpen(true); }}>登录</button>{open ? <div className="auth-modal-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) setOpen(false); }}><section className="auth-modal wechat-auth-modal" role="dialog" aria-modal="true" aria-labelledby="wechat-auth-title"><button type="button" className="auth-modal-close" aria-label="关闭" onClick={() => setOpen(false)}>X</button><h2 id="wechat-auth-title">微信扫码登录</h2><div className="wechat-login-placeholder"><strong>使用微信扫码登录</strong><span>首次登录将自动创建 Enprato 账号</span><b>微信登录即将开放</b></div>{message ? <p className="err">{message}</p> : null}<p className="wechat-auth-note">登录后将自动恢复你的会员状态、免费额度和历史课程。</p></section></div> : null}</div>;
}

function LicensePanel({
  status,
  busy,
  loadError,
  onActivate,
  onRetry,
  localCompat = false,
}: {
  status: LicenseStatus | null;
  busy: boolean;
  loadError: string;
  onActivate: (key: string) => void | Promise<void>;
  onRetry: () => void | Promise<void>;
  localCompat?: boolean;
}) {
  const [key, setKey] = useState("");
  const [payError, setPayError] = useState("");
  const [payPlan, setPayPlan] = useState<"monthly" | "lifetime" | null>(null);
  const [payMethod, setPayMethod] = useState<"wechat" | "alipay" | null>(null);

  function openPay(plan: "monthly" | "lifetime") {
    const url = plan === "monthly" ? status?.pay_monthly_url : status?.pay_lifetime_url;
    if (url?.trim()) {
      setPayError("");
      window.open(url.trim(), "_blank", "noopener,noreferrer");
      return;
    }
    setPayError("");
    setPayPlan(plan);
    setPayMethod(null);
  }

  const payTitle = payPlan === "lifetime" ? "年付版 ¥199/年" : payPlan === "monthly" ? "会员版 ¥19.9/月" : "";
  const payNote = payPlan === "lifetime" ? "Enprato年付" : payPlan === "monthly" ? "Enprato月付" : "";
  const payMethodLabel = payMethod === "wechat" ? "微信支付" : payMethod === "alipay" ? "支付宝" : "";
  const payQrSrc =
    payPlan && payMethod
      ? `/pay/${payMethod}-${payPlan === "lifetime" ? "lifetime" : "monthly"}.jpg`
      : "";

  const used = status
    ? `${status.trial_uses ?? status.trial_imports}/${status.trial_uses_limit ?? status.trial_imports_limit}`
    : "-";

  return (
    <section className={`license-panel ${localCompat ? "license-compat-inner" : ""} ${status?.active ? "license-ok" : "license-locked"}`}>
      {!localCompat ? (
        !status?.licensed ? (
          <div className="license-summary">
            <div>
              <span>免费听写 {used} 次</span>
            </div>
            <b>{status?.active ? "可用" : "需激活"}</b>
          </div>
        ) : null
      ) : (
        <p className="meta license-compat-note">本机兼容授权 · {status?.active ? "可用" : "需激活"}</p>
      )}
      {!localCompat ? (
      <div className="price-grid">
        <button type="button" className="price-card" disabled={busy} onClick={() => void openPay("monthly")}>
          <strong>¥19.9/月</strong>
          <span>适合持续练习，按月续费</span>
        </button>
        <button type="button" className="price-card" disabled={busy} onClick={() => void openPay("lifetime")}>
          <strong>¥199/年</strong>
          <span>适合长期练习，按年续费</span>
        </button>
      </div>
      ) : null}
      {loadError ? <p className="err">{loadError}</p> : null}
      {!status && !loadError ? (
        <p className="err">正在读取授权状态…若长时间无响应，请点下方重新检测。</p>
      ) : null}
      {payError ? <p className="err">{payError}</p> : null}
      {!localCompat ? (
      <p className="meta pay-hint">
        {payPlan ? "请选择付款方式，打开对应收款码" : "点上方套餐选择付款方式，付款后粘贴授权码激活"}
      </p>
      ) : (
        <p className="meta pay-hint">仅用于本机/LAN 兼容；账号会员请走右上角账号体系。</p>
      )}
      {!localCompat && payPlan ? (
        <div className="pay-inline">
          <div className="pay-inline-head">
            <strong>{payTitle}</strong>
            <button
              type="button"
              className="ghost pay-inline-close"
              onClick={() => {
                setPayPlan(null);
                setPayMethod(null);
              }}
            >
              收起
            </button>
          </div>
          <p className="pay-modal-note">
            付款金额 <strong>{payPlan === "lifetime" ? "¥199" : "¥19.9"}</strong>，备注写
            <strong> {payNote}</strong>。
          </p>
          <div className="pay-method-grid">
            <button type="button" className="pay-method-card" onClick={() => setPayMethod("wechat")}>
              <strong>微信支付</strong>
              <span>打开微信收款码</span>
            </button>
            <button type="button" className="pay-method-card" onClick={() => setPayMethod("alipay")}>
              <strong>支付宝</strong>
              <span>打开支付宝收款码</span>
            </button>
          </div>
          <p className="meta pay-modal-foot">付款后把截图发给客服获取授权码，收到后在下方输入并点击「激活」。</p>
        </div>
      ) : null}
      {!localCompat && payPlan && payMethod ? (
        <div className="pay-modal-backdrop" role="presentation" onClick={() => setPayMethod(null)}>
          <div
            className="pay-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="pay-modal-title"
            onClick={(event) => event.stopPropagation()}
          >
            <button type="button" className="pay-modal-close" aria-label="关闭" onClick={() => setPayMethod(null)}>
              ×
            </button>
            <h3 id="pay-modal-title">{payMethodLabel}</h3>
            <p className="pay-modal-note">
              {payTitle}，付款金额 <strong>{payPlan === "lifetime" ? "¥199" : "¥19.9"}</strong>，备注写
              <strong> {payNote}</strong>。
            </p>
            <figure className="pay-qr-single">
              <img src={payQrSrc} alt={`${payMethodLabel}收款码`} />
              <figcaption>{payMethodLabel}</figcaption>
            </figure>
            <p className="meta pay-modal-foot">付款后把截图发给客服获取授权码。</p>
          </div>
        </div>
      ) : null}
      <form
        className="license-activate"
        onSubmit={(event) => {
          event.preventDefault();
          const clean = key.trim();
          if (clean) void onActivate(clean);
        }}
      >
        <input
          value={key}
          onChange={(event) => setKey(event.target.value)}
          placeholder={localCompat ? "输入本机兼容授权码（可选）" : "输入付款后获得的授权码"}
          spellCheck={false}
        />
        <button type="submit" className="primary" disabled={busy || !key.trim()}>
          {busy ? "激活中" : "激活"}
        </button>
      </form>
      <button type="button" className="ghost pay-retry" disabled={busy} onClick={() => void onRetry()}>
        重新检测授权
      </button>
    </section>
  );
}

function formatLicenseDate(value: string): string {
  if (!value) return "永久";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
}

function sessionThumbUrl(sessionId: string, version?: string): string {
  const q = version ? `?v=${encodeURIComponent(version)}` : "";
  return `/api/session/${sessionId}/thumb${q}`;
}

function sessionDisplayTitle(item: SessionSummary | undefined): string {
  if (!item) return "选择视频";
  const raw = String(item.title || "").trim();
  if (!raw) return "未命名视频";
  if (/^https?:\/\//i.test(raw)) {
    try {
      const u = new URL(raw);
      const host = u.hostname.replace(/^www\./i, "");
      if (/bilibili\.com$/i.test(host) || host.endsWith(".bilibili.com")) {
        const id = u.pathname.split("/").filter(Boolean).pop() || "";
        return id ? `哔哩哔哩 · ${id}` : "哔哩哔哩视频";
      }
      if (/youtube\.com$/i.test(host) || host === "youtu.be") {
        const id = u.searchParams.get("v") || u.pathname.split("/").filter(Boolean).pop() || "";
        return id ? `YouTube · ${id}` : "YouTube 视频";
      }
      return host;
    } catch {
      return raw.length > 48 ? `${raw.slice(0, 46)}…` : raw;
    }
  }
  return raw.length > 72 ? `${raw.slice(0, 70)}…` : raw;
}

function fmtDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

type BrowserSpeechRecognition = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start: () => void;
  stop: () => void;
  onresult: ((event: BrowserSpeechRecognitionEvent) => void) | null;
  onerror: ((event: BrowserSpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
};

type BrowserSpeechRecognitionEvent = {
  resultIndex: number;
  results: {
    length: number;
    [index: number]: {
      isFinal: boolean;
      [index: number]: { transcript: string } | undefined;
    };
  };
};

type BrowserSpeechRecognitionErrorEvent = {
  error: string;
};

function createSpeechRecognition(): BrowserSpeechRecognition | null {
  const w = window as Window & {
    SpeechRecognition?: new () => BrowserSpeechRecognition;
    webkitSpeechRecognition?: new () => BrowserSpeechRecognition;
  };
  const Ctor = w.SpeechRecognition || w.webkitSpeechRecognition;
  return Ctor ? new Ctor() : null;
}

function sessionThumbSrc(sessionId: string, version?: string): string {
  return sessionThumbUrl(sessionId, version);
}

function SessionThumb({
  sessionId,
  label,
  version,
  coverUrl,
}: {
  sessionId: string;
  label?: string;
  version?: string;
  coverUrl?: string;
}) {
  const localSrc = sessionThumbSrc(sessionId, version);
  const [src, setSrc] = useState(localSrc);
  useEffect(() => {
    setSrc(sessionThumbSrc(sessionId, version));
  }, [sessionId, version]);
  return (
    <span className="session-video-thumb" aria-hidden="true">
      <img
        src={src}
        alt=""
        loading="lazy"
        referrerPolicy="no-referrer"
        onError={(e) => {
          if (coverUrl && src !== coverUrl) {
            setSrc(coverUrl);
            return;
          }
          e.currentTarget.style.display = "none";
        }}
      />
      <span className="thumb-fallback">{label ? label.slice(0, 1) : "课"}</span>
    </span>
  );
}

function SessionVideoPicker({
  history,
  sessionId,
  disabled,
  onPick,
}: {
  history: SessionSummary[];
  sessionId: string;
  disabled?: boolean;
  onPick: (id: string) => void | Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const current = history.find((item) => item.session_id === sessionId);

  useEffect(() => {
    if (!open) return;
    const onDoc = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  if (!history.length) return null;

  return (
    <div className="session-video-picker" ref={rootRef}>
      <button
        type="button"
        className="session-video-trigger"
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => setOpen((value) => !value)}
      >
        <SessionThumb
          sessionId={sessionId}
          label={sessionDisplayTitle(current)}
          version={current?.updated_at}
          coverUrl={current?.cover_url}
        />
        <span className="session-video-trigger-text">
          <strong>{sessionDisplayTitle(current)}</strong>
          <span>
            {current ? `${current.done}/${current.count} 句` : ""}
          </span>
        </span>
        <span className="session-video-chevron" aria-hidden="true">▾</span>
      </button>
      {open ? (
        <div className="session-video-menu" role="listbox" aria-label="历史视频">
          <div className="session-video-menu-head">切换历史视频</div>
          <div className="session-video-list">
            {history.map((item) => (
              <button
                key={item.session_id}
                type="button"
                role="option"
                aria-selected={item.session_id === sessionId}
                className={`session-video-item${item.session_id === sessionId ? " active" : ""}`}
                onClick={() => {
                  setOpen(false);
                  if (item.session_id !== sessionId) void onPick(item.session_id);
                }}
              >
                <SessionThumb
                  sessionId={item.session_id}
                  label={sessionDisplayTitle(item)}
                  version={item.updated_at}
                  coverUrl={item.cover_url}
                />
                <span className="session-video-item-body">
                  <strong>{sessionDisplayTitle(item)}</strong>
                  <span>
                    {item.done}/{item.count} 句 · {phaseLabel(item.phase)} · {fmtDuration(item.duration)}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function Studio({
  phase,
  setPhase,
  videoUrl,
  audioUrl,
  sessionId,
  sentences,
  orientation,
  initialIndex,
  initialDrafts,
  initialHighlights,
  initialScore,
  audioOnly = false,
  history,
  onOrientation,
  onRefreshHistory,
  onSwitchSession,
  onReset,
}: {
  phase: Phase;
  setPhase: (phase: Phase) => void;
  videoUrl: string;
  audioUrl: string;
  sessionId: string;
  sentences: Sentence[];
  orientation: Orientation;
  initialIndex: number;
  initialDrafts: Record<number, string>;
  initialHighlights: Highlight[];
  initialScore: ShadowScore | null;
  audioOnly?: boolean;
  history: SessionSummary[];
  onOrientation: (value: Orientation) => void;
  onRefreshHistory: () => Promise<void>;
  onSwitchSession: (id: string) => Promise<void>;
  onReset: () => void;
}) {
  void audioUrl;

  const videoRef = useRef<HTMLVideoElement>(null);
  const monitorRef = useRef<HTMLDivElement>(null);
  const phaseRef = useRef(phase);
  const indexRef = useRef(0);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const shadowChunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const draftsRef = useRef<Record<number, string>>({});
  const scrubbingRef = useRef(false);
  const skipAutoPlayRef = useRef(false);
  const pauseAtRef = useRef(0);
  const segmentEndRef = useRef<number | null>(null);
  const playTokenRef = useRef(0);
  const draftTextareaRef = useRef<HTMLTextAreaElement>(null);
  const prevPhaseRef = useRef(phase);
  const repeatClickRef = useRef({ at: 0, baseIndex: initialIndex, count: 0 });
  const speechRecRef = useRef<BrowserSpeechRecognition | null>(null);
  const micBaseDraftRef = useRef("");
  const micStartedAtRef = useRef(0);
  const speechFinalRef = useRef("");
  const micRevealTimerRef = useRef(0);
  const recordingRef = useRef(false);
  const ipadStudioRef = useRef(false);

  const [index, setIndex] = useState(initialIndex);
  const [drafts, setDrafts] = useState<Record<number, string>>(initialDrafts);
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [highlights, setHighlights] = useState<Highlight[]>(initialHighlights);
  const [sense, setSense] = useState<WordSense | null>(null);
  const [score, setScore] = useState<ShadowScore | null>(initialScore);
  const [userPaused, setUserPaused] = useState(false);
  const [now, setNow] = useState(sentences[initialIndex]?.start ?? 0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(1);
  const [captionMode, setCaptionMode] = useState<CaptionMode>("off");
  const [zhMap, setZhMap] = useState<Record<string, string>>({});
  const zhPendingRef = useRef<Record<string, boolean>>({});
  const [phoneMic, setPhoneMic] = useState(false);
  const [phoneLink, setPhoneLink] = useState("");
  const [ipadStudio, setIpadStudio] = useState(false);
  const [ipadLink, setIpadLink] = useState("");
  const [ipadHome, setIpadHome] = useState("");
  const [ipadBuild, setIpadBuild] = useState("");
  useEffect(() => {
    ipadStudioRef.current = ipadStudio;
  }, [ipadStudio]);
  const [phonePaired, setPhonePaired] = useState(false);
  const [switchingSession, setSwitchingSession] = useState(false);
  const [localAudioOnly, setLocalAudioOnly] = useState(audioOnly);
  const remoteAfterRef = useRef(0);
  const userPausedRef = useRef(userPaused);
  const draftLocalEditUntilRef = useRef(0);
  const draftManualEditAtRef = useRef<Record<number, number>>({});
  const draftServerSnapRef = useRef("");
  const draftBaselineRef = useRef("");
  const draftBeforeInputRef = useRef<DraftBeforeInput | null>(null);
  const [draftCanRestore, setDraftCanRestore] = useState(false);

  const sentence = sentences[index];
  const overlayEnglish =
    phase === "shadow" || phase === "result" ? currentCaption(sentences, now) : sentence?.text || "";
  const overlayZh = zhMap[overlayEnglish] || "";

  const captionLabel = captionMode === "off" ? "字幕：关" : captionMode === "en" ? "字幕：英语" : "字幕：双语";
  const showAudioOnly = localAudioOnly || audioOnly;

  function draftContentEnd(text: string): number {
    if (!text.trim()) return 0;
    const trimmedEnd = text.search(/\s+$/);
    return trimmedEnd < 0 ? text.length : trimmedEnd;
  }

  function snapshotDraftBaseline() {
    return JSON.stringify(draftsRef.current);
  }

  function sessionProgressPayload(
    payload: {
      phase: string;
      index?: number;
      drafts?: Record<number, string>;
      highlights: { sentenceId: number; word: string }[];
      score: ShadowScore | null;
      orientation: string;
    },
    options?: { includeIndex?: boolean },
  ) {
    if (options?.includeIndex || !ipadStudioRef.current) return payload;
    const { index: _index, ...rest } = payload;
    return rest;
  }

  function restoreDraftEdits() {
    try {
      const next = JSON.parse(draftBaselineRef.current) as Record<number, string>;
      draftsRef.current = next;
      setDrafts(next);
      setDraftCanRestore(false);
      void saveProgress(
        sessionId,
        sessionProgressPayload({
          phase: phaseRef.current,
          index: indexRef.current,
          drafts: next,
          highlights,
          score,
          orientation,
        }),
      );
      writeDraftsCache(sessionId, next, indexRef.current);
      window.setTimeout(() => revealDraftEnd(), 80);
    } catch {
      /* ignore */
    }
  }

  function clearSelectedDraftSentences(before: DraftBeforeInput, nextText: string): boolean {
    if (!isDraftDeleteInput(before.inputType)) return false;
    if (nextText.length >= before.value.length) return false;
    const cleared = selectedDraftIndexes(before).filter((i) => i >= 0 && i < sentences.length);
    if (!cleared.length) return false;

    const editedAt = Date.now();
    const next = { ...draftsRef.current };
    for (const i of cleared) {
      next[i] = "";
      draftManualEditAtRef.current[i] = editedAt;
    }
    const first = cleared[0];
    draftsRef.current = next;
    setDrafts(next);
    draftLocalEditUntilRef.current = editedAt + 8000;
    indexRef.current = first;
    setIndex(first);
    setPhase("listen");
    setCaptionMode("off");
    setDraftCanRestore(JSON.stringify(next) !== draftBaselineRef.current);
    writeDraftsCache(sessionId, next, first);
    void saveProgress(
      sessionId,
      sessionProgressPayload({
        phase: "listen",
        index: first,
        drafts: next,
        highlights,
        score,
        orientation,
      }),
    );
    const target = sentences[first];
    if (target && !recording) {
      pauseAtRef.current = target.start;
      playAt(target.start);
    }
    return true;
  }

  function revealDraftEnd() {
    const el = draftTextareaRef.current;
    if (!el) return;
    const pos = draftContentEnd(el.value || "");
    const apply = () => {
      try {
        el.setSelectionRange(pos, pos);
      } catch {
        /* ignore */
      }
      const style = window.getComputedStyle(el);
      let lh = parseFloat(style.lineHeight);
      if (!Number.isFinite(lh) || lh < 8) lh = (parseFloat(style.fontSize) || 16) * 1.7;
      const before = (el.value || "").slice(0, pos);
      const line = Math.max(1, before.split("\n").length);
      const maxScroll = Math.max(0, el.scrollHeight - el.clientHeight);
      el.scrollTop = Math.min(maxScroll, Math.max(0, line * lh - el.clientHeight * 0.4));
    };
    apply();
    requestAnimationFrame(apply);
    setTimeout(apply, 120);
  }

  useEffect(() => {
    const prev = prevPhaseRef.current;
    prevPhaseRef.current = phase;
    if (prev === "listen" && phase === "dictate") {
      window.setTimeout(() => revealDraftEnd(), 80);
    }
  }, [phase, index]);

  function cycleCaption() {
    setCaptionMode((mode) => (mode === "off" ? "en" : mode === "en" ? "bi" : "off"));
  }

  const prevIndexForCaptionRef = useRef(index);
  useEffect(() => {
    if (index === prevIndexForCaptionRef.current) return;
    if (phaseRef.current === "listen" || phaseRef.current === "dictate") {
      setCaptionMode("off");
    }
    prevIndexForCaptionRef.current = index;
  }, [index]);

  useEffect(() => {
    recordingRef.current = recording;
  }, [recording]);

  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  function stopSpeechRecognition() {
    const rec = speechRecRef.current;
    speechRecRef.current = null;
    if (!rec) return;
    try {
      rec.onresult = null;
      rec.onerror = null;
      rec.onend = null;
      rec.stop();
    } catch {
      /* ignore */
    }
  }

  function scheduleRevealWhileMic() {
    if (micRevealTimerRef.current) return;
    micRevealTimerRef.current = window.setTimeout(() => {
      micRevealTimerRef.current = 0;
      revealDraftEnd();
    }, 60);
  }

  function applyLiveMicDraft(livePiece: string, target: string, sentenceIndex: number) {
    const live = livePiece.trim();
    if (!live) return;
    if ((draftManualEditAtRef.current[sentenceIndex] || 0) >= micStartedAtRef.current) return;
    const merged = mergeDictationPiece(micBaseDraftRef.current, live, target);
    setDrafts((prev) => {
      const next = { ...prev, [sentenceIndex]: merged };
      draftsRef.current = next;
      return next;
    });
    scheduleRevealWhileMic();
  }

  function startSpeechRecognition(target: string, sentenceIndex: number) {
    const recognition = createSpeechRecognition();
    if (!recognition) return false;
    speechFinalRef.current = "";
    recognition.lang = "en-US";
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.onresult = (event) => {
      let interim = "";
      for (let j = event.resultIndex; j < event.results.length; j += 1) {
        const piece = event.results[j][0]?.transcript ?? "";
        if (event.results[j].isFinal) {
          speechFinalRef.current = `${speechFinalRef.current}${speechFinalRef.current ? " " : ""}${piece.trim()}`;
        } else {
          interim += piece;
        }
      }
      const live = `${speechFinalRef.current}${interim ? (speechFinalRef.current ? " " : "") + interim : ""}`;
      applyLiveMicDraft(live, target, sentenceIndex);
    };
    recognition.onerror = (event) => {
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        setError("浏览器拦住了实时语音识别，请允许麦克风后重试。");
      }
    };
    recognition.onend = () => {
      if (recordingRef.current && speechRecRef.current === recognition) {
        try {
          recognition.start();
        } catch {
          /* ignore */
        }
      }
    };
    try {
      recognition.start();
      speechRecRef.current = recognition;
      return true;
    } catch {
      return false;
    }
  }

  useEffect(() => {
    indexRef.current = index;
  }, [index]);

  useEffect(() => {
    userPausedRef.current = userPaused;
  }, [userPaused]);

  useEffect(() => {
    draftsRef.current = drafts;
  }, [drafts]);

  useEffect(() => {
    const monitor = monitorRef.current;
    const video = videoRef.current;
    if (!monitor || !video || showAudioOnly) return;
    const sync = () => applyBurnWipeLayout(monitor, video, orientation);
    sync();
    const ro = new ResizeObserver(sync);
    ro.observe(monitor);
    video.addEventListener("loadedmetadata", sync);
    window.addEventListener("resize", sync);
    return () => {
      ro.disconnect();
      video.removeEventListener("loadedmetadata", sync);
      window.removeEventListener("resize", sync);
    };
  }, [orientation, showAudioOnly, videoUrl]);

  useEffect(() => {
    if (captionMode !== "bi") return;
    const targets = [overlayEnglish, sentences[index + 1]?.text || ""].filter((item) => item.trim());
    for (const text of targets) {
      if (zhMap[text] || zhPendingRef.current[text]) continue;
      zhPendingRef.current[text] = true;
      void translateSentence(text)
        .then((zh) => {
          if (zh) setZhMap((prev) => ({ ...prev, [text]: zh }));
        })
        .catch(() => undefined)
        .finally(() => {
          delete zhPendingRef.current[text];
        });
    }
  }, [captionMode, overlayEnglish, index, sentences, zhMap]);

  useEffect(() => {
    if (!sessionId) return;
    const timer = window.setTimeout(() => {
      void saveProgress(
        sessionId,
        sessionProgressPayload({
          phase,
          index,
          drafts: draftsRef.current,
          highlights,
          score,
          orientation,
        }),
      );
      writeDraftsCache(sessionId, draftsRef.current, indexRef.current);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [sessionId, phase, index, drafts, highlights, score, orientation, ipadStudio]);

  useEffect(() => {
    if (!sessionId) return;
    writeDraftsCache(sessionId, drafts, index);
  }, [sessionId, drafts, index]);

  useEffect(() => {
    if (!sessionId) return;
    const timer = window.setTimeout(() => {
      void saveProgress(
        sessionId,
        sessionProgressPayload({
          phase: phaseRef.current,
          index: indexRef.current,
          drafts: draftsRef.current,
          highlights,
          score,
          orientation,
        }),
      );
      writeDraftsCache(sessionId, draftsRef.current, indexRef.current);
    }, 120);
    return () => window.clearTimeout(timer);
  }, [sessionId, highlights, score, orientation, ipadStudio]);

  useEffect(() => {
    if (!sessionId) return;
    const flush = () => {
      void saveProgress(
        sessionId,
        sessionProgressPayload({
          phase: phaseRef.current,
          index: indexRef.current,
          drafts: draftsRef.current,
          highlights,
          score,
          orientation,
        }),
        { keepalive: true },
      );
      writeDraftsCache(sessionId, draftsRef.current, indexRef.current);
    };
    const onHide = () => {
      if (document.visibilityState === "hidden") flush();
    };
    window.addEventListener("pagehide", flush);
    window.addEventListener("visibilitychange", onHide);
    return () => {
      window.removeEventListener("pagehide", flush);
      window.removeEventListener("visibilitychange", onHide);
      flush();
    };
  }, [sessionId, highlights, score, orientation, ipadStudio]);

  useEffect(() => {
    if ((!phoneMic && !ipadStudio) || !sessionId) return;
    void claimRemoteSession(sessionId).catch(() => undefined);
  }, [phoneMic, ipadStudio, sessionId]);

  useEffect(() => {
    if (!ipadStudio) return;
    let cancelled = false;
    const pull = async () => {
      try {
        const lan = await fetchLanLinks();
        if (cancelled) return;
        const build = lan.ipad_build || "";
        if (build) setIpadBuild(build);
        const home =
          lan.ipad_home?.[0] ||
          (lan.ips[0] ? `https://${lan.ips[0]}:${lan.port}/ipad` : "");
        if (home) setIpadHome(home);
        const base =
          lan.ipad_links?.[0] ||
          lan.links[0]?.replace(/\/remote$/, `/ipad/${build}`) ||
          (lan.ips[0] ? `https://${lan.ips[0]}:${lan.port}/ipad/${build}` : "");
        if (base) setIpadLink(base);
      } catch {
        /* ignore */
      }
    };
    void pull();
    const timer = window.setInterval(() => void pull(), 8000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [ipadStudio]);

  useEffect(() => {
    if ((!phoneMic && !ipadStudio) || !sessionId) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const data = await fetchRemoteInbox(sessionId, remoteAfterRef.current);
        if (cancelled) return;
        if (phoneMic && data.connected) setPhonePaired(true);
        if (phoneMic) {
          let pendingAdvanceIndex: number | null = null;
          for (const item of data.items) {
            remoteAfterRef.current = Math.max(remoteAfterRef.current, item.id);
            const text = String(item.text || "");
            const beforeIndex = indexRef.current;
            if (item.index >= indexRef.current) {
              indexRef.current = item.index;
              setIndex(item.index);
            }
            if (text.trim()) {
              pendingAdvanceIndex = null;
              const recentlyEdited = (draftManualEditAtRef.current[item.index] || 0) + 8000 > Date.now();
              const draftFocused = document.activeElement?.classList.contains("draft");
              if (!recentlyEdited && !draftFocused) {
                setDrafts((prev) => {
                  const next = { ...prev, [item.index]: text };
                  draftsRef.current = next;
                  return next;
                });
              }
              setPhase("dictate");
            } else if (item.index > beforeIndex) {
              pendingAdvanceIndex = item.index;
            }
            setPhonePaired(true);
            setError("");
          }
          if (pendingAdvanceIndex != null) {
            setPhase("listen");
            setCaptionMode("off");
            const s = sentences[pendingAdvanceIndex];
            if (s) {
              setUserPaused(false);
              playAt(s.start);
            }
          }
        }
        const state = await fetchRemoteState(sessionId);
        if (cancelled) return;
        if (phoneMic) setPhonePaired(true);
        if (
          ipadStudio &&
          typeof state.index === "number" &&
          state.index !== indexRef.current
        ) {
          indexRef.current = state.index;
          setIndex(state.index);
          setCaptionMode("off");
        } else if (
          !ipadStudio &&
          typeof state.index === "number" &&
          state.index > indexRef.current &&
          userPausedRef.current
        ) {
          indexRef.current = state.index;
          setIndex(state.index);
          setPhase("listen");
          setCaptionMode("off");
        }
        const draftFocused = document.activeElement?.classList.contains("draft");
        if (!draftFocused && Date.now() >= draftLocalEditUntilRef.current) {
          const serverDrafts = draftsFromServerMap(state.drafts);
          const snap = JSON.stringify(serverDrafts);
          const localSnap = JSON.stringify(collapseIdenticalDrafts(draftsRef.current));
          const localChars = localSnap.length;
          const serverChars = snap.length;
          if (snap !== draftServerSnapRef.current && serverChars >= localChars - 8) {
            draftServerSnapRef.current = snap;
            draftsRef.current = serverDrafts;
            setDrafts(serverDrafts);
            draftBaselineRef.current = snap;
            setDraftCanRestore(false);
          }
        }
      } catch {
        /* ignore poll errors */
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), 1200);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [phoneMic, ipadStudio, sessionId, sentences]);

  useEffect(() => {
    const node = videoRef.current;
    if (!node) return;
    const hideTracks = () => {
      for (const track of Array.from(node.textTracks)) {
        track.mode = "disabled";
      }
    };
    hideTracks();
    node.addEventListener("loadedmetadata", hideTracks);
    return () => node.removeEventListener("loadedmetadata", hideTracks);
  }, [videoUrl]);

  useEffect(() => {
    return () => {
      streamRef.current?.getTracks().forEach((track) => track.stop());
      void speakerStop(sessionId);
    };
  }, [sessionId]);

  function releaseMic() {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }

  function pauseMedia() {
    playTokenRef.current += 1;
    videoRef.current?.pause();
    void speakerStop(sessionId);
  }

  useEffect(() => {
    void onRefreshHistory();
  }, []);

  async function switchSession(nextId: string) {
    if (!nextId || nextId === sessionId || switchingSession) return;
    if (recording) {
      setError("请先停止录音再切换视频");
      return;
    }
    setSwitchingSession(true);
    setError("");
    pauseMedia();
    try {
      await saveProgress(sessionId, {
        phase: phaseRef.current,
        index: indexRef.current,
        drafts: draftsRef.current,
        highlights,
        score,
        orientation,
      });
      writeDraftsCache(sessionId, draftsRef.current, indexRef.current);
      await onSwitchSession(nextId);
      await onRefreshHistory();
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法切换视频");
    } finally {
      setSwitchingSession(false);
    }
  }

  function playAt(time: number, endOverride?: number) {
    const video = videoRef.current;
    if (!video) return;
    const token = ++playTokenRef.current;
    const current = sentences[indexRef.current];
    const end = endOverride ?? current?.end ?? time + 3;
    segmentEndRef.current = endOverride ?? null;
    userPausedRef.current = false;
    setUserPaused(false);
    setError("");

    video.muted = false;
    video.defaultMuted = false;
    video.volume = Math.max(0.01, Math.min(1, volume));
    try {
      video.currentTime = time;
    } catch {
      /* ignore */
    }
    const playPromise = video.play();
    if (playPromise) {
      void playPromise.catch((err: unknown) => {
        const name = err instanceof DOMException ? err.name : "";
        if (name === "NotAllowedError") {
          setError("\u6d4f\u89c8\u5668\u62e6\u4f4f\u4e86\u64ad\u653e\uff0c\u8bf7\u518d\u70b9\u4e00\u6b21\u300c\u91cd\u590d\u672c\u53e5\u300d\u3002");
        } else if (err instanceof Error && err.message) {
          setError("\u64ad\u653e\u5931\u8d25\uff1a" + err.message);
        }
      });
    }

    const canUseServerSpeaker =
      ENABLE_SERVER_SPEAKER && ["localhost", "127.0.0.1"].includes(window.location.hostname);
    if (!canUseServerSpeaker) return;

    video.muted = true;
    video.defaultMuted = true;
    void speakerPlay(sessionId, time, end, volume)
      .then(() => {
        if (token !== playTokenRef.current) return;
        video.muted = true;
      })
      .catch(() => {
        if (token !== playTokenRef.current) return;
        video.muted = false;
        video.defaultMuted = false;
        video.volume = Math.max(0.01, Math.min(1, volume));
      });
  }

  function playCurrent() {
    const current = sentences[indexRef.current];
    if (!current) return;
    segmentEndRef.current = null;
    playAt(current.start);
  }

  function clipTime(time: number) {
    const video = videoRef.current;
    const cap =
      video && Number.isFinite(video.duration) && video.duration > 0
        ? video.duration
        : sentences[sentences.length - 1]?.end || time;
    return Math.max(0, Math.min(time, cap));
  }

  function sentenceIndexAt(time: number) {
    const hit = sentences.findIndex((item) => time >= item.start && time <= item.end + 0.12);
    if (hit >= 0) return hit;
    let best = 0;
    for (let i = 0; i < sentences.length; i++) {
      if (sentences[i].start <= time) best = i;
      else break;
    }
    return best;
  }

  function commitSeek(time: number) {
    const video = videoRef.current;
    if (!video) return;
    repeatClickRef.current = { at: 0, baseIndex: indexRef.current, count: 0 };
    const t = clipTime(time);
    const i = sentenceIndexAt(t);
    if (i !== indexRef.current || phaseRef.current !== "listen") {
      skipAutoPlayRef.current = true;
    }
    indexRef.current = i;
    setIndex(i);
    setNow(t);
    if (phaseRef.current !== "shadow" && phaseRef.current !== "result") {
      setPhase("listen");
      playAt(t);
    } else if (videoRef.current) {
      videoRef.current.currentTime = t;
    }
  }

  function onTimeUpdate() {
    const video = videoRef.current;
    const current = sentences[indexRef.current];
    if (!video || !current) return;
    if (!scrubbingRef.current) setNow(video.currentTime);
    if (scrubbingRef.current) return;
    const mode = phaseRef.current;
    if (mode === "listen" || mode === "check") {
      const segmentEnd = segmentEndRef.current;
      const stopAt = segmentEnd ?? current.end;
      if (video.currentTime >= stopAt - 0.05) {
        if (segmentEnd == null) {
          pauseAtRef.current = current.end;
        }
        pauseMedia();
        video.currentTime = stopAt;
        setNow(stopAt);
        setUserPaused(true);
        segmentEndRef.current = null;
        if (segmentEnd == null && mode === "listen") setPhase("dictate");
      }
    }
  }

  function pause() {
    const t = videoRef.current?.currentTime ?? now;
    segmentEndRef.current = null;
    pauseAtRef.current = t;
    setNow(t);
    pauseMedia();
    userPausedRef.current = true;
    setUserPaused(true);
  }

  function shouldAdvanceFromPause(
    sentence: { start: number; end: number },
    t: number,
  ): boolean {
    const dur = Math.max(0.05, sentence.end - sentence.start);
    const tail = Math.min(0.12, dur * 0.35);
    return t >= sentence.end - tail;
  }

  function resume() {
    const current = sentences[indexRef.current];
    const videoT = videoRef.current?.currentTime ?? now;
    let t = pauseAtRef.current;
    if (!Number.isFinite(t) || t <= 0) {
      t = videoT;
    }
    t = Math.max(t, videoT);
    segmentEndRef.current = null;
    if (current && shouldAdvanceFromPause(current, t)) {
      const next = indexRef.current + 1;
      if (next < sentences.length) {
        indexRef.current = next;
        setIndex(next);
        pauseAtRef.current = sentences[next].start;
        setCaptionMode("off");
        setPhase("listen");
        playAt(sentences[next].start);
        return;
      }
      setPhase("shadow");
      pauseMedia();
      setUserPaused(true);
      if (videoRef.current) videoRef.current.currentTime = 0;
      return;
    }
    if (phaseRef.current === "dictate") setPhase("listen");
    playAt(t + 0.03);
  }

  function togglePause() {
    if (userPausedRef.current) {
      resume();
    } else {
      pause();
    }
  }

  function repeatCurrent() {
    if (phase === "shadow" || phase === "result") return;
    const current = sentences[indexRef.current];
    if (!current) return;
    const draftText =
      draftsRef.current[indexRef.current] ?? draftsRef.current[current.id] ?? "";
    const t = resumeTimeInSentence(current, draftText, pauseAtRef.current);
    setUserPaused(false);
    if (phase !== "check") setCaptionMode("off");
    setPhase(phase === "check" ? "check" : "listen");
    playAt(t);
  }

  function repeatAtIndex(target: number) {
    const sentence = sentences[target];
    if (!sentence) return;
    setSense(null);
    indexRef.current = target;
    setIndex(target);
    pauseAtRef.current = sentence.start;
    setUserPaused(false);
    setCaptionMode("off");
    setPhase("listen");
    void saveProgress(sessionId, {
      phase: "listen",
      index: target,
      drafts: draftsRef.current,
      highlights,
      score,
      orientation,
    });
    writeDraftsCache(sessionId, draftsRef.current, target);
    playAt(sentence.start);
  }

  function repeat() {
    if (phase === "shadow" || phase === "result") return;
    const nowMs = Date.now();
    const prev = repeatClickRef.current;
    const continued = nowMs - prev.at <= 900;
    const baseIndex = continued ? prev.baseIndex : indexRef.current;
    const count = continued ? prev.count + 1 : 1;
    const target = baseIndex - (count - 1);
    if (target < 0) return;
    repeatClickRef.current = { at: nowMs, baseIndex, count };
    if (target === indexRef.current) {
      repeatCurrent();
    } else {
      repeatAtIndex(target);
    }
  }

  function finishDictation() {
    const currentIndex = indexRef.current;
    void saveProgress(sessionId, {
      phase: "check",
      index: currentIndex,
      drafts: draftsRef.current,
      highlights,
      score,
      orientation,
    });
    writeDraftsCache(sessionId, draftsRef.current, currentIndex);
    pauseMedia();
    setUserPaused(true);
    setCaptionMode("en");
    setPhase("check");
  }

  async function togglePhoneMic() {
    if (phoneMic) {
      setPhoneMic(false);
      if (!ipadStudio) {
        setPhonePaired(false);
        void claimRemoteSession(null).catch(() => undefined);
      }
      return;
    }
    try {
      const lan = await fetchLanLinks();
      const base = lan.links[0] || (lan.ips[0] ? `https://${lan.ips[0]}:${lan.port}/remote` : "");
      if (!base) {
        setError("\u65e0\u6cd5\u83b7\u53d6\u624b\u673a\u5f55\u97f3\u5165\u53e3\uff0c\u8bf7\u5237\u65b0\u540e\u91cd\u8bd5\u3002");
        return;
      }
      remoteAfterRef.current = 0;
      setPhoneLink(base);
      setPhonePaired(false);
      setPhoneMic(true);
      setError("");
      void claimRemoteSession(sessionId).catch(() => undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法开启手机麦");
    }
  }

  async function toggleIpadStudio() {
    if (ipadStudio) {
      setIpadStudio(false);
      if (!phoneMic) {
        setPhonePaired(false);
        void claimRemoteSession(null).catch(() => undefined);
      }
      return;
    }
    try {
      const lan = await fetchLanLinks();
      const build = lan.ipad_build || "";
      const home =
        lan.ipad_home?.[0] ||
        (lan.ips[0] ? `https://${lan.ips[0]}:${lan.port}/ipad` : "");
      const base =
        lan.ipad_links?.[0] ||
        lan.links[0]?.replace(/\/remote$/, `/ipad/${build}`) ||
        (lan.ips[0] ? `https://${lan.ips[0]}:${lan.port}/ipad/${build}` : "");
      if (!base && !home) {
        setError("\u65e0\u6cd5\u83b7\u53d6\u624b\u673a\u5f55\u97f3\u5165\u53e3\uff0c\u8bf7\u5237\u65b0\u540e\u91cd\u8bd5\u3002");
        return;
      }
      remoteAfterRef.current = 0;
      setIpadLink(base || home);
      setIpadHome(home || ipadHomeUrl(base || ""));
      setIpadBuild(lan.ipad_build || "");
      setPhonePaired(false);
      setPhoneMic(false);
      setIpadStudio(true);
      setError("");
      void claimRemoteSession(sessionId).catch(() => undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法开启 iPad 模式");
    }
  }

  function nextSentence() {
    repeatClickRef.current = { at: 0, baseIndex: indexRef.current, count: 0 };
    setSense(null);
    const currentIndex = indexRef.current;
    if (currentIndex >= sentences.length - 1) {
      setPhase("shadow");
      pauseMedia();
      setUserPaused(true);
      if (videoRef.current) videoRef.current.currentTime = 0;
      return;
    }
    const next = currentIndex + 1;
    indexRef.current = next;
    setIndex(next);
    pauseAtRef.current = sentences[next]?.start ?? 0;
    setUserPaused(false);
    setCaptionMode("off");
    setPhase("listen");
    // 立刻把句号写入后端，手机麦不会还停在上一句
    void saveProgress(sessionId, {
      phase: "listen",
      index: next,
      drafts: draftsRef.current,
      highlights,
      score,
      orientation,
    });
    writeDraftsCache(sessionId, draftsRef.current, next);
    playCurrent();
  }

  async function ensureStream() {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("当前浏览器不支持麦克风，请用 Chrome 打开 http://127.0.0.1:5173");
    }
    if (!streamRef.current || streamRef.current.getTracks().some((t) => t.readyState === "ended")) {
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
      });
    }
    return streamRef.current;
  }

  async function toggleMic() {
    if (recording) {
      stopSpeechRecognition();
      if (micRevealTimerRef.current) {
        window.clearTimeout(micRevealTimerRef.current);
        micRevealTimerRef.current = 0;
      }
      try {
        if (recorderRef.current && recorderRef.current.state !== "inactive") {
          recorderRef.current.stop();
        } else {
          setRecording(false);
        }
      } catch {
        setRecording(false);
      }
      return;
    }
    setError("");
    const i = indexRef.current;
    const target = sentences[i]?.text || "";
    micBaseDraftRef.current = String(draftsRef.current[i] ?? "");
    micStartedAtRef.current = Date.now();
    setRecording(true);
    try {
      const stream = await ensureStream();
      const liveOk = startSpeechRecognition(target, i);
      if (!liveOk) {
        setError("当前浏览器不支持实时听写预览，停止后仍会识别；请用 Chrome 或 Edge。");
      }
      const recorder = new MediaRecorder(stream, recorderOptions());
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size) chunksRef.current.push(event.data);
      };
      recorder.onerror = () => {
        stopSpeechRecognition();
        setRecording(false);
        setError("录音中断，请再点一次「语音输入」。");
      };
      recorder.onstop = async () => {
        stopSpeechRecognition();
        if (micRevealTimerRef.current) {
          window.clearTimeout(micRevealTimerRef.current);
          micRevealTimerRef.current = 0;
        }
        setRecording(false);
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
        const base = micBaseDraftRef.current;
        const liveCur = String(draftsRef.current[i] ?? "").trim();
        const manuallyEdited = (draftManualEditAtRef.current[i] || 0) >= micStartedAtRef.current;
        if (blob.size < 800) {
          if (!manuallyEdited && liveCur && liveCur !== base.trim()) {
            setDrafts((prev) => {
              const next = { ...prev, [i]: liveCur };
              draftsRef.current = next;
              return next;
            });
          }
          setError("没有录到声音。请允许麦克风后靠近再说；说完再点停止语音输入。");
          return;
        }
        const hasLiveDraft = Boolean(liveCur && liveCur !== base.trim());
        if (hasLiveDraft && !manuallyEdited) {
          setDrafts((prev) => {
            const next = { ...prev, [i]: liveCur };
            draftsRef.current = next;
            return next;
          });
          setPhase("dictate");
          setError("");
          window.setTimeout(() => revealDraftEnd(), 80);
        } else {
          setBusy(true);
        }

        // Browser speech recognition gives us an immediate draft. Whisper refines it in the background.
        void (async () => {
          try {
            const prevDraft = i > 0 ? String(draftsRef.current[i - 1] ?? "").trim() : "";
            const context = prevDraft.length > 160 ? prevDraft.slice(-160) : prevDraft;
            const text = await transcribeUtterance(blob, context, target);
            const piece = String(text || "").trim();
            setDrafts((prev) => {
              if ((draftManualEditAtRef.current[i] || 0) >= micStartedAtRef.current) return prev;
              let merged = dedupeRepeatedClauses(base.trim());
              if (piece) {
                merged = dedupeRepeatedClauses(mergeDictationPiece(base, piece, target));
              } else if (liveCur && liveCur !== base.trim()) {
                merged = dedupeRepeatedClauses(liveCur);
              }
              if (!merged) return prev;
              const next = { ...prev, [i]: merged };
              draftsRef.current = next;
              return next;
            });
            if (!piece && !hasLiveDraft) {
              setError("没听清。请靠近麦克风，说完整句后再点停止；有口音也会按本句纠正拼写。");
            } else if (piece) {
              setError("");
            }
            setPhase("dictate");
            window.setTimeout(() => revealDraftEnd(), 80);
          } catch (err) {
            if (!hasLiveDraft) setError(err instanceof Error ? err.message : "识别失败");
          } finally {
            if (!hasLiveDraft) setBusy(false);
          }
        })();
      };
      recorderRef.current = recorder;
      recorder.start(250);
    } catch (err) {
      stopSpeechRecognition();
      setRecording(false);
      setError(micMessage(err));
    }
  }

  async function startShadow() {
    setError("");
    setScore(null);
    const stream = await ensureStream();
    const recorder = new MediaRecorder(stream);
    shadowChunksRef.current = [];
    recorder.ondataavailable = (event) => {
      if (event.data.size) shadowChunksRef.current.push(event.data);
    };
    recorderRef.current = recorder;
    recorder.start(1000);
    setRecording(true);
    playAt(0);
  }

  async function submitShadow() {
    pauseMedia();
    const recorder = recorderRef.current;
    if (!recorder || recorder.state === "inactive") {
      setError("请先开始完整跟读");
      return;
    }
    setBusy(true);
    const blob = await new Promise<Blob>((resolve) => {
      recorder.onstop = () => {
        resolve(new Blob(shadowChunksRef.current, { type: recorder.mimeType || "audio/webm" }));
      };
      recorder.stop();
      setRecording(false);
    });
    try {
      const result = await scoreShadow(sessionId, blob);
      setScore(result);
      setPhase("result");
    } catch (err) {
      setError(err instanceof Error ? err.message : "打分失败");
    } finally {
      setBusy(false);
      releaseMic();
    }
  }

  async function onPickWord(word: string) {
    const clean = word.replace(/[^A-Za-z'-]/g, "");
    if (!clean) return;
    setHighlights((prev) =>
      prev.some((item) => item.sentenceId === index && item.word.toLowerCase() === clean.toLowerCase())
        ? prev
        : [...prev, { sentenceId: index, word: clean }],
    );
    try {
      setSense(await defineWord(clean));
    } catch (err) {
      setError(err instanceof Error ? err.message : "词典失败");
    }
  }

  function speak(word: string, audio?: string) {
    if (audio) {
      const player = new Audio(audio);
      player.play().catch(() => speakLocal(word));
      return;
    }
    speakLocal(word);
  }

  function speakLocal(word: string) {
    const utter = new SpeechSynthesisUtterance(word);
    utter.lang = "en-US";
    speechSynthesis.cancel();
    speechSynthesis.speak(utter);
  }

  const lamp =
    phase === "listen" ? "take" : phase === "dictate" ? "write" : phase === "check" ? "check" : phase === "shadow" ? "shadow" : "score";
  const marked = new Set(
    highlights.filter((item) => item.sentenceId === index).map((item) => item.word.toLowerCase()),
  );

  return (
    <>
      <header className="topbar">
        <div className="brand">
          <strong>ENPRATO</strong>
          <span>
            {index + 1} / {sentences.length}
          </span>
        </div>
        <div className={`cue-lamp ${phase === "listen" || recording ? "on" : ""}`}>
          <i />
          {lamp}
        </div>
        <button className="ghost" type="button" onClick={onReset}>
          课单
        </button>
      </header>
      <div className="body">
        <section className="video-col">
          <div ref={monitorRef} className={`monitor hide-burn-subs${showAudioOnly ? " audio-only" : ""}`}>
            {showAudioOnly ? (
              <div className="audio-only-panel" aria-hidden="true">
                <strong>仅音频，无画面</strong>
                <p>本课导入时只拉到了声音。可听写、跟读；若要画面请重新导入或上传带画面的文件。</p>
              </div>
            ) : null}
            <div className="monitor-clip">
              <video
                ref={videoRef}
                src={videoUrl}
                playsInline
                preload="auto"
                onError={() => {
                  setError("原片加载失败，请回课单重新打开或重新导入");
                }}
                onLoadedMetadata={() => {
                  const node = videoRef.current;
                  if (!node) return;
                  node.muted = false;
                  node.defaultMuted = false;
                  node.volume = Math.max(0.01, Math.min(1, volume));
                  try {
                    localStorage.removeItem("enprato.audioOut");
                  } catch {
                    /* ignore */
                  }
                  if (Number.isFinite(node.duration) && node.duration > 0) setDuration(node.duration);
                  if (!node.videoWidth) setLocalAudioOnly(true);
                  else if (node.videoWidth) {
                    onOrientation(orientationFromSize(node.videoWidth, node.videoHeight));
                    applyBurnWipeLayout(monitorRef.current, node, orientationFromSize(node.videoWidth, node.videoHeight));
                  }
                  for (const track of Array.from(node.textTracks)) {
                    track.mode = "disabled";
                  }
                  // 拉一帧到句首，避免黑屏
                  try {
                    const t = sentences[indexRef.current]?.start ?? 0.05;
                    node.currentTime = Math.max(0.05, t);
                    setNow(node.currentTime);
                  } catch {
                    /* ignore */
                  }
                }}
                onDurationChange={() => {
                  const node = videoRef.current;
                  if (node && Number.isFinite(node.duration) && node.duration > 0) setDuration(node.duration);
                }}
                onTimeUpdate={onTimeUpdate}
                onEnded={() => {
                  if (phaseRef.current === "shadow" && recording) void submitShadow();
                }}
              />
            </div>
            {/* 挡住片源烧进画面的字幕（独立层，压在 video 合成层之上） */}
            <div className="burn-wipe" aria-hidden="true" />
            {captionMode === "off" || !overlayEnglish ? null : (
              <div className="caption-burn">
                <div className="caption-line">
                  <div>
                    {splitWords(overlayEnglish).map((part, i) =>
                      part.word ? (
                        <button
                          key={`${part.word}-${i}`}
                          type="button"
                          className={`w ${marked.has(part.word.toLowerCase()) ? "mark" : ""}`}
                          onClick={() => onPickWord(part.word)}
                        >
                          {part.raw}
                        </button>
                      ) : (
                        <span key={`s-${i}`}>{part.raw}</span>
                      ),
                    )}
                  </div>
                  {captionMode === "bi" ? <em>{overlayZh || "正在翻译..."}</em> : null}
                </div>
              </div>
            )}
          </div>
          <div className="seek">
            <span>{fmt(now)}</span>
            <input
              type="range"
              min={0}
              max={Math.max(duration || sentences[sentences.length - 1]?.end || 1, 0.01)}
              step={0.05}
              value={Math.min(now, duration || sentences[sentences.length - 1]?.end || now)}
              aria-label="播放进度"
              onPointerDown={() => {
                scrubbingRef.current = true;
                pauseMedia();
              }}
              onChange={(event) => {
                const t = Number(event.target.value);
                setNow(t);
                const video = videoRef.current;
                if (video) video.currentTime = t;
              }}
              onPointerUp={(event) => {
                const t = Number((event.target as HTMLInputElement).value);
                scrubbingRef.current = false;
                commitSeek(t);
              }}
              onPointerCancel={(event) => {
                const t = Number((event.target as HTMLInputElement).value);
                scrubbingRef.current = false;
                commitSeek(t);
              }}
              onKeyUp={(event) => commitSeek(Number((event.target as HTMLInputElement).value))}
            />
            <span>{fmt(duration || sentences[sentences.length - 1]?.end || 0)}</span>
            <label className="vol">
              音量
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={volume}
                aria-label="音量"
                onChange={(event) => {
                  const v = Number(event.target.value);
                  setVolume(v);
                }}
              />
            </label>
          </div>
          <div className="strip">
            {sentences.map((item, i) => (
              <button
                key={item.id}
                type="button"
                className={`beat ${i === index ? "now" : ""} ${(drafts[i] ?? drafts[item.id]) ? "done" : ""}`}
                title={`${i + 1}. ${item.text}`}
                onClick={() => {
                  repeatClickRef.current = { at: 0, baseIndex: i, count: 0 };
                  indexRef.current = i;
                  setIndex(i);
                  pauseAtRef.current = sentences[i].start;
                  setPhase("listen");
                  setCaptionMode("off");
                  playAt(sentences[i].start);
                }}
              >
                {i + 1}
              </button>
            ))}
          </div>
          <div className="transport">
            <button className="ghost" onClick={togglePause} title={userPaused ? "从暂停处继续" : "暂停"}>
              {userPaused ? "继续" : "暂停"}
            </button>
            <button
              onClick={repeat}
              disabled={phase === "shadow" || phase === "result"}
              title="连续点：一下重复本句，两下重复上一句，三下重复上上句"
            >
              重复本句
            </button>
            <button type="button" className={captionMode === "off" ? "ghost" : ""} onClick={cycleCaption}>
              {captionLabel}
            </button>
            {phase === "shadow" || phase === "result" ? (
              <button
                className={recording ? "mic-on" : "primary"}
                onClick={recording ? submitShadow : startShadow}
                disabled={busy}
              >
                {recording ? "停止并打分" : "开始完整跟读"}
              </button>
            ) : (
              <button onClick={phase === "check" ? nextSentence : finishDictation}>
                {phase === "check" ? (index >= sentences.length - 1 ? "去完整跟读" : "下一句") : "听写完成"}
              </button>
            )}
            <div className="transport-meta">
              <span className="meta-time">
                {fmt(sentence?.start ?? 0)}-{fmt(sentence?.end ?? 0)}
              </span>
              <SessionVideoPicker
                history={history}
                sessionId={sessionId}
                disabled={switchingSession || recording || busy}
                onPick={switchSession}
              />
            </div>
          </div>
        </section>
        <aside className="script-col">
          {phase === "result" && score ? (
            <ResultPane score={score} onReset={onReset} />
          ) : phase === "shadow" ? (
            <>
              <h2>完整跟读</h2>
              <p className="hint">用左下角字幕按钮打开英语或双语。按原片语速和语调一起说，结束后提交打分。</p>
              <p className="hint">跟读时请尽量靠近麦克风，环境安静一些。</p>
              {error ? <p className="err">{error}</p> : null}
            </>
          ) : (
            <>
              <div className="draft-head">
                <h2>听写稿</h2>
                {draftCanRestore ? (
                  <button type="button" className="ghost draft-restore-btn" onClick={restoreDraftEdits}>
                    恢复修改前
                  </button>
                ) : null}
              </div>
              <div className="draft-stack">
                <textarea
                  ref={draftTextareaRef}
                  className={`draft${recording ? " draft-listening" : ""}`}
                  value={draftsToText(drafts, index)}
                  placeholder="听写内容会出现在这里"
                  onFocus={(e) => {
                    const el = e.currentTarget;
                    if (!draftCanRestore) {
                      draftBaselineRef.current = snapshotDraftBaseline();
                    }
                    const val = el.value || "";
                    if (val.trim() && el.selectionStart <= 2 && el.selectionEnd <= 2) {
                      window.setTimeout(() => revealDraftEnd(), 0);
                    }
                  }}
                  onBeforeInput={(e) => {
                    const el = e.currentTarget;
                    const native = e.nativeEvent as InputEvent;
                    draftBeforeInputRef.current = {
                      value: el.value,
                      selectionStart: el.selectionStart,
                      selectionEnd: el.selectionEnd,
                      inputType: native.inputType || "",
                      rows: draftTextRows(draftsRef.current, indexRef.current).rows,
                    };
                  }}
                  onChange={(e) => {
                    const before = draftBeforeInputRef.current;
                    draftBeforeInputRef.current = null;
                    if (before && clearSelectedDraftSentences(before, e.target.value)) return;
                    const editedAt = Date.now();
                    draftLocalEditUntilRef.current = editedAt + 8000;
                    const parts = dedupeParagraphs(e.target.value.split(/\n\s*\n/));
                    const end = Math.max(index, parts.length - 1);
                    const next: Record<number, string> = { ...draftsRef.current };
                    for (let i = 0; i <= end; i++) {
                      next[i] = (parts[i] || "").trim();
                      draftManualEditAtRef.current[i] = editedAt;
                    }
                    draftsRef.current = next;
                    setDrafts(next);
                    setDraftCanRestore(JSON.stringify(next) !== draftBaselineRef.current);
                  }}
                  onBlur={() => {
                    if (!sessionId) return;
                    void saveProgress(
                      sessionId,
                      sessionProgressPayload({
                        phase: phaseRef.current,
                        index: indexRef.current,
                        drafts: draftsRef.current,
                        highlights,
                        score,
                        orientation,
                      }),
                    );
                    writeDraftsCache(sessionId, draftsRef.current, indexRef.current);
                  }}
                />
                <div className={`voice-pad${ipadStudio ? " voice-pad-ipad" : ""}`}>
                  {!ipadStudio ? (
                    <>
                      <button
                        type="button"
                        className={recording ? "mic-on primary" : "primary"}
                        onClick={() => void toggleMic()}
                        disabled={busy}
                      >
                        {recording ? "停止语音输入" : busy ? "正在识别..." : "语音输入"}
                      </button>
                      <button
                        type="button"
                        className={phoneMic ? "mic-on" : "ghost"}
                        onClick={() => void togglePhoneMic()}
                        disabled={busy}
                      >
                        {phoneMic ? "关闭手机麦" : "用手机说"}
                      </button>
                    </>
                  ) : null}
                  <button
                    type="button"
                    className={ipadStudio ? "mic-on" : "ghost"}
                    onClick={() => void toggleIpadStudio()}
                    disabled={busy}
                  >
                    {ipadStudio ? "关闭 iPad" : "iPad 播放+听写"}
                  </button>
                  {(phoneMic && phoneLink && !phonePaired) || (ipadStudio && ipadLink) ? (
                    <div className="phone-mic phone-mic-dual">
                      {phoneMic && phoneLink ? (
                        <div className="phone-mic-item">
                          <img
                            alt="手机麦二维码"
                            width={168}
                            height={168}
                            src={`https://api.qrserver.com/v1/create-qr-code/?size=168x168&data=${encodeURIComponent(`${phoneLink}?s=${sessionId}`)}`}
                          />
                          <div>
                            <strong>仅听写（手机麦）</strong>
                            <code>{`${phoneLink}?s=${sessionId}`}</code>
                            <button
                              type="button"
                              className="ghost"
                              onClick={() =>
                                void navigator.clipboard.writeText(`${phoneLink}?s=${sessionId}`)
                              }
                            >
                              复制链接
                            </button>
                          </div>
                        </div>
                      ) : null}
                      {ipadStudio && (ipadHome || ipadLink) ? (
                        <div className="phone-mic-item">
                          <img
                            alt="iPad 固定入口二维码"
                            width={168}
                            height={168}
                            src={`https://api.qrserver.com/v1/create-qr-code/?size=168x168&data=${encodeURIComponent(
                              ipadHome || ipadHomeUrl(ipadLink),
                            )}`}
                          />
                          <div>
                            <strong>iPad 固定入口（只需设置一次）</strong>
                            <code>{ipadHome || ipadHomeUrl(ipadLink)}</code>
                            {ipadBuild ? (
                              <span className="ipad-build-hint">界面版本 {ipadBuild}</span>
                            ) : null}
                            <p className="ipad-build-hint">
                              1. 用 Safari 扫码或打开上面地址（首次点「继续访问」信任证书）
                              <br />
                              2. 点分享 →「添加到主屏幕」
                              <br />
                              3. 以后每天点主屏幕图标即可；电脑点「iPad 播放+听写」后，iPad 会自动跟当前课
                              <br />
                              4. 电脑更新后，iPad 约十几秒内自动刷新到新界面，不用再扫码
                            </p>
                            <button
                              type="button"
                              className="ghost"
                              onClick={() =>
                                void navigator.clipboard.writeText(ipadHome || ipadHomeUrl(ipadLink))
                              }
                            >
                              复制固定地址
                            </button>
                          </div>
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                  {error ? <p className="err">{error}</p> : null}
                </div>
                {sense ? (
                  <div className="card">
                    <strong>{sense.word}</strong>
                    <span className="ipa">{sense.phonetic || ""}</span>
                    <button className="ghost" onClick={() => speak(sense.word, sense.audio)}>
                      发音
                    </button>
                    {sense.defs_zh.map((line) => (
                      <p key={line}>{line}</p>
                    ))}
                    {sense.defs_en.map((line) => (
                      <p key={line}>{line}</p>
                    ))}
                  </div>
                ) : null}
              </div>
            </>
          )}
        </aside>
      </div>
    </>
  );
}

function ResultPane({ score, onReset }: { score: ShadowScore; onReset: () => void }) {
  const rows = [
    ["语调", score.pitch],
    ["语速", score.speed],
    ["节奏", score.rhythm],
    ["内容", score.content],
  ] as const;
  return (
    <>
      <h2>跟读评分</h2>
      <p className="overall">{Math.round(score.overall)}</p>
      <p className="hint">
        原片 {score.orig_duration}s · 你的跟读 {score.user_duration}s
      </p>
      <div className="scores">
        {rows.map(([label, value]) => (
          <div className="bar" key={label}>
            <span>{label}</span>
            <i>
              <b style={{ width: `${Math.max(4, value)}%` }} />
            </i>
            <span>{Math.round(value)}</span>
          </div>
        ))}
      </div>
      <p className="hint">识别到的跟读：{score.transcript || "（空）"}</p>
      <div className="actions">
        <button className="primary" onClick={onReset}>
          返回课单
        </button>
      </div>
    </>
  );
}

function phaseLabel(phase: Phase): string {
  if (phase === "dictate") return "听写中";
  if (phase === "check") return "核对中";
  if (phase === "shadow") return "跟读中";
  if (phase === "result") return "已打分";
  return "进行中";
}

function currentCaption(sentences: Sentence[], time: number): string {
  const hit = sentences.find((item) => time >= item.start && time <= item.end + 0.12);
  return hit?.text ?? sentences[sentences.length - 1]?.text ?? "";
}

function fmt(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
