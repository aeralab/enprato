from __future__ import annotations

import re
from typing import Any

SOFT_DISPLAY_UNITS = 82.0
HARD_DISPLAY_UNITS = 124.0
SOFT_WORDS = 18
HARD_WORDS = 29
_BOUNDARY_WORDS = {"and", "but", "or", "so", "yet", "because", "although", "though", "while", "when", "where", "if", "unless", "that", "which", "who", "as", "especially", "rather", "instead", "then"}
_STRONG_CLAUSE_WORDS = {"and", "but", "or", "so", "yet", "because", "although", "though", "while", "when", "where", "if", "unless", "which", "who", "however"}
_PREPOSITION_WORDS = {"in", "on", "at", "for", "with", "from", "to", "by", "of", "about", "after", "before"}

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
    return number_sentences(split_long_cues(merged))


def words_to_sentences(
    words: list[Any],
    pause: float = 0.55,
    max_dur: float = 11.5,
) -> list[dict[str, Any]]:
    sentences: list[dict[str, Any]] = []
    current: list[Any] = []
    source_group_index = 0

    def flush() -> None:
        nonlocal source_group_index
        if not current:
            return
        text = "".join(w.word for w in current)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            current.clear()
            return
        children = _split_word_group(current)
        for segment_index, child in enumerate(children):
            child["parent_id"] = f"asr-{source_group_index}"
            child["segment_index"] = segment_index
        sentences.extend(children)
        source_group_index += 1
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


def _display_units(text: str) -> float:
    units = 0.0
    for char in text:
        if char.isspace():
            units += 0.28
        elif char.isalnum():
            units += 0.56
        else:
            units += 0.32
    return units


def _tokens(text: str) -> list[str]:
    return [part for part in re.split(r"\s+", text.strip()) if part]


def _word_core(token: str) -> str:
    return re.sub(r"^[^A-Za-z]+|[^A-Za-z]+$", "", token).lower()


def _boundary_strength(tokens: list[str], end: int) -> int:
    if end <= 0 or end > len(tokens):
        return -1
    token = tokens[end - 1].rstrip()
    if re.search(r"[.!?;:]\s*[\"'\u201d\u2019)]*$", token):
        return 100
    next_word = _word_core(tokens[end]) if end < len(tokens) else ""
    if re.search(r",\s*[\"'\u201d\u2019)]*$", token):
        return 94 if next_word in _STRONG_CLAUSE_WORDS else 82
    if next_word in _BOUNDARY_WORDS:
        return 66
    if _word_core(tokens[end - 1]) in _PREPOSITION_WORDS:
        return 18
    return 8 if end == len(tokens) else 0


def _choose_text_cut(tokens: list[str]) -> int:
    if len(tokens) <= SOFT_WORDS and _display_units(" ".join(tokens)) <= SOFT_DISPLAY_UNITS:
        return len(tokens)
    candidates = [i for i in range(1, len(tokens) + 1) if _boundary_strength(tokens, i) >= 0]
    valid = [i for i in candidates if i >= 5 and i <= HARD_WORDS and _display_units(" ".join(tokens[:i])) <= HARD_DISPLAY_UNITS]
    if not valid:
        valid = [i for i in range(5, min(len(tokens), HARD_WORDS) + 1)] or [min(len(tokens), HARD_WORDS)]
    soft_valid = [
        i for i in valid
        if i <= SOFT_WORDS and _display_units(" ".join(tokens[:i])) <= SOFT_DISPLAY_UNITS
    ]
    pool = soft_valid or valid
    scored: list[tuple[float, int]] = []
    for i in pool:
        units = _display_units(" ".join(tokens[:i]))
        strength = _boundary_strength(tokens, i)
        scored.append((abs(units - SOFT_DISPLAY_UNITS) * 0.3 - strength, i))
    return min(scored)[1]


def split_long_text(text: str) -> list[str]:
    tokens = _tokens(text)
    if not tokens:
        return []
    parts: list[str] = []
    while tokens:
        cut = _choose_text_cut(tokens)
        parts.append(" ".join(tokens[:cut]).strip())
        tokens = tokens[cut:]
    return parts


def _split_cue(cue: dict[str, Any], parts: list[str]) -> list[dict[str, Any]]:
    if len(parts) <= 1:
        return [cue]
    start = float(cue["start"])
    end = float(cue["end"])
    weights = [max(1.0, _display_units(part)) for part in parts]
    total = sum(weights)
    out: list[dict[str, Any]] = []
    cursor = start
    for i, (part, weight) in enumerate(zip(parts, weights)):
        next_cursor = end if i == len(parts) - 1 else start + (end - start) * sum(weights[: i + 1]) / total
        out.append({"start": cursor, "end": max(cursor + 0.08, next_cursor), "text": part})
        cursor = next_cursor
    out[-1]["end"] = end
    return out


def split_long_cues(cues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for source_index, cue in enumerate(cues):
        parts = split_long_text(str(cue.get("text") or ""))
        children = _split_cue(cue, parts)
        for segment_index, child in enumerate(children):
            child["parent_id"] = str(cue.get("parent_id") or f"cue-{source_index}")
            child["segment_index"] = segment_index
        out.extend(children)
    return out


def split_timed_sentences(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for source_index, item in enumerate(items):
        parts = split_long_text(str(item.get("text") or ""))
        children = _split_cue(item, parts)
        for segment_index, child in enumerate(children):
            child["parent_id"] = str(item.get("parent_id") or f"asr-{source_index}")
            child["segment_index"] = segment_index
        out.extend(children)
    return out


def _split_word_group(words: list[Any]) -> list[dict[str, Any]]:
    if not words:
        return []
    tokens = [str(getattr(word, "word", "") or "").strip() for word in words]
    if len(tokens) <= SOFT_WORDS and _display_units(" ".join(tokens)) <= SOFT_DISPLAY_UNITS:
        return [{"start": float(words[0].start), "end": float(words[-1].end), "text": "".join(getattr(word, "word", "") or "" for word in words).strip()}]
    cut = _choose_text_cut(tokens)
    left = _split_word_group(words[:cut])
    right = _split_word_group(words[cut:])
    if not right:
        return left
    return left + right


def number_sentences(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    numbered = []
    for i, item in enumerate(items):
        item_out = {
                "id": i,
                "start": round(float(item["start"]), 3),
                "end": round(float(item["end"]), 3),
                "text": item["text"].strip(),
            }
        if item.get("parent_id") is not None:
            item_out["parent_id"] = str(item["parent_id"])
            item_out["segment_index"] = int(item.get("segment_index") or 0)
        numbered.append(item_out)
    return numbered
