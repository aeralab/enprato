from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from .sentences import number_sentences, split_timed_sentences, words_to_sentences


def _ms_to_sec(value: Any) -> float:
    try:
        return max(0.0, float(value) / 1000.0)
    except (TypeError, ValueError):
        return 0.0


def _word_text(item: dict[str, Any]) -> str:
    text = str(item.get("text") or item.get("word") or "")
    punct = str(item.get("punctuation") or "")
    return (text + punct).strip() or text


def _collect_words(payload: dict[str, Any]) -> list[Any]:
    words: list[Any] = []
    for transcript in payload.get("transcripts") or []:
        if not isinstance(transcript, dict):
            continue
        for sentence in transcript.get("sentences") or []:
            if not isinstance(sentence, dict):
                continue
            raw_words = sentence.get("words") or []
            if not isinstance(raw_words, list):
                continue
            for item in raw_words:
                if not isinstance(item, dict):
                    continue
                token = _word_text(item)
                if not token:
                    continue
                words.append(
                    SimpleNamespace(
                        word=token if token.endswith(" ") else token + " ",
                        start=_ms_to_sec(item.get("begin_time")),
                        end=_ms_to_sec(item.get("end_time")),
                    )
                )
    return words


def _collect_sentence_cues(payload: dict[str, Any]) -> list[dict[str, Any]]:
    cues: list[dict[str, Any]] = []
    for transcript in payload.get("transcripts") or []:
        if not isinstance(transcript, dict):
            continue
        for sentence in transcript.get("sentences") or []:
            if not isinstance(sentence, dict):
                continue
            text = str(sentence.get("text") or "").strip()
            if not text:
                continue
            cues.append(
                {
                    "start": _ms_to_sec(sentence.get("begin_time")),
                    "end": _ms_to_sec(sentence.get("end_time")),
                    "text": text,
                }
            )
    return cues


def transcription_has_word_timestamps(payload: dict[str, Any]) -> bool:
    return bool(_collect_words(payload))


def transcription_has_sentence_timestamps(payload: dict[str, Any]) -> bool:
    return bool(_collect_sentence_cues(payload))


def aliyun_transcription_to_sentences(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Map DashScope file-transcribe JSON onto Enprato sentence objects.

    Prefers word timestamps so the existing splitter can cut listening units.
    Falls back to sentence timestamps when words are missing.
    """
    if not isinstance(payload, dict):
        return []
    words = _collect_words(payload)
    if words:
        return words_to_sentences(words)
    cues = _collect_sentence_cues(payload)
    if not cues:
        return []
    return number_sentences(split_timed_sentences(cues))
