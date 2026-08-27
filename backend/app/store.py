from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ingest import (
    adopt_downloaded_thumbnail,
    fetch_bilibili_thumbnail,
    fetch_media_title,
    fetch_url_thumbnail,
    find_session_media,
    is_garbled_title,
    parse_bilibili_bvid,
    fetch_bilibili_view,
)
from .media import extract_video_thumbnail, stream_codec

PRACTICE_PHASES = {"listen", "dictate", "check", "shadow", "result"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_meta(folder: Path) -> dict[str, Any]:
    meta = read_json(folder / "meta.json", {})
    return meta if isinstance(meta, dict) else {}


def merge_draft_maps(existing: dict[str, str], incoming: dict[str, str] | None) -> dict[str, str]:
    """合并听写稿：保留更长/非空内容，避免电脑与手机互相覆盖丢字。"""
    out = {str(k): str(v) for k, v in existing.items()}
    if not incoming:
        return out
    for key, value in incoming.items():
        key = str(key)
        text = str(value or "")
        prev = str(out.get(key) or "")
        if text.strip() or not prev.strip():
            if not prev.strip() or len(text.strip()) >= len(prev.strip()):
                out[key] = text
    return out


def apply_draft_snapshot(existing: dict[str, str], incoming: dict[str, str] | None) -> dict[str, str]:
    """整页听写稿快照：incoming 覆盖 0..max 句，允许用空串清空；保留更后面的句。"""
    if incoming is None:
        return {str(k): str(v) for k, v in existing.items()}
    snap = {str(k): str(v or "") for k, v in incoming.items()}
    max_i = -1
    for key in snap:
        try:
            max_i = max(max_i, int(key))
        except (TypeError, ValueError):
            continue
    out: dict[str, str] = {}
    if max_i >= 0:
        for i in range(max_i + 1):
            out[str(i)] = snap.get(str(i), "")
    for key, value in existing.items():
        key = str(key)
        try:
            i = int(key)
        except (TypeError, ValueError):
            continue
        if i > max_i and str(value or "").strip():
            out[key] = str(value)
    return out


def collapse_identical_drafts(drafts: dict[str, str]) -> dict[str, str]:
    """相同听写内容只保留最早一句，其余清空。"""
    out = {str(k): str(v) for k, v in drafts.items()}
    keys = sorted(int(k) for k in out if str(k).isdigit())
    seen: dict[str, int] = {}
    for k in keys:
        text = out[str(k)].strip()
        if not text:
            continue
        if text in seen:
            out[str(k)] = ""
        else:
            seen[text] = k
    return out


def write_meta(folder: Path, **fields: Any) -> dict[str, Any]:
    meta = read_meta(folder)
    for key, value in fields.items():
        if value is not None:
            meta[key] = value
    if "created_at" not in meta:
        meta["created_at"] = now_iso()
    meta["updated_at"] = now_iso()
    write_json(folder / "meta.json", meta)
    return meta


def _drafts_int(raw: Any) -> dict[int, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[int, str] = {}
    for key, value in raw.items():
        try:
            out[int(key)] = str(value)
        except (TypeError, ValueError):
            continue
    return out


_MIN_THUMB_BYTES = 8000


def _thumb_good(thumb: Path) -> bool:
    return thumb.is_file() and thumb.stat().st_size >= _MIN_THUMB_BYTES


def ensure_session_title(folder: Path) -> None:
    meta = read_meta(folder)
    title = str(meta.get("title") or "").strip()
    url = str(meta.get("source_url") or "").strip()
    if not url.startswith("http"):
        return
    stale = is_garbled_title(title)
    if not stale:
        return
    bvid = parse_bilibili_bvid(url)
    if bvid:
        info = fetch_bilibili_view(bvid)
        if info and info.get("title"):
            fields: dict[str, Any] = {"title": str(info["title"]).strip()[:80]}
            if info.get("pic"):
                fields["cover_url"] = str(info["pic"]).strip()
            write_meta(folder, **fields)
            return
    new_title = fetch_media_title(url)
    if new_title and new_title.strip() and new_title.strip() != title:
        write_meta(folder, title=new_title.strip()[:80])


def ensure_session_thumbnail(folder: Path) -> bool:
    thumb = folder / "thumb.jpg"
    meta = read_meta(folder)
    source_url = str(meta.get("source_url") or "").strip()
    cur_size = thumb.stat().st_size if thumb.is_file() else 0
    ensure_session_title(folder)
    meta = read_meta(folder)
    source_url = str(meta.get("source_url") or "").strip()
    if "bilibili.com" in source_url.lower():
        try:
            if fetch_bilibili_thumbnail(source_url, thumb) and thumb.stat().st_size > 2000:
                return True
        except Exception:
            pass
    if source_url.startswith("http") and cur_size < 50000:
        try:
            if fetch_url_thumbnail(source_url, thumb) and _thumb_good(thumb):
                return True
        except Exception:
            pass
    if _thumb_good(thumb):
        return True
    if adopt_downloaded_thumbnail(folder) and _thumb_good(thumb):
        return True
    media = find_session_media(folder)
    if media is None or not session_has_video(folder):
        return thumb.is_file() and thumb.stat().st_size > 2000
    tmp = folder / "_thumb_ffmpeg.jpg"
    try:
        extract_video_thumbnail(media, tmp)
        if tmp.is_file():
            cur = thumb.stat().st_size if thumb.is_file() else 0
            if tmp.stat().st_size > cur:
                tmp.replace(thumb)
            else:
                tmp.unlink(missing_ok=True)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return thumb.is_file() and thumb.stat().st_size > 2000


def session_has_video(folder: Path) -> bool:
    media = find_session_media(folder)
    if not media:
        return False
    return bool(stream_codec(media, "v"))


def session_detail(folder: Path, session_id: str) -> dict[str, Any] | None:
    sentences_path = folder / "sentences.json"
    if not folder.is_dir() or not sentences_path.is_file():
        return None
    sentences = read_json(sentences_path, [])
    if not isinstance(sentences, list) or not sentences:
        return None
    meta = read_meta(folder)
    drafts = _drafts_int(meta.get("drafts"))
    drafts = {
        int(k): v
        for k, v in collapse_identical_drafts({str(k): v for k, v in drafts.items()}).items()
        if str(k).isdigit()
    }
    phase = meta.get("phase") if meta.get("phase") in PRACTICE_PHASES else "listen"
    title = str(meta.get("title") or sentences[0].get("text") or session_id).strip()
    cover_url = str(meta.get("cover_url") or "").strip()
    index = int(meta.get("index") or 0)
    index = max(0, min(index, len(sentences) - 1))
    return {
        "session_id": session_id,
        "title": title[:80],
        "source_url": str(meta.get("source_url") or ""),
        "source_kind": str(meta.get("source_kind") or "file"),
        "created_at": str(meta.get("created_at") or ""),
        "updated_at": str(meta.get("updated_at") or ""),
        "phase": phase,
        "index": index,
        "drafts": {str(k): v for k, v in drafts.items()},
        "highlights": meta.get("highlights") if isinstance(meta.get("highlights"), list) else [],
        "score": meta.get("score"),
        "orientation": meta.get("orientation") or "landscape",
        "sentences": sentences,
        "duration": sentences[-1].get("end") or 0,
        "count": len(sentences),
        "done": sum(1 for text in drafts.values() if str(text).strip()),
        "video_url": f"/api/session/{session_id}/video",
        "audio_url": f"/api/session/{session_id}/audio",
        "has_video": session_has_video(folder),
        "thumbnail_url": f"/api/session/{session_id}/thumb",
        "cover_url": cover_url,
    }


def session_summary(detail: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": detail["session_id"],
        "title": detail["title"],
        "source_url": detail["source_url"],
        "source_kind": detail["source_kind"],
        "updated_at": detail["updated_at"],
        "phase": detail["phase"],
        "index": detail["index"],
        "count": detail["count"],
        "done": detail["done"],
        "duration": detail["duration"],
        "has_video": detail.get("has_video"),
        "thumbnail_url": detail.get("thumbnail_url"),
        "cover_url": detail.get("cover_url") or "",
    }


def list_sessions(root: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not root.is_dir():
        return items
    for folder in root.iterdir():
        if not folder.is_dir() or folder.name.startswith("_"):
            continue
        try:
            refresh_session_catalog(folder)
        except Exception:
            pass
        detail = session_detail(folder, folder.name)
        if detail:
            items.append(session_summary(detail))
    items.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return items


def refresh_session_catalog(folder: Path) -> None:
    meta = read_meta(folder)
    title = str(meta.get("title") or "")
    thumb = folder / "thumb.jpg"
    url = str(meta.get("source_url") or "")
    thumb_size = thumb.stat().st_size if thumb.is_file() else 0
    if is_garbled_title(title) or thumb_size < 8000:
        ensure_session_title(folder)
        ensure_session_thumbnail(folder)
    elif url.startswith("http") and "bilibili.com" in url.lower() and thumb_size < 40000:
        ensure_session_thumbnail(folder)


def find_session_id_by_url(root: Path, url: str) -> str | None:
    target = url.strip()
    if not target or not root.is_dir():
        return None
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        meta = read_meta(folder)
        if str(meta.get("source_url") or "").strip() == target and (folder / "sentences.json").is_file():
            return folder.name
    return None
