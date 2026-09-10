from __future__ import annotations

import logging
import shutil
import sqlite3
import threading
import time
import uuid
from typing import Any

from . import db
from .ingest import (
    classify_ingest_error,
    log_url_import_stage,
    public_url_import_error,
    read_import_title,
    url_host_family,
    url_preview,
)
from .sentences import parse_srt, parse_vtt

logger = logging.getLogger("enprato")

STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_READY = "ready"
STATUS_FAILED = "failed"

STAGE_QUEUED = "queued"
STAGE_METADATA = "metadata"
STAGE_DOWNLOADING = "downloading"
STAGE_AUDIO = "processing_audio"
STAGE_TRANSCRIBING = "transcribing"
STAGE_FINALIZING = "finalizing"
STAGE_READY = "ready"
STAGE_FAILED = "failed"

STAGE_MESSAGES = {
    STAGE_QUEUED: "正在排队…",
    STAGE_METADATA: "正在读取视频信息…",
    STAGE_DOWNLOADING: "正在准备视频…",
    STAGE_AUDIO: "正在处理音频…",
    STAGE_TRANSCRIBING: "正在识别语音…",
    STAGE_FINALIZING: "正在生成听写内容…",
    STAGE_READY: "准备完成",
    STAGE_FAILED: "导入失败",
}

ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_PROCESSING)
# Wall clock from claim, not queue wait. 40 分钟片 ASR≈12 分钟 + ingest。
JOB_MAX_WALL_SEC = 45 * 60
INTERRUPTED_MESSAGE = "导入被中断，请重新提交链接。"

_worker_lock = threading.Lock()
_wake = threading.Event()
_parked = threading.Event()
_started = False
_running = False
_asr_lock = threading.Lock()
accept_lock = threading.Lock()
_thread: threading.Thread | None = None
_parked.set()


def _owner_id(user: dict[str, Any] | str | None) -> str:
    if isinstance(user, str):
        return user or "lan-local"
    if not user:
        return "lan-local"
    return str(user.get("id") or "lan-local")


def create_job(user_id: str, session_id: str, url: str) -> dict[str, Any]:
    db.migrate()
    ensure_worker()
    now = db.iso()
    job = {
        "job_id": uuid.uuid4().hex[:16],
        "user_id": user_id or "lan-local",
        "session_id": session_id,
        "source_url": url,
        "url_key": url_preview(url) or url,
        "status": STATUS_QUEUED,
        "stage": STAGE_QUEUED,
        "error_kind": None,
        "error_message": None,
        "created_at": now,
        "updated_at": now,
    }
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO import_jobs(job_id,user_id,session_id,source_url,url_key,status,stage,error_kind,error_message,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                job["job_id"],
                job["user_id"],
                job["session_id"],
                job["source_url"],
                job["url_key"],
                job["status"],
                job["stage"],
                job["error_kind"],
                job["error_message"],
                job["created_at"],
                job["updated_at"],
            ),
        )
    finally:
        conn.close()
    logger.info(
        "url_import_job job_id=%s session_id=%s stage=%s status=%s",
        job["job_id"],
        session_id,
        STAGE_QUEUED,
        STATUS_QUEUED,
    )
    kick()
    return job


def find_active_job(user_id: str, url: str | None = None) -> dict[str, Any] | None:
    conn = db.connect()
    try:
        if url:
            key = url_preview(url) or url
            row = conn.execute(
                "SELECT * FROM import_jobs WHERE user_id=? AND url_key=? AND status IN (?,?) ORDER BY created_at DESC LIMIT 1",
                (user_id, key, STATUS_QUEUED, STATUS_PROCESSING),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM import_jobs WHERE user_id=? AND status IN (?,?) ORDER BY created_at DESC LIMIT 1",
                (user_id, STATUS_QUEUED, STATUS_PROCESSING),
            ).fetchone()
        return dict(row) if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def get_job(job_id: str) -> dict[str, Any] | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM import_jobs WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    status = str(job.get("status") or "")
    stage = str(job.get("stage") or "")
    payload: dict[str, Any] = {
        "job_id": job.get("job_id"),
        "session_id": job.get("session_id"),
        "status": status,
        "stage": stage,
        "message": STAGE_MESSAGES.get(stage, STAGE_MESSAGES[STAGE_QUEUED]),
    }
    if status == STATUS_FAILED:
        payload["error_kind"] = job.get("error_kind") or "import_failed"
        payload["message"] = job.get("error_message") or STAGE_MESSAGES[STAGE_FAILED]
        payload["error"] = payload["message"]
    if status == STATUS_READY:
        payload["message"] = STAGE_MESSAGES[STAGE_READY]
    return payload


def update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = db.iso()
    assignments = ", ".join(f"{key}=?" for key in fields)
    conn = db.connect()
    try:
        conn.execute(
            f"UPDATE import_jobs SET {assignments} WHERE job_id=?",
            [*fields.values(), job_id],
        )
    finally:
        conn.close()


def set_stage(job_id: str, session_id: str, stage: str, started: float, host: str = "unknown") -> None:
    status = STATUS_READY if stage == STAGE_READY else STATUS_FAILED if stage == STAGE_FAILED else STATUS_PROCESSING
    if stage == STAGE_QUEUED:
        status = STATUS_QUEUED
    update_job(job_id, status=status, stage=stage)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    log_url_import_stage(host, stage, elapsed_ms=elapsed_ms, job_id=job_id, session_id=session_id)
    logger.info(
        "url_import_job job_id=%s session_id=%s stage=%s elapsed_ms=%s",
        job_id,
        session_id,
        stage,
        elapsed_ms,
    )


def claim_next() -> dict[str, Any] | None:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM import_jobs WHERE status=? ORDER BY created_at ASC LIMIT 1",
                (STATUS_QUEUED,),
            ).fetchone()
        except sqlite3.OperationalError:
            conn.execute("ROLLBACK")
            return None
        if not row:
            conn.execute("COMMIT")
            return None
        job = dict(row)
        now = db.iso()
        conn.execute(
            "UPDATE import_jobs SET status=?, stage=?, updated_at=? WHERE job_id=? AND status=?",
            (STATUS_PROCESSING, STAGE_METADATA, now, job["job_id"], STATUS_QUEUED),
        )
        conn.execute("COMMIT")
        job["status"] = STATUS_PROCESSING
        job["stage"] = STAGE_METADATA
        job["updated_at"] = now
        return job
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def has_active_jobs() -> bool:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM import_jobs WHERE status IN (?,?) LIMIT 1",
            (STATUS_QUEUED, STATUS_PROCESSING),
        ).fetchone()
        return bool(row)
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def recover_stale_jobs() -> int:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM import_jobs WHERE status IN (?,?)",
            (STATUS_QUEUED, STATUS_PROCESSING),
        ).fetchall()
        jobs = [dict(row) for row in rows]
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()
    count = 0
    for job in jobs:
        fail_job(job, "interrupted", INTERRUPTED_MESSAGE)
        count += 1
    if count:
        logger.info("url_import_job recovered_stale count=%s", count)
    return count


def fail_job(job: dict[str, Any], error_kind: str, message: str) -> None:
    job_id = str(job.get("job_id") or "")
    session_id = str(job.get("session_id") or "")
    update_job(
        job_id,
        status=STATUS_FAILED,
        stage=STAGE_FAILED,
        error_kind=error_kind,
        error_message=message,
    )
    logger.info(
        "url_import_job job_id=%s session_id=%s stage=failed error_kind=%s",
        job_id,
        session_id,
        error_kind,
    )
    if session_id:
        from . import main as app_main

        shutil.rmtree(app_main.DATA / session_id, ignore_errors=True)


def _cues_from_text(raw: str) -> list[dict[str, Any]]:
    if raw.lstrip().startswith("WEBVTT"):
        return parse_vtt(raw)
    return parse_srt(raw)


def run_job(job: dict[str, Any]) -> None:
    from . import main as app_main

    job_id = str(job["job_id"])
    session_id = str(job["session_id"])
    url = str(job["source_url"])
    host = url_host_family(url)
    folder = app_main.DATA / session_id
    started = time.monotonic()
    claimed_at = time.monotonic()

    def stage(name: str) -> None:
        set_stage(job_id, session_id, name, started, host=host)

    def timed_out() -> bool:
        return (time.monotonic() - claimed_at) > JOB_MAX_WALL_SEC

    try:
        stage(STAGE_METADATA)
        if timed_out():
            fail_job(job, "import_timeout", public_url_import_error("导入时间过长"))
            return

        def on_stage(name: str) -> None:
            if name in {STAGE_METADATA, STAGE_DOWNLOADING, STAGE_AUDIO}:
                stage(name)

        stage(STAGE_DOWNLOADING)
        try:
            media, audio, caption_text = app_main.ingest_url(
                url, folder, on_stage=on_stage, job_id=job_id, session_id=session_id
            )
        except TypeError as exc:
            if "on_stage" not in str(exc) and "unexpected keyword" not in str(exc):
                raise
            media, audio, caption_text = app_main.ingest_url(url, folder)
        stage(STAGE_AUDIO)
        sentences: list[dict[str, Any]] = []
        t_split0 = time.monotonic()
        if caption_text:
            sentences = _cues_from_text(caption_text)
            log_url_import_stage(
                host,
                "T_split",
                elapsed_ms=int((time.monotonic() - t_split0) * 1000),
                job_id=job_id,
                session_id=session_id,
                captions="1",
            )
        if not sentences:
            if timed_out():
                fail_job(job, "import_timeout", public_url_import_error("导入时间过长"))
                return
            stage(STAGE_TRANSCRIBING)
            asr_path = folder / "playback.m4a"
            if not asr_path.is_file() or asr_path.stat().st_size < 200:
                asr_path = audio
            from .dashscope_asr import transcribe_url_import

            with _asr_lock:
                sentences = transcribe_url_import(
                    asr_path,
                    folder=folder,
                    job_id=job_id,
                    session_id=session_id,
                    host=host,
                    whisper_fn=app_main.transcribe_sentences,
                    extract_wav_fn=app_main.extract_wav,
                )
        if not sentences:
            fail_job(job, "asr_empty", "无法从视频中分出句子，请补一份英文字幕文件")
            return
        stage(STAGE_FINALIZING)
        title = read_import_title(folder) or url
        finish_audio = audio
        playback = folder / "playback.m4a"
        if (not finish_audio.is_file() or finish_audio.stat().st_size < 200) and playback.is_file():
            finish_audio = playback
        app_main._finish_session(
            folder,
            session_id,
            finish_audio,
            sentences,
            title=title,
            source_url=url,
            source_kind="url",
            host=host,
        )
        update_job(job_id, status=STATUS_READY, stage=STAGE_READY, error_kind=None, error_message=None)
        ready_ms = int((time.monotonic() - started) * 1000)
        log_url_import_stage(
            host,
            "T_session_ready",
            elapsed_ms=ready_ms,
            job_id=job_id,
            session_id=session_id,
        )
        log_url_import_stage(
            host,
            STAGE_READY,
            elapsed_ms=ready_ms,
            job_id=job_id,
            session_id=session_id,
        )
        logger.info(
            "url_import_job job_id=%s session_id=%s stage=ready elapsed_ms=%s",
            job_id,
            session_id,
            ready_ms,
        )
        try:
            from .bilibili_media import kick_bilibili_media

            kick_bilibili_media(
                url=url,
                folder=folder,
                session_id=session_id,
                job_id=job_id,
                host=host,
                started=started,
            )
        except Exception:
            logger.exception(
                "url_import_job media_kick_failed job_id=%s session_id=%s",
                job_id,
                session_id,
            )
    except Exception as exc:
        detail = str(getattr(exc, "detail", "") or exc)
        kind = classify_ingest_error(detail)
        message = public_url_import_error(detail)
        if "无法从视频中分出句子" in detail:
            message = "无法从视频中分出句子，请补一份英文字幕文件"
            kind = "asr_empty"
        fail_job(job, kind, message)
        log_url_import_stage(
            host,
            "job_failed",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            job_id=job_id,
            session_id=session_id,
            error_kind=kind,
        )


def _loop() -> None:
    global _running
    while True:
        _parked.set()
        _wake.wait(timeout=1.0)
        _wake.clear()
        _parked.clear()
        try:
            while True:
                try:
                    job = claim_next()
                except Exception:
                    logger.exception("url_import_job claim_next failed")
                    break
                if not job:
                    break
                _running = True
                try:
                    logger.info(
                        "url_import_job job_id=%s session_id=%s stage=%s",
                        job.get("job_id"),
                        job.get("session_id"),
                        STAGE_METADATA,
                    )
                    run_job(job)
                except Exception:
                    logger.exception("url_import_job worker crashed job_id=%s", job.get("job_id"))
                    try:
                        fail_job(job, "import_failed", public_url_import_error("导入失败"))
                    except Exception:
                        logger.exception("url_import_job fail_job crashed")
                finally:
                    _running = False
        finally:
            _parked.set()


def kick() -> None:
    _wake.set()


def ensure_worker() -> None:
    global _started, _thread
    with _worker_lock:
        if _thread is not None and _thread.is_alive():
            return
        db.migrate()
        if not _started:
            recover_stale_jobs()
            _started = True
        _thread = threading.Thread(target=_loop, name="url-import-worker", daemon=True)
        _thread.start()
        kick()


def wait_idle(timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        parked = _parked.wait(timeout=0.2)
        if parked and not _running and not has_active_jobs():
            time.sleep(0.15)
            if _parked.is_set() and not _running and not has_active_jobs():
                return
        time.sleep(0.05)
    raise TimeoutError("url import worker still busy")
