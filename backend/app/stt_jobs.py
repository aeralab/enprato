from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}
TTL_SEC = 60 * 60


def _key(owner: str, client_request_id: str) -> str:
    return f"{owner}:{client_request_id}"


def _purge_locked(now: float) -> None:
    dead = [key for key, item in _jobs.items() if now - float(item.get("updated_at") or 0) > TTL_SEC]
    for key in dead:
        _jobs.pop(key, None)


def begin(owner: str, client_request_id: str) -> tuple[str, dict[str, Any] | None]:
    if not client_request_id:
        return "start", None
    now = time.time()
    key = _key(owner, client_request_id)
    with _lock:
        _purge_locked(now)
        item = _jobs.get(key)
        if item and item.get("status") == "done":
            return "replay", item.get("result")
        if item and item.get("status") == "running":
            return "inflight", None
        _jobs[key] = {"status": "running", "updated_at": now, "result": None}
        return "start", None


def finish(owner: str, client_request_id: str, result: dict[str, Any]) -> None:
    if not client_request_id:
        return
    with _lock:
        _jobs[_key(owner, client_request_id)] = {
            "status": "done",
            "updated_at": time.time(),
            "result": result,
        }


def fail(owner: str, client_request_id: str) -> None:
    if not client_request_id:
        return
    with _lock:
        _jobs.pop(_key(owner, client_request_id), None)


def clear() -> None:
    with _lock:
        _jobs.clear()
