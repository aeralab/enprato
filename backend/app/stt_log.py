from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "data" / "logs" / "stt.jsonl"
_lock = threading.Lock()


def event(stage: str, **fields: Any) -> None:
    payload = {
        "timestamp": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "stage": stage,
    }
    for key, value in fields.items():
        if value is not None:
            payload[key] = value
    line = json.dumps(payload, ensure_ascii=False)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
