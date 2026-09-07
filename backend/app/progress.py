from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from . import db
from .store import read_json, read_meta

LOCAL_ZONE = ZoneInfo("Asia/Shanghai")
_WORD_RE = re.compile(r"[a-z']+", re.I)


def owner_key(user: dict[str, Any]) -> str:
    return str(user.get("id") or "lan-local")


def _local_date(value: str | None = None) -> str:
    if value:
        try:
            return db.parse_time(value).astimezone(LOCAL_ZONE).date().isoformat()
        except (TypeError, ValueError):
            pass
    return datetime.now(LOCAL_ZONE).date().isoformat()


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def _word_eval(target: str, draft: str) -> dict[str, int] | None:
    ref = _tokens(target)
    hyp = _tokens(draft)
    if not ref or not hyp:
        return None
    matcher = SequenceMatcher(a=ref, b=hyp, autojunk=False)
    correct = sum(block.size for block in matcher.get_matching_blocks())
    correct = min(int(correct), len(ref))
    return {
        "evaluated_words": len(ref),
        "correct_words": correct,
        "dictation_words": len(hyp),
    }


def _streak(dates: list[str], today: date | None = None) -> tuple[int, int]:
    unique = sorted({date.fromisoformat(value) for value in dates})
    if not unique:
        return 0, 0
    longest = run = 1
    for previous, value in zip(unique, unique[1:]):
        if value == previous + timedelta(days=1):
            run += 1
            longest = max(longest, run)
        else:
            run = 1
    end = today or datetime.now(LOCAL_ZONE).date()
    if unique[-1] not in {end, end - timedelta(days=1)}:
        return 0, longest
    current = 1
    for i in range(len(unique) - 1, 0, -1):
        if unique[i] == unique[i - 1] + timedelta(days=1):
            current += 1
        else:
            break
    return current, longest


def _first_streak_date(dates: list[str], target: int) -> str | None:
    unique = sorted({date.fromisoformat(value) for value in dates})
    run = 0
    for i, value in enumerate(unique):
        run = run + 1 if i and value == unique[i - 1] + timedelta(days=1) else 1
        if run >= target:
            return value.isoformat()
    return None


def _first_word_date(days: list[dict[str, Any]], target: int) -> str | None:
    total = 0
    for item in days:
        total += int(item["dictation_words"])
        if total >= target:
            return str(item["learning_date"])
    return None


def _first_accuracy_date(days: list[dict[str, Any]], target: float) -> str | None:
    for item in days:
        if item["accuracy"] is not None and float(item["accuracy"]) >= target:
            return str(item["learning_date"])
    return None


def _weighted_accuracy(evaluated: int, correct: int) -> float | None:
    if evaluated <= 0:
        return None
    return round(100.0 * correct / evaluated, 1)


def _rebuild_daily(conn: Any, key: str, learning_date: str) -> None:
    row = conn.execute(
        "SELECT COUNT(*) AS units, COALESCE(SUM(evaluated_words),0) AS evaluated, "
        "COALESCE(SUM(correct_words),0) AS correct, COALESCE(SUM(dictation_words),0) AS words, "
        "COALESCE(SUM(audio_seconds),0) AS audio FROM learning_events WHERE owner_key=? AND learning_date=?",
        (key, learning_date),
    ).fetchone()
    units = int(row["units"] or 0)
    if units <= 0:
        conn.execute("DELETE FROM learning_daily WHERE owner_key=? AND learning_date=?", (key, learning_date))
        return
    evaluated = int(row["evaluated"] or 0)
    correct = int(row["correct"] or 0)
    conn.execute(
        "INSERT INTO learning_daily(owner_key,learning_date,evaluated_words,correct_words,dictation_words,audio_seconds,completed_units,accuracy,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(owner_key,learning_date) DO UPDATE SET evaluated_words=excluded.evaluated_words,correct_words=excluded.correct_words,"
        "dictation_words=excluded.dictation_words,audio_seconds=excluded.audio_seconds,completed_units=excluded.completed_units,"
        "accuracy=excluded.accuracy,updated_at=excluded.updated_at",
        (key, learning_date, evaluated, correct, int(row["words"] or 0), float(row["audio"] or 0), units, _weighted_accuracy(evaluated, correct), db.iso()),
    )


def _load_daily(key: str) -> list[dict[str, Any]]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM learning_daily WHERE owner_key=? ORDER BY learning_date ASC",
            (key,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _milestones(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dates = [str(item["learning_date"]) for item in days]
    _, longest = _streak(dates)
    first_date = days[0]["learning_date"] if days else None
    total_words = sum(int(item["dictation_words"]) for item in days)
    checks = [
        ("first_learning", "完成第一次有效听写", bool(days), lambda: first_date),
        ("streak_3", "连续学习 3 天", longest >= 3, lambda: _first_streak_date(dates, 3)),
        ("streak_7", "连续学习 7 天", longest >= 7, lambda: _first_streak_date(dates, 7)),
        ("streak_30", "连续学习 30 天", longest >= 30, lambda: _first_streak_date(dates, 30)),
        ("words_100", "累计听写 100 个单词", total_words >= 100, lambda: _first_word_date(days, 100)),
        ("words_500", "累计听写 500 个单词", total_words >= 500, lambda: _first_word_date(days, 500)),
        ("words_1000", "累计听写 1000 个单词", total_words >= 1000, lambda: _first_word_date(days, 1000)),
        ("accuracy_80", "单日正确率首次达到 80%", _first_accuracy_date(days, 80) is not None, lambda: _first_accuracy_date(days, 80)),
        ("accuracy_90", "单日正确率首次达到 90%", _first_accuracy_date(days, 90) is not None, lambda: _first_accuracy_date(days, 90)),
    ]
    return [{"key": key, "label": label, "achieved": bool(ok), "achieved_at": finder() if ok else None} for key, label, ok, finder in checks]


def _range_stats(days: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = sum(int(item["evaluated_words"]) for item in days)
    correct = sum(int(item["correct_words"]) for item in days)
    words = sum(int(item["dictation_words"]) for item in days)
    audio = sum(float(item["audio_seconds"] or 0) for item in days)
    units = sum(int(item["completed_units"]) for item in days)
    return {
        "evaluated_words": evaluated,
        "correct_words": correct,
        "dictation_words": words,
        "audio_seconds": round(audio, 1),
        "completed_units": units,
        "accuracy": _weighted_accuracy(evaluated, correct),
    }


def progress_for_user(user: dict[str, Any], days: int | None = None) -> dict[str, Any]:
    key = owner_key(user)
    all_days = _load_daily(key)
    dates = [str(item["learning_date"]) for item in all_days]
    today = datetime.now(LOCAL_ZONE).date()
    current, longest = _streak(dates, today)
    window = all_days
    if days is not None:
        cutoff = (today - timedelta(days=days - 1)).isoformat()
        window = [item for item in all_days if str(item["learning_date"]) >= cutoff]
    totals = _range_stats(all_days)
    window_stats = _range_stats(window)
    recent = _range_stats([item for item in all_days if str(item["learning_date"]) >= (today - timedelta(days=6)).isoformat()])
    older = _range_stats([item for item in all_days if (today - timedelta(days=13)).isoformat() <= str(item["learning_date"]) < (today - timedelta(days=6)).isoformat()])
    day_one = len(all_days) == 1
    today_key = today.isoformat()
    today_row = next((item for item in all_days if str(item["learning_date"]) == today_key), None)
    today_stats = _range_stats([today_row] if today_row else [])
    accuracy_delta = None
    if (not day_one) and recent["accuracy"] is not None and older["accuracy"] is not None:
        accuracy_delta = round(float(recent["accuracy"]) - float(older["accuracy"]), 1)
    records = []
    for item in reversed(window):
        evaluated = int(item["evaluated_words"])
        records.append({
            "id": f"{key}:{item['learning_date']}",
            "session_id": "",
            "learning_date": item["learning_date"],
            "language": "en",
            "title": "",
            "source_kind": "file",
            "completed_at": item["learning_date"],
            "duration_seconds": int(round(float(item["audio_seconds"] or 0))),
            "duration_minutes": round(float(item["audio_seconds"] or 0) / 60, 1),
            "sentence_count": int(item["completed_units"]),
            "completed_sentence_count": int(item["completed_units"]),
            "dictation_words": int(item["dictation_words"]),
            "evaluated_words": evaluated,
            "accuracy": item["accuracy"],
            "score": item["accuracy"],
            "completion_ratio": 1 if int(item["completed_units"]) else 0,
            "completion_percent": 100 if int(item["completed_units"]) else 0,
        })
    return {
        "days_learned": len(all_days),
        "day_one": day_one,
        "total_duration_seconds": totals["audio_seconds"],
        "total_duration_minutes": round(totals["audio_seconds"] / 60, 1),
        "total_sentences": totals["completed_units"],
        "total_dictation_words": totals["dictation_words"],
        "current_streak": current,
        "longest_streak": longest,
        "recent_accuracy": recent["accuracy"] if all_days else None,
        "starting_accuracy": all_days[0]["accuracy"] if all_days else None,
        "window_accuracy": window_stats["accuracy"],
        "today": {
            "date": today_key,
            "active": bool(today_row),
            "dictation_words": today_stats["dictation_words"],
            "evaluated_words": today_stats["evaluated_words"],
            "correct_words": today_stats["correct_words"],
            "accuracy": today_stats["accuracy"],
            "completed_units": today_stats["completed_units"],
            "audio_seconds": today_stats["audio_seconds"],
        },
        "comparison": {
            "has_history": (not day_one) and older["evaluated_words"] > 0,
            "previous_count": 7 if older["evaluated_words"] else 0,
            "accuracy_delta": accuracy_delta,
            "sentence_delta": None,
        },
        "records": records,
        "milestones": _milestones(all_days),
    }


def complete_session(user: dict[str, Any], session_id: str, data_root: Path, duration_seconds: int | None = None) -> dict[str, Any]:
    # Kept for the existing /api/progress/complete call. Events are written on real dictation saves, not here.
    _ = session_id, data_root, duration_seconds
    return progress_for_user(user)


def record_new_dictations(
    user: dict[str, Any],
    session_id: str,
    folder: Path,
    previous_drafts: dict[str, str],
    incoming_drafts: dict[str, str],
) -> int:
    """Create Learning Events only for chunks that just became a real dictation."""
    sentences = read_json(folder / "sentences.json", [])
    if not isinstance(sentences, list) or not sentences:
        return 0
    meta = read_meta(folder)
    material = str(meta.get("title") or meta.get("source_url") or "")
    splitter_version = str(meta.get("splitter_version") or "") or None
    key = owner_key(user)
    user_id = None if key == "lan-local" else key
    created = 0
    touched_dates: set[str] = set()
    now = db.iso()
    learning_date = _local_date(now)
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for raw_key, draft in incoming_drafts.items():
            try:
                idx = int(raw_key)
            except (TypeError, ValueError):
                continue
            if idx < 0 or idx >= len(sentences):
                continue
            if not str(draft or "").strip():
                continue
            if str(previous_drafts.get(str(idx)) or previous_drafts.get(raw_key) or "").strip():
                continue
            sentence = sentences[idx] if isinstance(sentences[idx], dict) else {}
            stats = _word_eval(str(sentence.get("text") or ""), str(draft))
            if not stats:
                continue
            unit_key = f"chunk:{idx}"
            parent_id = str(sentence["parent_id"]) if sentence.get("parent_id") is not None else None
            start = float(sentence.get("start") or 0)
            end = float(sentence.get("end") or 0)
            audio_seconds = max(0.0, end - start)
            cur = conn.execute(
                "INSERT OR IGNORE INTO learning_events(id,owner_key,user_id,session_id,unit_key,parent_id,created_at,learning_date,language,material,evaluated_words,correct_words,dictation_words,audio_seconds,splitter_version) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uuid4().hex,
                    key,
                    user_id,
                    session_id,
                    unit_key,
                    parent_id,
                    now,
                    learning_date,
                    "en",
                    material,
                    stats["evaluated_words"],
                    stats["correct_words"],
                    stats["dictation_words"],
                    audio_seconds,
                    splitter_version,
                ),
            )
            if int(cur.rowcount or 0) > 0:
                created += 1
                touched_dates.add(learning_date)
        for day in touched_dates:
            _rebuild_daily(conn, key, day)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return created
