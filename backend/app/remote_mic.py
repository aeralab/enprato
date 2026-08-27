from __future__ import annotations

import socket
import threading
import time
from typing import Any


_lock = threading.Lock()
_inbox: dict[str, list[dict[str, Any]]] = {}
_seq = 0
_phone_seen: dict[str, float] = {}
_active_remote_session: str | None = None
_active_remote_at: float = 0.0


def get_active_remote() -> str | None:
    with _lock:
        return _active_remote_session


def set_active_remote(session_id: str | None) -> str | None:
    global _active_remote_session, _active_remote_at
    with _lock:
        sid = (session_id or "").strip() or None
        _active_remote_session = sid
        _active_remote_at = time.time() if sid else 0.0
        return _active_remote_session




def lan_ipv4s() -> list[str]:
    found: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in found:
                found.append(ip)
    except Exception:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        ip = probe.getsockname()[0]
        probe.close()
        if ip and not ip.startswith("127.") and ip not in found:
            found.insert(0, ip)
    except Exception:
        pass
    return found


def push_remote_result(session_id: str, index: int, text: str) -> dict[str, Any]:
    global _seq
    with _lock:
        _seq += 1
        item = {"id": _seq, "index": int(index), "text": text, "session_id": session_id}
        bucket = _inbox.setdefault(session_id, [])
        bucket.append(item)
        if len(bucket) > 40:
            del bucket[:-40]
        return item


def pull_remote_results(session_id: str, after_id: int = 0) -> list[dict[str, Any]]:
    with _lock:
        bucket = _inbox.get(session_id, [])
        return [item for item in bucket if int(item["id"]) > after_id]


def touch_phone(session_id: str) -> None:
    with _lock:
        _phone_seen[session_id] = time.time()


def phone_connected(session_id: str, within: float = 10.0) -> bool:
    with _lock:
        seen = _phone_seen.get(session_id, 0.0)
    return seen > 0 and (time.time() - seen) < within


_REMOTE_BTN_SPEAK = "\u8bf4\u8bdd"
_REMOTE_BTN_STOP = "\u505c\u6b62"

REMOTE_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>手机麦 · Enprato</title>
  <style>
    * { box-sizing: border-box; }
    html, body {
      margin: 0;
      height: 100%;
      overflow: hidden;
    }
    body {
      position: fixed;
      inset: 0;
      width: 100%;
      min-height: 100dvh;
      padding: 16px 16px calc(96px + env(safe-area-inset-bottom, 0px));
      font-family: system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
      background: #e4ecf7; color: #333;
      display: flex; flex-direction: column; gap: 10px;
    }
    .top { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .meta { color: #888; font-size: 0.85rem; }
    .next {
      border: 0; background: transparent; color: #111; font-size: 0.9rem;
      font-weight: 600; padding: 6px 0; cursor: pointer; white-space: nowrap;
    }
    textarea {
      flex: 1; width: 100%; min-height: 40vh; border: 0; outline: none;
      font-size: 1.1rem; line-height: 1.7; resize: none; font-family: inherit;
      padding: 0 56px 0 0;
      overflow-y: auto;
      -webkit-overflow-scrolling: touch;
    }
    .status { font-size: 0.85rem; color: #888; min-height: 1.2em; }
    .ok { color: #059669; }
    .err { color: #b91c1c; }
    .warn {
      display: none; background: #fff7ed; color: #9a3412;
      padding: 10px; border-radius: 8px; font-size: 0.88rem;
    }
    /* 右侧悬浮说话键：默认贴右下；键盘弹出时用 transform 上移（iPad 比改 bottom 稳） */
    #btn {
      position: fixed;
      right: 14px;
      bottom: 18px;
      top: auto;
      left: auto;
      z-index: 1000;
      min-width: 64px; height: 64px; padding: 0 14px;
      border: 0; border-radius: 999px;
      background: #e6a23c; color: #1a1408;
      font-size: 1rem; font-weight: 700;
      line-height: 1.1;
      font-family: "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
      box-shadow: 0 6px 20px rgba(0,0,0,0.22);
      cursor: pointer;
      -webkit-touch-callout: none;
      touch-action: manipulation;
      will-change: transform;
    }
    #btn.rec { background: #dc2626; }
    #btn:disabled { opacity: 0.5; }
  </style>
</head>
<body>
  <div id="insecure" class="warn">请用 HTTPS 打开（扫描电脑二维码）</div>
  <div class="top">
    <div id="meta" class="meta">第 1 句 / 共 0 句</div>
    <button id="nextBtn" class="next" type="button">下一句</button>
  </div>
  <textarea id="doc" placeholder="听写内容会显示在这里，一句一行"></textarea>
  <div id="status" class="status"></div>
  <button id="btn" type="button" aria-label="__REMOTE_BTN_SPEAK__" data-speak="__REMOTE_BTN_SPEAK__" data-stop="__REMOTE_BTN_STOP__">__REMOTE_BTN_SPEAK__</button>
  <script>
    let sessionId = new URLSearchParams(location.search).get('s') || '';
    let switchingSession = false;
    const metaEl = document.getElementById('meta');
    const docEl = document.getElementById('doc');
    const btn = document.getElementById('btn');
    const nextBtn = document.getElementById('nextBtn');
    const statusEl = document.getElementById('status');

    function setBtnLabel(rec) {
      btn.textContent = rec ? btn.dataset.stop : btn.dataset.speak;
      if (rec) btn.classList.add('rec');
      else btn.classList.remove('rec');
    }
    setBtnLabel(false);

    let index = 0, total = 0, drafts = {}, recording = false;
    let mediaRecorder = null, chunks = [], stream = null;
    let saveTimer = 0, lastSent = '';
    let caretStart = 0, caretEnd = 0;

    if (location.protocol !== 'https:' && location.hostname !== 'localhost' && location.hostname !== '127.0.0.1') {
      document.getElementById('insecure').style.display = 'block';
    }

    function rememberCaret() {
      if (document.activeElement === docEl) {
        caretStart = docEl.selectionStart;
        caretEnd = docEl.selectionEnd;
      }
    }
    docEl.addEventListener('keyup', rememberCaret);
    docEl.addEventListener('mouseup', rememberCaret);
    docEl.addEventListener('select', rememberCaret);
    docEl.addEventListener('touchend', rememberCaret);
    btn.addEventListener('pointerdown', rememberCaret);

    let baseViewportH = window.innerHeight;
    window.addEventListener('resize', () => {
      // 无焦点时更新基准高度，避免把键盘高度当成窗口高度
      if (document.activeElement !== docEl) baseViewportH = window.innerHeight;
    });

    function placeMic() {
      const btnH = btn.offsetHeight || 64;
      // 相对键盘顶边，至少再上移 2 个「说话」球的高度
      const clear = btnH * 2;
      const gap = 18;
      const editing = document.activeElement === docEl;
      const vv = window.visualViewport;

      // 固定用 bottom 锚定，不用改 top（避免越算越往下）
      btn.style.top = 'auto';
      btn.style.bottom = gap + 'px';
      btn.style.right = '14px';

      let keyboard = 0;
      if (vv) {
        keyboard = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
        // 用「打开页时的高度 - 当前可视高度」估算键盘（iPad 上更稳）
        keyboard = Math.max(keyboard, baseViewportH - vv.height - (vv.offsetTop || 0));
        if (editing) {
          const layoutH = Math.max(baseViewportH, window.innerHeight, document.documentElement.clientHeight || 0);
          keyboard = Math.max(keyboard, layoutH - vv.height);
        }
      } else if (editing) {
        keyboard = Math.max(0, baseViewportH - window.innerHeight);
      }

      let shift = 0;
      if (editing || keyboard > 40) {
        // 先抬过键盘，再额外抬两个球
        shift = Math.max(0, keyboard) + clear;
      }
      btn.style.transform = shift > 0 ? ('translate3d(0,' + (-shift) + 'px,0)') : 'none';
    }
    function schedulePlaceMic() {
      if (schedulePlaceMic._pending) return;
      schedulePlaceMic._pending = true;
      const run = () => {
        schedulePlaceMic._pending = false;
        placeMic();
      };
      requestAnimationFrame(run);
      [60, 180, 360].forEach((ms) => setTimeout(run, ms));
    }
    placeMic();
    window.addEventListener('orientationchange', schedulePlaceMic);
    window.addEventListener('resize', schedulePlaceMic);
    window.addEventListener('focusin', schedulePlaceMic);
    window.addEventListener('focusout', schedulePlaceMic);
    docEl.addEventListener('focus', schedulePlaceMic);
    docEl.addEventListener('blur', schedulePlaceMic);
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', schedulePlaceMic);
    }

    function setStatus(msg, cls) {
      statusEl.className = 'status' + (cls ? ' ' + cls : '');
      statusEl.textContent = msg || '';
    }

    function localKey() {
      return 'enprato.remoteDrafts.' + (sessionId || 'none');
    }

    function persistLocal() {
      if (!sessionId) return;
      try {
        const snapshot = { drafts: { ...drafts }, index, total, savedAt: Date.now() };
        localStorage.setItem(localKey(), JSON.stringify(snapshot));
        const historyKey = localKey() + '.history';
        let history = [];
        try { history = JSON.parse(localStorage.getItem(historyKey) || '[]'); } catch (e) {}
        if (!Array.isArray(history)) history = [];
        const nonEmpty = Object.values(snapshot.drafts).some((value) => String(value || '').trim());
        if (nonEmpty) {
          history.push(snapshot);
          localStorage.setItem(historyKey, JSON.stringify(history.slice(-30)));
        }
      } catch (e) {}
    }

    function restoreLocal() {
      if (!sessionId) return;
      let changed = false;
      try {
        const raw = localStorage.getItem(localKey());
        if (!raw) return false;
        const data = JSON.parse(raw);
        const incoming = data && data.drafts ? data.drafts : {};
        for (const [k, v] of Object.entries(incoming)) {
          const cur = draftAt(Number(k));
          const next = String(v || '');
          if (next.trim() && next.trim().length >= cur.trim().length) {
            drafts[String(k)] = next;
            changed = true;
          }
        }
        if (typeof data.index === 'number') index = Math.max(index, data.index);
      } catch (e) {}
      return changed;
    }

    function draftAt(i) {
      return String(drafts[String(i)] || drafts[i] || '');
    }

    function toDoc() {
      const lines = [];
      let end = index;
      for (const k of Object.keys(drafts)) {
        const i = Number(k);
        if (Number.isFinite(i)) end = Math.max(end, i);
      }
      end = Math.min(Math.max(end, 0), Math.max(total - 1, 0));
      // 姣忓彞涓€娈碉紝绌哄彞涔熷崰浣嶏紝閬垮厤鍒锋柊鍚庢钀介敊浣嶄涪鍐呭
      for (let i = 0; i <= end; i++) lines.push(draftAt(i));
      return lines.join('\\n\\n');
    }

    function fromDoc(text) {
      const parts = String(text || '').split(/\\n\\s*\\n/);
      let end = Math.max(index, parts.length - 1, 0);
      end = Math.min(end, Math.max(total - 1, 0));
      for (let i = 0; i <= end; i++) {
        drafts[String(i)] = (parts[i] || '').trim();
      }
      persistLocal();
    }

    function render(keepCaret) {
      const next = toDoc();
      if (keepCaret && document.activeElement === docEl) {
        if (docEl.value === next) return;
      }
      docEl.value = next;
      lastSent = toDoc();
      revealLatest();
    }

    function latestFocusPos() {
      const val = docEl.value || '';
      let pos = Math.max(0, Math.min(caretStart || 0, val.length));
      // 光标落在大片空白里时，改盯「最后有字的位置」
      const trimmedEnd = val.search(/\\s+$/);
      const lastTyped = trimmedEnd < 0 ? val.length : trimmedEnd;
      if (!val.trim()) return 0;
      if (pos <= 0 || pos > lastTyped + 2) pos = lastTyped;
      return Math.max(0, Math.min(pos, val.length));
    }

    function contentEndPos() {
      const val = docEl.value || '';
      if (!val.trim()) return 0;
      const trimmedEnd = val.search(/\\s+$/);
      return trimmedEnd < 0 ? val.length : trimmedEnd;
    }

    function revealLatest() {
      const pos = Math.max(latestFocusPos(), contentEndPos());
      const apply = () => {
        const el = docEl;
        const style = window.getComputedStyle(el);
        let lh = parseFloat(style.lineHeight);
        if (!Number.isFinite(lh) || lh < 8) {
          lh = (parseFloat(style.fontSize) || 17) * 1.7;
        }
        const before = (el.value || '').slice(0, pos);
        const line = Math.max(1, before.split('\\n').length);
        const endLine = Math.max(1, (el.value || '').slice(0, contentEndPos()).split('\\n').length);
        const maxScroll = Math.max(0, el.scrollHeight - el.clientHeight);
        const target = line * lh - el.clientHeight * 0.35;
        const minShow = endLine * lh - el.clientHeight * 0.7;
        el.scrollTop = Math.min(maxScroll, Math.max(0, target, minShow));
      };
      apply();
      requestAnimationFrame(apply);
      [80, 240, 500].forEach((ms) => setTimeout(apply, ms));
      setTimeout(schedulePlaceMic, 120);
    }

    async function followActiveSession() {
      if (switchingSession || recording) return false;
      try {
        const res = await fetch('/api/remote-active');
        if (!res.ok) return false;
        const data = await res.json();
        const active = String(data.session_id || '');
        if (!active) {
          if (!sessionId) {
            metaEl.textContent = '等待电脑端打开手机麦';
            btn.disabled = true;
          }
          return false;
        }
        if (active === sessionId) return false;
        switchingSession = true;
        try {
          // 鍏堟妸涓婁竴璇惧惉鍐欑钀界洏锛屽啀鍒囧埌鐢佃剳褰撳墠璇?
          if (sessionId) {
            try { await saveAll(); } catch (e) {}
          }
          sessionId = active;
          drafts = {};
          index = 0;
          total = 0;
          lastSent = '';
          caretStart = 0;
          caretEnd = 0;
          const url = new URL(location.href);
          url.searchParams.set('s', sessionId);
          history.replaceState(null, '', url.pathname + url.search);
          btn.disabled = false;
          setStatus('已切换到电脑当前句（上一句内容已保存）', 'ok');
          return true;
        } finally {
          switchingSession = false;
        }
      } catch (e) {
        return false;
      }
    }

    async function syncState() {
      const switched = await followActiveSession();
      if (!sessionId) {
        metaEl.textContent = '等待电脑端打开手机麦';
        btn.disabled = true;
        return;
      }
      try {
        const res = await fetch('/api/session/' + sessionId + '/remote-state');
        if (!res.ok) throw new Error('fail');
        const data = await res.json();
        index = data.index;
        total = data.total || 0;
        const localRestored = restoreLocal();
        const incoming = data.drafts || {};
        // 鍚堝苟锛氭湇鍔″櫒鏈夌殑鍐欏叆锛涙湰鍦版洿闀跨殑宸插啓鍐呭涓嶄涪
        for (const [k, v] of Object.entries(incoming)) {
          const cur = draftAt(Number(k));
          const next = String(v || '');
          if (!cur.trim() || next.trim().length >= cur.trim().length) {
            drafts[String(k)] = next;
          }
        }
        if (data.draft != null && !draftAt(index).trim()) {
          drafts[String(index)] = data.draft;
        }
        persistLocal();
        if (localRestored) setTimeout(() => { void saveAll(); }, 0);
        if (switched) {
          docEl.value = toDoc();
          lastSent = toDoc();
        }
        metaEl.textContent = '第 ' + (index + 1) + ' 句 / 共 ' + total + ' 句';
        if (document.activeElement !== docEl || switched) render(false);
      } catch (e) {
        setStatus('同步失败，请检查电脑端连接', 'err');
      }
    }

    async function saveAll() {
      fromDoc(docEl.value);
      const payload = {};
      let end = index;
      for (const k of Object.keys(drafts)) {
        const i = Number(k);
        if (Number.isFinite(i)) end = Math.max(end, i);
      }
      for (let i = 0; i <= end; i++) payload[String(i)] = draftAt(i);
      const snap = JSON.stringify(payload);
      if (snap === lastSent) return;
      try {
        const res = await fetch('/api/session/' + sessionId + '/remote-drafts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ drafts: payload, index }),
        });
        if (!res.ok) throw new Error('fail');
        lastSent = snap;
        persistLocal();
        setStatus('已保存', 'ok');
      } catch (e) {
        setStatus('保存失败，请重试', 'err');
      }
    }

    docEl.addEventListener('input', () => {
      fromDoc(docEl.value);
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => { void saveAll(); }, 400);
    });
    docEl.addEventListener('blur', () => { void saveAll(); });
    setInterval(() => {
      if (sessionId && !recording) void saveAll();
    }, 3000);
    document.addEventListener('visibilitychange', () => {
      fromDoc(docEl.value);
      persistLocal();
      if (document.visibilityState === 'hidden') void saveAll();
    });
    window.addEventListener('pagehide', () => {
      fromDoc(docEl.value);
      persistLocal();
      if (!sessionId) return;
      const payload = {};
      let end = index;
      for (const k of Object.keys(drafts)) {
        const i = Number(k);
        if (Number.isFinite(i)) end = Math.max(end, i);
      }
      for (let i = 0; i <= end; i++) payload[String(i)] = draftAt(i);
      try {
        const body = JSON.stringify({ drafts: payload, index });
        navigator.sendBeacon('/api/session/' + sessionId + '/remote-drafts', new Blob([body], { type: 'application/json' }));
      } catch (e) {}
    });

    let audioCtx = null;
    let audioSource = null;
    let audioProc = null;
    let audioSink = null;
    let pcmChunks = [];
    let recStartedAt = 0;
    let peakLevel = 0;
    let recMime = '';

    function encodeWav(floatChunks, sampleRate) {
      let len = 0;
      for (const c of floatChunks) len += c.length;
      const pcm = new Int16Array(len);
      let off = 0;
      for (const c of floatChunks) {
        for (let i = 0; i < c.length; i++) {
          const x = Math.max(-1, Math.min(1, c[i]));
          pcm[off++] = x < 0 ? x * 0x8000 : x * 0x7fff;
        }
      }
      const bytes = pcm.length * 2;
      const buf = new ArrayBuffer(44 + bytes);
      const view = new DataView(buf);
      const writeStr = (o, s) => { for (let i = 0; i < s.length; i++) view.setUint8(o + i, s.charCodeAt(i)); };
      writeStr(0, 'RIFF');
      view.setUint32(4, 36 + bytes, true);
      writeStr(8, 'WAVE');
      writeStr(12, 'fmt ');
      view.setUint32(16, 16, true);
      view.setUint16(20, 1, true);
      view.setUint16(22, 1, true);
      view.setUint32(24, sampleRate, true);
      view.setUint32(28, sampleRate * 2, true);
      view.setUint16(32, 2, true);
      view.setUint16(34, 16, true);
      writeStr(36, 'data');
      view.setUint32(40, bytes, true);
      new Uint8Array(buf, 44).set(new Uint8Array(pcm.buffer));
      return new Blob([buf], { type: 'audio/wav' });
    }

    async function ensureStream() {
      if (stream && stream.getAudioTracks().some((t) => t.readyState === 'live' && t.enabled)) {
        return stream;
      }
      try { stream?.getTracks().forEach((t) => t.stop()); } catch (e) {}
      stream = null;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
            channelCount: 1
          }
        });
      } catch (e1) {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      }
      const track = stream.getAudioTracks()[0];
      if (track) {
        try { track.enabled = true; } catch (e) {}
      }
      return stream;
    }

    function stopAudioGraph() {
      try { audioProc && (audioProc.onaudioprocess = null); } catch (e) {}
      try { audioProc && audioProc.disconnect(); } catch (e) {}
      try { audioSource && audioSource.disconnect(); } catch (e) {}
      try { audioSink && audioSink.disconnect(); } catch (e) {}
      audioProc = null;
      audioSource = null;
      audioSink = null;
      if (audioCtx) {
        try { audioCtx.close(); } catch (e) {}
        audioCtx = null;
      }
    }

    function pickRecorderMime() {
      const cands = [
        'audio/mp4',
        'audio/aac',
        'audio/webm;codecs=opus',
        'audio/webm',
        'audio/ogg;codecs=opus'
      ];
      if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) return '';
      for (const m of cands) {
        try { if (MediaRecorder.isTypeSupported(m)) return m; } catch (e) {}
      }
      return '';
    }

    async function startRec() {
      setStatus('正在准备麦克风…');
      const s = await ensureStream();
      const track = s.getAudioTracks()[0];
      if (!track || track.readyState !== 'live') {
        stream = null;
        throw new Error('麦克风未就绪，请允许权限后重试');
      }
      stopAudioGraph();
      if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        try { mediaRecorder.stop(); } catch (e) {}
      }
      mediaRecorder = null;
      chunks = [];
      pcmChunks = [];
      peakLevel = 0;
      recMime = '';

      const AC = window.AudioContext || window.webkitAudioContext;
      audioCtx = new AC();
      if (audioCtx.state === 'suspended') await audioCtx.resume();
      audioSource = audioCtx.createMediaStreamSource(s);
      // 涓嶈鐢?gain=0 鎺ュ埌鎵０鍣細閮ㄥ垎 iOS 浼氫紭鍖栨帀鏁存潯鍥撅紝onaudioprocess 姘镐笉瑙﹀彂
      audioSink = audioCtx.createMediaStreamDestination();
      const bufferSize = 4096;
      audioProc = audioCtx.createScriptProcessor(bufferSize, 1, 1);
      audioProc.onaudioprocess = (ev) => {
        const input = ev.inputBuffer.getChannelData(0);
        const copy = new Float32Array(input.length);
        copy.set(input);
        pcmChunks.push(copy);
        let peak = 0;
        for (let i = 0; i < input.length; i++) {
          const a = Math.abs(input[i]);
          if (a > peak) peak = a;
        }
        if (peak > peakLevel) peakLevel = peak;
      };
      audioSource.connect(audioProc);
      audioProc.connect(audioSink);

      // MediaRecorder 浣滃厹搴曪紙iOS 甯哥敤 audio/mp4锛?
      recMime = pickRecorderMime();
      try {
        mediaRecorder = recMime
          ? new MediaRecorder(s, { mimeType: recMime, audioBitsPerSecond: 128000 })
          : new MediaRecorder(s);
        mediaRecorder.ondataavailable = (ev) => {
          if (ev.data && ev.data.size > 0) chunks.push(ev.data);
        };
        mediaRecorder.start(250);
      } catch (e) {
        mediaRecorder = null;
      }

      recStartedAt = Date.now();
      recording = true;
      setBtnLabel(true);
    }

    function stopRec() {
      const elapsed = Date.now() - recStartedAt;
      const rate = (audioCtx && audioCtx.sampleRate) || 44100;
      const pcmCopy = pcmChunks.slice();
      const peak = peakLevel;
      const mr = mediaRecorder;
      recording = false;
      setBtnLabel(false);
      try { docEl.blur(); } catch (e) {}
      revealLatest();

      const finish = () => {
        stopAudioGraph();
        mediaRecorder = null;
        const mrChunks = chunks.slice();
        chunks = [];
        void uploadAudio(pcmCopy, rate, elapsed, peak, mrChunks, recMime);
      };

      if (mr && mr.state !== 'inactive') {
        try {
          let done = false;
          const once = () => { if (done) return; done = true; finish(); };
          mr.onstop = once;
          mr.stop();
          setTimeout(once, 900);
          return;
        } catch (e) {}
      }
      finish();
    }

    function insertAtCaret(piece) {
      const val = docEl.value;
      const s = Math.max(0, Math.min(caretStart, val.length));
      const e = Math.max(s, Math.min(caretEnd, val.length));
      let left = val.slice(0, s);
      let right = val.slice(e);
      let mid = String(piece || '').trim();
      if (!mid) return;
      if (left && !/\\s$/.test(left)) left += ' ';
      if (right && !/^\\s/.test(right)) mid += ' ';
      docEl.value = left + mid + right;
      fromDoc(docEl.value);
      const pos = left.length + mid.length;
      caretStart = pos;
      caretEnd = pos;
      // 不强制拉起键盘，避免 iPad 视口被顶到空白区
      try { docEl.blur(); } catch (e) {}
      try { docEl.setSelectionRange(pos, pos); } catch (e) {}
      const before = docEl.value.slice(0, pos);
      const parts = before.split(/\\n\\s*\\n/);
      index = Math.max(0, Math.min(parts.length - 1, Math.max(total - 1, 0)));
      metaEl.textContent = '第 ' + (index + 1) + ' 句 / 共 ' + total + ' 句';
      revealLatest();
    }

    async function uploadAudio(floatChunks, sampleRate, elapsedMs, peak, mrChunks, mime) {
      if ((elapsedMs || 0) < 500) {
        setStatus('录音太短，请说完后再点停止', 'err');
        return;
      }
      let blob = null;
      let filename = 'phone.wav';
      const pcmSamples = floatChunks.reduce((n, c) => n + c.length, 0);
      if (pcmSamples > 800) {
        blob = encodeWav(floatChunks, sampleRate);
        filename = 'phone.wav';
      } else if (mrChunks && mrChunks.length) {
        const type = (mime || mrChunks[0].type || 'audio/mp4').split(';')[0];
        blob = new Blob(mrChunks, { type: type || 'audio/mp4' });
        filename = type.indexOf('mp4') >= 0 || type.indexOf('aac') >= 0
          ? 'phone.m4a'
          : (type.indexOf('ogg') >= 0 ? 'phone.ogg' : 'phone.webm');
      }
      if (!blob || blob.size < 200) {
        setStatus('没有录到声音，请检查 HTTPS 和麦克风权限后重试', 'err');
        return;
      }
      // 浠呬綔鎻愮ず锛氭瀬灏忓０浠嶄笂浼狅紝閬垮厤璇潃
      if (peak > 0 && peak < 0.0008 && pcmSamples > 800) {
        setStatus('声音偏小，仍在识别…');
      } else {
        setStatus('正在识别…');
      }
      btn.disabled = true;
      const insertStart = caretStart;
      const insertEnd = caretEnd;
      try {
        await saveAll();
        const body = new FormData();
        body.append('audio', blob, filename);
        body.append('index', String(index));
        body.append('mode', 'insert');
        const res = await fetch('/api/session/' + sessionId + '/remote-stt', { method: 'POST', body });
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        if (data.text) {
          caretStart = insertStart;
          caretEnd = insertEnd;
          insertAtCaret(data.text);
          lastSent = '';
          await saveAll();
          setStatus('已保存', 'ok');
          revealLatest();
        } else {
          setStatus('识别为空，请靠近麦克风再说一遍', 'err');
          revealLatest();
        }
      } catch (e) {
        setStatus('识别失败：' + (e && e.message ? e.message : e), 'err');
        revealLatest();
      } finally {
        btn.disabled = false;
        schedulePlaceMic();
        revealLatest();
      }
    }

    btn.addEventListener('click', async () => {
      try {
        if (recording) stopRec();
        else await startRec();
      } catch (e) {
        setStatus((e && e.message) || '麦克风启动失败，请确认 HTTPS 和权限', 'err');
      }
    });

    nextBtn.addEventListener('click', async () => {
      if (recording) return;
      await saveAll();
      try {
        const res = await fetch('/api/session/' + sessionId + '/remote-next', { method: 'POST' });
        if (!res.ok) throw new Error('fail');
        const data = await res.json();
        index = data.index;
        if (!drafts[String(index)]) drafts[String(index)] = '';
        await syncState();
        render(false);
        setStatus('第 ' + (index + 1) + ' 句', 'ok');
      } catch (e) {
        setStatus('无法切换到下一句', 'err');
      }
    });

    syncState();
    setInterval(syncState, 2500);
    if (sessionId) {
      restoreLocal();
      render(false);
    }
  </script>
</body>
</html>
""".replace("__REMOTE_BTN_SPEAK__", _REMOTE_BTN_SPEAK).replace(
    "__REMOTE_BTN_STOP__", _REMOTE_BTN_STOP
)
