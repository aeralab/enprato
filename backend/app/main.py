from __future__ import annotations

import asyncio
import json
import logging
import shutil
import threading
import uuid
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel

from .asr import transcribe_sentences, transcribe_speech, warmup
from .dictionary import lookup_word, translate_en_zh
from .ingest import fetch_media_title, find_session_media, ingest_url, validate_media_url
from .license import activate_license, checkout_license, license_status, note_trial_use, require_active
from .media import convert_to_wav, ensure_playback_audio, extract_wav
from .ipad_studio import IPAD_BUILD, IPAD_PAGE
from .remote_mic import (
    REMOTE_PAGE,
    get_active_remote,
    lan_ipv4s,
    phone_connected,
    pull_remote_results,
    push_remote_result,
    set_active_remote,
    touch_phone,
)
from .score import score_shadowing
from .sentences import parse_srt, parse_vtt
from .speaker import play_speaker, stop_speaker
from .store import (
    find_session_id_by_url,
    list_sessions,
    merge_draft_maps,
    apply_draft_snapshot,
    collapse_identical_drafts,
    read_json,
    read_meta,
    ensure_session_thumbnail,
    session_detail,
    write_meta,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "sessions"
DATA.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger(__name__)

app = FastAPI(title="Enprato", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup_warm_asr() -> None:
    def _run() -> None:
        try:
            warmup()
            logger.info("ASR warmup complete")
        except Exception:
            logger.exception("ASR warmup failed")

    threading.Thread(target=_run, name="asr-warmup", daemon=True).start()


def _session_dir(session_id: str) -> Path:
    path = DATA / session_id
    if not path.is_dir():
        raise HTTPException(404, "session 不存在")
    return path


def _save_upload(upload: UploadFile, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as handle:
        shutil.copyfileobj(upload.file, handle)


def _cues_from_text(raw: str, filename: str = "") -> list[dict[str, Any]]:
    name = filename.lower()
    if name.endswith(".vtt") or raw.lstrip().startswith("WEBVTT"):
        return parse_vtt(raw)
    return parse_srt(raw)


def _finish_session(
    folder: Path,
    session_id: str,
    audio: Path,
    sentences: list[dict[str, Any]],
    *,
    title: str = "",
    source_url: str = "",
    source_kind: str = "file",
) -> dict[str, Any]:
    if not sentences:
        sentences = transcribe_sentences(audio)
    if not sentences:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(400, "无法从视频中分出句子，请补一份英文字幕文件")
    (folder / "sentences.json").write_text(
        json.dumps(sentences, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    label = (title or sentences[0]["text"]).strip()[:80]
    write_meta(
        folder,
        title=label,
        source_url=source_url,
        source_kind=source_kind,
        phase="listen",
        index=0,
        drafts={},
        highlights=[],
    )
    try:
        ensure_session_thumbnail(folder)
    except Exception:
        logger.exception("thumbnail failed for %s", session_id)
    detail = session_detail(folder, session_id)
    if not detail:
        raise HTTPException(500, "会话写入失败")
    return detail


class PrepareUrlBody(BaseModel):
    url: str


class ProgressBody(BaseModel):
    phase: str | None = None
    index: int | None = None
    drafts: dict[str, str] | None = None
    highlights: list[dict[str, Any]] | None = None
    score: dict[str, Any] | None = None
    orientation: str | None = None


class LicenseActivateBody(BaseModel):
    key: str


class LicenseCheckoutBody(BaseModel):
    plan: str


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "ipad_build": IPAD_BUILD}


@app.get("/api/lan")
def api_lan() -> dict[str, Any]:
    ips = lan_ipv4s()
    port = 18787
    # iPhone 开麦必须用 https
    links = [f"https://{ip}:{port}/remote" for ip in ips]
    ipad_links = [f"https://{ip}:{port}/ipad/{IPAD_BUILD}" for ip in ips]
    return {
        "ips": ips,
        "port": port,
        "scheme": "https",
        "links": links,
        "ipad_links": ipad_links,
        "ipad_build": IPAD_BUILD,
    }


@app.get("/api/license")
def api_license() -> dict[str, Any]:
    return license_status(DATA)


@app.post("/api/license/activate")
def api_license_activate(body: LicenseActivateBody) -> dict[str, Any]:
    try:
        return activate_license(DATA, body.key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/license/checkout")
def api_license_checkout(body: LicenseCheckoutBody) -> dict[str, Any]:
    try:
        return checkout_license(DATA, body.plan)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc




class RemoteClaimBody(BaseModel):
    session_id: str | None = None


@app.get("/api/remote-active")
def remote_active() -> dict[str, Any]:
    sid = get_active_remote()
    return {"session_id": sid or ""}


@app.post("/api/remote-claim")
def remote_claim(body: RemoteClaimBody) -> dict[str, Any]:
    """电脑声明当前手机麦应对准哪一课；关掉手机麦时传空。"""
    sid = (body.session_id or "").strip()
    if sid:
        folder = DATA / sid
        if not folder.exists():
            raise HTTPException(404, "课程不存在")
        set_active_remote(sid)
        return {"session_id": sid}
    set_active_remote(None)
    return {"session_id": ""}

@app.get("/remote", response_class=HTMLResponse)
def remote_mic_page(s: str = "") -> HTMLResponse:
    _ = s
    return HTMLResponse(
        content=REMOTE_PAGE,
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.get("/ipad")
def ipad_studio_redirect(s: str = "", b: str = "") -> RedirectResponse:
    from urllib.parse import urlencode

    _ = b
    q: dict[str, str] = {"b": IPAD_BUILD}
    if s:
        q["s"] = s
    return RedirectResponse(url=f"/ipad/{IPAD_BUILD}?{urlencode(q)}", status_code=302)


@app.get("/ipad/{page_build}", response_class=HTMLResponse)
def ipad_studio_page(page_build: str, s: str = "", b: str = "") -> HTMLResponse:
    _ = page_build
    _ = s
    _ = b
    return HTMLResponse(
        content=IPAD_PAGE,
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0, private",
            "Pragma": "no-cache",
            "Expires": "0",
            "CDN-Cache-Control": "no-store",
            "Surrogate-Control": "no-store",
            "Vary": "*",
        },
    )


@app.get("/")
def backend_home() -> RedirectResponse:
    """Keep browser back from landing on an unhandled backend root URL."""
    return RedirectResponse(url="/remote", status_code=307)


@app.get("/api/session/{session_id}/remote-state")
def remote_state(session_id: str) -> dict[str, Any]:
    folder = _session_dir(session_id)
    detail = session_detail(folder, session_id)
    if not detail:
        raise HTTPException(404, "课程不存在")
    touch_phone(session_id)
    sentences = detail["sentences"]
    index = int(detail["index"])
    target = ""
    if 0 <= index < len(sentences):
        target = str(sentences[index].get("text") or "")
    drafts = detail.get("drafts") if isinstance(detail.get("drafts"), dict) else {}
    draft = str(drafts.get(str(index)) or drafts.get(index) or "")
    drafts_out = {str(k): str(v) for k, v in drafts.items()}
    sentences_out = [
        {
            "start": float(s.get("start") or 0),
            "end": float(s.get("end") or 0),
            "text": str(s.get("text") or ""),
        }
        for s in sentences
    ]
    return {
        "session_id": session_id,
        "index": index,
        "total": len(sentences),
        "target": target,
        "draft": draft,
        "drafts": drafts_out,
        "phase": detail["phase"],
        "sentences": sentences_out,
    }


class RemoteDraftBody(BaseModel):
    index: int
    text: str


class RemoteDraftsBody(BaseModel):
    drafts: dict[str, str]
    index: int | None = None


def _match_sentence_index(sentences: list[Any], text: str, hint: int) -> int:
    """听写内容更像后面某句时，写到那一句（避免句号落后把第2句写进第1句）。"""
    if not sentences:
        return 0
    hint = max(0, min(int(hint), len(sentences) - 1))
    spoken = " ".join(str(text or "").lower().split())
    if len(spoken) < 8:
        return hint

    def score(i: int) -> float:
        target = " ".join(str(sentences[i].get("text") or "").lower().split())
        return SequenceMatcher(None, spoken, target).ratio()

    best_i = hint
    best = score(hint)
    for i in range(max(0, hint - 1), min(len(sentences), hint + 4)):
        s = score(i)
        # 明显更像附近另一句 → 跟过去
        if s >= best + 0.1 and s >= 0.35:
            best = s
            best_i = i
    return best_i


@app.post("/api/session/{session_id}/remote-draft")
def remote_draft(session_id: str, body: RemoteDraftBody) -> dict[str, Any]:
    folder = _session_dir(session_id)
    sentences = read_json(folder / "sentences.json", [])
    if not isinstance(sentences, list) or not sentences:
        raise HTTPException(404, "没有句子")
    meta = read_meta(folder)
    # 改字：按手机点的段落写入，不因句号落后而串句
    idx = max(0, min(int(body.index), len(sentences) - 1))
    drafts = meta.get("drafts") if isinstance(meta.get("drafts"), dict) else {}
    drafts = {str(k): str(v) for k, v in drafts.items()}
    drafts[str(idx)] = body.text
    if body.text.strip():
        note_trial_use(DATA, session_id)
    write_meta(folder, drafts=drafts, index=idx, phase="dictate")
    item = push_remote_result(session_id, idx, body.text)
    touch_phone(session_id)
    return {"text": body.text, "index": idx, "id": item["id"]}


@app.post("/api/session/{session_id}/remote-drafts")
def remote_drafts_bulk(session_id: str, body: RemoteDraftsBody) -> dict[str, Any]:
    """手机整页听写稿一次写入，按句号合并，不丢中间句。"""
    folder = _session_dir(session_id)
    sentences = read_json(folder / "sentences.json", [])
    if not isinstance(sentences, list) or not sentences:
        raise HTTPException(404, "没有句子")
    meta = read_meta(folder)
    meta_idx = int(meta.get("index") or 0)
    drafts = meta.get("drafts") if isinstance(meta.get("drafts"), dict) else {}
    drafts = {str(k): str(v) for k, v in drafts.items()}
    backup_path = folder / "drafts-backup.json"
    try:
        history = read_json(backup_path, [])
        if not isinstance(history, list):
            history = []
        history.append({
            "saved_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            "index": meta_idx,
            "drafts": dict(drafts),
        })
        backup_path.write_text(json.dumps(history[-30:], ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logger.exception("failed to write remote draft backup for %s", session_id)
    max_i = len(sentences) - 1
    incoming: dict[str, str] = {}
    for key, value in (body.drafts or {}).items():
        try:
            i = int(key)
        except (TypeError, ValueError):
            continue
        if 0 <= i <= max_i:
            incoming[str(i)] = str(value or "")
    drafts = collapse_identical_drafts(apply_draft_snapshot(drafts, incoming))
    if body.index is not None:
        keep_index = max(0, min(int(body.index), max_i))
    else:
        keep_index = meta_idx
    old_at_index = str(
        (meta.get("drafts") or {}).get(str(keep_index), "")
        if isinstance(meta.get("drafts"), dict)
        else ""
    )
    write_meta(folder, drafts=drafts, index=keep_index, phase="dictate")
    if any(str(v or "").strip() for v in drafts.values()):
        note_trial_use(DATA, session_id)
    touch_phone(session_id)
    new_at_index = str(drafts.get(str(keep_index), ""))
    if keep_index != meta_idx or (new_at_index.strip() and new_at_index != old_at_index):
        push_remote_result(session_id, keep_index, new_at_index)
    return {"ok": True, "index": keep_index, "drafts": drafts}


@app.post("/api/session/{session_id}/remote-next")
def remote_next(session_id: str) -> dict[str, Any]:
    """手机点「下一句」：推进课程序号，电脑会跟着走。"""
    folder = _session_dir(session_id)
    sentences = read_json(folder / "sentences.json", [])
    if not isinstance(sentences, list) or not sentences:
        raise HTTPException(404, "没有句子")
    meta = read_meta(folder)
    cur = int(meta.get("index") or 0)
    nxt = min(cur + 1, len(sentences) - 1)
    write_meta(folder, index=nxt, phase="listen")
    item = push_remote_result(session_id, nxt, "")
    touch_phone(session_id)
    return {"index": nxt, "total": len(sentences), "id": item["id"]}


@app.post("/api/session/{session_id}/remote-stt")
async def remote_stt(
    session_id: str,
    audio: UploadFile = File(...),
    index: int = Form(...),
    mode: str = Form("replace"),
) -> dict[str, Any]:
    require_active(DATA)
    folder = _session_dir(session_id)
    sentences = read_json(folder / "sentences.json", [])
    if not isinstance(sentences, list) or not sentences:
        raise HTTPException(404, "没有句子")
    meta = read_meta(folder)
    phone_idx = max(0, min(int(index), len(sentences) - 1))
    # iPad/手机点哪句就按哪句识别，不因电脑进度更大而抬升序号
    idx = phone_idx
    target = str(sentences[idx].get("text") or "")
    drafts = meta.get("drafts") if isinstance(meta.get("drafts"), dict) else {}
    drafts = {str(k): str(v) for k, v in drafts.items()}
    context_bits: list[str] = []
    for prev_i in range(max(0, idx - 1), idx):
        bit = str(drafts.get(str(prev_i)) or "").strip()
        if not bit:
            bit = str(sentences[prev_i].get("text") or "")
        if bit:
            context_bits.append(bit[-160:])
    context = " ".join(context_bits)[:200]

    tmp_dir = DATA / "_stt"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    upload_id = uuid.uuid4().hex
    raw = tmp_dir / f"{upload_id}{Path(audio.filename or 'phone.webm').suffix or '.webm'}"
    wav = tmp_dir / f"{upload_id}.converted.wav"
    fast = True
    try:
        _save_upload(audio, raw)
        await run_in_threadpool(convert_to_wav, raw, wav)
        try:
            text = await asyncio.wait_for(
                run_in_threadpool(
                    lambda: transcribe_speech(wav, context, target, fast=fast),
                ),
                timeout=75.0,
            )
        except asyncio.TimeoutError as exc:
            raise HTTPException(504, "语音识别超时，请缩短录音后重试") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "remote-stt failed session=%s index=%s filename=%s content_type=%s",
            session_id,
            idx,
            audio.filename,
            audio.content_type,
        )
        raise HTTPException(500, f"remote-stt failed: {exc}") from exc
    finally:
        raw.unlink(missing_ok=True)
        wav.unlink(missing_ok=True)

    if text.strip():
        from .asr import _clean_stt, _spell_toward_target

        # 光标插入模式：只返回识别文本，不整句覆盖听写稿
        if mode != "insert":
            idx = _match_sentence_index(sentences, text, idx)
            target = str(sentences[idx].get("text") or "")
        text = _spell_toward_target(_clean_stt(text), target) if target else _clean_stt(text)

    touch_phone(session_id)
    if mode == "insert":
        return {"text": text, "index": idx, "id": 0}

    prev = str(drafts.get(str(idx)) or "").strip()
    if text.strip():
        from .asr import merge_dictation_text

        text = merge_dictation_text(prev, text, target)
    if text.strip():
        drafts[str(idx)] = text
        note_trial_use(DATA, session_id)
        write_meta(folder, drafts=drafts, index=idx, phase="dictate")
        item = push_remote_result(session_id, idx, text)
    else:
        write_meta(folder, index=idx, phase="dictate")
        item = {"id": 0, "index": idx, "text": ""}
    return {"text": text, "index": idx, "id": item["id"]}


@app.get("/api/session/{session_id}/remote-inbox")
def remote_inbox(session_id: str, after: int = 0) -> dict[str, Any]:
    _session_dir(session_id)
    items = pull_remote_results(session_id, after_id=after)
    return {"items": items, "connected": phone_connected(session_id)}


@app.post("/api/warmup")
def api_warmup() -> dict[str, str]:
    model = warmup()
    return {"model": str(model)}


@app.post("/api/prepare")
async def prepare(
    video: UploadFile = File(...),
    captions: UploadFile | None = File(default=None),
) -> dict[str, Any]:
    require_active(DATA)
    session_id = uuid.uuid4().hex[:12]
    folder = DATA / session_id
    folder.mkdir(parents=True, exist_ok=True)
    suffix = Path(video.filename or "video.mp4").suffix or ".mp4"
    source = folder / f"source{suffix}"
    audio = folder / "audio.wav"
    _save_upload(video, source)
    try:
        extract_wav(source, audio)
        ensure_playback_audio(folder, source)
    except Exception as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(400, f"抽音频失败（需要视频里有音轨，并已安装 ffmpeg）: {exc}") from exc

    sentences: list[dict[str, Any]] = []
    if captions is not None and captions.filename:
        raw = (await captions.read()).decode("utf-8", errors="replace")
        sentences = _cues_from_text(raw, captions.filename or "")
    detail = _finish_session(
        folder,
        session_id,
        audio,
        sentences,
        title=Path(video.filename or "video").stem,
        source_kind="file",
    )
    return detail


@app.post("/api/prepare-url")
async def prepare_url(body: PrepareUrlBody) -> dict[str, Any]:
    try:
        url = validate_media_url(body.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    existing = find_session_id_by_url(DATA, url)
    if existing:
        folder_existing = DATA / existing
        if find_session_media(folder_existing):
            detail = session_detail(folder_existing, existing)
            if detail:
                return detail
        # 课还在但片子丢了：清掉重下
        shutil.rmtree(folder_existing, ignore_errors=True)
    require_active(DATA)
    session_id = uuid.uuid4().hex[:12]
    folder = DATA / session_id
    folder.mkdir(parents=True, exist_ok=True)
    try:
        _media, audio, caption_text = await run_in_threadpool(ingest_url, url, folder)
    except Exception as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(400, f"链接无法用于学习：{exc}") from exc
    sentences: list[dict[str, Any]] = []
    if caption_text:
        sentences = _cues_from_text(caption_text)
    display_title = await run_in_threadpool(fetch_media_title, url) or url
    detail = _finish_session(
        folder,
        session_id,
        audio,
        sentences,
        title=display_title,
        source_url=url,
        source_kind="url",
    )
    return detail


@app.get("/api/sessions")
def api_sessions() -> dict[str, Any]:
    return {"sessions": list_sessions(DATA)}


@app.get("/api/session/{session_id}")
def api_session(session_id: str) -> dict[str, Any]:
    folder = _session_dir(session_id)
    detail = session_detail(folder, session_id)
    if not detail:
        raise HTTPException(404, "session 不存在")
    return detail


@app.patch("/api/session/{session_id}")
def api_save_progress(session_id: str, body: ProgressBody) -> dict[str, str]:
    folder = _session_dir(session_id)
    fields: dict[str, Any] = {
        "phase": body.phase,
        "index": body.index,
        "highlights": body.highlights,
        "score": body.score,
        "orientation": body.orientation,
    }
    if body.drafts is not None:
        meta = read_meta(folder)
        existing = meta.get("drafts") if isinstance(meta.get("drafts"), dict) else {}
        existing = {str(k): str(v) for k, v in existing.items()}
        fields["drafts"] = collapse_identical_drafts(
            apply_draft_snapshot(existing, body.drafts)
        )
        if any(str(v or "").strip() for v in body.drafts.values()):
            note_trial_use(DATA, session_id)
    write_meta(folder, **fields)
    return {"status": "ok"}


@app.delete("/api/session/{session_id}")
def api_delete_session(session_id: str) -> dict[str, str]:
    folder = _session_dir(session_id)
    shutil.rmtree(folder, ignore_errors=True)
    return {"status": "ok"}


class SpeakerPlayBody(BaseModel):
    start: float
    end: float
    volume: float = 1.0


@app.post("/api/session/{session_id}/speaker-play")
def session_speaker_play(session_id: str, body: SpeakerPlayBody) -> dict[str, str]:
    """Play sentence audio via ffplay → Windows default device (Realtek Digital Output)."""
    folder = _session_dir(session_id)
    media = find_session_media(folder)
    playback = ensure_playback_audio(folder, media)
    path = playback if playback is not None and playback.is_file() else media
    if path is None or not path.is_file():
        raise HTTPException(404, "没有可播放的音轨")
    try:
        play_speaker(path, body.start, body.end, body.volume)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"系统出声失败：{exc}") from exc
    return {"status": "ok"}


@app.post("/api/session/{session_id}/speaker-stop")
def session_speaker_stop(session_id: str) -> dict[str, str]:
    _ = session_id
    stop_speaker()
    return {"status": "ok"}


@app.get("/api/session/{session_id}/audio")
def session_audio(session_id: str) -> FileResponse:
    folder = _session_dir(session_id)
    media = find_session_media(folder)
    playback = ensure_playback_audio(folder, media)
    if playback is not None and playback.is_file():
        suffix = playback.suffix.lower()
        mime = {
            ".m4a": "audio/mp4",
            ".mp4": "audio/mp4",
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".aac": "audio/aac",
            ".ogg": "audio/ogg",
            ".opus": "audio/ogg",
        }.get(suffix, "application/octet-stream")
        return FileResponse(playback, media_type=mime, filename=playback.name)
    wav = folder / "audio.wav"
    if not wav.is_file():
        raise HTTPException(404, "没有音轨")
    return FileResponse(wav, media_type="audio/wav", filename="audio.wav")


@app.get("/api/session/{session_id}/thumb")
def session_thumb(session_id: str) -> FileResponse:
    folder = _session_dir(session_id)
    thumb = folder / "thumb.jpg"
    try:
        if not thumb.is_file() or thumb.stat().st_size < 8000:
            ensure_session_thumbnail(folder)
    except Exception:
        logger.exception("lazy thumbnail failed for %s", session_id)
    if not thumb.is_file() or thumb.stat().st_size == 0:
        raise HTTPException(404, "没有封面")
    return FileResponse(
        thumb,
        media_type="image/jpeg",
        filename="thumb.jpg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/session/{session_id}/video")
def session_video(session_id: str) -> FileResponse:
    folder = _session_dir(session_id)
    media = find_session_media(folder)
    if media is None or not media.is_file():
        raise HTTPException(404, "没有可播放的视频")
    suffix = media.suffix.lower()
    mime = {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
        ".m4v": "video/mp4",
        ".mov": "video/quicktime",
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".opus": "audio/ogg",
        ".ogg": "audio/ogg",
        ".aac": "audio/aac",
    }.get(suffix, "application/octet-stream")
    return FileResponse(
        media,
        media_type=mime,
        filename=media.name,
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/stt")
async def stt(
    audio: UploadFile = File(...),
    context: str = Form(default=""),
    target: str = Form(default=""),
) -> dict[str, str]:
    require_active(DATA)
    tmp_dir = DATA / "_stt"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    raw = tmp_dir / f"{uuid.uuid4().hex}{Path(audio.filename or 'clip.webm').suffix or '.webm'}"
    wav = raw.with_suffix(".wav")
    try:
        _save_upload(audio, raw)
        await run_in_threadpool(convert_to_wav, raw, wav)
        text = await run_in_threadpool(
            lambda: transcribe_speech(wav, context=context, target=target, fast=True),
        )
        return {"text": text}
    finally:
        raw.unlink(missing_ok=True)
        wav.unlink(missing_ok=True)


@app.get("/api/define")
def define(word: str) -> dict[str, Any]:
    if not word.strip():
        raise HTTPException(400, "缺少单词")
    return lookup_word(word)


@app.get("/api/translate")
def api_translate(text: str) -> dict[str, str]:
    if not text.strip():
        raise HTTPException(400, "缺少句子")
    return {"text": text, "zh": translate_en_zh(text)}


@app.post("/api/score")
async def score(
    audio: UploadFile = File(...),
    session_id: str = Form(...),
) -> dict[str, Any]:
    require_active(DATA)
    folder = _session_dir(session_id)
    original = folder / "audio.wav"
    if not original.is_file():
        raise HTTPException(400, "原音频不存在")
    sentences = json.loads((folder / "sentences.json").read_text(encoding="utf-8"))
    reference = " ".join(item["text"] for item in sentences)
    raw = folder / f"user{Path(audio.filename or 'shadow.webm').suffix or '.webm'}"
    wav = folder / "user.wav"
    _save_upload(audio, raw)
    convert_to_wav(raw, wav)
    return score_shadowing(original, wav, reference)
