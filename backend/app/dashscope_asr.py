from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
import httpx

from .aliyun_asr import (
    aliyun_transcription_to_sentences,
    transcription_has_sentence_timestamps,
    transcription_has_word_timestamps,
)

logger = logging.getLogger("enprato")

MODEL = "paraformer-v2"
UPLOADS_URL = "https://dashscope.aliyuncs.com/api/v1/uploads"
SUBMIT_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription"
TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
POLL_SEC = 2.0
MAX_WAIT_SEC = 20 * 60
UPLOAD_TIMEOUT_SEC = 300.0


class AliyunASRError(RuntimeError):
    def __init__(self, kind: str, message: str = ""):
        self.kind = kind
        super().__init__(message or kind)


@dataclass
class AliyunASRResult:
    sentences: list[dict[str, Any]]
    timings: dict[str, float] = field(default_factory=dict)
    has_word_ts: bool = False
    has_sentence_ts: bool = False
    backend: str = "aliyun"


def _api_key() -> str:
    return (os.environ.get("DASHSCOPE_API_KEY") or "").strip()


def language_hints(explicit: list[str] | None = None) -> list[str]:
    if explicit:
        return [str(item).strip() for item in explicit if str(item).strip()]
    raw = (os.environ.get("DASHSCOPE_LANGUAGE_HINTS") or "").strip()
    if not raw or raw.lower() == "auto":
        return ["zh", "en"]
    return [part.strip() for part in raw.split(",") if part.strip()]


def asr_backend_name() -> str:
    return (os.environ.get("ENPRATO_ASR_BACKEND") or "auto").strip().lower() or "auto"


def aliyun_enabled() -> bool:
    mode = asr_backend_name()
    if mode == "whisper":
        return False
    if mode == "aliyun":
        return bool(_api_key())
    return bool(_api_key())


class AliyunASRBackend:
    """DashScope file transcription. Temporary oss:// upload is an implementation detail."""

    def __init__(self, model: str = MODEL):
        self.model = model

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        key = _api_key()
        if not key:
            raise AliyunASRError("aliyun_unconfigured")
        headers = {"Authorization": f"Bearer {key}"}
        if extra:
            headers.update(extra)
        return headers

    def upload_audio(self, path: Path) -> tuple[str, float, float]:
        t0 = time.monotonic()
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(
                UPLOADS_URL,
                headers=self._headers({"Content-Type": "application/json"}),
                params={"action": "getPolicy", "model": self.model},
            )
        if resp.status_code != 200:
            raise AliyunASRError("aliyun_upload_policy", f"http={resp.status_code}")
        policy = (resp.json() or {}).get("data") or {}
        t_policy = time.monotonic() - t0
        if not policy.get("upload_dir") or not policy.get("upload_host"):
            raise AliyunASRError("aliyun_upload_policy", "policy missing")
        oss_key = f"{policy['upload_dir']}/{path.name}"
        t1 = time.monotonic()
        with path.open("rb") as handle:
            files = {
                "OSSAccessKeyId": (None, policy["oss_access_key_id"]),
                "Signature": (None, policy["signature"]),
                "policy": (None, policy["policy"]),
                "x-oss-object-acl": (None, policy["x_oss_object_acl"]),
                "x-oss-forbid-overwrite": (None, policy["x_oss_forbid_overwrite"]),
                "key": (None, oss_key),
                "success_action_status": (None, "200"),
                "file": (path.name, handle),
            }
            with httpx.Client(timeout=UPLOAD_TIMEOUT_SEC) as client:
                up = client.post(policy["upload_host"], files=files)
        if up.status_code != 200:
            raise AliyunASRError("aliyun_upload", f"http={up.status_code}")
        return f"oss://{oss_key}", t_policy, time.monotonic() - t1

    def submit(self, oss_url: str, hints: list[str]) -> tuple[str, float]:
        body = {
            "model": self.model,
            "input": {"file_urls": [oss_url]},
            "parameters": {
                "channel_id": [0],
                "timestamp_alignment_enabled": True,
                "language_hints": hints,
            },
        }
        t0 = time.monotonic()
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                SUBMIT_URL,
                headers=self._headers(
                    {
                        "Content-Type": "application/json",
                        "X-DashScope-Async": "enable",
                        "X-DashScope-OssResourceResolve": "enable",
                    }
                ),
                json=body,
            )
        elapsed = time.monotonic() - t0
        if resp.status_code != 200:
            raise AliyunASRError("aliyun_submit", f"http={resp.status_code}")
        task_id = str(((resp.json() or {}).get("output") or {}).get("task_id") or "")
        if not task_id:
            raise AliyunASRError("aliyun_submit", "missing task_id")
        return task_id, elapsed

    def poll(self, task_id: str) -> tuple[dict[str, Any], float, float]:
        t0 = time.monotonic()
        first_running: float | None = None
        last_status = ""
        while True:
            elapsed = time.monotonic() - t0
            if elapsed > MAX_WAIT_SEC:
                raise AliyunASRError("aliyun_timeout", f"last_status={last_status}")
            payload = self._query(task_id)
            output = payload.get("output") or {}
            status = str(output.get("task_status") or "")
            last_status = status
            if status == "RUNNING" and first_running is None:
                first_running = elapsed
            if status == "SUCCEEDED":
                results = output.get("results") or []
                if results and str((results[0] or {}).get("subtask_status") or "") == "FAILED":
                    code = str((results[0] or {}).get("code") or "subtask_failed")
                    raise AliyunASRError("aliyun_failed", code)
                queue_s = first_running if first_running is not None else elapsed
                running_s = elapsed - first_running if first_running is not None else elapsed
                return payload, queue_s, running_s
            if status not in {"PENDING", "RUNNING", ""}:
                raise AliyunASRError("aliyun_failed", status)
            time.sleep(POLL_SEC)

    def _query(self, task_id: str) -> dict[str, Any]:
        url = TASK_URL.format(task_id=task_id)
        headers = self._headers({"Content-Type": "application/json"})
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 405:
                resp = client.post(url, headers=headers)
        if resp.status_code != 200:
            raise AliyunASRError("aliyun_poll", f"http={resp.status_code}")
        return resp.json() or {}

    def fetch(self, transcription_url: str) -> tuple[dict[str, Any], float]:
        t0 = time.monotonic()
        with httpx.Client(timeout=60.0) as client:
            resp = client.get(transcription_url)
        if resp.status_code != 200:
            raise AliyunASRError("aliyun_fetch", f"http={resp.status_code}")
        return resp.json(), time.monotonic() - t0

    def adapt(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return aliyun_transcription_to_sentences(payload)

    def transcribe(self, path: Path, hints: list[str] | None = None) -> AliyunASRResult:
        if not path.is_file() or path.stat().st_size < 200:
            raise AliyunASRError("aliyun_audio_missing")
        resolved_hints = language_hints(hints)
        oss_url, t_policy, t_upload = self.upload_audio(path)
        task_id, t_submit = self.submit(oss_url, resolved_hints)
        polled, t_queue, t_asr = self.poll(task_id)
        output = polled.get("output") or {}
        results = output.get("results") or []
        trans_url = str((results[0] or {}).get("transcription_url") or "") if results else ""
        if not trans_url:
            raise AliyunASRError("aliyun_fetch", "missing transcription_url")
        payload, t_fetch = self.fetch(trans_url)
        t_parse0 = time.monotonic()
        sentences = self.adapt(payload)
        t_parse = time.monotonic() - t_parse0
        timings = {
            "T_asr_upload": round(t_policy + t_upload, 3),
            "T_asr_submit": round(t_submit, 3),
            "T_asr_queue": round(t_queue, 3),
            "T_asr": round(t_asr, 3),
            "T_asr_fetch": round(t_fetch, 3),
            "T_parse": round(t_parse, 3),
        }
        return AliyunASRResult(
            sentences=sentences,
            timings=timings,
            has_word_ts=transcription_has_word_timestamps(payload),
            has_sentence_ts=transcription_has_sentence_timestamps(payload),
        )


def _log_timings(host: str, job_id: str, session_id: str, timings: dict[str, float], **fields: object) -> None:
    from .ingest import log_url_import_stage

    for key, value in timings.items():
        log_url_import_stage(
            host,
            key,
            elapsed_ms=int(float(value) * 1000),
            job_id=job_id,
            session_id=session_id,
            **{k: v for k, v in fields.items() if v is not None},
        )


def transcribe_url_import(
    audio_path: Path,
    *,
    folder: Path,
    job_id: str,
    session_id: str,
    host: str,
    whisper_fn: Callable[[Path], list[dict[str, Any]]],
    extract_wav_fn: Callable[[Path, Path], None] | None = None,
    hints: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Aliyun first, existing Whisper fallback. Same job, no extra quota."""
    if aliyun_enabled():
        try:
            result = AliyunASRBackend().transcribe(audio_path, hints=hints)
            if result.sentences:
                _log_timings(
                    host,
                    job_id,
                    session_id,
                    result.timings,
                    asr_backend="aliyun",
                    word_ts="1" if result.has_word_ts else "0",
                    sentence_ts="1" if result.has_sentence_ts else "0",
                )
                return result.sentences
            raise AliyunASRError("asr_empty")
        except Exception as exc:
            kind = getattr(exc, "kind", None) or type(exc).__name__
            logger.info(
                "url_import_job job_id=%s session_id=%s asr_backend=aliyun_failed kind=%s",
                job_id,
                session_id,
                kind,
            )
            from .ingest import log_url_import_stage

            log_url_import_stage(
                host,
                "asr_fallback",
                job_id=job_id,
                session_id=session_id,
                error_kind=str(kind),
                recovered="whisper",
            )
    wav = folder / "audio.wav"
    source = audio_path
    if source.suffix.lower() != ".wav":
        if extract_wav_fn and (not wav.is_file() or wav.stat().st_size < 200):
            extract_wav_fn(source, wav)
        if wav.is_file() and wav.stat().st_size >= 200:
            source = wav
    t0 = time.monotonic()
    sentences = whisper_fn(source)
    from .ingest import log_url_import_stage

    log_url_import_stage(
        host,
        "T_asr",
        elapsed_ms=int((time.monotonic() - t0) * 1000),
        job_id=job_id,
        session_id=session_id,
        asr_backend="whisper",
    )
    return sentences
