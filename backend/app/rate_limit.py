from __future__ import annotations

import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

_hits: dict[str, deque[float]] = defaultdict(deque)

def enforce(request: Request, bucket: str) -> None:
    limit = max(1, int(os.environ.get("ENPRATO_PAYMENT_RATE_LIMIT", "10")))
    window = max(1, int(os.environ.get("ENPRATO_PAYMENT_RATE_WINDOW", "60")))
    key = bucket + ":" + (request.client.host if request.client else "unknown")
    now = time.monotonic()
    hits = _hits[key]
    while hits and now - hits[0] >= window: hits.popleft()
    if len(hits) >= limit: raise HTTPException(429, "请求过于频繁，请稍后重试")
    hits.append(now)
