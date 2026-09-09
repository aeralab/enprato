from __future__ import annotations

import time
from typing import Any

from fastapi.testclient import TestClient


def wait_job(client: TestClient, job_id: str, timeout: float = 12.0) -> tuple[dict[str, Any], list[str]]:
    deadline = time.time() + timeout
    seen: list[str] = []
    last: dict[str, Any] = {}
    while time.time() < deadline:
        res = client.get(f"/api/import-status/{job_id}")
        if res.status_code == 200:
            last = res.json()
            stage = str(last.get("stage") or "")
            if stage and (not seen or seen[-1] != stage):
                seen.append(stage)
            if last.get("status") in {"ready", "failed"}:
                return last, seen
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} still {last} stages={seen}")


def wait_ready(client: TestClient, post_res, timeout: float = 12.0) -> dict[str, Any]:
    body = post_res.json()
    if body.get("status") == "ready" and body.get("sentences"):
        return body
    job_id = body.get("job_id")
    if not job_id:
        raise AssertionError(f"no job_id in {body}")
    job, _ = wait_job(client, job_id, timeout=timeout)
    if job.get("status") != "ready":
        raise AssertionError(f"job failed: {job}")
    session_id = job["session_id"]
    detail = client.get(f"/api/session/{session_id}")
    if detail.status_code != 200:
        raise AssertionError(detail.text)
    return detail.json()
