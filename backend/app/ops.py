from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .auth import cookie_secure

COOKIE_NAME = "enprato_ops"
NOINDEX = {"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store"}
_hits: dict[str, deque[float]] = defaultdict(deque)

OPS_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="robots" content="noindex,nofollow" />
  <title>Enprato 数据</title>
  <style>
    :root { color-scheme: light; }
    body { margin: 0; font-family: "Segoe UI", "PingFang SC", sans-serif; background: #f4f7f6; color: #12221f; }
    main { max-width: 720px; margin: 0 auto; padding: 32px 20px; }
    h1 { margin: 0 0 6px; font-size: 22px; }
    .meta { margin: 0 0 24px; color: #5b6b67; font-size: 13px; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    article { background: #fff; border: 1px solid #d7e3df; border-radius: 16px; padding: 18px 16px; }
    span { display: block; color: #5b6b67; font-size: 13px; }
    strong { display: block; margin-top: 8px; font-size: 36px; letter-spacing: -0.03em; }
    @media (max-width: 560px) { .grid { grid-template-columns: 1fr; } strong { font-size: 30px; } }
  </style>
</head>
<body>
  <main>
    <h1>Enprato 数据</h1>
    <p class="meta" id="meta">每 5 秒刷新</p>
    <section class="grid">
      <article><span>注册人数</span><strong id="registered">-</strong></article>
      <article><span>付费人数</span><strong id="paidUsers">-</strong></article>
      <article><span>付费金额</span><strong id="paidAmount">-</strong></article>
      <article><span>在线人数</span><strong id="online">-</strong></article>
    </section>
  </main>
  <script>
    const yuan = (n) => Number(n || 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " 元";
    async function load() {
      const res = await fetch("/api/ops/stats", { credentials: "same-origin", cache: "no-store" });
      if (!res.ok) { document.getElementById("meta").textContent = "无法读取数据"; return; }
      const data = await res.json();
      document.getElementById("registered").textContent = data.registered_users;
      document.getElementById("paidUsers").textContent = data.paid_users;
      document.getElementById("paidAmount").textContent = yuan(data.paid_amount_yuan);
      document.getElementById("online").textContent = data.online_users;
      document.getElementById("meta").textContent = "近 " + data.online_window_minutes + " 分钟有请求的登录用户 · " + (data.as_of || "");
    }
    load();
    setInterval(load, 5000);
  </script>
</body>
</html>
"""


def enforce_ops(request: Request) -> None:
    key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    hits = _hits[key]
    while hits and now - hits[0] >= 60:
        hits.popleft()
    if len(hits) >= 60:
        raise HTTPException(429, "请求过于频繁，请稍后重试")
    hits.append(now)


def configured_token() -> str:
    return os.environ.get("ENPRATO_OPS_TOKEN", "").strip()


def token_configured() -> bool:
    return len(configured_token()) >= 16


def token_matches(candidate: str) -> bool:
    expected = configured_token()
    given = (candidate or "").strip()
    if not expected or not given:
        return False
    return hmac.compare_digest(
        hashlib.sha256(expected.encode("utf-8")).digest(),
        hashlib.sha256(given.encode("utf-8")).digest(),
    )


def request_token(request: Request) -> str:
    query = str(request.query_params.get("k") or "")
    if query:
        return query
    cookie = request.cookies.get(COOKIE_NAME, "")
    if cookie:
        return cookie
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def require_ops(request: Request) -> None:
    if not token_configured():
        raise HTTPException(404, "Not Found")
    if not token_matches(request_token(request)):
        raise HTTPException(404, "Not Found")


def set_ops_cookie(response: Response, request: Request) -> None:
    response.set_cookie(
        COOKIE_NAME,
        configured_token(),
        httponly=True,
        secure=cookie_secure(request),
        samesite="strict",
        path="/api/ops",
        max_age=60 * 60 * 24 * 30,
    )


def ops_page_response() -> HTMLResponse:
    return HTMLResponse(OPS_PAGE, headers=NOINDEX)


def ops_json_response(payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse(payload, headers=NOINDEX)


def ops_login_redirect(request: Request) -> RedirectResponse:
    response = RedirectResponse("/api/ops", status_code=303)
    set_ops_cookie(response, request)
    for key, value in NOINDEX.items():
        response.headers[key] = value
    return response
