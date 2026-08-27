const video = document.querySelector("#video");
const emptyVideo = document.querySelector("#emptyVideo");
const videoInput = document.querySelector("#videoInput");
const subtitleInput = document.querySelector("#subtitleInput");
const subtitleOverlay = document.querySelector("#subtitleOverlay");
const subtitleText = document.querySelector("#subtitleText");
const currentCue = document.querySelector("#currentCue");
const cueCounter = document.querySelector("#cueCounter");
const dictationInput = document.querySelector("#dictationInput");
const compareResult = document.querySelector("#compareResult");
const wordList = document.querySelector("#wordList");
const definitionBox = document.querySelector("#definitionBox");
const shadowInput = document.querySelector("#shadowInput");
const scoreCard = document.querySelector("#scoreCard");

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

const state = {
  cues: [],
  cueIndex: 0,
  subtitleVisible: false,
  recognition: null,
  shadowRecognition: null,
  words: new Set(JSON.parse(localStorage.getItem("enpratoWords") || "[]")),
  shadowStartedAt: 0,
  shadowEndedAt: 0,
};

const fallbackDictionary = {
  practice: { phonetic: "/ˈpræktɪs/", zh: "练习；实践", en: "to do an activity repeatedly to improve" },
  repeat: { phonetic: "/rɪˈpiːt/", zh: "重复；重说", en: "to say or do something again" },
  sentence: { phonetic: "/ˈsentəns/", zh: "句子", en: "a group of words that expresses a complete idea" },
  subtitle: { phonetic: "/ˈsʌbˌtaɪtl/", zh: "字幕", en: "words shown on a video that represent speech" },
  accent: { phonetic: "/ˈæksent/", zh: "口音；重音", en: "a way of pronouncing words shaped by region or language background" },
  pronunciation: { phonetic: "/prəˌnʌnsiˈeɪʃn/", zh: "发音", en: "the way a word is spoken" },
  rhythm: { phonetic: "/ˈrɪðəm/", zh: "节奏；韵律", en: "a regular pattern of sound or movement" },
  intonation: { phonetic: "/ˌɪntəˈneɪʃn/", zh: "语调", en: "the rise and fall of the voice when speaking" },
};

videoInput.addEventListener("change", () => {
  const [file] = videoInput.files;
  if (!file) return;
  video.src = URL.createObjectURL(file);
  emptyVideo.style.display = "none";
});

subtitleInput.addEventListener("change", async () => {
  const [file] = subtitleInput.files;
  if (!file) return;
  const text = await file.text();
  state.cues = parseSubtitles(text);
  state.cueIndex = 0;
  state.subtitleVisible = false;
  renderCue();
});

document.querySelector("#playSentenceBtn").addEventListener("click", playCurrentCue);
document.querySelector("#repeatBtn").addEventListener("click", playCurrentCue);
document.querySelector("#prevBtn").addEventListener("click", () => moveCue(-1));
document.querySelector("#nextBtn").addEventListener("click", () => moveCue(1));
document.querySelector("#revealBtn").addEventListener("click", () => {
  state.subtitleVisible = true;
  renderCue();
});
document.querySelector("#compareBtn").addEventListener("click", compareCurrent);
document.querySelector("#clearBtn").addEventListener("click", () => {
  dictationInput.value = "";
  compareResult.innerHTML = "";
});
document.querySelector("#clearWordsBtn").addEventListener("click", () => {
  state.words.clear();
  persistWords();
  renderWords();
  renderCue();
});

document.querySelector("#dictationTab").addEventListener("click", () => switchPanel("dictation"));
document.querySelector("#shadowTab").addEventListener("click", () => switchPanel("shadow"));
document.querySelector("#speechBtn").addEventListener("click", () => toggleRecognition("dictation"));
document.querySelector("#shadowRecordBtn").addEventListener("click", () => toggleRecognition("shadow"));
document.querySelector("#playAllBtn").addEventListener("click", () => {
  video.currentTime = 0;
  video.play();
});
document.querySelector("#stopAllBtn").addEventListener("click", () => video.pause());
document.querySelector("#scoreBtn").addEventListener("click", scoreShadowing);

video.addEventListener("timeupdate", () => {
  if (!state.cues.length) return;
  const cue = state.cues[state.cueIndex];
  if (video.currentTime >= cue.end && document.querySelector("#autoPauseToggle").checked) {
    video.pause();
    video.currentTime = cue.end;
  }
  const activeIndex = state.cues.findIndex((item) => video.currentTime >= item.start && video.currentTime < item.end);
  if (activeIndex >= 0 && activeIndex !== state.cueIndex) {
    state.cueIndex = activeIndex;
    state.subtitleVisible = false;
    renderCue();
  }
});

subtitleText.addEventListener("mouseup", handleWordSelection);
renderWords();

function parseSubtitles(input) {
  const normalized = input.replace(/\r/g, "").replace(/^WEBVTT[^\n]*\n/i, "").trim();
  const blocks = normalized.split(/\n{2,}/);
  return blocks
    .map((block) => {
      const lines = block.split("\n").filter(Boolean);
      const timeLineIndex = lines.findIndex((line) => line.includes("-->"));
      if (timeLineIndex < 0) return null;
      const [startRaw, endRaw] = lines[timeLineIndex].split("-->").map((part) => part.trim().split(/\s+/)[0]);
      const text = lines.slice(timeLineIndex + 1).join(" ").replace(/<[^>]+>/g, "").trim();
      return {
        start: parseTime(startRaw),
        end: parseTime(endRaw),
        text,
      };
    })
    .filter((cue) => cue && Number.isFinite(cue.start) && Number.isFinite(cue.end) && cue.text)
    .sort((a, b) => a.start - b.start);
}

function parseTime(value) {
  const parts = value.replace(",", ".").split(":").map(Number);
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  return Number.NaN;
}

function renderCue() {
  const cue = state.cues[state.cueIndex];
  cueCounter.textContent = `${state.cues.length ? state.cueIndex + 1 : 0} / ${state.cues.length}`;
  if (!cue) {
    currentCue.textContent = "导入字幕后会在这里显示当前句子。第一遍默认隐藏视频字幕。";
    subtitleText.textContent = "";
    subtitleOverlay.textContent = "";
    return;
  }
  currentCue.textContent = state.subtitleVisible ? cue.text : "字幕已隐藏。播放本句后，在右侧听写。";
  subtitleOverlay.textContent = state.subtitleVisible ? cue.text : "";
  subtitleText.innerHTML = renderMarkedText(cue.text);
}

function renderMarkedText(text) {
  return text.replace(/[A-Za-z][A-Za-z'-]*/g, (word) => {
    const key = normalizeWord(word);
    const marked = state.words.has(key) ? " marked" : "";
    return `<span class="word-token${marked}" data-word="${key}">${word}</span>`;
  });
}

function playCurrentCue() {
  const cue = state.cues[state.cueIndex];
  if (!cue || !video.src) return;
  state.subtitleVisible = false;
  renderCue();
  video.currentTime = Math.max(0, cue.start + 0.02);
  video.play();
}

function moveCue(delta) {
  if (!state.cues.length) return;
  state.cueIndex = Math.max(0, Math.min(state.cues.length - 1, state.cueIndex + delta));
  state.subtitleVisible = false;
  dictationInput.value = "";
  compareResult.innerHTML = "";
  renderCue();
}

function compareCurrent() {
  const cue = state.cues[state.cueIndex];
  if (!cue) return;
  const expected = tokenize(cue.text);
  const actual = tokenize(dictationInput.value);
  const similarity = similarityScore(expected, actual);
  compareResult.innerHTML = `
    <strong>本句匹配度：${Math.round(similarity * 100)}%</strong>
    <div>${renderDiff(expected, actual)}</div>
  `;
}

function tokenize(text) {
  return (text.toLowerCase().match(/[a-z0-9]+(?:'[a-z0-9]+)?/g) || []).map((word) => word.trim());
}

function similarityScore(expected, actual) {
  if (!expected.length && !actual.length) return 1;
  const distance = levenshtein(expected, actual);
  return Math.max(0, 1 - distance / Math.max(expected.length, actual.length, 1));
}

function levenshtein(a, b) {
  const dp = Array.from({ length: a.length + 1 }, () => Array(b.length + 1).fill(0));
  for (let i = 0; i <= a.length; i += 1) dp[i][0] = i;
  for (let j = 0; j <= b.length; j += 1) dp[0][j] = j;
  for (let i = 1; i <= a.length; i += 1) {
    for (let j = 1; j <= b.length; j += 1) {
      dp[i][j] = Math.min(
        dp[i - 1][j] + 1,
        dp[i][j - 1] + 1,
        dp[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1),
      );
    }
  }
  return dp[a.length][b.length];
}

function renderDiff(expected, actual) {
  const actualSet = new Set(actual);
  const expectedSet = new Set(expected);
  const missed = expected.filter((word) => !actualSet.has(word));
  const extra = actual.filter((word) => !expectedSet.has(word));
  const correct = expected.filter((word) => actualSet.has(word));
  return [
    `<p><span class="diff-good">正确：</span>${correct.join(" ") || "无"}</p>`,
    `<p><span class="diff-miss">漏听：</span>${missed.join(" ") || "无"}</p>`,
    `<p><span class="diff-extra">多余：</span>${extra.join(" ") || "无"}</p>`,
  ].join("");
}

function handleWordSelection() {
  const selection = window.getSelection().toString().trim();
  const [word] = selection.match(/[A-Za-z][A-Za-z'-]*/g) || [];
  if (!word) return;
  const normalized = normalizeWord(word);
  state.words.add(normalized);
  persistWords();
  renderWords();
  renderCue();
  showDefinition(normalized);
}

function normalizeWord(word) {
  return word.toLowerCase().replace(/^'+|'+$/g, "");
}

function persistWords() {
  localStorage.setItem("enpratoWords", JSON.stringify([...state.words]));
}

function renderWords() {
  wordList.innerHTML = "";
  [...state.words].sort().forEach((word) => {
    const chip = document.createElement("button");
    chip.className = "word-chip";
    chip.type = "button";
    chip.textContent = word;
    chip.addEventListener("click", () => showDefinition(word));
    wordList.append(chip);
  });
}

async function showDefinition(word) {
  definitionBox.innerHTML = `<strong>${word}</strong> 查询中...`;
  const local = fallbackDictionary[word];
  try {
    const response = await fetch(`https://api.dictionaryapi.dev/api/v2/entries/en/${encodeURIComponent(word)}`);
    if (!response.ok) throw new Error("dictionary lookup failed");
    const [entry] = await response.json();
    const meaning = entry.meanings?.[0]?.definitions?.[0]?.definition || local?.en || "暂无英文释义";
    const phonetic = entry.phonetic || entry.phonetics?.find((item) => item.text)?.text || local?.phonetic || "";
    definitionBox.innerHTML = definitionMarkup(word, phonetic, meaning, local?.zh || "可结合上下文记录中文释义");
  } catch {
    const data = local || { phonetic: "", zh: "暂无本地中文释义", en: "网络词典不可用时，可先保存单词并稍后查询。" };
    definitionBox.innerHTML = definitionMarkup(word, data.phonetic, data.en, data.zh);
  }
}

function definitionMarkup(word, phonetic, en, zh) {
  return `
    <strong>${word}</strong> ${phonetic ? `<span>${phonetic}</span>` : ""}
    <button type="button" onclick="speakWord('${word.replace(/'/g, "\\'")}')">发音</button>
    <div>中文：${zh}</div>
    <div>English: ${en}</div>
  `;
}

window.speakWord = (word) => {
  const utterance = new SpeechSynthesisUtterance(word);
  utterance.lang = "en-US";
  speechSynthesis.speak(utterance);
};

function toggleRecognition(mode) {
  if (!SpeechRecognition) {
    alert("当前浏览器不支持 Web Speech API。建议使用 Chrome 或 Edge。");
    return;
  }
  const button = mode === "dictation" ? document.querySelector("#speechBtn") : document.querySelector("#shadowRecordBtn");
  const target = mode === "dictation" ? dictationInput : shadowInput;
  const existing = mode === "dictation" ? state.recognition : state.shadowRecognition;
  if (existing) {
    existing.stop();
    return;
  }
  const recognition = new SpeechRecognition();
  recognition.lang = "en-US";
  recognition.continuous = true;
  recognition.interimResults = true;
  let finalText = target.value.trim();
  if (mode === "shadow") state.shadowStartedAt = performance.now();

  recognition.onresult = (event) => {
    let interim = "";
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const transcript = event.results[i][0].transcript;
      if (event.results[i].isFinal) finalText += `${finalText ? " " : ""}${transcript.trim()}`;
      else interim += transcript;
    }
    target.value = `${finalText}${interim ? ` ${interim}` : ""}`.trim();
  };
  recognition.onend = () => {
    if (mode === "shadow") state.shadowEndedAt = performance.now();
    if (mode === "dictation") state.recognition = null;
    else state.shadowRecognition = null;
    button.classList.remove("recording");
    button.textContent = mode === "dictation" ? "开始语音输入" : "开始跟读录音";
  };
  recognition.start();
  if (mode === "dictation") state.recognition = recognition;
  else state.shadowRecognition = recognition;
  button.classList.add("recording");
  button.textContent = "停止";
}

function scoreShadowing() {
  const expectedText = state.cues.map((cue) => cue.text).join(" ");
  const expected = tokenize(expectedText);
  const actual = tokenize(shadowInput.value);
  const textScore = similarityScore(expected, actual);
  const subtitleDuration = state.cues.length ? state.cues[state.cues.length - 1].end - state.cues[0].start : video.duration || 0;
  const spokenDuration = Math.max(0, (state.shadowEndedAt - state.shadowStartedAt) / 1000);
  const paceScore = subtitleDuration && spokenDuration ? Math.max(0, 1 - Math.abs(spokenDuration - subtitleDuration) / subtitleDuration) : 0;
  const finalScore = Math.round((textScore * 0.72 + paceScore * 0.28) * 100);
  scoreCard.innerHTML = `
    <strong>${finalScore} 分</strong>
    <div class="score-meter"><span style="width:${finalScore}%"></span></div>
    <span>文本准确度 ${Math.round(textScore * 100)}%，语速贴合度 ${Math.round(paceScore * 100)}%。浏览器无法直接稳定读取原视频语调，本版用字幕时长与识别文本做基础评分。</span>
  `;
}

function switchPanel(mode) {
  document.querySelector("#dictationTab").classList.toggle("active", mode === "dictation");
  document.querySelector("#shadowTab").classList.toggle("active", mode === "shadow");
  document.querySelector("#dictationPanel").classList.toggle("active", mode === "dictation");
  document.querySelector("#shadowPanel").classList.toggle("active", mode === "shadow");
}
