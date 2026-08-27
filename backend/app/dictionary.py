from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

import httpx

UA = {"User-Agent": "enprato/0.1"}


def _norm(word: str) -> str:
    return re.sub(r"[^A-Za-z'-]", "", word).strip()


@lru_cache(maxsize=256)
def lookup_word(raw: str) -> dict[str, Any]:
    word = _norm(raw)
    if not word:
        return {"word": raw, "error": "empty"}

    english = _english_entry(word)
    chinese = _chinese_gloss(word)
    phonetic = english.get("phonetic") or ""
    audio = english.get("audio") or ""
    defs_en = english.get("defs") or []
    return {
        "word": word,
        "phonetic": phonetic,
        "audio": audio,
        "defs_en": defs_en[:4],
        "defs_zh": chinese[:4],
    }


def _english_entry(word: str) -> dict[str, Any]:
    url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
    try:
        with httpx.Client(timeout=8.0, headers=UA) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return {}
            data = resp.json()
    except Exception:
        return {}
    if not isinstance(data, list) or not data:
        return {}
    entry = data[0]
    phonetic = entry.get("phonetic") or ""
    audio = ""
    for item in entry.get("phonetics") or []:
        if not phonetic and item.get("text"):
            phonetic = item["text"]
        if not audio and item.get("audio"):
            audio = item["audio"]
    defs: list[str] = []
    for meaning in entry.get("meanings") or []:
        pos = meaning.get("partOfSpeech") or ""
        for definition in meaning.get("definitions") or []:
            text = definition.get("definition") or ""
            if text:
                defs.append(f"{pos}: {text}" if pos else text)
            if len(defs) >= 4:
                break
        if len(defs) >= 4:
            break
    return {"phonetic": phonetic, "audio": audio, "defs": defs}


def _chinese_gloss(word: str) -> list[str]:
    try:
        with httpx.Client(timeout=8.0, headers=UA) as client:
            resp = client.get(
                "https://dict.youdao.com/suggest",
                params={"q": word, "le": "en", "num": 5, "doctype": "json"},
            )
            if resp.status_code != 200:
                return []
            payload = resp.json()
    except Exception:
        return []
    entries = (
        (payload.get("data") or {}).get("entries")
        or (payload.get("result") or {}).get("entries")
        or []
    )
    glosses: list[str] = []
    for item in entries:
        explain = (item.get("explain") or item.get("entry") or "").strip()
        if explain:
            glosses.append(explain)
    return glosses


@lru_cache(maxsize=512)
def translate_en_zh(raw: str) -> str:
    text = " ".join(raw.split())
    if not text:
        return ""
    zh = _youdao_translate(text) or _mymemory_translate(text)
    return zh.strip()


def _youdao_translate(text: str) -> str:
    try:
        with httpx.Client(timeout=10.0, headers=UA) as client:
            resp = client.get(
                "https://fanyi.youdao.com/translate",
                params={"doctype": "json", "type": "EN2ZH_CN", "i": text[:800]},
            )
            if resp.status_code != 200:
                return ""
            data = resp.json()
    except Exception:
        return ""
    parts: list[str] = []
    for block in data.get("translateResult") or []:
        if not isinstance(block, list):
            continue
        for item in block:
            tgt = (item.get("tgt") or "").strip()
            if tgt:
                parts.append(tgt)
    return "".join(parts)


def _mymemory_translate(text: str) -> str:
    try:
        with httpx.Client(timeout=10.0, headers=UA) as client:
            resp = client.get(
                "https://api.mymemory.translated.net/get",
                params={"q": text[:500], "langpair": "en|zh-CN"},
            )
            if resp.status_code != 200:
                return ""
            data = resp.json()
    except Exception:
        return ""
    return str((data.get("responseData") or {}).get("translatedText") or "").strip()
