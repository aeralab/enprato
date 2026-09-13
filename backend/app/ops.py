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
    main { max-width: 760px; margin: 0 auto; padding: 32px 20px; }
    h1 { margin: 0 0 6px; font-size: 22px; }
    .meta { margin: 0 0 24px; color: #5b6b67; font-size: 13px; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    article { background: #fff; border: 1px solid #d7e3df; border-radius: 16px; padding: 18px 16px; }
    span { display: block; color: #5b6b67; font-size: 13px; }
    strong { display: block; margin-top: 8px; font-size: 36px; letter-spacing: -0.03em; }
    .charts { display: grid; gap: 12px; margin-top: 12px; }
    .chart-card h2 { margin: 0; font-size: 15px; font-weight: 650; }
    .chart-card .note { margin: 6px 0 14px; color: #5b6b67; font-size: 12px; }
    .chart-svg { width: 100%; height: 168px; display: block; }
    .chart-svg .grid { stroke: #e4eeea; stroke-width: 1; }
    .chart-svg .fill { opacity: .18; }
    .chart-svg .line { fill: none; stroke-width: 2.5; stroke-linejoin: round; stroke-linecap: round; }
    .chart-svg .dot { stroke: #fff; stroke-width: 1.6; }
    .axis { display: flex; justify-content: space-between; margin-top: 6px; color: #5b6b67; font-size: 12px; }
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
    <section class="charts">
      <article class="chart-card">
        <h2>每日注册</h2>
        <p class="note" id="regNote">按北京时间近 30 天</p>
        <div id="regChart"></div>
        <div class="axis" id="regAxis"></div>
      </article>
      <article class="chart-card">
        <h2>每日付费金额</h2>
        <p class="note" id="payNote">按北京时间近 30 天</p>
        <div id="payChart"></div>
        <div class="axis" id="payAxis"></div>
      </article>
    </section>
  </main>
  <script>
    const yuan = (n) => Number(n || 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " 元";
    const md = (d) => (d || "").slice(5);
    function trend(el, axis, series, key, color) {
      const rows = series || [];
      const values = rows.map((d) => Number(d[key]) || 0);
      const max = Math.max(1, ...values);
      const w = 680, h = 168, left = 8, right = 8, top = 16, bottom = 12;
      const n = Math.max(1, rows.length - 1);
      const pts = rows.map((d, i) => {
        const x = left + (i / n) * (w - left - right);
        const y = top + (1 - values[i] / max) * (h - top - bottom);
        return { x, y, d, v: values[i] };
      });
      const line = pts.map((p, i) => (i ? "L" : "M") + p.x.toFixed(1) + " " + p.y.toFixed(1)).join(" ");
      const area = pts.length
        ? line + " L " + pts[pts.length - 1].x.toFixed(1) + " " + (h - bottom) + " L " + pts[0].x.toFixed(1) + " " + (h - bottom) + " Z"
        : "";
      const gridY = [0.25, 0.5, 0.75].map((p) => {
        const y = top + (1 - p) * (h - top - bottom);
        return '<line class="grid" x1="' + left + '" x2="' + (w - right) + '" y1="' + y + '" y2="' + y + '" />';
      }).join("");
      const dots = pts.map((p, i) => {
        const last = i === pts.length - 1;
        if (!last && p.v <= 0) return "";
        const r = last ? 4.5 : 3;
        const label = key === "registered" ? (p.v + " 人") : yuan(p.v);
        return '<circle class="dot" cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) + '" r="' + r + '" fill="' + color + '"><title>' + p.d.date + "  " + label + "</title></circle>";
      }).join("");
      el.innerHTML = '<svg class="chart-svg" viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none" role="img">'
        + gridY
        + '<path class="fill" d="' + area + '" fill="' + color + '"></path>'
        + '<path class="line" d="' + line + '" stroke="' + color + '"></path>'
        + dots
        + "</svg>";
      const first = rows[0] ? md(rows[0].date) : "";
      const mid = rows[Math.floor(rows.length / 2)] ? md(rows[Math.floor(rows.length / 2)].date) : "";
      const last = rows[rows.length - 1] ? md(rows[rows.length - 1].date) : "";
      axis.innerHTML = "<span>" + first + "</span><span>" + mid + "</span><span>" + last + "</span>";
    }
    async function load() {
      let data = window.__OPS_PREVIEW__;
      if (!data) {
        const res = await fetch("/api/ops/stats", { credentials: "same-origin", cache: "no-store" });
        if (!res.ok) { document.getElementById("meta").textContent = "无法读取数据"; return; }
        data = await res.json();
      }
      const daily = data.daily || [];
      const days = data.daily_days || daily.length || 30;
      const regSum = daily.reduce((n, d) => n + (Number(d.registered) || 0), 0);
      const paySum = daily.reduce((n, d) => n + (Number(d.paid_amount_yuan) || 0), 0);
      document.getElementById("registered").textContent = data.registered_users;
      document.getElementById("paidUsers").textContent = data.paid_users;
      document.getElementById("paidAmount").textContent = yuan(data.paid_amount_yuan);
      document.getElementById("online").textContent = data.online_users;
      document.getElementById("meta").textContent = (data.preview_note || ("近 " + data.online_window_minutes + " 分钟有请求的登录用户 · " + (data.as_of || "") + " · 北京时间"));
      document.getElementById("regNote").textContent = "今日 " + (data.today_registered || 0) + " 人 · 近 " + days + " 日共 " + regSum + " 人";
      document.getElementById("payNote").textContent = "今日 " + yuan(data.today_paid_amount_yuan) + " · 近 " + days + " 日共 " + yuan(paySum);
      trend(document.getElementById("regChart"), document.getElementById("regAxis"), daily, "registered", "#2f6f5e");
      trend(document.getElementById("payChart"), document.getElementById("payAxis"), daily, "paid_amount_yuan", "#c2781e");
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
