from __future__ import annotations

import re
from typing import Any

SENTENCE_END = re.compile(r"[.!?。！？][\"'”’)]*$")
SRT_BLOCK = re.compile(
    r"(\d+)\s+([\d:,.]+)\s+-->\s+([\d:,.]+)\s+([\s\S]*?)(?=\n\s*\n|\Z)",
    re.MULTILINE,
)


def _ts_to_seconds(stamp: str) -> float:
    stamp = stamp.strip().replace(",", ".")
    parts = stamp.split(":")
    if len(parts) == 3:
        h, m, rest = parts
        return int(h) * 3600 + int(m) * 60 + float(rest)
    if len(parts) == 2:
        m, rest = parts
        return int(m) * 60 + float(rest)
    return float(stamp)


def parse_srt(text: str) -> list[dict[str, Any]]:
    cues: list[dict[str, Any]] = []
    normalized = text.replace("\r\n", "\n").strip() + "\n\n"
    for match in SRT_BLOCK.finditer(normalized):
        body = re.sub(r"<[^>]+>", "", match.group(4))
        body = " ".join(line.strip() for line in body.splitlines() if line.strip())
        body = body.replace("{\\an8}", "").strip()
        if not body:
            continue
        cues.append(
            {
                "start": _ts_to_seconds(match.group(2)),
                "end": _ts_to_seconds(match.group(3)),
                "text": body,
            }
        )
    return merge_short_cues(cues)


def parse_vtt(text: str) -> list[dict[str, Any]]:
    lines = text.replace("\r\n", "\n").split("\n")
    cues: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "-->" in line:
            start_s, end_s = [p.strip().split(" ")[0] for p in line.split("-->")]
            i += 1
            body_lines: list[str] = []
            while i < len(lines) and lines[i].strip():
                body_lines.append(re.sub(r"<[^>]+>", "", lines[i]).strip())
                i += 1
            body = " ".join(p for p in body_lines if p)
            if body:
                cues.append(
                    {
                        "start": _ts_to_seconds(start_s),
                        "end": _ts_to_seconds(end_s),
                        "text": body,
                    }
                )
        i += 1
    return merge_short_cues(cues)


def merge_short_cues(cues: list[dict[str, Any]], min_dur: float = 1.15) -> list[dict[str, Any]]:
    if not cues:
        return []
    merged: list[dict[str, Any]] = []
    buf = dict(cues[0])
    for cue in cues[1:]:
        dur = buf["end"] - buf["start"]
        ended = bool(SENTENCE_END.search(buf["text"].strip()))
        gap = cue["start"] - buf["end"]
        if (not ended and dur < min_dur) or (gap < 0.18 and dur < 2.2):
            buf["end"] = cue["end"]
            buf["text"] = (buf["text"].rstrip() + " " + cue["text"].lstrip()).strip()
        else:
            merged.append(buf)
            buf = dict(cue)
    merged.append(buf)
    return number_sentences(merged)


def words_to_sentences(
    words: list[Any],
    pause: float = 0.55,
    max_dur: float = 11.5,
) -> list[dict[str, Any]]:
    sentences: list[dict[str, Any]] = []
    current: list[Any] = []

    def flush() -> None:
        if not current:
            return
        text = "".join(w.word for w in current)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            current.clear()
            return
        sentences.append(
            {
                "start": float(current[0].start),
                "end": float(current[-1].end),
                "text": text,
            }
        )
        current.clear()

    for word in words:
        token = getattr(word, "word", "") or ""
        if not token.strip():
            continue
        if not current:
            current.append(word)
            continue
        gap = float(word.start) - float(current[-1].end)
        dur = float(word.end) - float(current[0].start)
        prev = current[-1].word.strip()
        punct = bool(SENTENCE_END.search(prev))
        if punct or gap >= pause or dur >= max_dur:
            flush()
            current.append(word)
        else:
            current.append(word)
    flush()
    return number_sentences(sentences)


def number_sentences(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    numbered = []
    for i, item in enumerate(items):
        numbered.append(
            {
                "id": i,
                "start": round(float(item["start"]), 3),
                "end": round(float(item["end"]), 3),
                "text": item["text"].strip(),
            }
        )
    return numbered
