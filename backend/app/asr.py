from __future__ import annotations

import os
import re
from collections import Counter
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

from faster_whisper import WhisperModel

from .sentences import words_to_sentences

MODEL_ROOT = Path(__file__).resolve().parents[1] / "data" / "models"


def _model_name() -> str:
    name = os.environ.get("WHISPER_MODEL", "small.en")
    local = MODEL_ROOT / "faster-whisper-small.en"
    if name == "small.en" and local.is_dir():
        return str(local)
    return name


@lru_cache(maxsize=1)
def get_model() -> WhisperModel:
    name = _model_name()
    device = os.environ.get("WHISPER_DEVICE", "cuda")
    compute = os.environ.get("WHISPER_COMPUTE", "float16")
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        return WhisperModel(name, device=device, compute_type=compute, download_root=str(MODEL_ROOT))
    except Exception:
        return WhisperModel(name, device="cpu", compute_type="int8", download_root=str(MODEL_ROOT))


_warmed = False


def _warm_model_once(model: WhisperModel) -> None:
    global _warmed
    if _warmed:
        return
    import tempfile
    import wave

    tmp = Path(tempfile.gettempdir()) / "enprato-asr-warmup.wav"
    try:
        with wave.open(str(tmp), "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * 8000)
        _run_transcribe(model, tmp, "English dictation.", vad=False, beam_size=1)
        _warmed = True
    except Exception:
        pass
    finally:
        tmp.unlink(missing_ok=True)


def warmup() -> str:
    model = get_model()
    _warm_model_once(model)
    return getattr(model, "model_path", os.environ.get("WHISPER_MODEL", "small.en"))


def transcribe_sentences(audio_path: Path) -> list[dict[str, Any]]:
    """Full-video sentence split for imports without English captions.

    Tuned for speed: beam_size=1 and no previous-text conditioning.
    Word timestamps stay on so we can cut sentence boundaries.
    """
    model = get_model()
    _warm_model_once(model)
    segments, _info = model.transcribe(
        str(audio_path),
        language="en",
        beam_size=1,
        vad_filter=True,
        word_timestamps=True,
        condition_on_previous_text=False,
    )
    words = []
    for segment in segments:
        if not segment.words:
            continue
        words.extend(segment.words)
    if not words:
        return []
    return words_to_sentences(words)


def merge_dictation_text(prev: str, new: str, target: str = "") -> str:
    p = collapse_repeated_clauses(" ".join(str(prev or "").split()))
    n = collapse_repeated_clauses(" ".join(str(new or "").split()))
    if not n:
        return p
    if not p:
        return n
    pl = re.sub(r"[^\w\s]", " ", p.lower())
    pl = " ".join(pl.split())
    nl = re.sub(r"[^\w\s]", " ", n.lower())
    nl = " ".join(nl.split())
    if nl in pl:
        return p
    if pl in nl:
        return n
    tt = " ".join(target.split()).lower()
    if tt and nl == tt:
        return n
    # 末尾已有高度重叠则不追加，避免「又识别一遍」叠成重复句
    pw = pl.split()
    nw = nl.split()
    if pw and nw:
        max_overlap = min(len(pw), len(nw), 24)
        for k in range(max_overlap, 2, -1):
            if pw[-k:] == nw[:k]:
                merged = p + " " + " ".join(n.split()[k:])
                return collapse_repeated_clauses(merged.strip())
    return collapse_repeated_clauses(f"{p} {n}")


def collapse_repeated_clauses(text: str) -> str:
    """压掉 Whisper/STT 常见的整句、子句连环重复（含标点微差）。"""
    text = " ".join(str(text or "").split())
    if len(text) < 20:
        return text

    # 1) 按句号切开，去掉连续近重复句
    parts = re.split(r"(?<=[.!?])\s+", text)
    if len(parts) >= 2:
        kept: list[str] = []
        for part in parts:
            if not part.strip():
                continue
            if kept and _clause_near_duplicate(kept[-1], part):
                continue
            kept.append(part.strip())
        text = " ".join(kept)

    words = text.split()
    if len(words) < 6:
        return text

    # 2) 连续 n-gram 重复压成一次（n=2..20）
    for _ in range(10):
        changed = False
        for n in range(min(20, len(words) // 2), 1, -1):
            i = 0
            out: list[str] = []
            while i < len(words):
                if i + 2 * n <= len(words) and _norm_word_span(words[i : i + n]) == _norm_word_span(
                    words[i + n : i + 2 * n]
                ):
                    out.extend(words[i : i + n])
                    i += n
                    while i + n <= len(words) and _norm_word_span(out[-n:]) == _norm_word_span(words[i : i + n]):
                        i += n
                        changed = True
                    continue
                out.append(words[i])
                i += 1
            words = out
            if changed:
                break
        if not changed:
            break
    return " ".join(words)


def _norm_word_span(words: list[str]) -> tuple[str, ...]:
    return tuple(_norm_token(w) for w in words if _norm_token(w))


def _clause_near_duplicate(a: str, b: str) -> bool:
    na = " ".join(_norm_token(t) for t in a.split() if _norm_token(t))
    nb = " ".join(_norm_token(t) for t in b.split() if _norm_token(t))
    if not na or not nb:
        return False
    if na == nb:
        return True
    if len(na) >= 12 and (na in nb or nb in na):
        return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.92


def prefer_cleaner_draft(cur: str, incoming: str) -> str:
    """合并听写时不要无脑偏爱「更长」——重复幻觉长稿应让位给干净短稿。"""
    c = str(cur or "")
    n = str(incoming or "")
    ct = c.strip()
    nt = n.strip()
    if not nt:
        return c
    if not ct:
        return collapse_repeated_clauses(n)
    cc = collapse_repeated_clauses(ct)
    nn = collapse_repeated_clauses(nt)
    c_bloated = len(ct) > max(48, int(len(cc) * 1.35))
    n_bloated = len(nt) > max(48, int(len(nn) * 1.35))
    if c_bloated and not n_bloated:
        return nn
    if n_bloated and not c_bloated:
        return cc
    if c_bloated and n_bloated:
        return nn if len(nn) >= len(cc) else cc
    # 两边都干净：保留信息更多的一侧
    return nn if len(nn) >= len(cc) else cc


def transcribe_speech(
    audio_path: Path,
    context: str = "",
    target: str = "",
    *,
    fast: bool = False,
) -> str:
    """Dictation / shadowing with accent: bias spelling toward the expected line."""
    model = get_model()
    _warm_model_once(model)
    target = " ".join(target.split())
    ctx = " ".join(context.split())[:600]
    prompt = _build_prompt(target=target, context="" if fast else ctx, compact=fast)
    beam = 1

    # 短句听写：只跑一遍。VAD 常把短录音判成空，再无 VAD 重跑会把等待时间翻倍。
    text = _run_transcribe(
        model,
        audio_path,
        prompt,
        vad=False,
        beam_size=beam,
        timestamps=False,
        hotwords=not fast,
    )

    text = _clean_stt(text)
    if target:
        text = _spell_toward_target(text, target)
    return text


def _build_prompt(*, target: str, context: str, compact: bool = False) -> str:
    if compact:
        parts = ["English dictation. Correct spelling. No extra sentences."]
        if target:
            parts.append("Expected: " + target)
        return " ".join(parts)
    parts = [
        "English dictation by a non-native speaker with an accent.",
        "Output correct English spelling and punctuation.",
        "Do not invent extra sentences.",
        "Never repeat the same word over and over.",
    ]
    if target:
        # 本句词汇进 prompt，帮助专有名词/难词拼写（听写场景：跟读刚播的那句）
        parts.append("The expected sentence (for spelling only): " + target)
        rare = _rare_words(target)
        if rare:
            parts.append("Spell these words exactly: " + ", ".join(rare))
    if context:
        parts.append("Topic from earlier lines: " + context)
    return " ".join(parts)


def _rare_words(text: str) -> list[str]:
    stop = {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "to",
        "of",
        "in",
        "on",
        "for",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "it",
        "this",
        "that",
        "with",
        "as",
        "at",
        "by",
        "from",
        "he",
        "she",
        "they",
        "we",
        "you",
        "i",
        "has",
        "have",
        "had",
        "not",
        "his",
        "her",
        "their",
    }
    out: list[str] = []
    seen: set[str] = set()
    for raw in re.findall(r"[A-Za-z][A-Za-z'\-]*", text):
        key = raw.lower()
        if key in stop or key in seen:
            continue
        if len(raw) >= 5 or raw[0].isupper():
            seen.add(key)
            out.append(raw)
    return out[:24]


def _run_transcribe(
    model: WhisperModel,
    audio_path: Path,
    prompt: str,
    *,
    vad: bool,
    beam_size: int = 5,
    timestamps: bool = True,
    hotwords: bool = True,
) -> str:
    kwargs: dict[str, Any] = {
        "language": "en",
        "beam_size": beam_size,
        "vad_filter": vad,
        "initial_prompt": prompt,
        "temperature": 0.0,
        "condition_on_previous_text": False,
        "without_timestamps": not timestamps,
        "no_speech_threshold": 0.35 if not vad else 0.5,
        "compression_ratio_threshold": 2.6,
        "log_prob_threshold": -1.2,
    }
    if vad:
        kwargs["vad_parameters"] = {
            "min_silence_duration_ms": 400,
            "speech_pad_ms": 400,
        }
    rare = _rare_words(prompt) if hotwords else []
    if rare:
        kwargs["hotwords"] = " ".join(rare[:20])
    try:
        segments, _info = model.transcribe(str(audio_path), **kwargs)
    except TypeError:
        kwargs.pop("hotwords", None)
        kwargs.pop("without_timestamps", None)
        segments, _info = model.transcribe(str(audio_path), **kwargs)
    return " ".join(seg.text.strip() for seg in segments if seg.text).strip()


def _norm_token(token: str) -> str:
    return re.sub(r"[^a-z0-9']", "", token.lower())


def _spell_toward_target(spoken: str, target: str) -> str:
    """Keep what the student roughly said, but fix spelling using the expected line."""
    if not spoken.strip():
        return spoken
    spoken_tokens = spoken.split()
    target_tokens = target.split()
    if not spoken_tokens or not target_tokens:
        return spoken

    # 整体很像：当作成功跟读，直接用正确拼写的目标句（听写要的是正确拼写）
    ratio = SequenceMatcher(
        None,
        " ".join(_norm_token(t) for t in spoken_tokens),
        " ".join(_norm_token(t) for t in target_tokens),
    ).ratio()
    if ratio >= 0.72:
        return _clean_stt(target)

    sm = SequenceMatcher(
        None,
        [_norm_token(t) for t in spoken_tokens],
        [_norm_token(t) for t in target_tokens],
    )
    out: list[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out.extend(target_tokens[j1:j2])
        elif tag == "replace":
            # 逐词：像则用目标拼写，不像则保留识别结果
            a = spoken_tokens[i1:i2]
            b = target_tokens[j1:j2]
            for k, hyp in enumerate(a):
                if k < len(b) and SequenceMatcher(None, _norm_token(hyp), _norm_token(b[k])).ratio() >= 0.55:
                    out.append(b[k])
                else:
                    out.append(hyp)
            if len(b) > len(a):
                # 漏读的目标词不硬塞，避免“没说也写上”
                pass
        elif tag == "delete":
            out.extend(spoken_tokens[i1:i2])
        # insert: 目标多出来的词，不自动补全（防止没说的也被填进听写稿）
    return _clean_stt(" ".join(out))


def _clean_stt(text: str) -> str:
    text = " ".join(text.split())
    replacements = {
        " i ": " I ",
        " i'm ": " I'm ",
        " i've ": " I've ",
        " i'd ": " I'd ",
        " i'll ": " I'll ",
        " dont ": " don't ",
        " doesnt ": " doesn't ",
        " cant ": " can't ",
        " wont ": " won't ",
        " didnt ": " didn't ",
        " isnt ": " isn't ",
        " wasnt ": " wasn't ",
        " thats ": " that's ",
        " its a ": " it's a ",
        " gonna ": " going to ",
        " wanna ": " want to ",
    }
    padded = f" {text} "
    for src, dst in replacements.items():
        padded = padded.replace(src, dst)
        padded = padded.replace(src.title(), dst)
    cleaned = padded.strip()
    if cleaned and cleaned[0].isalpha():
        cleaned = cleaned[0].upper() + cleaned[1:]
    cleaned = _collapse_hallucination(cleaned)
    return collapse_repeated_clauses(cleaned)


def _collapse_hallucination(text: str) -> str:
    """Whisper 在静音/噪声时常循环 Facebook / Thank you 等，不是病毒。"""
    tokens = text.split()
    if len(tokens) < 3:
        return text
    norms = [_norm_token(t) for t in tokens]
    alive = [n for n in norms if n]
    if not alive:
        return ""
    top, count = Counter(alive).most_common(1)[0]
    # 大半是同一个词 → 判为幻觉，整段丢弃让用户重说
    if count >= 4 and count / len(alive) >= 0.45:
        return ""
    # 连续重复压成一次：word word word → word
    out: list[str] = []
    for token, key in zip(tokens, norms):
        if key and out and _norm_token(out[-1]) == key:
            continue
        out.append(token)
    # 短循环 ABABAB…
    if len(out) >= 6:
        for n in (1, 2, 3):
            unit = out[:n]
            if len(out) >= n * 3 and all(out[i : i + n] == unit for i in range(0, len(out) - len(out) % n, n)):
                return " ".join(unit)
    return " ".join(out)
