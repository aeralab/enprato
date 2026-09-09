from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from .asr import transcribe_sentences, transcribe_speech, transcribe_speech_detailed, warmup
from . import db
from .auth import (
    COOKIE_NAME,
    auth_required,
    cookie_secure,
    current_user,
    hash_password,
    require_user,
    require_user_or_local,
    verify_password,
)
from .curated import list_curated_lessons
from .dictionary import lookup_word, translate_en_zh
from .ingest import (
    fetch_media_title,
    find_session_media,
    ingest_url,
    log_url_import_stage,
    url_host_family,
    url_preview,
    validate_media_url,
)
from .license import activate_license, checkout_license, license_status, note_trial_use
from .media import convert_to_wav, ensure_playback_audio, extract_wav, probe_duration
from .payment import PaymentConfigError, mock_provider_enabled, provider_for
from .progress import complete_session, progress_for_user, record_new_dictations
from .resplit import resplit_remaining_session, rollback_resplit_session
from .client_ip import resolve_client_ip
from .rate_limit import enforce as enforce_rate_limit
from . import sms as sms_service
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
from . import stt_jobs, stt_log
from . import url_import_jobs
from . import wechat_oauth
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
ASR_IMPORT_TIMEOUT_SEC = 360.0

app = FastAPI(title="Enprato", version="0.1.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.environ.get("ENPRATO_CORS_ORIGINS", "http://localhost:5173,https://enprato.site").split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Client-Request-ID"],
)


STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    request_id = uuid.uuid4().hex[:12]
    client_request_id = (request.headers.get("x-client-request-id") or "").strip()
    request.state.request_id = request_id
    request.state.client_request_id = client_request_id
    stt_path = "/remote-stt" in str(request.url.path)
    if stt_path:
        stt_log.event(
            "STT_RECEIVED",
            client_request_id=client_request_id or None,
            server_request_id=request_id,
            method=request.method,
            path=str(request.url.path),
            content_type=request.headers.get("content-type", ""),
            content_length=request.headers.get("content-length", ""),
            user_agent=request.headers.get("user-agent", ""),
            client_ip=request.client.host if request.client else "",
        )
        logger.info(
            "STT_RECEIVED client_request_id=%s server_request_id=%s method=%s path=%s content_type=%s content_length=%s",
            client_request_id or "-",
            request_id,
            request.method,
            request.url.path,
            request.headers.get("content-type", ""),
            request.headers.get("content-length", ""),
        )
    try:
        response = await call_next(request)
    except Exception as exc:
        if stt_path:
            stt_log.event(
                "STT_FAILED",
                client_request_id=client_request_id or None,
                server_request_id=request_id,
                exception=type(exc).__name__,
                error_type="unhandled",
                http_status=500,
            )
        raise
    response.headers["X-Request-ID"] = request_id
    if client_request_id:
        response.headers["X-Client-Request-ID"] = client_request_id
    return response


@app.get("/ipad-assets/stt_job.js")
def ipad_stt_job_js() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "stt_job.js",
        media_type="text/javascript; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


class SttDiagIn(BaseModel):
    stage: str = "SENTENCE_INSERT"
    client_request_id: str = ""
    session_id: str = ""
    index: int = -1
    text_len: int = 0
    text_preview: str = ""
    server_request_id: str = ""


@app.post("/api/stt-diag")
async def stt_diag(payload: SttDiagIn, request: Request) -> dict[str, str]:
    stt_log.event(
        str(payload.stage or "SENTENCE_INSERT"),
        client_request_id=(payload.client_request_id or "").strip() or None,
        server_request_id=(payload.server_request_id or str(getattr(request.state, "request_id", "") or "")).strip() or None,
        session_id=(payload.session_id or "").strip() or None,
        index=int(payload.index),
        text_len=int(payload.text_len),
        text_preview=str(payload.text_preview or "")[:80],
    )
    return {"ok": "1"}


@app.on_event("startup")
def _startup_warm_asr() -> None:
    db.migrate()
    db.ensure_legacy_sessions(DATA)
    url_import_jobs.ensure_worker()

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


def _refund_failed_prepare(user: dict[str, Any], session_id: str) -> None:
    if user.get("id") and user["id"] != "lan-local":
        db.refund_trial(user["id"], "prepare:" + session_id)


def _transcribe_import(audio: Path, host: str = "unknown") -> list[dict[str, Any]]:
    log_url_import_stage(host, "asr_start")
    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            sentences = pool.submit(transcribe_sentences, audio).result(timeout=ASR_IMPORT_TIMEOUT_SEC)
    except FuturesTimeoutError:
        log_url_import_stage(host, "asr", elapsed_ms=int((time.monotonic() - started) * 1000), error_kind="asr_timeout")
        raise HTTPException(400, "语音识别时间过长，已停止。请换较短的视频，或先下载到本地再上传。") from None
    log_url_import_stage(host, "asr", elapsed_ms=int((time.monotonic() - started) * 1000), sentences=len(sentences or []))
    return sentences


def _finish_session(
    folder: Path,
    session_id: str,
    audio: Path,
    sentences: list[dict[str, Any]],
    *,
    title: str = "",
    source_url: str = "",
    source_kind: str = "file",
    host: str = "unknown",
) -> dict[str, Any]:
    if not sentences:
        if not audio.is_file():
            media = find_session_media(folder)
            if media is None:
                raise HTTPException(400, "无法从视频中分出句子，请补一份英文字幕文件")
            extract_wav(media, audio)
            ensure_playback_audio(folder, media)
        sentences = _transcribe_import(audio, host=host)
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
    create_new_session: bool = False


class ProgressBody(BaseModel):
    phase: str | None = None
    index: int | None = None
    drafts: dict[str, str] | None = None
    highlights: list[dict[str, Any]] | None = None
    score: dict[str, Any] | None = None
    orientation: str | None = None
    source_session_id: str | None = None
    save_reason: str | None = None


class LearningCompleteBody(BaseModel):
    duration_seconds: int | None = None


class ResplitBody(BaseModel):
    backup_id: str | None = None


class LicenseActivateBody(BaseModel):
    key: str


class LicenseCheckoutBody(BaseModel):
    plan: str


class AuthBody(BaseModel):
    email: str
    password: str


class PhoneCodeBody(BaseModel):
    phone: str


class PhoneVerifyBody(BaseModel):
    phone: str
    challenge_id: str
    code: str


class OrderBody(BaseModel):
    plan: str = "monthly_30d"
    provider: str = "wechat"


def public_user(user):
    email = user["email"] if isinstance(user, dict) else user["email"]
    user_id = user["id"]
    return {
        "id": user_id,
        "email": email,
        "login_label": db.login_label(user_id, email),
        "status": user["status"],
        "membership": db.membership_status(user_id),
        "trial": db.trial_status(user_id),
    }


def _public_base(request: Request) -> str:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "https").split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).split(",")[0].strip()
    return f"{proto}://{host}"


def _safe_record_dictations(user: dict[str, Any], session_id: str, folder: Path, previous: dict[str, str], incoming: dict[str, str]) -> None:
    try:
        record_new_dictations(user, session_id, folder, previous, incoming)
    except Exception:
        logger.exception("learning progress record failed session=%s", session_id)


def require_member_or_trial(user: dict[str, Any]) -> None:
    # iPad/手机局域网访问无账号：沿用本机 license.json（试用/买断）
    if user.get("id") == "lan-local":
        status = license_status(DATA)
        if status.get("active"):
            return
        raise HTTPException(402, "免费听写次数已用完，请开通会员后继续")
    # 已登录官网用户：只看 SQLite membership + usage_quotas，不混用 license.json
    membership = db.membership_status(user["id"])
    if membership.get("active"):
        return
    quota = db.trial_status(user["id"])
    if quota["remaining"] <= 0:
        raise HTTPException(402, "免费学习素材次数已用完")


def require_session_access(session_id: str, request: Request) -> dict[str, Any]:
    """PC 登录用户 / 远程 token；仅本机未强制登录时允许目录兜底为 lan-local。"""
    user = current_user(request)
    if user and db.owns_learning_session(session_id, user["id"]):
        return dict(user)
    token = request.cookies.get("enprato_remote_token", "") or request.query_params.get("token", "")
    owner_id = db.remote_token_owner(token, session_id)
    if owner_id:
        owner = db.user_by_id(owner_id)
        if owner:
            return dict(owner)
    if user:
        raise HTTPException(404, "session not found")
    # 家庭局域网单机：ENPRATO_REQUIRE_AUTH 未开时，iPad 无登录态可按会话目录放行
    if not auth_required() and (DATA / session_id).is_dir():
        return {"id": "lan-local", "email": "", "status": "active"}
    raise HTTPException(401, "请先登录")


def require_owned_session(session_id: str, user: dict[str, Any]) -> Path:
    # lan-local 伪用户：本机会话即可
    if user.get("id") == "lan-local":
        folder = _session_dir(session_id)
        return folder
    if not db.owns_learning_session(session_id, user["id"]):
        raise HTTPException(404, "session not found")
    return _session_dir(session_id)


def set_session_cookie(response, token, request: Request | None = None):
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=cookie_secure(request),
        samesite="lax",
        max_age=30 * 24 * 60 * 60,
        path="/",
    )


@app.post("/api/auth/register")
def auth_register(body: AuthBody, request: Request, response: Response):
    email = body.email.strip().lower()
    if "@" not in email or len(email) > 254 or len(body.password) < 8:
        raise HTTPException(400, "请输入有效邮箱，密码至少 8 位")
    try:
        user = db.create_user(email, hash_password(body.password))
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise HTTPException(409, "邮箱已注册") from exc
        raise
    set_session_cookie(response, db.create_auth_session(user["id"]), request)
    return public_user(user)


@app.post("/api/auth/login")
def auth_login(body: AuthBody, request: Request, response: Response):
    row = db.find_user(body.email.strip().lower())
    if not row or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(401, "邮箱或密码错误")
    user = dict(row)
    set_session_cookie(response, db.create_auth_session(user["id"]), request)
    return public_user(user)


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE_NAME, "")
    if token:
        db.delete_auth_session(token)
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=cookie_secure(request), samesite="lax")
    return {"status": "ok"}


@app.get("/api/auth/me")
def auth_me(user=Depends(current_user)):
    if user:
        return {"user": public_user(user), "require_auth": auth_required()}
    if auth_required():
        raise HTTPException(401, "请先登录")
    return {"user": None, "require_auth": False}


@app.get("/api/auth/methods")
def auth_methods() -> dict[str, Any]:
    return {"wechat": wechat_oauth.public_status(), "phone": sms_service.public_status()}


@app.get("/api/auth/wechat/start")
def auth_wechat_start(request: Request):
    channel = wechat_oauth.wechat_channel(request.headers.get("user-agent") or "")
    if not channel:
        return RedirectResponse("/?auth_error=wechat_unconfigured", status_code=302)
    state = f"{channel}.{secrets.token_urlsafe(24)}"
    try:
        url = wechat_oauth.authorize_url(channel=channel, state=state, request_base=_public_base(request))
    except wechat_oauth.WechatNotConfigured:
        return RedirectResponse("/?auth_error=wechat_unconfigured", status_code=302)
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        wechat_oauth.STATE_COOKIE,
        state,
        httponly=True,
        secure=cookie_secure(request),
        samesite="lax",
        max_age=600,
        path="/",
    )
    return response


@app.get("/api/auth/wechat/callback")
def auth_wechat_callback(request: Request, code: str = "", state: str = ""):
    fail = RedirectResponse("/?auth_error=wechat", status_code=302)
    fail.delete_cookie(wechat_oauth.STATE_COOKIE, path="/")
    cookie_state = request.cookies.get(wechat_oauth.STATE_COOKIE, "")
    if not code or not state or not cookie_state or not secrets.compare_digest(cookie_state, state):
        return fail
    channel, _, nonce = state.partition(".")
    if channel not in {"web", "oa"} or not nonce:
        return fail
    try:
        profile = wechat_oauth.exchange_code(code, channel)
        user_id = db.login_or_create_identities(wechat_oauth.identity_pairs(profile))
    except wechat_oauth.WechatOAuthError:
        logger.info("wechat oauth failed")
        return fail
    user = db.user_by_id(user_id)
    if not user:
        return fail
    redirect = RedirectResponse("/", status_code=302)
    redirect.delete_cookie(wechat_oauth.STATE_COOKIE, path="/")
    set_session_cookie(redirect, db.create_auth_session(user_id), request)
    return redirect


@app.post("/api/auth/phone/send")
def auth_phone_send(body: PhoneCodeBody, request: Request):
    try:
        phone = db.normalize_phone(body.phone)
    except ValueError:
        raise HTTPException(400, "手机号格式不正确")
    if not sms_service.sms_send_ready():
        raise HTTPException(503, "短信登录尚未配置真实短信服务")
    challenge_id = secrets.token_urlsafe(18)
    code = f"{secrets.randbelow(1000000):06d}"
    request_ip = resolve_client_ip(request)
    if not db.create_phone_challenge(phone, db.hash_token(challenge_id + ":" + code), request_ip, challenge_id):
        raise HTTPException(429, "验证码发送过于频繁，请稍后再试")
    try:
        sms_service.send_code(phone, code)
    except sms_service.SmsNotConfigured:
        db.delete_phone_challenge(challenge_id)
        raise HTTPException(503, "短信登录尚未配置真实短信服务")
    except sms_service.SmsError:
        db.delete_phone_challenge(challenge_id)
        logger.info("sms send failed")
        raise HTTPException(503, "验证码发送失败，请稍后重试")
    logger.info("sms challenge created")
    return {"challenge_id": challenge_id, "expires_in": 300}


@app.post("/api/auth/phone/verify")
def auth_phone_verify(body: PhoneVerifyBody, request: Request, response: Response):
    try:
        phone = db.normalize_phone(body.phone)
    except ValueError:
        raise HTTPException(400, "手机号格式不正确")
    if len(body.code) != 6 or not body.code.isdigit():
        raise HTTPException(400, "验证码格式不正确")
    user_id = db.consume_phone_challenge(body.challenge_id, phone, db.hash_token(body.challenge_id + ":" + body.code))
    if not user_id:
        raise HTTPException(401, "验证码错误或已失效")
    set_session_cookie(response, db.create_auth_session(user_id), request)
    user = db.user_by_id(user_id)
    if not user:
        raise HTTPException(401, "验证码错误或已失效")
    return public_user(user)


@app.get("/api/plans")
def api_plans(user: dict[str, Any] = Depends(require_user)):
    conn = db.connect()
    try: return {"plans": [dict(row) for row in conn.execute("SELECT code,name,price_fen,duration_days FROM plans WHERE active=1").fetchall()]}
    finally: conn.close()


@app.post("/api/payments/wechat/native")
@app.post("/api/orders")
def api_create_order(request: Request, body: OrderBody, user: dict[str, Any] = Depends(require_user)):
    enforce_rate_limit(request, "payment-create")
    if body.provider != "wechat": raise HTTPException(400, "当前仅支持微信支付")
    try:
        order = db.create_order(user["id"], body.plan, body.provider)
        if mock_provider_enabled():
            logger.warning("payment mock order created order=%s provider=mock", order["order_no"])
            return {**order, "payment": {"provider": "mock", "code_url": "mock://" + order["order_no"]}}
        payment = provider_for(body.provider).create_native_payment(order_no=order["order_no"], description="Enprato 月度会员 30 天", amount_fen=order["amount_fen"])
        logger.info("payment order created order=%s provider=%s", order["order_no"], body.provider)
        return {**order, "payment": payment}
    except (ValueError, PaymentConfigError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/payments/orders/{order_no}")
@app.get("/api/orders/{order_no}")
def api_get_order(order_no: str, user: dict[str, Any] = Depends(require_user)):
    order = db.get_order(order_no, user["id"])
    if not order: raise HTTPException(404, "订单不存在")
    return order


@app.post("/api/payments/wechat/notify")
async def wechat_notify(request: Request):
    enforce_rate_limit(request, "payment-notify")
    body = await request.body()
    try:
        provider = provider_for("wechat")
        payload = provider.verify_and_decode_notify(headers=dict(request.headers), body=body)
        order_no = str(payload.get("out_trade_no") or "")
        trade_no = str(payload.get("transaction_id") or "")
        amount = int((payload.get("amount") or {}).get("total") or 0)
        if not order_no or not trade_no or not payload.get("mchid") or not payload.get("appid"):
            raise ValueError("invalid payment transaction")
        result = db.complete_payment(provider="wechat", event_id=trade_no, payload_hash=hashlib.sha256(body).hexdigest(), order_no=order_no, trade_no=trade_no, amount_fen=amount, payment_status=str(payload.get("trade_state") or ""), merchant_id=str(payload.get("mchid")), app_id=str(payload.get("appid")))
        logger.info("payment callback processed provider=wechat order=%s result=%s", order_no, result)
        return {"code": "SUCCESS", "message": result}
    except (ValueError, KeyError, PaymentConfigError) as exc:
        raise HTTPException(400, str(exc)) from exc

@app.post("/api/payments/orders/{order_no}/sync")
def sync_wechat_order(request: Request, order_no: str, user: dict[str, Any] = Depends(require_user)):
    enforce_rate_limit(request, "payment-query")
    order = db.get_order(order_no, user["id"])
    if not order: raise HTTPException(404, "order not found")
    if order["status"] in {"paid", "closed", "refunded"}: return order
    try:
        provider = provider_for("wechat")
        payload = provider.query_order(order_no=order_no)
        if str(payload.get("out_trade_no") or "") != order_no: raise ValueError("order number mismatch")
        amount = int((payload.get("amount") or {}).get("total") or 0)
        trade_no = str(payload.get("transaction_id") or "")
        if str(payload.get("mchid") or "") != os.environ.get("WECHATPAY_MCH_ID", "").strip(): raise ValueError("merchant mismatch")
        if payload.get("trade_state") == "SUCCESS" and (amount != 1990 or not trade_no): raise ValueError("payment transaction mismatch")
        if payload.get("trade_state") == "SUCCESS": db.complete_payment(provider="wechat", event_id=trade_no, payload_hash="query", order_no=order_no, trade_no=trade_no, amount_fen=amount, payment_status="SUCCESS", merchant_id=str(payload.get("mchid")), app_id=str(payload.get("appid") or ""))
    except (ValueError, PaymentConfigError, RuntimeError) as exc: raise HTTPException(400, str(exc)) from exc
    return db.get_order(order_no, user["id"])

@app.post("/api/dev/orders/{order_no}/pay")
def dev_pay(order_no: str, user: dict[str, Any] = Depends(require_user)):
    if not mock_provider_enabled(): raise HTTPException(404, "开发 mock 支付未启用")
    order = db.get_order(order_no, user["id"])
    if not order: raise HTTPException(404, "订单不存在")
    try:
        result = db.complete_payment(provider="mock", event_id="mock-" + order_no, payload_hash="dev", order_no=order_no, trade_no="mock-" + order_no, amount_fen=order["amount_fen"], payment_status="SUCCESS")
    except ValueError as exc: raise HTTPException(400, str(exc)) from exc
    return {"status": result, "membership": db.membership_status(user["id"])}


@app.post("/api/dev/membership")
def dev_membership(user = Depends(require_user)):
    try:
        return {"membership": db.grant_dev_membership(user["id"])}
    except PermissionError as exc:
        raise HTTPException(404, "开发环境人工开通未启用") from exc


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "ipad_build": IPAD_BUILD}


@app.get("/api/catalog")
def api_catalog() -> dict[str, Any]:
    """云端推荐课目录。本机默认关闭（ENPRATO_ENABLE_CURATED 未设时返回空）。"""
    return {"lessons": list_curated_lessons()}


@app.get("/icon/{name}")
def app_icon(name: str) -> FileResponse:
    allowed = {
        "enprato-180.png": "enprato-180.png",
        "enprato-192.png": "enprato-192.png",
        "enprato-512.png": "enprato-512.png",
    }
    filename = allowed.get(name)
    if not filename:
        raise HTTPException(404, "icon not found")
    path = ROOT / "static" / "icons" / filename
    if not path.is_file():
        raise HTTPException(404, "icon not found")
    return FileResponse(
        path,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=86400",
        },
    )


@app.get("/api/lan")
def api_lan(request: Request) -> dict[str, Any]:
    ips = lan_ipv4s()
    port = request.url.port or int(os.environ.get("ENPRATO_BACKEND_PORT", "18788"))
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "https").split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    public_origin = ""
    if host and not host.startswith(("localhost", "127.0.0.1")) and not host.endswith(f":{port}"):
        public_origin = f"{proto}://{host}".rstrip("/")

    links = []
    ipad_links = []
    ipad_home = []
    if public_origin:
        links.append(f"{public_origin}/remote")
        ipad_links.append(f"{public_origin}/ipad/{IPAD_BUILD}")
        ipad_home.append(f"{public_origin}/ipad")
    links.extend(f"https://{ip}:{port}/remote" for ip in ips)
    ipad_links.extend(f"https://{ip}:{port}/ipad/{IPAD_BUILD}" for ip in ips)
    ipad_home.extend(f"https://{ip}:{port}/ipad" for ip in ips)
    return {
        "ips": ips,
        "port": port,
        "scheme": "https",
        "links": links,
        "ipad_links": ipad_links,
        "ipad_home": ipad_home,
        "ipad_build": IPAD_BUILD,
    }


def _protect_global_license(request: Request) -> None:
    if not auth_required():
        return
    if not current_user(request):
        raise HTTPException(401, "请先登录")
    raise HTTPException(403, "账号模式下请使用会员订阅，不再使用全局授权码")


@app.get("/api/license")
def api_license(request: Request) -> dict[str, Any]:
    if auth_required() and not current_user(request):
        raise HTTPException(401, "请先登录")
    return license_status(DATA)


@app.post("/api/license/activate")
def api_license_activate(request: Request, body: LicenseActivateBody) -> dict[str, Any]:
    _protect_global_license(request)
    try:
        return activate_license(DATA, body.key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/license/checkout")
def api_license_checkout(request: Request, body: LicenseCheckoutBody) -> dict[str, Any]:
    _protect_global_license(request)
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


@app.post("/api/remote-token/{session_id}")
def remote_token(session_id: str, user: dict[str, Any] = Depends(require_user)) -> dict[str, str]:
    try:
        return {"token": db.create_remote_token(session_id, user["id"])}
    except PermissionError as exc:
        raise HTTPException(404, "session not found") from exc


@app.post("/api/remote-claim")
def remote_claim(body: RemoteClaimBody, request: Request) -> dict[str, Any]:
    """电脑声明当前手机麦/iPad 应对准哪一课；关掉时传空。本机可无登录。"""
    user = current_user(request)
    sid = (body.session_id or "").strip()
    if sid:
        if user and not db.owns_learning_session(sid, user["id"]):
            raise HTTPException(403, "无权访问该课程")
        folder = DATA / sid
        if not folder.exists():
            raise HTTPException(404, "课程不存在")
        set_active_remote(sid)
        return {"session_id": sid}
    set_active_remote(None)
    return {"session_id": ""}

@app.get("/remote", response_class=HTMLResponse)
def remote_mic_page(response: Response, s: str = "", token: str = "") -> HTMLResponse:
    _ = s
    page = HTMLResponse(
        content=REMOTE_PAGE,
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )
    if token and response is not None:
        page.set_cookie("enprato_remote_token", token, httponly=True, secure=cookie_secure(), samesite="lax", max_age=30 * 60, path="/")
    return page


@app.get("/ipad")
def ipad_studio_redirect(s: str = "", b: str = "") -> RedirectResponse:
    from urllib.parse import urlencode

    _ = b
    q: dict[str, str] = {"b": IPAD_BUILD}
    if s:
        q["s"] = s
    return RedirectResponse(url=f"/ipad/{IPAD_BUILD}?{urlencode(q)}", status_code=302)


@app.get("/ipad/{page_build}", response_class=HTMLResponse)
def ipad_studio_page(response: Response, page_build: str, s: str = "", b: str = "", token: str = "") -> HTMLResponse:
    _ = page_build
    _ = s
    _ = b
    page = HTMLResponse(
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
    if token and response is not None:
        page.set_cookie("enprato_remote_token", token, httponly=True, secure=cookie_secure(), samesite="lax", max_age=30 * 60, path="/")
    return page


@app.get("/")
def backend_home() -> RedirectResponse:
    """Keep browser back from landing on an unhandled backend root URL."""
    return RedirectResponse(url="/remote", status_code=307)


@app.get("/api/session/{session_id}/remote-state")
def remote_state(
    session_id: str,
    user: dict[str, Any] = Depends(require_session_access),
    sentences_rev: str = "",
) -> dict[str, Any]:
    folder = require_owned_session(session_id, user)
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
    sentences_out = []
    for sentence in sentences:
        item = {
            "start": float(sentence.get("start") or 0),
            "end": float(sentence.get("end") or 0),
            "text": str(sentence.get("text") or ""),
        }
        if sentence.get("parent_id") is not None:
            item["parent_id"] = str(sentence["parent_id"])
            item["segment_index"] = int(sentence.get("segment_index") or 0)
        sentences_out.append(item)
    current_rev = hashlib.sha256(json.dumps(sentences_out, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
    payload: dict[str, Any] = {
        "session_id": session_id,
        "index": index,
        "total": len(sentences),
        "target": target,
        "draft": draft,
        "drafts": drafts_out,
        "phase": detail["phase"],
        "sentences_rev": current_rev,
    }
    if sentences_rev.strip() != current_rev:
        payload["sentences"] = sentences_out
    return payload


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
def remote_draft(session_id: str, body: RemoteDraftBody, user: dict[str, Any] = Depends(require_session_access)) -> dict[str, Any]:
    folder = require_owned_session(session_id, user)
    sentences = read_json(folder / "sentences.json", [])
    if not isinstance(sentences, list) or not sentences:
        raise HTTPException(404, "没有句子")
    meta = read_meta(folder)
    # 改字：按手机点的段落写入，不因句号落后而串句
    idx = max(0, min(int(body.index), len(sentences) - 1))
    drafts = meta.get("drafts") if isinstance(meta.get("drafts"), dict) else {}
    drafts = {str(k): str(v) for k, v in drafts.items()}
    previous = dict(drafts)
    drafts[str(idx)] = body.text
    if body.text.strip() and user.get("id") == "lan-local":
        note_trial_use(DATA, session_id)
    _safe_record_dictations(user, session_id, folder, previous, {str(idx): body.text})
    write_meta(folder, drafts=drafts, index=idx, phase="dictate")
    item = push_remote_result(session_id, idx, body.text)
    touch_phone(session_id)
    return {"text": body.text, "index": idx, "id": item["id"]}


@app.post("/api/session/{session_id}/remote-drafts")
def remote_drafts_bulk(session_id: str, body: RemoteDraftsBody, user: dict[str, Any] = Depends(require_session_access)) -> dict[str, Any]:
    """手机整页听写稿一次写入，按句号合并，不丢中间句。"""
    folder = require_owned_session(session_id, user)
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
    previous = dict(drafts)
    drafts = collapse_identical_drafts(apply_draft_snapshot(drafts, incoming))
    _safe_record_dictations(user, session_id, folder, previous, drafts)
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
    if user.get("id") == "lan-local" and any(str(v or "").strip() for v in drafts.values()):
        note_trial_use(DATA, session_id)
    touch_phone(session_id)
    new_at_index = str(drafts.get(str(keep_index), ""))
    if keep_index != meta_idx or (new_at_index.strip() and new_at_index != old_at_index):
        push_remote_result(session_id, keep_index, new_at_index)
    return {"ok": True, "index": keep_index, "drafts": drafts}


@app.post("/api/session/{session_id}/remote-next")
def remote_next(session_id: str, user: dict[str, Any] = Depends(require_session_access)) -> dict[str, Any]:
    """手机点「下一句」：推进课程序号，电脑会跟着走。"""
    folder = require_owned_session(session_id, user)
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


async def _recognize_remote_audio(
    *,
    session_id: str,
    user: dict[str, Any],
    folder: Path,
    raw: Path,
    mime: str,
    index: int,
    mode: str,
    request_id: str,
    client_request_id: str = "",
) -> dict[str, Any]:
    sentences = read_json(folder / "sentences.json", [])
    if not isinstance(sentences, list) or not sentences:
        raise HTTPException(404, "没有句子")
    meta = read_meta(folder)
    idx = max(0, min(int(index), len(sentences) - 1))
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
    wav = raw.with_name(raw.name + ".converted.wav")
    raw_size = raw.stat().st_size if raw.is_file() else 0
    raw_duration = 0.0
    wav_duration = 0.0
    asr_started = 0.0
    diagnostics: dict[str, Any] = {}
    text = ""
    asr_code = ""
    try:
        try:
            raw_duration = probe_duration(raw)
        except Exception:
            raw_duration = 0.0
        try:
            await run_in_threadpool(convert_to_wav, raw, wav)
            wav_duration = probe_duration(wav)
        except Exception as exc:
            stt_log.event("STT_FAILED", client_request_id=client_request_id or None, server_request_id=request_id, exception=type(exc).__name__, error_type="invalid_audio", http_status=400)
            raise HTTPException(400, "音频格式无法转换，请重新录音") from exc
        stt_log.event("STT_AUDIO_VALIDATED", client_request_id=client_request_id or None, server_request_id=request_id, bytes=raw_size, mime=mime, wav_duration=wav_duration)
        try:
            asr_started = asyncio.get_running_loop().time()
            stt_log.event("STT_ASR_STARTED", client_request_id=client_request_id or None, server_request_id=request_id)
            diagnostics = await asyncio.wait_for(
                run_in_threadpool(
                    lambda: transcribe_speech_detailed(wav, context, target, fast=True),
                ),
                timeout=120.0,
            )
            text = str(diagnostics.get("text") or "")
            asr_elapsed = asyncio.get_running_loop().time() - asr_started if asr_started else 0.0
            stt_log.event(
                "STT_ASR_FINISHED",
                client_request_id=client_request_id or None,
                server_request_id=request_id,
                session_id=session_id,
                index=idx,
                audio_duration=round(float(wav_duration or raw_duration or 0.0), 3),
                audio_size=raw_size,
                mime_type=mime,
                words=len(text.split()),
                text_preview=text[:120],
                fast=bool(diagnostics.get("fast")),
                retried=bool(diagnostics.get("retried")),
                retry_reason=str(diagnostics.get("retry_reason") or ""),
                last_end=round(float(diagnostics.get("last_end") or 0.0), 3),
                prompt_mode=str(diagnostics.get("prompt_mode") or ""),
                compact_prompt_enabled=bool(diagnostics.get("compact_prompt_enabled")),
                expected_target_len=int(diagnostics.get("expected_target_len") or 0),
                expected_target_preview=str(diagnostics.get("expected_target_preview") or "")[:80],
                prompt_guard_result=str(diagnostics.get("prompt_guard_result") or ""),
                asr_elapsed=round(asr_elapsed, 3),
            )
            logger.info(
                "asr request_id=%s session=%s index=%s size=%s mime=%s raw_duration=%.3f wav_duration=%.3f asr_elapsed=%.3f segments=%s last_end=%.3f words=%s retried=%s retry_reason=%s prompt_guard_result=%s result=success",
                request_id, session_id, idx, raw_size, mime, raw_duration, wav_duration, asr_elapsed,
                diagnostics.get("segment_count", 0), diagnostics.get("last_end", 0.0), len(text.split()),
                diagnostics.get("retried", False), diagnostics.get("retry_reason", ""),
                diagnostics.get("prompt_guard_result", ""),
            )
            asr_code = str(diagnostics.get("code") or "")
            if asr_code not in {"prompt_leakage", "empty_transcript"}:
                if wav_duration > 8 and float(diagnostics.get("last_end") or 0.0) < wav_duration * 0.55:
                    raise HTTPException(422, f"ASR_INCOMPLETE: 这次只识别到部分内容，请再试一次。Request ID: {request_id}")
        except asyncio.TimeoutError as exc:
            stt_log.event("STT_FAILED", client_request_id=client_request_id or None, server_request_id=request_id, exception="TimeoutError", error_type="asr_timeout", http_status=504)
            raise HTTPException(504, f"ASR_TIMEOUT: server recognition timed out. Request ID: {request_id}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        stt_log.event("STT_FAILED", client_request_id=client_request_id or None, server_request_id=request_id, exception=type(exc).__name__, error_type="asr_exception", http_status=500)
        logger.exception(
            "asr request_id=%s session=%s index=%s size=%s mime=%s raw_duration=%.3f wav_duration=%.3f asr_elapsed=%.3f error=%s",
            request_id, session_id, idx, raw_size, mime, raw_duration,
            wav_duration, asyncio.get_running_loop().time() - asr_started if asr_started else 0.0,
            type(exc).__name__,
        )
        raise HTTPException(500, f"ASR_SERVER_ERROR: 服务器识别失败，请稍后重试。Request ID: {request_id}") from exc
    finally:
        wav.unlink(missing_ok=True)

    asr_code = str(diagnostics.get("code") or "")
    if asr_code in {"prompt_leakage", "empty_transcript"}:
        text = ""

    if text.strip():
        from .asr import _clean_stt, _spell_toward_target, collapse_repeated_clauses

        if mode != "insert":
            idx = _match_sentence_index(sentences, text, idx)
            target = str(sentences[idx].get("text") or "")
        text = _spell_toward_target(_clean_stt(text), target) if target else _clean_stt(text)
        text = collapse_repeated_clauses(text)

    touch_phone(session_id)
    payload = {
        "text": text,
        "index": idx,
        "id": 0,
        "request_id": request_id,
        "client_request_id": client_request_id,
    }
    if asr_code:
        payload["code"] = asr_code
        payload["message"] = "这次没有听清，请重新说一次"
    if mode == "insert":
        return payload

    prev = str(drafts.get(str(idx)) or "").strip()
    if text.strip():
        from .asr import merge_dictation_text

        text = merge_dictation_text(prev, text, target)
    if text.strip():
        previous = dict(drafts)
        drafts[str(idx)] = text
        if user.get("id") == "lan-local":
            note_trial_use(DATA, session_id)
        _safe_record_dictations(user, session_id, folder, previous, {str(idx): text})
        write_meta(folder, drafts=drafts, index=idx, phase="dictate")
        item = push_remote_result(session_id, idx, text)
    else:
        write_meta(folder, index=idx, phase="dictate")
        item = {"id": 0, "index": idx, "text": ""}
    out = {"text": text, "index": idx, "id": item["id"], "request_id": request_id, "client_request_id": client_request_id}
    if asr_code:
        out["code"] = asr_code
        out["message"] = "这次没有听清，请重新说一次"
    return out


@app.post("/api/session/{session_id}/remote-stt")
async def remote_stt(
    session_id: str,
    request: Request,
    audio: UploadFile = File(...),
    index: int = Form(...),
    mode: str = Form("replace"),
    user: dict[str, Any] = Depends(require_session_access),
) -> dict[str, Any]:
    require_member_or_trial(user)
    folder = require_owned_session(session_id, user)
    request_id = str(getattr(request.state, "request_id", "") or uuid.uuid4().hex[:12])
    tmp_dir = DATA / "_stt"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    upload_id = uuid.uuid4().hex
    raw = tmp_dir / f"{upload_id}{Path(audio.filename or 'phone.webm').suffix or '.webm'}"
    try:
        try:
            _save_upload(audio, raw)
        except Exception as exc:
            raise HTTPException(400, "音频上传失败，请重新录音") from exc
        return await _recognize_remote_audio(
            session_id=session_id,
            user=user,
            folder=folder,
            raw=raw,
            mime=str(audio.content_type or ""),
            index=index,
            mode=mode,
            request_id=request_id,
            client_request_id=str(getattr(request.state, "client_request_id", "") or ""),
        )
    finally:
        raw.unlink(missing_ok=True)


async def _stt_from_raw_bytes(
    session_id: str,
    request: Request,
    user: dict[str, Any],
    index: int,
    mode: str,
    body: bytes,
) -> dict[str, Any]:
    require_member_or_trial(user)
    folder = require_owned_session(session_id, user)
    request_id = str(getattr(request.state, "request_id", "") or uuid.uuid4().hex[:12])
    client_request_id = str(getattr(request.state, "client_request_id", "") or "")
    stt_log.event(
        "STT_BODY_RECEIVED",
        client_request_id=client_request_id or None,
        server_request_id=request_id,
        bytes=len(body or b""),
        content_type=request.headers.get("content-type", ""),
    )
    if not body or len(body) < 200:
        stt_log.event("STT_FAILED", client_request_id=client_request_id or None, server_request_id=request_id, error_type="empty_body", http_status=400)
        raise HTTPException(400, "音频上传失败，请重新录音")
    if len(body) > 25 * 1024 * 1024:
        stt_log.event("STT_FAILED", client_request_id=client_request_id or None, server_request_id=request_id, error_type="too_large", http_status=413)
        raise HTTPException(413, "录音过大，请缩短后重试")
    mime = str(request.headers.get("content-type") or "audio/wav").split(";")[0].strip().lower()
    if mime in {"application/json", "text/plain", "text/html"}:
        stt_log.event("STT_FAILED", client_request_id=client_request_id or None, server_request_id=request_id, error_type="invalid_mime", http_status=400)
        raise HTTPException(400, "无效的音频类型")
    owner = str(user.get("id") or "lan-local")
    action, cached = stt_jobs.begin(owner, client_request_id)
    if action == "replay" and isinstance(cached, dict):
        replayed = dict(cached)
        replayed["request_id"] = request_id
        replayed["client_request_id"] = client_request_id
        stt_log.event("STT_RESPONSE_SENT", client_request_id=client_request_id or None, server_request_id=request_id, replay=True)
        return replayed
    if action == "inflight":
        raise HTTPException(409, "同一段录音正在识别，请稍候")
    suffix = ".wav"
    if "mp4" in mime or "aac" in mime or "m4a" in mime:
        suffix = ".m4a"
    elif "ogg" in mime:
        suffix = ".ogg"
    elif "webm" in mime:
        suffix = ".webm"
    tmp_dir = DATA / "_stt"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    raw = tmp_dir / f"{uuid.uuid4().hex}{suffix}"
    try:
        raw.write_bytes(body)
        result = await _recognize_remote_audio(
            session_id=session_id,
            user=user,
            folder=folder,
            raw=raw,
            mime=mime,
            index=index,
            mode=mode,
            request_id=request_id,
            client_request_id=client_request_id,
        )
        stt_jobs.finish(owner, client_request_id, result)
        stt_log.event("STT_RESPONSE_SENT", client_request_id=client_request_id or None, server_request_id=request_id, replay=False)
        return result
    except Exception:
        stt_jobs.fail(owner, client_request_id)
        raise
    finally:
        raw.unlink(missing_ok=True)


@app.api_route("/api/session/{session_id}/remote-stt-bin", methods=["PUT", "POST"])
async def remote_stt_bin(
    session_id: str,
    request: Request,
    index: int = 0,
    mode: str = "insert",
    user: dict[str, Any] = Depends(require_session_access),
) -> dict[str, Any]:
    body = await request.body()
    return await _stt_from_raw_bytes(session_id, request, user, index, mode, body)


@app.get("/api/session/{session_id}/remote-inbox")
def remote_inbox(session_id: str, after: int = 0, user: dict[str, Any] = Depends(require_session_access)) -> dict[str, Any]:
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
    user: dict[str, Any] = Depends(require_user_or_local),
) -> dict[str, Any]:
    require_member_or_trial(user)
    session_id = uuid.uuid4().hex[:12]
    folder = DATA / session_id
    folder.mkdir(parents=True, exist_ok=True)
    db.register_learning_session(session_id, user["id"])
    if user["id"] != "lan-local":
        if not db.membership_status(user["id"]).get("active") and not db.consume_trial(user["id"], "prepare:" + session_id):
            shutil.rmtree(folder, ignore_errors=True)
            raise HTTPException(402, "免费学习素材次数已用完")

    suffix = Path(video.filename or "video.mp4").suffix or ".mp4"
    source = folder / f"source{suffix}"
    audio = folder / "audio.wav"
    _save_upload(video, source)
    try:
        media = find_session_media(folder) or source
        extract_wav(media, audio)
        ensure_playback_audio(folder, media)
    except Exception as exc:
        _refund_failed_prepare(user, session_id)
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(400, f"抽音频失败（需要视频里有音轨，并已安装 ffmpeg）: {exc}") from exc

    sentences: list[dict[str, Any]] = []
    if captions is not None and captions.filename:
        raw = (await captions.read()).decode("utf-8", errors="replace")
        sentences = _cues_from_text(raw, captions.filename or "")
    try:
        return _finish_session(
            folder,
            session_id,
            audio,
            sentences,
            title=Path(video.filename or "video").stem,
            source_kind="file",
        )
    except Exception:
        _refund_failed_prepare(user, session_id)
        raise


@app.post("/api/prepare-url")
async def prepare_url(body: PrepareUrlBody, user: dict[str, Any] = Depends(require_user_or_local)) -> dict[str, Any]:
    try:
        url = validate_media_url(body.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    preview = url_preview(url)
    host = url_host_family(url)
    logger.info("url_import_start host=%s preview=%s", host, preview)
    owner = url_import_jobs._owner_id(user)
    with url_import_jobs.accept_lock:
        active = url_import_jobs.find_active_job(owner, url)
        if active:
            logger.info(
                "url_import_job job_id=%s session_id=%s stage=%s reused=1",
                active.get("job_id"),
                active.get("session_id"),
                active.get("stage"),
            )
            return url_import_jobs.public_job(active)
        if not body.create_new_session:
            existing = find_session_id_by_url(DATA, url)
            can_reuse = bool(existing) and (
                user["id"] == "lan-local" or db.owns_learning_session(existing, user["id"])
            )
            if can_reuse and existing:
                folder_existing = DATA / existing
                if find_session_media(folder_existing):
                    detail = session_detail(folder_existing, existing)
                    if detail:
                        logger.info("url_import_start host=%s reused_session=%s", preview, existing)
                        return {"status": "ready", **detail}
                shutil.rmtree(folder_existing, ignore_errors=True)
        require_member_or_trial(user)
        session_id = uuid.uuid4().hex[:12]
        folder = DATA / session_id
        folder.mkdir(parents=True, exist_ok=True)
        db.register_learning_session(session_id, user["id"])
        if user["id"] != "lan-local":
            if not db.membership_status(user["id"]).get("active") and not db.consume_trial(user["id"], "prepare:" + session_id):
                shutil.rmtree(folder, ignore_errors=True)
                raise HTTPException(402, "免费学习素材次数已用完")
        job = url_import_jobs.create_job(owner, session_id, url)
        return {
            "status": "processing",
            "job_id": job["job_id"],
            "session_id": session_id,
            "stage": job["stage"],
            "message": url_import_jobs.STAGE_MESSAGES[job["stage"]],
        }


@app.get("/api/import-status/{job_id}")
def import_status(job_id: str, user: dict[str, Any] = Depends(require_user_or_local)) -> dict[str, Any]:
    job = url_import_jobs.get_job(job_id)
    owner = url_import_jobs._owner_id(user)
    if not job or str(job.get("user_id") or "") != owner:
        raise HTTPException(404, "任务不存在")
    return url_import_jobs.public_job(job)


@app.get("/api/import-jobs/active")
def import_jobs_active(user: dict[str, Any] = Depends(require_user_or_local)) -> dict[str, Any]:
    job = url_import_jobs.find_active_job(url_import_jobs._owner_id(user))
    if not job:
        return {"job": None}
    return {"job": url_import_jobs.public_job(job)}


@app.get("/api/sessions")
def api_sessions(user: dict[str, Any] = Depends(require_user_or_local)) -> dict[str, Any]:
    items = list_sessions(DATA)
    if user.get("id") != "lan-local":
        items = [item for item in items if db.owns_learning_session(item["session_id"], user["id"])]
    return {"sessions": items}


@app.get("/api/progress")
def api_progress(days: int | None = None, user: dict[str, Any] = Depends(require_user_or_local)) -> dict[str, Any]:
    if days not in (None, 7, 30):
        raise HTTPException(400, "days must be 7, 30, or omitted")
    return progress_for_user(user, days)


@app.post("/api/progress/complete/{session_id}")
def api_progress_complete(
    session_id: str,
    body: LearningCompleteBody,
    user: dict[str, Any] = Depends(require_session_access),
) -> dict[str, Any]:
    try:
        return complete_session(user, session_id, DATA, body.duration_seconds)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/session/{session_id}/resplit-remaining")
def api_resplit_remaining(session_id: str, body: ResplitBody, user: dict[str, Any] = Depends(require_session_access)) -> dict[str, Any]:
    if os.environ.get("ENPRATO_ENABLE_RESPLIT_REMAINING") != "1":
        raise HTTPException(404, "resplit tool is disabled")
    folder = require_owned_session(session_id, user)
    try:
        if body.backup_id:
            return rollback_resplit_session(folder, body.backup_id)
        return resplit_remaining_session(folder)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/session/{session_id}")
def api_session(session_id: str, user: dict[str, Any] = Depends(require_session_access)) -> dict[str, Any]:
    folder = require_owned_session(session_id, user)
    detail = session_detail(folder, session_id)
    if not detail:
        raise HTTPException(404, "session 不存在")
    return detail


@app.patch("/api/session/{session_id}")
def api_save_progress(session_id: str, body: ProgressBody, user: dict[str, Any] = Depends(require_session_access)) -> dict[str, str]:
    source = str(body.source_session_id or "").strip()
    if source and source != session_id:
        logging.getLogger("enprato.progress").warning(
            "rejected progress patch: source_session_id=%s target=%s",
            source,
            session_id,
        )
        raise HTTPException(400, "source_session_id mismatch")
    folder = require_owned_session(session_id, user)
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
        _safe_record_dictations(user, session_id, folder, existing, fields["drafts"])
        if any(str(v or "").strip() for v in body.drafts.values()) and user.get("id") == "lan-local":
            note_trial_use(DATA, session_id)
    write_meta(folder, **fields)
    return {"status": "ok"}


@app.delete("/api/session/{session_id}")
def api_delete_session(session_id: str, user: dict[str, Any] = Depends(require_session_access)) -> dict[str, str]:
    folder = require_owned_session(session_id, user)
    shutil.rmtree(folder, ignore_errors=True)
    return {"status": "ok"}


class SpeakerPlayBody(BaseModel):
    start: float
    end: float
    volume: float = 1.0


@app.post("/api/session/{session_id}/speaker-play")
def session_speaker_play(session_id: str, body: SpeakerPlayBody, user: dict[str, Any] = Depends(require_session_access)) -> dict[str, str]:
    """Play sentence audio via ffplay → Windows default device (Realtek Digital Output)."""
    folder = require_owned_session(session_id, user)
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
def session_speaker_stop(session_id: str, user: dict[str, Any] = Depends(require_session_access)) -> dict[str, str]:
    _ = session_id
    stop_speaker()
    return {"status": "ok"}


@app.get("/api/session/{session_id}/audio")
def session_audio(session_id: str, user: dict[str, Any] = Depends(require_session_access)) -> FileResponse:
    folder = require_owned_session(session_id, user)
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
        return FileResponse(
            playback,
            media_type=mime,
            filename=playback.name,
            content_disposition_type="inline",
            headers={"Accept-Ranges": "bytes", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
    wav = folder / "audio.wav"
    if not wav.is_file():
        raise HTTPException(404, "没有音轨")
    return FileResponse(
        wav,
        media_type="audio/wav",
        filename="audio.wav",
        content_disposition_type="inline",
        headers={"Accept-Ranges": "bytes", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.get("/api/session/{session_id}/thumb")
def session_thumb(session_id: str, user: dict[str, Any] = Depends(require_session_access)) -> FileResponse:
    folder = require_owned_session(session_id, user)
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
        content_disposition_type="inline",
        headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )


@app.get("/api/session/{session_id}/video")
def session_video(session_id: str, user: dict[str, Any] = Depends(require_session_access)) -> FileResponse:
    folder = require_owned_session(session_id, user)
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
        content_disposition_type="inline",
        headers={"Accept-Ranges": "bytes", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.post("/api/stt")
async def stt(
    request: Request,
    audio: UploadFile = File(...),
    context: str = Form(default=""),
    target: str = Form(default=""),
    user: dict[str, Any] = Depends(require_user_or_local),
) -> dict[str, str]:
    require_member_or_trial(user)
    tmp_dir = DATA / "_stt"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    request_id = str(getattr(request.state, "request_id", "") or uuid.uuid4().hex[:12])
    raw = tmp_dir / f"{uuid.uuid4().hex}{Path(audio.filename or 'clip.webm').suffix or '.webm'}"
    wav = raw.with_suffix(".wav")
    try:
        _save_upload(audio, raw)
        raw_size = raw.stat().st_size
        raw_duration = probe_duration(raw)
        await run_in_threadpool(convert_to_wav, raw, wav)
        wav_duration = probe_duration(wav)
        asr_started = asyncio.get_running_loop().time()
        result = await asyncio.wait_for(run_in_threadpool(
            lambda: transcribe_speech_detailed(wav, context=context, target=target, fast=True),
        ), timeout=120.0)
        text = str(result.get("text") or "")
        logger.info("asr request_id=%s size=%s mime=%s raw_duration=%.3f wav_duration=%.3f asr_elapsed=%.3f segments=%s last_end=%.3f words=%s retried=%s retry_reason=%s result=success", request_id, raw_size, audio.content_type, raw_duration, wav_duration, asyncio.get_running_loop().time() - asr_started, result.get("segment_count", 0), result.get("last_end", 0.0), len(text.split()), result.get("retried", False), result.get("retry_reason", ""))
        if wav_duration > 8 and float(result.get("last_end") or 0.0) < wav_duration * 0.55:
            raise HTTPException(422, f"ASR_INCOMPLETE: partial recognition. Request ID: {request_id}")
        return {"text": text, "request_id": request_id}
    except asyncio.TimeoutError as exc:
        raise HTTPException(504, f"ASR_TIMEOUT: server recognition timed out. Request ID: {request_id}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("asr request_id=%s error=%s", request_id, type(exc).__name__)
        raise HTTPException(500, f"ASR_SERVER_ERROR: server recognition failed. Request ID: {request_id}") from exc
    finally:
        raw.unlink(missing_ok=True)
        wav.unlink(missing_ok=True)


@app.get("/api/define")
def define(word: str) -> dict[str, Any]:
    if not word.strip():
        raise HTTPException(400, "缺少单词")
    return lookup_word(word)


@app.get("/api/translate")
async def api_translate(text: str) -> dict[str, str]:
    if not text.strip():
        raise HTTPException(400, "缺少句子")
    zh = await run_in_threadpool(translate_en_zh, text)
    return {"text": text, "zh": zh}


@app.post("/api/score")
async def score(
    audio: UploadFile = File(...),
    session_id: str = Form(...),
    user: dict[str, Any] = Depends(require_user_or_local),
) -> dict[str, Any]:
    require_member_or_trial(user)
    folder = require_owned_session(session_id, user)
    original = folder / "audio.wav"
    if not original.is_file():
        media = find_session_media(folder)
        if media is None:
            raise HTTPException(400, "原音频不存在")
        try:
            extract_wav(media, original)
        except Exception:
            raise HTTPException(400, "原音频不存在") from None
    sentences = json.loads((folder / "sentences.json").read_text(encoding="utf-8"))
    reference = " ".join(item["text"] for item in sentences)
    raw = folder / f"user{Path(audio.filename or 'shadow.webm').suffix or '.webm'}"
    wav = folder / "user.wav"
    _save_upload(audio, raw)
    convert_to_wav(raw, wav)
    return score_shadowing(original, wav, reference)
