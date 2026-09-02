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
    p = " ".join(str(prev or "").split())
    n = " ".join(str(new or "").split())
    if not n:
        return p
    if not p:
        return n
    pl = p.lower()
    nl = n.lower()
    if nl in pl:
        return p
    if pl in nl:
        return n
    tt = " ".join(target.split()).lower()
    if tt and nl == tt:
        return n
    return f"{p} {n}"


def transcribe_speech(
    audio_path: Path,
    context: str = "",
    target: str = "",
    *,
    fast: bool = False,
) -> str:
    """Dictation / shadowing with accent: bias spelling toward the expected line."""
    model = get_model()
    target = " ".join(target.split())
    ctx = " ".join(context.split())[:600]
    prompt = _build_prompt(target=target, context=ctx)
    beam = 1 if fast else 5

    if fast:
        text = _run_transcribe(model, audio_path, prompt, vad=True, beam_size=beam)
        if not text:
            text = _run_transcribe(model, audio_path, prompt, vad=False, beam_size=beam)
    else:
        text = _run_transcribe(model, audio_path, prompt, vad=False, beam_size=beam)
        if not text:
            text = _run_transcribe(model, audio_path, prompt, vad=True, beam_size=beam)

    text = _clean_stt(text)
    if target:
        text = _spell_toward_target(text, target)
    return text


def _build_prompt(*, target: str, context: str) -> str:
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
) -> str:
    kwargs: dict[str, Any] = {
        "language": "en",
        "beam_size": beam_size,
        "vad_filter": vad,
        "initial_prompt": prompt,
        "temperature": 0.0,
        "condition_on_previous_text": False,
        "no_speech_threshold": 0.35 if not vad else 0.5,
        "compression_ratio_threshold": 2.6,
        "log_prob_threshold": -1.2,
    }
    if vad:
        kwargs["vad_parameters"] = {
            "min_silence_duration_ms": 400,
            "speech_pad_ms": 400,
        }
    rare = _rare_words(prompt)
    if rare:
        kwargs["hotwords"] = " ".join(rare[:20])
    try:
        segments, _info = model.transcribe(str(audio_path), **kwargs)
    except TypeError:
        kwargs.pop("hotwords", None)
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
    return _collapse_hallucination(cleaned)


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
