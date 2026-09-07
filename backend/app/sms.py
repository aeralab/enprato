from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx


class SmsError(RuntimeError):
    pass


class SmsNotConfigured(SmsError):
    pass


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def production_env() -> bool:
    return _env("ENPRATO_ENV").lower() in {"production", "prod"}


def tencent_configured() -> bool:
    return all(
        (
            _env("TENCENT_SMS_SECRET_ID"),
            _env("TENCENT_SMS_SECRET_KEY"),
            _env("TENCENT_SMS_SDK_APP_ID"),
            _env("TENCENT_SMS_SIGN_NAME"),
            _env("TENCENT_SMS_TEMPLATE_ID"),
        )
    )


def dev_sms_allowed() -> bool:
    if production_env():
        return False
    provider = _env("ENPRATO_SMS_PROVIDER").lower() or "disabled"
    allow = _env("ENPRATO_ALLOW_DEV_SMS").lower() in {"1", "true", "yes"}
    return provider == "dev" and allow


def sms_send_ready() -> bool:
    provider = _env("ENPRATO_SMS_PROVIDER").lower() or "disabled"
    if provider == "tencent":
        return tencent_configured()
    return dev_sms_allowed()


def public_status() -> dict[str, Any]:
    provider = _env("ENPRATO_SMS_PROVIDER").lower() or "disabled"
    if provider == "tencent":
        return {"available": tencent_configured(), "provider": "tencent" if tencent_configured() else "disabled"}
    if dev_sms_allowed():
        return {"available": True, "provider": "dev"}
    return {"available": False, "provider": "disabled"}


def _tc3_headers(payload: bytes) -> dict[str, str]:
    secret_id = _env("TENCENT_SMS_SECRET_ID")
    secret_key = _env("TENCENT_SMS_SECRET_KEY")
    host = "sms.tencentcloudapi.com"
    service = "sms"
    algorithm = "TC3-HMAC-SHA256"
    timestamp = int(time.time())
    date = datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d")
    hashed = hashlib.sha256(payload).hexdigest()
    canonical = (
        "POST\n/\n\n"
        "content-type:application/json; charset=utf-8\n"
        f"host:{host}\n\n"
        "content-type;host\n"
        f"{hashed}"
    )
    credential_scope = f"{date}/{service}/tc3_request"
    string_to_sign = (
        f"{algorithm}\n{timestamp}\n{credential_scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    )
    secret_date = hmac.new(("TC3" + secret_key).encode(), date.encode(), hashlib.sha256).digest()
    secret_service = hmac.new(secret_date, service.encode(), hashlib.sha256).digest()
    secret_signing = hmac.new(secret_service, b"tc3_request", hashlib.sha256).digest()
    signature = hmac.new(secret_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = (
        f"{algorithm} Credential={secret_id}/{credential_scope}, "
        "SignedHeaders=content-type;host, "
        f"Signature={signature}"
    )
    return {
        "Authorization": authorization,
        "Content-Type": "application/json; charset=utf-8",
        "Host": host,
        "X-TC-Action": "SendSms",
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": "2021-01-11",
        "X-TC-Region": _env("TENCENT_SMS_REGION") or "ap-guangzhou",
    }


def send_tencent_code(phone: str, code: str) -> None:
    if not tencent_configured():
        raise SmsNotConfigured("tencent sms is not configured")
    payload = json.dumps(
        {
            "PhoneNumberSet": [f"+86{phone}"],
            "SmsSdkAppId": _env("TENCENT_SMS_SDK_APP_ID"),
            "SignName": _env("TENCENT_SMS_SIGN_NAME"),
            "TemplateId": _env("TENCENT_SMS_TEMPLATE_ID"),
            "TemplateParamSet": [code, "5"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    response = httpx.post(
        "https://sms.tencentcloudapi.com",
        content=payload,
        headers=_tc3_headers(payload),
        timeout=15,
    )
    data = response.json()
    send_status = ((data.get("Response") or {}).get("SendStatusSet") or [{}])[0]
    if data.get("Response", {}).get("Error") or str(send_status.get("Code") or "") not in {"Ok", "ok", ""}:
        if data.get("Response", {}).get("Error"):
            raise SmsError("tencent sms send failed")
        if send_status and str(send_status.get("Code") or "") not in {"Ok", "ok"}:
            raise SmsError("tencent sms send failed")


def send_code(phone: str, code: str) -> None:
    provider = _env("ENPRATO_SMS_PROVIDER").lower() or "disabled"
    if provider == "tencent":
        send_tencent_code(phone, code)
        return
    if dev_sms_allowed():
        return
    raise SmsNotConfigured("sms is not configured")
