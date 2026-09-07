from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlencode

import httpx

STATE_COOKIE = "enprato_oauth_state"


class WechatOAuthError(RuntimeError):
    pass


class WechatNotConfigured(WechatOAuthError):
    pass


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def web_configured() -> bool:
    return bool(_env("WECHAT_WEB_APP_ID") and _env("WECHAT_WEB_APP_SECRET"))


def oa_configured() -> bool:
    return bool(_env("WECHAT_OA_APP_ID") and _env("WECHAT_OA_APP_SECRET"))


def wechat_login_configured() -> bool:
    return web_configured() or oa_configured()


def is_wechat_browser(user_agent: str) -> bool:
    return "micromessenger" in (user_agent or "").lower()


def wechat_channel(user_agent: str) -> str | None:
    if is_wechat_browser(user_agent):
        return "oa" if oa_configured() else None
    if web_configured():
        return "web"
    return None


def redirect_uri(request_base: str = "") -> str:
    configured = _env("WECHAT_OAUTH_REDIRECT_URI")
    if configured:
        return configured
    base = request_base.rstrip("/")
    return f"{base}/api/auth/wechat/callback"


def authorize_url(*, channel: str, state: str, request_base: str = "") -> str:
    callback = redirect_uri(request_base)
    if channel == "oa":
        appid = _env("WECHAT_OA_APP_ID")
        if not appid:
            raise WechatNotConfigured("wechat official account is not configured")
        query = urlencode(
            {
                "appid": appid,
                "redirect_uri": callback,
                "response_type": "code",
                "scope": "snsapi_base",
                "state": state,
            }
        )
        return f"https://open.weixin.qq.com/connect/oauth2/authorize?{query}#wechat_redirect"
    appid = _env("WECHAT_WEB_APP_ID")
    if not appid:
        raise WechatNotConfigured("wechat website app is not configured")
    query = urlencode(
        {
            "appid": appid,
            "redirect_uri": callback,
            "response_type": "code",
            "scope": "snsapi_login",
            "state": state,
        }
    )
    return f"https://open.weixin.qq.com/connect/qrconnect?{query}#wechat_redirect"


def _app_for_channel(channel: str) -> tuple[str, str]:
    if channel == "oa":
        return _env("WECHAT_OA_APP_ID"), _env("WECHAT_OA_APP_SECRET")
    return _env("WECHAT_WEB_APP_ID"), _env("WECHAT_WEB_APP_SECRET")


def identity_pairs(profile: dict[str, Any]) -> list[tuple[str, str]]:
    appid = str(profile.get("appid") or "")
    openid = str(profile.get("openid") or "").strip()
    unionid = str(profile.get("unionid") or "").strip()
    pairs: list[tuple[str, str]] = []
    if unionid:
        pairs.append(("wechat", unionid))
    if appid and openid:
        pairs.append(("wechat_openid", f"{appid}:{openid}"))
    elif openid:
        pairs.append(("wechat_openid", openid))
    if not pairs:
        raise WechatOAuthError("wechat profile missing openid")
    return pairs


def exchange_code(code: str, channel: str) -> dict[str, Any]:
    appid, secret = _app_for_channel(channel)
    if not appid or not secret:
        raise WechatNotConfigured("wechat login is not configured")
    token_code = (code or "").strip()
    if not token_code:
        raise WechatOAuthError("missing wechat code")
    response = httpx.get(
        "https://api.weixin.qq.com/sns/oauth2/access_token",
        params={
            "appid": appid,
            "secret": secret,
            "code": token_code,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    data = response.json()
    if data.get("errcode"):
        raise WechatOAuthError("wechat code exchange failed")
    openid = str(data.get("openid") or "").strip()
    if not openid:
        raise WechatOAuthError("wechat code exchange missing openid")
    return {
        "appid": appid,
        "openid": openid,
        "unionid": str(data.get("unionid") or "").strip(),
    }


def public_status() -> dict[str, Any]:
    return {
        "available": wechat_login_configured(),
        "web": web_configured(),
        "official_account": oa_configured(),
    }
