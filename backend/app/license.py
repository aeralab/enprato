from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

TRIAL_USES = 5
TRIAL_USE_PAUSE_SECONDS = 60 * 60
LICENSE_VERSION = 1
DEFAULT_SECRET = "change-this-before-selling-enprato"


def now_utc() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return now_utc().isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def license_secret() -> str:
    return os.environ.get("ENPRATO_LICENSE_SECRET", DEFAULT_SECRET)


def _state_path(data_root: Path) -> Path:
    return data_root.parent / "license.json"


def _read_state(data_root: Path) -> dict[str, Any]:
    path = _state_path(data_root)
    if not path.is_file():
        state = {
            "trial_started_at": iso_now(),
            "trial_uses": 0,
            "trial_session_last_used_at": {},
            "license": None,
        }
        _write_state(data_root, state)
        return state
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_state(data_root: Path, state: dict[str, Any]) -> None:
    path = _state_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _b64_json(data: dict[str, Any]) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64_decode_json(value: str) -> dict[str, Any]:
    padded = value + ("=" * (-len(value) % 4))
    data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("invalid license payload")
    return data


def _sign(payload_b64: str) -> str:
    digest = hmac.new(license_secret().encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def make_license_key(
    *,
    plan: str,
    email: str = "",
    order_id: str = "",
    days: int | None = None,
) -> str:
    if plan not in {"monthly", "lifetime"}:
        raise ValueError("plan must be monthly or lifetime")
    payload: dict[str, Any] = {
        "v": LICENSE_VERSION,
        "product": "enprato",
        "plan": plan,
        "email": email.strip(),
        "order_id": order_id.strip(),
        "issued_at": iso_now(),
    }
    if plan == "monthly":
        payload["expires_at"] = (
            now_utc() + timedelta(days=days or 31)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
    payload_b64 = _b64_json(payload)
    return f"ENP-{payload_b64}.{_sign(payload_b64)}"


def verify_license_key(key: str) -> dict[str, Any]:
    raw = key.strip()
    if raw.startswith("ENP-"):
        raw = raw[4:]
    if "." not in raw:
        raise ValueError("授权码格式不正确")
    payload_b64, sig = raw.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload_b64), sig):
        raise ValueError("授权码签名无效")
    payload = _b64_decode_json(payload_b64)
    if payload.get("product") != "enprato":
        raise ValueError("授权码不属于 Enprato")
    if payload.get("plan") not in {"monthly", "lifetime"}:
        raise ValueError("授权类型无效")
    expires_at = str(payload.get("expires_at") or "")
    if expires_at:
        exp = parse_iso(expires_at)
        if not exp or exp <= now_utc():
            raise ValueError("授权码已过期")
    return payload


def _trial_uses(state: dict[str, Any]) -> int:
    legacy_imports = int(state.get("trial_imports") or 0)
    return max(0, int(state.get("trial_uses") or legacy_imports))


def _pay_links_path(data_root: Path) -> Path:
    return data_root.parent / "pay-links.json"


def pay_urls(data_root: Path) -> dict[str, str]:
    monthly = os.environ.get("ENPRATO_PAY_MONTHLY_URL", "").strip()
    lifetime = os.environ.get("ENPRATO_PAY_LIFETIME_URL", "").strip()
    path = _pay_links_path(data_root)
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                monthly = str(raw.get("monthly") or raw.get("monthly_url") or monthly).strip()
                lifetime = str(raw.get("lifetime") or raw.get("lifetime_url") or lifetime).strip()
        except Exception:
            pass
    return {"pay_monthly_url": monthly, "pay_lifetime_url": lifetime}


def mock_pay_enabled(data_root: Path) -> bool:
    if os.environ.get("ENPRATO_ALLOW_MOCK_PAY", "").strip().lower() in {"0", "false", "no"}:
        return False
    if os.environ.get("ENPRATO_ALLOW_MOCK_PAY", "").strip().lower() in {"1", "true", "yes"}:
        return True
    urls = pay_urls(data_root)
    return not urls["pay_monthly_url"] and not urls["pay_lifetime_url"]


def checkout_license(data_root: Path, plan: str) -> dict[str, Any]:
    if plan not in {"monthly", "lifetime"}:
        raise ValueError("套餐类型无效")
    urls = pay_urls(data_root)
    pay_url = urls["pay_monthly_url"] if plan == "monthly" else urls["pay_lifetime_url"]
    if pay_url:
        raise ValueError("已配置外部付款链接，请使用在线支付")
    if not mock_pay_enabled(data_root):
        raise ValueError("付款链接尚未配置")
    key = make_license_key(plan=plan, order_id=f"mock-{int(now_utc().timestamp())}")
    return activate_license(data_root, key)


def license_status(data_root: Path) -> dict[str, Any]:
    state = _read_state(data_root)
    trial_uses = _trial_uses(state)
    trial_active = trial_uses < TRIAL_USES

    lic = state.get("license") if isinstance(state.get("license"), dict) else None
    license_active = False
    plan = "trial"
    expires_at = ""
    email = ""
    if lic:
        try:
            payload = verify_license_key(str(lic.get("key") or ""))
            license_active = True
            plan = str(payload.get("plan") or "monthly")
            expires_at = str(payload.get("expires_at") or "")
            email = str(payload.get("email") or "")
        except ValueError:
            license_active = False

    active = license_active or trial_active
    return {
        "active": active,
        "plan": plan if active else "expired",
        "licensed": license_active,
        "trial_active": trial_active,
        "trial_uses": trial_uses,
        "trial_uses_limit": TRIAL_USES,
        "trial_imports": trial_uses,
        "trial_imports_limit": TRIAL_USES,
        "trial_days": 0,
        "trial_ends_at": "",
        "expires_at": expires_at,
        "email": email,
        "mock_pay_enabled": mock_pay_enabled(data_root),
        **pay_urls(data_root),
    }


def activate_license(data_root: Path, key: str) -> dict[str, Any]:
    payload = verify_license_key(key)
    state = _read_state(data_root)
    state["license"] = {
        "key": key.strip(),
        "activated_at": iso_now(),
        "plan": payload.get("plan"),
        "email": payload.get("email") or "",
        "expires_at": payload.get("expires_at") or "",
    }
    _write_state(data_root, state)
    return license_status(data_root)


def note_trial_use(data_root: Path, session_id: str) -> None:
    status = license_status(data_root)
    if status.get("licensed") or not status.get("trial_active"):
        return

    state = _read_state(data_root)
    sid = session_id.strip()
    if not sid:
        return

    now = now_utc()
    last_map_raw = state.get("trial_session_last_used_at")
    last_map: dict[str, str] = (
        {str(k): str(v) for k, v in last_map_raw.items()}
        if isinstance(last_map_raw, dict)
        else {}
    )
    last = parse_iso(last_map.get(sid, ""))
    if last and (now - last).total_seconds() < TRIAL_USE_PAUSE_SECONDS:
        last_map[sid] = iso_now()
        state["trial_session_last_used_at"] = last_map
        _write_state(data_root, state)
        return

    state["trial_uses"] = _trial_uses(state) + 1
    last_map[sid] = iso_now()
    state["trial_session_last_used_at"] = last_map
    _write_state(data_root, state)


def require_active(data_root: Path) -> None:
    status = license_status(data_root)
    if status.get("active"):
        return
    from fastapi import HTTPException

    raise HTTPException(402, "免费听写 5 次已用完。请开通 19.9 元/月会员，或 199 元/年会员后输入授权码。")
