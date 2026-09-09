from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from .bilibili import prepare_bilibili_video, source_mp4_complete
from .ingest import is_bilibili_url, log_url_import_stage

logger = logging.getLogger("enprato")

MEDIA_NAME = "media.json"
VALID_STATUS = {"preparing", "ready", "failed", "audio"}

_guard = threading.Lock()
_active: set[str] = set()
_threads: dict[str, threading.Thread] = {}


def media_state_path(folder: Path) -> Path:
    return folder / MEDIA_NAME


def read_media_state(folder: Path) -> str:
    path = media_state_path(folder)
    if not path.is_file():
        if (folder / "source.mp4.tmp").is_file():
            return "preparing"
        return "audio"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return "audio"
    status = str(payload.get("status") or "").strip().lower()
    if status not in VALID_STATUS:
        return "audio"
    if (folder / "source.mp4.tmp").is_file() and status != "failed":
        return "preparing"
    return status


def write_media_state(folder: Path, status: str, error_kind: str | None = None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"status": status, "updated_at": time.time()}
    if error_kind:
        payload["error_kind"] = str(error_kind)
    tmp = folder / "media.json.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(media_state_path(folder))


def media_payload(folder: Path, session_id: str) -> dict[str, Any]:
    has_video = source_mp4_complete(folder)
    if has_video:
        status = "ready"
    else:
        status = read_media_state(folder)
        if status == "ready":
            status = "audio"
    return {"has_video": has_video, "status": status, "session_id": session_id}


def wait_media(session_id: str, timeout: float = 8.0) -> None:
    thread = _threads.get(session_id)
    if thread is None:
        return
    thread.join(timeout)


def kick_bilibili_media(
    *,
    url: str,
    folder: Path,
    session_id: str,
    job_id: str,
    host: str = "unknown",
    started: float | None = None,
) -> None:
    if not is_bilibili_url(url):
        return
    clock = started if started is not None else time.monotonic()
    with _guard:
        if session_id in _active:
            return
        if source_mp4_complete(folder):
            write_media_state(folder, "ready")
            return
        existing = read_media_state(folder)
        if existing in {"ready", "failed"}:
            return
        if existing == "preparing" and session_id in _active:
            return
        _active.add(session_id)
        write_media_state(folder, "preparing")
        thread = threading.Thread(
            target=_run_prepare,
            kwargs={
                "url": url,
                "folder": folder,
                "session_id": session_id,
                "job_id": job_id,
                "host": host,
                "clock": clock,
            },
            name=f"bili-media-{session_id}",
            daemon=True,
        )
        _threads[session_id] = thread
        thread.start()


def _run_prepare(
    *,
    url: str,
    folder: Path,
    session_id: str,
    job_id: str,
    host: str,
    clock: float,
) -> None:
    try:
        log_url_import_stage(
            host,
            "T_video_task_start",
            elapsed_ms=int((time.monotonic() - clock) * 1000),
            job_id=job_id,
            session_id=session_id,
        )
        result = prepare_bilibili_video(url, folder)
        log_url_import_stage(
            host,
            "T_video_download",
            elapsed_ms=int(result.get("t_video_download_ms") or 0),
            job_id=job_id,
            session_id=session_id,
        )
        log_url_import_stage(
            host,
            "T_video_merge",
            elapsed_ms=int(result.get("t_video_merge_ms") or 0),
            job_id=job_id,
            session_id=session_id,
        )
        log_url_import_stage(
            host,
            "T_video_ready",
            elapsed_ms=int((time.monotonic() - clock) * 1000),
            job_id=job_id,
            session_id=session_id,
            skipped="1" if result.get("skipped") else "0",
        )
        write_media_state(folder, "ready")
    except Exception as exc:
        kind = getattr(exc, "kind", None) or type(exc).__name__
        logger.info(
            "media_prepare_failed job_id=%s session_id=%s error_kind=%s elapsed_ms=%s",
            job_id,
            session_id,
            kind,
            int((time.monotonic() - clock) * 1000),
        )
        log_url_import_stage(
            host,
            "media_prepare_failed",
            elapsed_ms=int((time.monotonic() - clock) * 1000),
            job_id=job_id,
            session_id=session_id,
            error_kind=str(kind),
        )
        try:
            write_media_state(folder, "failed", error_kind=str(kind))
        except Exception:
            logger.exception("media_prepare_failed write_state session_id=%s", session_id)
    finally:
        with _guard:
            _active.discard(session_id)
