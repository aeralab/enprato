from __future__ import annotations

_IPAD_BTN_SPEAK = "\u8bf4\u8bdd"
_IPAD_BTN_STOP = "\u505c\u6b62"
# 改 iPad 页后递增；固定入口 /ipad 会跳到最新版，页内也会自动检测并刷新
IPAD_BUILD = "20260902a"

IPAD_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate" />
  <meta http-equiv="Pragma" content="no-cache" />
  <meta http-equiv="Expires" content="0" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-status-bar-style" content="black" />
  <meta name="apple-mobile-web-app-title" content="Enprato" />
  <meta name="mobile-web-app-capable" content="yes" />
  <meta name="theme-color" content="#000000" />
  <link rel="apple-touch-icon" sizes="180x180" href="/icon/enprato-180.png?v=__IPAD_BUILD__" />
  <link rel="icon" type="image/png" sizes="192x192" href="/icon/enprato-192.png?v=__IPAD_BUILD__" />
  <link rel="icon" type="image/png" sizes="512x512" href="/icon/enprato-512.png?v=__IPAD_BUILD__" />
  <title>Enprato</title>
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
      padding: 0;
      font-family: system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
      background: #e4ecf7;
      color: #333;
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
    .warn {
      display: none;
      background: #fff7ed;
      color: #9a3412;
      padding: 8px 10px;
      border-radius: 8px;
      font-size: 0.85rem;
      margin: 8px 10px 0;
      flex-shrink: 0;
    }
    .layout {
      flex: 1;
      display: grid;
      grid-template-columns: 1.12fr 0.88fr;
      min-height: 0;
      height: 100%;
      overflow: hidden;
    }
    .play-col {
      display: flex;
      flex-direction: column;
      min-width: 0;
      min-height: 0;
      background: #fff;
      border-right: 1px solid #e8edf4;
      padding: 10px 12px;
      gap: 8px;
    }
    .video-wrap {
      flex: 1;
      min-height: 0;
      position: relative;
      background: #000;
      border-radius: 10px;
      overflow: hidden;
      isolation: isolate;
    }
    .video-clip {
      position: absolute;
      inset: 0;
      overflow: hidden;
      z-index: 0;
    }
    .video-wrap.hide-burn-subs .video-clip video {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      object-fit: contain;
      object-position: center center;
    }
    #video {
      width: 100%;
      height: 100%;
      max-height: none;
      background: #000;
      object-fit: contain;
      display: block;
    }
    .burn-wipe {
      position: absolute;
      left: 0;
      right: 0;
      bottom: 0;
      height: 36%;
      z-index: 5;
      pointer-events: none;
      background: #000;
    }
    .video-wrap.burn-layout-ready .burn-wipe {
      left: var(--burn-left, 0);
      right: auto;
      bottom: auto;
      top: var(--burn-top, auto);
      width: var(--burn-width, 100%);
      height: var(--burn-height, 36%);
    }
    .caption-burn {
      position: absolute;
      left: 0;
      right: 0;
      bottom: 3%;
      z-index: 6;
      display: none;
      align-items: flex-end;
      justify-content: center;
      padding: 0 10px 8px;
      pointer-events: none;
    }
    .video-wrap.burn-layout-ready .caption-burn {
      left: var(--burn-left, 0);
      right: auto;
      width: var(--burn-width, 100%);
      bottom: var(--caption-bottom, 3%);
    }
    .caption-burn.show { display: flex; }
    .caption-line {
      max-width: 94%;
      font-size: clamp(14px, 2.4vw, 22px);
      font-weight: 600;
      line-height: 1.35;
      color: #fff;
      text-align: center;
      text-shadow: 0 1px 2px #000, 0 0 12px #000;
      background: rgba(0, 0, 0, 0.38);
      padding: 6px 10px;
    }
    .caption-line em {
      display: block;
      margin-top: 4px;
      font-style: normal;
      font-size: 0.72em;
      font-weight: 500;
      color: #f2d39a;
    }
    .write-col {
      display: flex;
      flex-direction: column;
      min-width: 0;
      min-height: 0;
      background: #fff;
      padding: 10px 12px calc(10px + env(safe-area-inset-bottom, 0px));
      gap: 6px;
      position: relative;
    }
    .controls {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      flex-shrink: 0;
    }
    .session-row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      align-items: center;
      flex-shrink: 0;
    }
    #sessionPicker {
      width: 100%;
      min-width: 0;
      height: 38px;
      border: 1px solid #d1d5db;
      border-radius: 8px;
      background: #fff;
      color: #1f2937;
      padding: 0 10px;
      font-size: 14px;
    }
    #refreshSessionsBtn {
      height: 38px;
      padding: 0 12px;
      border: 1px solid #d1d5db;
      border-radius: 8px;
      background: #fff;
      color: #374151;
      font-size: 14px;
      font-weight: 650;
    }
    .seek {
      display: grid;
      grid-template-columns: 3.2em 1fr 3.2em;
      gap: 8px;
      align-items: center;
      font-size: 12px;
      font-variant-numeric: tabular-nums;
      color: #6b7280;
      flex-shrink: 0;
    }
    .seek input[type="range"] {
      width: 100%;
      height: 18px;
      margin: 0;
      accent-color: #e6a23c;
      cursor: pointer;
    }
    .strip {
      display: flex;
      gap: 6px;
      overflow-x: auto;
      overflow-y: hidden;
      padding: 4px 0 6px;
      scroll-snap-type: x proximity;
      -webkit-overflow-scrolling: touch;
      flex-shrink: 0;
    }
    .beat {
      flex: 0 0 auto;
      min-width: 44px;
      height: 44px;
      padding: 0 8px;
      border: 1px solid #d1d5db;
      border-radius: 8px;
      background: #fff;
      color: #6b7280;
      font-size: 14px;
      font-weight: 650;
      line-height: 1;
      scroll-snap-align: start;
      cursor: pointer;
      touch-action: manipulation;
      -webkit-tap-highlight-color: transparent;
      user-select: none;
    }
    .beat.done {
      background: #eef4fc;
      border-color: #b8ccec;
      color: #1e4a8a;
    }
    .beat.now {
      background: #fdf3e0;
      border-color: #e6a23c;
      color: #7a4a08;
    }
    .controls button {
      border: 0;
      border-radius: 999px;
      padding: 8px 14px;
      font-size: 0.9rem;
      font-weight: 600;
      background: #fff;
      color: #1f2937;
      border: 1px solid #dddddd;
      cursor: pointer;
      touch-action: manipulation;
    }
    .controls button.primary {
      background: #e6a23c;
      color: #1a1408;
      border-color: #e6a23c;
    }
    .meta {
      color: #666;
      font-size: 0.85rem;
      min-height: 1.2em;
      flex-shrink: 0;
    }
    .doc-stack {
      flex: 1;
      min-height: 0;
      display: flex;
      flex-direction: column;
      gap: 8px;
      overflow: hidden;
    }
    .doc-tail {
      flex: 1;
      min-height: 0;
      overflow-y: auto;
      -webkit-overflow-scrolling: touch;
      padding: 2px 64px 6px 0;
    }
    .doc-tail-empty {
      margin: 0;
      color: #9ca3af;
      font-size: 0.95rem;
      line-height: 1.5;
    }
    .doc-tail-item {
      display: flex;
      align-items: flex-start;
      gap: 8px;
      margin: 0 0 10px;
      font-size: 0.98rem;
      line-height: 1.55;
      color: #374151;
    }
    .doc-tail-no {
      flex: 0 0 auto;
      min-width: 28px;
      height: 28px;
      padding: 0 4px;
      border: 1px solid #d1d5db;
      border-radius: 6px;
      background: #fff;
      color: #6b7280;
      font-size: 12px;
      font-weight: 650;
      line-height: 26px;
      text-align: center;
      cursor: pointer;
      touch-action: manipulation;
      -webkit-tap-highlight-color: transparent;
      user-select: none;
    }
    .doc-tail-item.doc-tail-done .doc-tail-no {
      background: #eef4fc;
      border-color: #b8ccec;
      color: #1e4a8a;
    }
    .doc-tail-item.doc-tail-now .doc-tail-no {
      background: #fdf3e0;
      border-color: #e6a23c;
      color: #7a4a08;
    }
    .doc-tail-text {
      flex: 1;
      min-width: 0;
      padding-top: 3px;
    }
    .doc-tail-item.doc-tail-empty-line .doc-tail-text {
      color: #9ca3af;
      font-style: italic;
    }
    .doc-tail-item.doc-tail-now .doc-tail-text {
      color: #111;
      font-weight: 550;
      font-style: normal;
    }
    .doc-current-row {
      flex: 0 0 auto;
      display: flex;
      align-items: flex-start;
      gap: 8px;
      max-height: 36%;
      min-height: 5.2em;
    }
    .doc-current-no {
      flex: 0 0 auto;
      min-width: 36px;
      height: 36px;
      margin-top: 2px;
      padding: 0 6px;
      border: 1px solid #e6a23c;
      border-radius: 8px;
      background: #fdf3e0;
      color: #7a4a08;
      font-size: 14px;
      font-weight: 700;
      line-height: 34px;
      text-align: center;
      user-select: none;
    }
    .doc-current-main {
      flex: 1;
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: 4px;
      min-height: 0;
    }
    .doc-current-label {
      flex-shrink: 0;
      font-size: 0.78rem;
      font-weight: 650;
      color: #9ca3af;
      letter-spacing: 0.02em;
    }
    textarea.doc-area {
      flex: 1;
      width: 100%;
      min-height: 4.2em;
      border: 0;
      outline: none;
      font-size: 1.05rem;
      line-height: 1.7;
      resize: none;
      font-family: inherit;
      padding: 0 56px 4px 0;
      overflow-y: auto;
      -webkit-overflow-scrolling: touch;
      background: transparent;
      display: block;
      box-sizing: border-box;
    }
    .status {
      font-size: 0.85rem;
      color: #888;
      min-height: 1.2em;
      flex-shrink: 0;
      padding-right: 72px;
    }
    .doc-tools {
      flex-shrink: 0;
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      min-height: 0;
    }
    .restore-btn {
      border: 1px solid #d1d5db;
      background: #fff;
      color: #374151;
      border-radius: 999px;
      padding: 6px 12px;
      font-size: 0.85rem;
      font-weight: 600;
      cursor: pointer;
      touch-action: manipulation;
    }
    .restore-btn[hidden] { display: none; }
    .ok { color: #059669; }
    .err { color: #b91c1c; }
    #btn {
      position: absolute;
      right: 12px;
      bottom: calc(12px + env(safe-area-inset-bottom, 0px));
      top: auto;
      left: auto;
      z-index: 10;
      min-width: 64px;
      height: 64px;
      padding: 0 14px;
      border: 0;
      border-radius: 999px;
      background: #e6a23c;
      color: #1a1408;
      font-size: 1rem;
      font-weight: 700;
      line-height: 1.1;
      font-family: "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
      box-shadow: 0 6px 20px rgba(0,0,0,0.22);
      cursor: pointer;
      -webkit-touch-callout: none;
      touch-action: manipulation;
      will-change: transform;
    }
    #btn.rec { background: #dc2626; color: #fff; }
    #btn:disabled { opacity: 0.5; }
    .page-ver {
      position: fixed;
      left: 8px;
      bottom: calc(6px + env(safe-area-inset-bottom, 0px));
      z-index: 5;
      font-size: 10px;
      color: #9ca3af;
      pointer-events: none;
      user-select: none;
    }
    @media (orientation: portrait), (max-width: 820px) {
      .layout {
        grid-template-columns: 1fr;
        grid-template-rows: minmax(34vh, 42vh) 1fr;
      }
      .play-col {
        border-right: 0;
        border-bottom: 1px solid #e8edf4;
      }
    }
  </style>
</head>
<body>
  <div id="insecure" class="warn">请用 HTTPS 打开。首次在 Safari 打开固定地址后，点分享 → 添加到主屏幕，以后每天点图标即可。</div>
  <div class="layout">
    <div class="play-col">
      <div id="videoWrap" class="video-wrap hide-burn-subs">
        <div class="video-clip">
          <video id="video" playsinline webkit-playsinline preload="none"></video>
        </div>
        <div class="burn-wipe" aria-hidden="true"></div>
        <div id="captionBurn" class="caption-burn">
          <div class="caption-line">
            <div id="captionEn"></div>
            <em id="captionZh"></em>
          </div>
        </div>
      </div>
      <div class="seek">
        <span id="seekNow">0:00</span>
        <input id="seekBar" type="range" min="0" max="1" step="0.05" value="0" aria-label="播放进度" />
        <span id="seekDur">0:00</span>
      </div>
      <div id="strip" class="strip"></div>
      <div class="controls">
        <button id="resumeBtn" class="primary" type="button">继续</button>
        <button id="repeatBtn" type="button" title="连续点：一下重复本句，两下重复上一句，三下重复上上句">重复本句</button>
        <button id="captionBtn" type="button">字幕：关</button>
        <button id="nextBtn" type="button">下一句</button>
      </div>
      <div id="meta" class="meta">第 1 句 / 共 0 句</div>
      <div class="session-row">
        <select id="sessionPicker" aria-label="选择视频">
          <option value="">选择视频</option>
        </select>
        <button id="refreshSessionsBtn" type="button">刷新</button>
      </div>
    </div>
    <div class="write-col">
      <div class="doc-stack">
        <div id="docTail" class="doc-tail" aria-live="polite"></div>
        <div class="doc-current-row">
          <div id="docCurrentNo" class="doc-current-no" aria-hidden="true">1</div>
          <div class="doc-current-main">
            <div id="docCurrentLabel" class="doc-current-label">当前句 · 与下方进度条编号一致</div>
            <textarea id="doc" class="doc-area" placeholder="在这里听写当前句"></textarea>
          </div>
        </div>
      </div>
      <div class="doc-tools">
        <button id="restoreBtn" type="button" class="restore-btn" hidden>恢复修改前</button>
      </div>
      <div id="status" class="status"></div>
      <button id="btn" type="button" aria-label="__IPAD_BTN_SPEAK__" data-speak="__IPAD_BTN_SPEAK__" data-stop="__IPAD_BTN_STOP__">__IPAD_BTN_SPEAK__</button>
    </div>
  </div>
  <div id="pageVer" class="page-ver" aria-hidden="true">__IPAD_BUILD__</div>
  <script>
    let sessionId = new URLSearchParams(location.search).get('s') || localStorage.getItem('enprato.ipad.lastSession') || '';
    const PAGE_BUILD = '__IPAD_BUILD__';
    (function ensurePageBuild() {
      const docProbe = document.getElementById('docTail');
      const hasNewShell = !!docProbe;
      const parts = String(location.pathname || '').split('/').filter(Boolean);
      const pathBuild = parts.length >= 2 && parts[0] === 'ipad' ? parts[1] : '';
      const p = new URLSearchParams(location.search);
      if (hasNewShell && pathBuild === PAGE_BUILD && p.get('b') === PAGE_BUILD) return;
      const u = new URL(location.origin + '/ipad/' + PAGE_BUILD);
      if (sessionId) u.searchParams.set('s', sessionId);
      u.searchParams.set('b', PAGE_BUILD);
      u.searchParams.set('_', String(Date.now()));
      location.replace(u.href);
    })();
    // 电脑更新后自动跟新：轮询 health，版本变了就刷新到最新 /ipad
    setInterval(async () => {
      try {
        const res = await fetch('/api/health?_=' + Date.now(), { cache: 'no-store' });
        if (!res.ok) return;
        const data = await res.json();
        const remote = String(data.ipad_build || '');
        if (remote && remote !== PAGE_BUILD) {
          const u = new URL(location.origin + '/ipad');
          if (sessionId) u.searchParams.set('s', sessionId);
          u.searchParams.set('_', String(Date.now()));
          location.replace(u.href);
        }
      } catch (e) {}
    }, 12000);
    let switchingSession = false;
    const videoEl = document.getElementById('video');
    const videoWrapEl = document.getElementById('videoWrap');
    const metaEl = document.getElementById('meta');
    const docEl = document.getElementById('doc');
    const docTailEl = document.getElementById('docTail');
    const docCurrentLabelEl = document.getElementById('docCurrentLabel');
    const docCurrentNoEl = document.getElementById('docCurrentNo');
    const btn = document.getElementById('btn');
    const resumeBtn = document.getElementById('resumeBtn');
    const repeatBtn = document.getElementById('repeatBtn');
    const seekBar = document.getElementById('seekBar');
    const seekNowEl = document.getElementById('seekNow');
    const seekDurEl = document.getElementById('seekDur');
    const stripEl = document.getElementById('strip');
    const captionBtn = document.getElementById('captionBtn');
    const nextBtn = document.getElementById('nextBtn');
    const captionBurnEl = document.getElementById('captionBurn');
    const captionEnEl = document.getElementById('captionEn');
    const captionZhEl = document.getElementById('captionZh');
    const statusEl = document.getElementById('status');
    const restoreBtn = document.getElementById('restoreBtn');
    const sessionPicker = document.getElementById('sessionPicker');
    const refreshSessionsBtn = document.getElementById('refreshSessionsBtn');

    function setBtnLabel(rec) {
      btn.textContent = rec ? btn.dataset.stop : btn.dataset.speak;
      if (rec) btn.classList.add('rec');
      else btn.classList.remove('rec');
    }
    setBtnLabel(false);

    let index = 0, total = 0, drafts = {}, sentences = [];
    let recording = false, userPaused = true, pauseAt = 0, segmentEnd = null;
    let now = 0, duration = 0, scrubbing = false;
    let repeatClick = { at: 0, baseIndex: 0, count: 0 };
    let captionMode = 'off';
    const zhMap = {};
    let mediaRecorder = null, chunks = [], stream = null;
    let saveTimer = 0, lastSent = '';
    let sttBusy = false;
    let caretStart = 0, caretEnd = 0;
    let editBaseline = null;
    let localIndexControl = false;
    let draftLocalRev = {};
    let draftSavedRev = {};

    videoEl.muted = false;
    videoEl.defaultMuted = false;
    videoEl.volume = 1;
    videoEl.playsInline = true;
    videoEl.setAttribute('playsinline', '');
    videoEl.setAttribute('webkit-playsinline', '');

    function fmt(seconds) {
      const s = Number(seconds) || 0;
      const m = Math.floor(s / 60);
      const sec = Math.floor(s % 60);
      return m + ':' + String(sec).padStart(2, '0');
    }

    function wordTokens(input) {
      return String(input || '').toLowerCase().replace(/[^a-z'\s]/g, ' ').split(/\s+/).filter(Boolean);
    }

    function coveredPrefixCount(target, draft) {
      const a = wordTokens(target);
      const b = wordTokens(draft);
      if (!a.length || !b.length) return 0;
      let i = 0, j = 0;
      while (i < a.length && j < b.length) {
        const tw = a[i], dw = b[j];
        if (tw === dw || tw.startsWith(dw) || dw.startsWith(tw)) {
          i += 1;
          j += 1;
          continue;
        }
        if (j + 1 < b.length && (a[i] === b[j + 1] || a[i].startsWith(b[j + 1]))) {
          j += 1;
          continue;
        }
        break;
      }
      return i;
    }

    function resumeTimeInSentence(sentence, draft, pausedAt) {
      const words = wordTokens(sentence.text);
      const covered = coveredPrefixCount(sentence.text, draft);
      const dur = Math.max(0.05, sentence.end - sentence.start);
      let t = sentence.start;
      if (words.length >= 2 && covered > 0 && covered < words.length) {
        t = sentence.start + (covered / words.length) * dur - 0.4;
      } else if (
        pausedAt != null &&
        pausedAt > sentence.start + 0.45 &&
        pausedAt < sentence.end - 0.25
      ) {
        t = pausedAt - 0.2;
      }
      return Math.max(sentence.start, Math.min(t, sentence.end - 0.45));
    }

    function updateSeekUi() {
      const dur = duration || (sentences[sentences.length - 1]?.end || 0);
      const t = Math.min(now, dur || now);
      seekNowEl.textContent = fmt(t);
      seekDurEl.textContent = fmt(dur);
      if (!scrubbing && seekBar) {
        seekBar.max = String(Math.max(dur, 0.01));
        seekBar.value = String(t);
      }
    }

    function renderStrip() {
      if (!stripEl) return;
      stripEl.innerHTML = '';
      sentences.forEach((item, i) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'beat' + (i === index ? ' now' : '') + (draftAt(i).trim() ? ' done' : '');
        btn.textContent = String(i + 1);
        btn.title = (i + 1) + '. ' + (item.text || '');
        btn.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          void pickSentence(i);
        });
        stripEl.appendChild(btn);
      });
      const nowBtn = stripEl.querySelector('.beat.now');
      if (nowBtn) nowBtn.scrollIntoView({ block: 'nearest', inline: 'center' });
    }

    function sentenceIndexAt(time) {
      let best = 0;
      for (let i = 0; i < sentences.length; i++) {
        if (sentences[i].start <= time) best = i;
        else break;
      }
      return best;
    }

    function commitSeek(time) {
      localIndexControl = true;
      repeatClick = { at: 0, baseIndex: index, count: 0 };
      const t = clipTime(time);
      const i = sentenceIndexAt(t);
      if (i !== index) captionsOffForNewSentence();
      index = i;
      pauseAt = t;
      now = t;
      metaEl.textContent = '第 ' + (index + 1) + ' 句 / 共 ' + total + ' 句';
      refreshCaption();
      renderStrip();
      docEl.value = draftAt(i);
      renderDocTail();
      updateCurrentSentenceLabel();
      segmentEnd = null;
      userPaused = false;
      playAt(t);
      updateSeekUi();
      void saveAll();
    }

    function overlayEnglish() {
      const cur = sentences[index];
      return String(cur?.text || '');
    }

    function captionLabel() {
      return captionMode === 'off' ? '字幕：关' : captionMode === 'en' ? '字幕：英语' : '字幕：双语';
    }

    function refreshCaption() {
      captionBtn.textContent = captionLabel();
      captionBtn.classList.toggle('primary', captionMode !== 'off');
      const text = overlayEnglish();
      if (captionMode === 'off' || !text.trim()) {
        captionBurnEl.classList.remove('show');
        captionEnEl.textContent = '';
        captionZhEl.textContent = '';
        return;
      }
      captionBurnEl.classList.add('show');
      captionEnEl.textContent = text;
      if (captionMode === 'bi') {
        captionZhEl.style.display = 'block';
        if (zhMap[text]) {
          captionZhEl.textContent = zhMap[text];
        } else {
          captionZhEl.textContent = '正在翻译…';
          fetch('/api/translate?text=' + encodeURIComponent(text))
            .then((r) => (r.ok ? r.json() : Promise.reject()))
            .then((d) => {
              zhMap[text] = String(d.zh || '');
              if (overlayEnglish() === text) captionZhEl.textContent = zhMap[text] || '';
            })
            .catch(() => {
              if (overlayEnglish() === text) captionZhEl.textContent = '';
            });
        }
      } else {
        captionZhEl.style.display = 'none';
        captionZhEl.textContent = '';
      }
    }

    function cycleCaption() {
      captionMode = captionMode === 'off' ? 'en' : captionMode === 'en' ? 'bi' : 'off';
      refreshCaption();
    }

    function captionsOffForNewSentence() {
      if (captionMode === 'off') return;
      captionMode = 'off';
      refreshCaption();
    }

    function burnWipeRatio() {
      const portrait = window.matchMedia('(orientation: portrait), (max-width: 820px)').matches;
      return portrait ? 0.44 : 0.42;
    }

    function syncBurnWipeLayout() {
      if (!videoWrapEl || !videoEl || !videoEl.videoWidth || !videoEl.videoHeight) return;
      const cw = videoWrapEl.clientWidth;
      const ch = videoWrapEl.clientHeight;
      const vw = videoEl.videoWidth;
      const vh = videoEl.videoHeight;
      if (!cw || !ch) return;
      const containerAR = cw / ch;
      const videoAR = vw / vh;
      let left = 0;
      let top = 0;
      let boxW = cw;
      let boxH = ch;
      if (videoAR > containerAR) {
        boxW = cw;
        boxH = cw / videoAR;
        top = (ch - boxH) / 2;
      } else {
        boxH = ch;
        boxW = ch * videoAR;
        left = (cw - boxW) / 2;
      }
      const wipeH = boxH * burnWipeRatio();
      const wipeTop = top + boxH - wipeH;
      const captionBottom = Math.max(8, ch - top - boxH * 0.97);
      videoWrapEl.style.setProperty('--burn-left', left + 'px');
      videoWrapEl.style.setProperty('--burn-top', wipeTop + 'px');
      videoWrapEl.style.setProperty('--burn-width', boxW + 'px');
      videoWrapEl.style.setProperty('--burn-height', wipeH + 'px');
      videoWrapEl.style.setProperty('--caption-bottom', captionBottom + 'px');
      videoWrapEl.classList.add('burn-layout-ready');
    }

    captionBtn.addEventListener('click', cycleCaption);

    videoEl.addEventListener('loadedmetadata', () => {
      try {
        const tracks = videoEl.textTracks;
        if (tracks) {
          for (let i = 0; i < tracks.length; i++) tracks[i].mode = 'disabled';
        }
      } catch (e) {}
      syncBurnWipeLayout();
    });
    window.addEventListener('resize', syncBurnWipeLayout);

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
      if (document.activeElement !== docEl) baseViewportH = window.innerHeight;
    });

    function placeMic() {
      const btnH = btn.offsetHeight || 64;
      const clear = btnH * 2;
      const gap = 18;
      const editing = document.activeElement === docEl;
      const vv = window.visualViewport;

      btn.style.top = 'auto';
      btn.style.bottom = gap + 'px';
      btn.style.right = '14px';

      let keyboard = 0;
      if (vv) {
        keyboard = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
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
      return 'enprato.ipadDrafts.' + (sessionId || 'none');
    }

    function persistLocal() {
      if (!sessionId) return;
      try {
        const snapshot = { drafts: { ...drafts }, index, total, savedAt: Date.now() };
        localStorage.setItem(localKey(), JSON.stringify(snapshot));
      } catch (e) {}
    }

    function collapseIdenticalDrafts() {
      const keys = Object.keys(drafts).map(Number).filter(Number.isFinite).sort((a, b) => a - b);
      const seen = new Set();
      for (const k of keys) {
        const text = draftAt(k).trim();
        if (!text) continue;
        if (seen.has(text)) drafts[String(k)] = '';
        else seen.add(text);
      }
    }

    function bumpDraftLocal(i) {
      const key = String(i);
      draftLocalRev[key] = (draftLocalRev[key] || 0) + 1;
    }

    function markDraftsSaved() {
      for (const k of Object.keys(drafts)) {
        draftSavedRev[k] = draftLocalRev[k] || 0;
      }
      draftSavedRev[String(index)] = draftLocalRev[String(index)] || 0;
    }

    function resetDraftRevs() {
      draftLocalRev = {};
      draftSavedRev = {};
    }

    function hasUnsavedDraftEdit(i) {
      const key = String(i);
      return (draftLocalRev[key] || 0) > (draftSavedRev[key] || 0);
    }

    function mergeDraftText(cur, incoming, idx) {
      const c = String(cur ?? '');
      const n = String(incoming ?? '');
      if (idx != null && hasUnsavedDraftEdit(idx)) return c;
      const ct = c.trim();
      const nt = n.trim();
      if (!nt) return c;
      if (!ct) return n;
      return nt.length >= ct.length ? n : c;
    }

    function restoreLocal() {
      if (!sessionId) return false;
      let changed = false;
      try {
        const raw = localStorage.getItem(localKey());
        if (!raw) return false;
        const data = JSON.parse(raw);
        const incoming = data && data.drafts ? data.drafts : {};
        for (const [k, v] of Object.entries(incoming)) {
          const i = Number(k);
          if (!Number.isFinite(i)) continue;
          const merged = mergeDraftText(draftAt(i), v, i);
          if (merged !== draftAt(i)) {
            drafts[String(i)] = merged;
            changed = true;
          }
        }
        if (typeof data.index === 'number' && !localIndexControl) {
          index = data.index;
        }
      } catch (e) {}
      return changed;
    }

    async function pickSentence(i) {
      if (recording) return;
      if (!sentences[i]) return;
      localIndexControl = true;
      repeatClick = { at: 0, baseIndex: i, count: 0 };
      index = i;
      pauseAt = sentences[i].start;
      captionsOffForNewSentence();
      metaEl.textContent = '第 ' + (index + 1) + ' 句 / 共 ' + total + ' 句';
      refreshCaption();
      renderStrip();
      docEl.value = draftAt(i);
      renderDocTail();
      updateCurrentSentenceLabel();
      segmentEnd = null;
      userPaused = false;
      playAt(sentences[i].start);
      persistLocal();
      lastSent = '';
      try {
        await saveAll();
      } catch (e) {}
    }

    function draftAt(i) {
      return String(drafts[String(i)] || drafts[i] || '');
    }

    function escapeHtml(text) {
      return String(text || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
    }

    function syncCurrentDraft() {
      drafts[String(index)] = String(docEl.value || '');
      bumpDraftLocal(index);
      persistLocal();
    }

    function updateCurrentSentenceLabel() {
      const n = index + 1;
      if (docCurrentNoEl) docCurrentNoEl.textContent = String(n);
      if (docCurrentLabelEl) {
        docCurrentLabelEl.textContent = '当前句 · 第 ' + n + ' 句（与进度条编号一致）';
      }
    }

    function recentDraftEntries(limit) {
      const maxItems = limit || 8;
      let end = index;
      for (const k of Object.keys(drafts)) {
        const i = Number(k);
        if (Number.isFinite(i) && draftAt(i).trim()) end = Math.max(end, i);
      }
      end = Math.min(Math.max(end, 0), Math.max(total - 1, 0));
      const start = Math.max(0, end - maxItems + 1);
      const rows = [];
      for (let i = start; i <= end; i++) {
        let t = draftAt(i).trim();
        if (i === index && docEl) t = String(docEl.value || '').trim() || t;
        rows.push({ i, t });
      }
      return rows;
    }

    function renderDocTail() {
      if (!docTailEl) return;
      const entries = recentDraftEntries(8);
      if (!entries.length) {
        docTailEl.innerHTML = '<p class="doc-tail-empty">听写后会按进度条编号显示在这里</p>';
        return;
      }
      docTailEl.innerHTML = entries.map(({ i, t }) => {
        const n = i + 1;
        const isNow = i === index;
        const done = !!t;
        const cls = 'doc-tail-item'
          + (isNow ? ' doc-tail-now' : '')
          + (done ? ' doc-tail-done' : ' doc-tail-empty-line');
        const text = done ? escapeHtml(t) : (isNow ? '正在听写…' : '（未听写）');
        return '<div class="' + cls + '" data-i="' + i + '">'
          + '<button type="button" class="doc-tail-no" data-i="' + i + '" title="跳到第 ' + n + ' 句">' + n + '</button>'
          + '<span class="doc-tail-text">' + text + '</span></div>';
      }).join('');
      docTailEl.querySelectorAll('.doc-tail-no').forEach((btn) => {
        btn.addEventListener('click', (e) => {
          e.preventDefault();
          const i = Number(btn.getAttribute('data-i'));
          if (Number.isFinite(i)) void pickSentence(i);
        });
      });
      const scrollTail = () => {
        docTailEl.scrollTop = docTailEl.scrollHeight;
      };
      scrollTail();
      requestAnimationFrame(scrollTail);
    }

    function renderDocView(keepCaret) {
      updateCurrentSentenceLabel();
      if (!(keepCaret && document.activeElement === docEl)) {
        docEl.value = draftAt(index);
      }
      renderDocTail();
      if (document.activeElement !== docEl) resetEditBaseline();
      else updateRestoreBtn();
    }

    function snapshotDrafts() {
      return { drafts: { ...drafts }, index, current: String(docEl.value || '') };
    }

    function snapKey(snap) {
      return JSON.stringify({ drafts: snap.drafts, index: snap.index, current: snap.current });
    }

    function resetEditBaseline() {
      editBaseline = snapshotDrafts();
      updateRestoreBtn();
    }

    function updateRestoreBtn() {
      if (!restoreBtn) return;
      if (!editBaseline) {
        restoreBtn.hidden = true;
        return;
      }
      const dirty = snapKey(snapshotDrafts()) !== snapKey(editBaseline);
      restoreBtn.hidden = !dirty;
    }

    async function restoreDraftEdits() {
      if (!editBaseline) return;
      if (recording) return;
      drafts = { ...editBaseline.drafts };
      index = editBaseline.index;
      docEl.value = editBaseline.current || draftAt(index);
      caretStart = docEl.value.length;
      caretEnd = caretStart;
      lastSent = '';
      persistLocal();
      renderDocTail();
      try {
        await saveAll();
        resetEditBaseline();
        setStatus('已恢复修改前的听写稿', 'ok');
      } catch (e) {
        updateRestoreBtn();
        setStatus('已恢复本地听写稿，同步保存失败', 'err');
      }
    }

    function markDictationContentChanged() {
      renderDocTail();
    }

    function contentEndPos() {
      return String(docEl.value || '').length;
    }

    function revealLatest() {
      renderDocTail();
      const endPos = contentEndPos();
      caretStart = endPos;
      caretEnd = endPos;
      if (document.activeElement === docEl) {
        try { docEl.setSelectionRange(endPos, endPos); } catch (e) {}
      }
      schedulePlaceMic();
    }

    let boundVideoSession = '';

    function clearVideoSrc(reason) {
      try {
        videoEl.removeAttribute('src');
        videoEl.load();
      } catch (e) {}
      boundVideoSession = '';
      if (reason) setStatus(reason, 'err');
    }

    function updateVideoSrc() {
      if (!sessionId || !videoEl) return;
      if (boundVideoSession === sessionId && videoEl.getAttribute('src')) return;
      boundVideoSession = sessionId;
      // 延后到下一帧再挂视频，避免首屏就拉超大 mp4 把 Safari 进程打崩
      const sid = sessionId;
      requestAnimationFrame(() => {
        if (sessionId !== sid) return;
        try {
          videoEl.src = '/api/session/' + sid + '/video';
          videoEl.load();
        } catch (e) {
          clearVideoSrc('视频加载失败，请在电脑端换一门课再试');
        }
      });
    }

    videoEl.addEventListener('error', () => {
      if (!sessionId) return;
      clearVideoSrc('这门课的视频 iPad 暂时播不了（文件过大或格式不兼容）。请在下方换一门课，或电脑端重新导入。');
    });

    function clipTime(time) {
      const cap = (videoEl && Number.isFinite(videoEl.duration) && videoEl.duration > 0)
        ? videoEl.duration
        : (sentences[sentences.length - 1]?.end || time);
      return Math.max(0, Math.min(time, cap));
    }

    function isPlaybackActive() {
      return !userPaused && !videoEl.paused;
    }

    function updateTransportBtn() {
      if (!resumeBtn) return;
      resumeBtn.textContent = isPlaybackActive() ? '暂停' : '继续';
      resumeBtn.title = isPlaybackActive()
        ? '暂停，先听写这一段'
        : '从暂停处继续播放';
    }

    function playAt(time, endOverride) {
      if (!sessionId) return;
      const current = sentences[index];
      const end = endOverride ?? current?.end ?? time + 3;
      segmentEnd = endOverride ?? null;
      userPaused = false;
      videoEl.muted = false;
      videoEl.defaultMuted = false;
      videoEl.volume = 1;
      const t = clipTime(time);
      try { videoEl.currentTime = t; } catch (e) {}
      pauseAt = t;
      now = t;
      updateSeekUi();
      updateTransportBtn();
      const playPromise = videoEl.play();
      if (playPromise) {
        void playPromise.then(() => { updateTransportBtn(); }).catch((err) => {
          userPaused = true;
          updateTransportBtn();
          const name = err && err.name ? err.name : '';
          if (name === 'NotAllowedError') {
            setStatus('浏览器拦住了播放，请再点一次「重复本句」或「继续」。', 'err');
          } else if (err && err.message) {
            setStatus('播放失败：' + err.message, 'err');
          }
        });
      }
    }

    function playCurrent() {
      const current = sentences[index];
      if (!current) return;
      segmentEnd = null;
      playAt(current.start);
    }

    function pausePlayback(at) {
      const t = Number.isFinite(at) ? at : videoEl.currentTime;
      segmentEnd = null;
      pauseAt = t;
      now = t;
      updateSeekUi();
      try { videoEl.pause(); } catch (e) {}
      userPaused = true;
      updateTransportBtn();
    }

    function onTimeUpdate() {
      if (!scrubbing) {
        const live = videoEl.currentTime;
        if (Number.isFinite(live)) {
          if (!userPaused) {
            now = live;
          } else if (live > 0.05 && live + 0.3 >= now) {
            now = live;
          } else if (live > now) {
            now = live;
          }
        }
        updateSeekUi();
      }
      const current = sentences[index];
      if (!current || userPaused || recording) return;
      const stopAt = segmentEnd ?? current.end;
      if (videoEl.currentTime >= stopAt - 0.05) {
        try { videoEl.pause(); } catch (e) {}
        videoEl.currentTime = stopAt;
        pauseAt = stopAt;
        now = stopAt;
        updateSeekUi();
        userPaused = true;
        segmentEnd = null;
        updateTransportBtn();
        moveCaretToContentEnd();
      }
    }

    function moveCaretToContentEnd() {
      markDictationContentChanged();
      const pos = contentEndPos();
      caretStart = pos;
      caretEnd = pos;
      revealLatest();
    }
    videoEl.addEventListener('timeupdate', onTimeUpdate);
    videoEl.addEventListener('loadedmetadata', () => {
      if (Number.isFinite(videoEl.duration) && videoEl.duration > 0) {
        duration = videoEl.duration;
        updateSeekUi();
      }
    });
    videoEl.addEventListener('durationchange', () => {
      if (Number.isFinite(videoEl.duration) && videoEl.duration > 0) {
        duration = videoEl.duration;
        updateSeekUi();
      }
    });

    if (seekBar) {
      seekBar.addEventListener('pointerdown', () => {
        scrubbing = true;
        pausePlayback(videoEl.currentTime);
      });
      seekBar.addEventListener('input', () => {
        const t = Number(seekBar.value);
        now = t;
        seekNowEl.textContent = fmt(t);
        try { videoEl.currentTime = t; } catch (e) {}
      });
      seekBar.addEventListener('pointerup', () => {
        scrubbing = false;
        commitSeek(Number(seekBar.value));
      });
      seekBar.addEventListener('pointercancel', () => {
        scrubbing = false;
        commitSeek(Number(seekBar.value));
      });
    }

    function shouldAdvanceFromPause(current, t) {
      if (!current) return false;
      const dur = Math.max(0.05, current.end - current.start);
      const tail = Math.min(0.12, dur * 0.35);
      return t >= current.end - tail;
    }

    function resumePlayback() {
      const current = sentences[index];
      const videoT = videoEl.currentTime;
      let t = pauseAt;
      if (!Number.isFinite(t) || t <= 0) t = videoT;
      t = Math.max(t, videoT);
      segmentEnd = null;
      if (current && shouldAdvanceFromPause(current, t)) {
        const next = index + 1;
        if (next < sentences.length) {
          localIndexControl = true;
          index = next;
          pauseAt = sentences[next].start;
          captionsOffForNewSentence();
          metaEl.textContent = '第 ' + (index + 1) + ' 句 / 共 ' + total + ' 句';
          refreshCaption();
          renderStrip();
          docEl.value = draftAt(next);
          renderDocTail();
          updateCurrentSentenceLabel();
          playAt(sentences[next].start);
          persistLocal();
          void saveAll();
          return;
        }
        pausePlayback(t);
        return;
      }
      playAt(t + 0.03);
    }

    function sessionOptionTitle(item) {
      const title = String(item && item.title || item && item.source_url || item && item.session_id || '').trim();
      return title.length > 48 ? title.slice(0, 46) + '…' : (title || '未命名视频');
    }

    function updateSessionPickerSelection() {
      if (!sessionPicker) return;
      sessionPicker.value = sessionId || '';
    }

    async function loadSessionOptions() {
      if (!sessionPicker) return;
      try {
        const res = await fetch('/api/sessions');
        if (!res.ok) return;
        const data = await res.json();
        const rows = Array.isArray(data.sessions) ? data.sessions : [];
        sessionPicker.innerHTML = '';
        const empty = document.createElement('option');
        empty.value = '';
        empty.textContent = rows.length ? '选择视频' : '暂无历史视频';
        sessionPicker.appendChild(empty);
        rows.forEach((item) => {
          const opt = document.createElement('option');
          opt.value = String(item.session_id || '');
          const done = Number(item.done || 0);
          const count = Number(item.count || 0);
          opt.textContent = sessionOptionTitle(item) + (count ? ' · ' + done + '/' + count : '');
          sessionPicker.appendChild(opt);
        });
        updateSessionPickerSelection();
      } catch (e) {}
    }

    async function switchToSession(nextId, message, claimActive) {
      const next = String(nextId || '').trim();
      if (!next || next === sessionId || switchingSession || recording) return false;
      switchingSession = true;
      try {
        if (sessionId) {
          try { await saveAll(); } catch (e) {}
        }
        if (claimActive) {
          try {
            await fetch('/api/remote-claim', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ session_id: next }),
            });
          } catch (e) {}
        }
        sessionId = next;
        try { localStorage.setItem('enprato.ipad.lastSession', sessionId); } catch (e) {}
        boundVideoSession = '';
        localIndexControl = false;
        drafts = {};
        resetDraftRevs();
        index = 0;
        total = 0;
        sentences = [];
        lastSent = '';
        caretStart = 0;
        caretEnd = 0;
        userPaused = true;
        pauseAt = 0;
        segmentEnd = null;
        captionMode = 'off';
        updateTransportBtn();
        const url = new URL(location.href);
        url.searchParams.set('s', sessionId);
        history.replaceState(null, '', url.pathname + url.search);
        updateSessionPickerSelection();
        btn.disabled = false;
        updateVideoSrc();
        await syncState();
        setStatus(message || '已切换视频', 'ok');
        return true;
      } finally {
        switchingSession = false;
      }
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
            metaEl.textContent = '等待电脑端打开 iPad 听写';
            btn.disabled = true;
          }
          return false;
        }
        if (active === sessionId) return false;
        return await switchToSession(active, '已切换到电脑当前课（上一课内容已保存）', false);
      } catch (e) {
        return false;
      }
    }

    async function syncState() {
      const switched = await followActiveSession();
      if (!sessionId) {
        metaEl.textContent = '等待电脑端打开 iPad 听写';
        btn.disabled = true;
        return;
      }
      try {
        const res = await fetch('/api/session/' + sessionId + '/remote-state');
        if (!res.ok) throw new Error('fail');
        const data = await res.json();
        const prevIndex = index;
        total = data.total || 0;
        if (Array.isArray(data.sentences) && data.sentences.length) {
          sentences = data.sentences;
        }
        const incoming = data.drafts || {};
        const typing = document.activeElement === docEl;
        const serverChars = Object.values(incoming).reduce((n, v) => n + String(v || '').trim().length, 0);
        const localCharsBefore = draftCharCount();
        if (!typing) {
          const keys = new Set(Object.keys(drafts).concat(Object.keys(incoming)));
          const nextDrafts = {};
          for (const k of keys) {
            nextDrafts[k] = mergeDraftText(drafts[k], incoming[k], Number(k));
          }
          drafts = nextDrafts;
          restoreLocal();
          collapseIdenticalDrafts();
          persistLocal();
          if (localCharsBefore > serverChars + 20) {
            void saveAll();
          }
        } else {
          for (const [k, v] of Object.entries(incoming)) {
            const merged = mergeDraftText(draftAt(Number(k)), v, Number(k));
            if (merged !== draftAt(Number(k))) drafts[String(k)] = merged;
          }
        }
        if (data.draft != null && !draftAt(index).trim() && !hasUnsavedDraftEdit(index)) {
          drafts[String(index)] = data.draft;
        }
        if (typeof data.index === 'number' && !localIndexControl && !recording) {
          index = data.index;
        }
        if (!typing) {
          markSavedSnap();
          if (!hasUnsavedDraftEdit(index)) markDraftsSaved();
          renderDocView(false);
        } else if (hasUnsavedDraftEdit(index)) {
          renderDocTail();
        } else if (switched) {
          renderDocView(false);
        } else {
          renderDocTail();
        }
        metaEl.textContent = '第 ' + (index + 1) + ' 句 / 共 ' + total + ' 句';
        refreshCaption();
        renderStrip();
        updateSeekUi();
        updateVideoSrc();
        if (prevIndex !== index) {
          repeatClick = { at: 0, baseIndex: index, count: 0 };
          captionsOffForNewSentence();
        }
        if (!typing) resetEditBaseline();
      } catch (e) {
        // 上次会话失效时清掉自动恢复，避免反复请求把页面拖崩
        if (sessionId) {
          try { localStorage.removeItem('enprato.ipad.lastSession'); } catch (err) {}
          clearVideoSrc('');
        }
        setStatus('同步失败，请检查电脑端连接，或从下方重新选课', 'err');
      }
    }

    function buildPayload() {
      const payload = {};
      let end = index;
      for (const k of Object.keys(drafts)) {
        const i = Number(k);
        if (Number.isFinite(i)) end = Math.max(end, i);
      }
      for (let i = 0; i <= end; i++) payload[String(i)] = draftAt(i);
      return payload;
    }

    function markSavedSnap() {
      lastSent = JSON.stringify({ drafts: buildPayload(), index });
    }

    function draftCharCount() {
      let n = 0;
      for (const k of Object.keys(drafts)) {
        const i = Number(k);
        if (Number.isFinite(i)) n += draftAt(i).trim().length;
      }
      return n;
    }

    async function absorbServerDraftsIfRicher() {
      if (document.activeElement === docEl) return false;
      try {
        const res = await fetch('/api/session/' + sessionId + '/remote-state');
        if (!res.ok) return false;
        const data = await res.json();
        const incoming = data.drafts || {};
        let serverChars = 0;
        for (const v of Object.values(incoming)) serverChars += String(v || '').trim().length;
        const localChars = draftCharCount();
        if (serverChars <= localChars + 20) return false;
        const keys = new Set(Object.keys(drafts).concat(Object.keys(incoming)));
        const nextDrafts = {};
        for (const k of keys) {
          nextDrafts[k] = mergeDraftText(drafts[k], incoming[k], Number(k));
        }
        drafts = nextDrafts;
        collapseIdenticalDrafts();
        if (!hasUnsavedDraftEdit(index)) {
          docEl.value = draftAt(index);
        }
        renderDocTail();
        persistLocal();
        markSavedSnap();
        return true;
      } catch (e) {
        return false;
      }
    }

    async function saveAll() {
      if (recording) return;
      syncCurrentDraft();
      if (document.activeElement !== docEl && !hasUnsavedDraftEdit(index)) {
        await absorbServerDraftsIfRicher();
      }
      collapseIdenticalDrafts();
      const payload = buildPayload();
      const snap = JSON.stringify({ drafts: payload, index });
      if (snap === lastSent) return;
      try {
        const res = await fetch('/api/session/' + sessionId + '/remote-drafts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ drafts: payload, index }),
        });
        if (!res.ok) throw new Error('fail');
        lastSent = snap;
        markDraftsSaved();
        persistLocal();
        setStatus('已保存', 'ok');
      } catch (e) {
        setStatus('保存失败，请重试', 'err');
      }
    }

    docEl.addEventListener('focus', () => {
      if (recording) return;
      if (!editBaseline) resetEditBaseline();
    });
    docEl.addEventListener('input', () => {
      syncCurrentDraft();
      renderDocTail();
      updateRestoreBtn();
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => { void saveAll(); }, 250);
    });
    if (restoreBtn) {
      restoreBtn.addEventListener('click', () => { void restoreDraftEdits(); });
    }
    docEl.addEventListener('blur', () => {
      updateRestoreBtn();
      void saveAll();
    });
    setInterval(() => {
      if (sessionId && !recording && !sttBusy) void saveAll();
    }, 3000);
    setInterval(() => {
      if (sessionId && !sttBusy) void syncState();
    }, 2500);
    document.addEventListener('visibilitychange', () => {
      syncCurrentDraft();
      persistLocal();
      if (document.visibilityState === 'hidden') void saveAll();
    });
    window.addEventListener('pagehide', () => {
      syncCurrentDraft();
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
      pausePlayback(videoEl.currentTime);
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
      markDictationContentChanged();
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
      let s = Math.max(0, Math.min(caretStart, val.length));
      let e = Math.max(s, Math.min(caretEnd, val.length));
      let left = val.slice(0, s);
      let right = val.slice(e);
      let mid = String(piece || '').trim();
      if (!mid) return;
      if (val.trim() && s >= val.length - 2 && val.includes(mid)) return;
      if (left && !/\\s$/.test(left)) left += ' ';
      if (right && !/^\\s/.test(right)) mid += ' ';
      docEl.value = left + mid + right;
      syncCurrentDraft();
      const pos = left.length + mid.length;
      caretStart = pos;
      caretEnd = pos;
      try { docEl.setSelectionRange(pos, pos); } catch (e) {}
      renderDocTail();
      revealLatest();
      resetEditBaseline();
    }

    async function uploadAudio(floatChunks, sampleRate, elapsedMs, peak, mrChunks, mime) {
      if ((elapsedMs || 0) < 500) {
        setStatus('录音太短，请说完后再点停止', 'err');
        return;
      }
      let blob = null;
      let filename = 'ipad.wav';
      const pcmSamples = floatChunks.reduce((n, c) => n + c.length, 0);
      if (pcmSamples > 800) {
        blob = encodeWav(floatChunks, sampleRate);
        filename = 'ipad.wav';
      } else if (mrChunks && mrChunks.length) {
        const type = (mime || mrChunks[0].type || 'audio/mp4').split(';')[0];
        blob = new Blob(mrChunks, { type: type || 'audio/mp4' });
        filename = type.indexOf('mp4') >= 0 || type.indexOf('aac') >= 0
          ? 'ipad.m4a'
          : (type.indexOf('ogg') >= 0 ? 'ipad.ogg' : 'ipad.webm');
      }
      if (!blob || blob.size < 200) {
        setStatus('没有录到声音，请检查 HTTPS 和麦克风权限后重试', 'err');
        return;
      }
      sttBusy = true;
      btn.disabled = true;
      if (peak > 0 && peak < 0.0008 && pcmSamples > 800) {
        setStatus('声音偏小，仍在识别…');
      } else {
        setStatus('正在识别…');
      }
      const insertStart = caretStart;
      const insertEnd = caretEnd;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 90000);
      try {
        const body = new FormData();
        body.append('audio', blob, filename);
        body.append('index', String(index));
        body.append('mode', 'insert');
        const res = await fetch('/api/session/' + sessionId + '/remote-stt', {
          method: 'POST',
          body,
          signal: controller.signal,
        });
        if (!res.ok) {
          let detail = '';
          try { detail = await res.text(); } catch (e) {}
          throw new Error(detail || ('HTTP ' + res.status));
        }
        const data = await res.json();
        if (data.text) {
          caretStart = insertStart;
          caretEnd = insertEnd;
          insertAtCaret(data.text);
          lastSent = '';
          void saveAll();
          setStatus('已保存', 'ok');
          revealLatest();
        } else {
          setStatus('识别为空，请靠近麦克风再说一遍', 'err');
          revealLatest();
        }
      } catch (e) {
        const aborted = e && (e.name === 'AbortError' || String(e.message || '').includes('aborted'));
        if (aborted) {
          setStatus('识别超时，请再说一遍（说完后稍等几秒）', 'err');
        } else {
          setStatus('识别失败：' + (e && e.message ? e.message : e), 'err');
        }
        revealLatest();
      } finally {
        clearTimeout(timeout);
        sttBusy = false;
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

    function repeatCurrent() {
      const current = sentences[index];
      if (!current) return;
      const draftText = draftAt(index);
      const t = resumeTimeInSentence(current, draftText, pauseAt);
      segmentEnd = null;
      userPaused = false;
      playAt(t);
    }

    function repeatAtIndex(target) {
      const sentence = sentences[target];
      if (!sentence) return;
      localIndexControl = true;
      index = target;
      pauseAt = sentence.start;
      captionsOffForNewSentence();
      metaEl.textContent = '第 ' + (index + 1) + ' 句 / 共 ' + total + ' 句';
      refreshCaption();
      renderStrip();
      docEl.value = draftAt(target);
      renderDocTail();
      updateCurrentSentenceLabel();
      segmentEnd = null;
      userPaused = false;
      playAt(sentence.start);
      void saveAll();
    }

    function repeat() {
      const nowMs = Date.now();
      const prev = repeatClick;
      const continued = nowMs - prev.at <= 900;
      const baseIndex = continued ? prev.baseIndex : index;
      const count = continued ? prev.count + 1 : 1;
      const target = baseIndex - (count - 1);
      if (target < 0) return;
      repeatClick = { at: nowMs, baseIndex, count };
      if (target === index) {
        repeatCurrent();
      } else {
        repeatAtIndex(target);
      }
    }

    function togglePausePlayback() {
      if (recording) return;
      if (isPlaybackActive()) {
        pausePlayback(videoEl.currentTime);
      } else {
        resumePlayback();
      }
      updateTransportBtn();
    }

    resumeBtn.addEventListener('click', () => {
      togglePausePlayback();
    });
    videoEl.addEventListener('play', updateTransportBtn);
    videoEl.addEventListener('pause', updateTransportBtn);

    repeatBtn.addEventListener('click', () => {
      if (recording) return;
      refreshCaption();
      repeat();
    });

    if (sessionPicker) {
      sessionPicker.addEventListener('change', () => {
        const next = sessionPicker.value;
        if (!next || next === sessionId) return;
        void switchToSession(next, '已切换视频', true).then(() => loadSessionOptions());
      });
    }

    if (refreshSessionsBtn) {
      refreshSessionsBtn.addEventListener('click', () => {
        void loadSessionOptions();
      });
    }

    nextBtn.addEventListener('click', async () => {
      if (recording) return;
      pausePlayback(videoEl.currentTime);
      await saveAll();
      try {
        const res = await fetch('/api/session/' + sessionId + '/remote-next', { method: 'POST' });
        if (!res.ok) throw new Error('fail');
        const data = await res.json();
        index = data.index;
        if (!drafts[String(index)]) drafts[String(index)] = '';
        repeatClick = { at: 0, baseIndex: index, count: 0 };
        captionsOffForNewSentence();
        await syncState();
        renderDocView(false);
        if (sentences[index]) {
          pauseAt = sentences[index].start;
          playCurrent();
        }
        refreshCaption();
        setStatus('第 ' + (index + 1) + ' 句', 'ok');
      } catch (e) {
        setStatus('无法切换到下一句', 'err');
      }
    });

    void loadSessionOptions();
    // 不在 iPad 首屏触发 /api/warmup（会拖慢甚至拖垮首屏）；电脑端打开课时再预热即可
    void syncState();
    window.addEventListener('load', () => {
      resetEditBaseline();
      renderDocView(false);
      updateTransportBtn();
    });
  </script>
</body>
</html>
""".replace("__IPAD_BTN_SPEAK__", _IPAD_BTN_SPEAK).replace(
    "__IPAD_BTN_STOP__", _IPAD_BTN_STOP
).replace("__IPAD_BUILD__", IPAD_BUILD)
