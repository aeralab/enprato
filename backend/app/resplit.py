from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .sentences import split_long_text


def _json_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _snapshot(folder: Path) -> dict[str, Any]:
    files: dict[str, str] = {}
    for path in sorted(folder.rglob("*")):
        if "_resplit_backups" in path.parts or not path.is_file():
            continue
        files[str(path.relative_to(folder))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def _verify_backup(backup: Path) -> dict[str, Any]:
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    snapshot_root = backup / "session"
    files = {}
    for path in sorted(snapshot_root.rglob("*")):
        if path.is_file():
            files[str(path.relative_to(snapshot_root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    if files != manifest.get("files"):
        raise ValueError("backup verification failed; migration stopped")
    return manifest


def resplit_remaining_session(folder: Path) -> dict[str, Any]:
    """Resplit only segments after the current sentence, with a rollback snapshot."""
    sentences_path = folder / "sentences.json"
    meta_path = folder / "meta.json"
    if not folder.is_dir() or not sentences_path.is_file() or not meta_path.is_file():
        raise ValueError("session data is incomplete")
    sentences = json.loads(sentences_path.read_text(encoding="utf-8"))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if not isinstance(sentences, list) or not sentences:
        raise ValueError("session has no sentences")
    cutoff = max(0, min(int(meta.get("index") or 0), len(sentences) - 1))
    drafts = meta.get("drafts") if isinstance(meta.get("drafts"), dict) else {}
    future_drafts = [key for key, value in drafts.items() if int(key) > cutoff and str(value or "").strip()]
    if future_drafts:
        raise ValueError("future sentences already contain drafts; migration stopped")

    before_hash = _json_hash(sentences[: cutoff + 1])
    backup_root = folder / "_resplit_backups"
    backup_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backup_root / backup_id
    backup.mkdir(parents=True, exist_ok=False)
    snapshot_root = backup / "session"
    snapshot_root.mkdir()
    for source in sorted(folder.rglob("*")):
        if "_resplit_backups" in source.parts or not source.is_file():
            continue
        target = snapshot_root / source.relative_to(folder)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    manifest = {
        "backup_id": backup_id,
        "session_id": folder.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cutoff": cutoff,
        "frozen_through_index": cutoff,
        "progress_before": (cutoff + 1) / len(sentences),
        "splitter_version": "sentences-display-v1",
        "frozen_hash": before_hash,
        "files": _snapshot(folder),
    }
    (backup / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _verify_backup(backup)

    frozen = [dict(item) for item in sentences[: cutoff + 1]]
    future: list[dict[str, Any]] = []
    next_id = max(int(item.get("id", i)) for i, item in enumerate(frozen)) + 1
    for old_index, item in enumerate(sentences[cutoff + 1:], cutoff + 1):
        parts = split_long_text(str(item.get("text") or ""))
        if len(parts) <= 1:
            future.append(dict(item))
            continue
        parent_id = str(item.get("parent_id") or f"resplit-{item.get('id', old_index)}")
        start = float(item.get("start") or 0)
        end = float(item.get("end") or start)
        weights = [max(1.0, len(part)) for part in parts]
        total = sum(weights)
        cursor = start
        for segment_index, (part, weight) in enumerate(zip(parts, weights)):
            next_cursor = end if segment_index == len(parts) - 1 else start + (end - start) * sum(weights[: segment_index + 1]) / total
            future.append({
                "id": next_id,
                "start": round(cursor, 3),
                "end": round(next_cursor, 3),
                "text": part,
                "parent_id": parent_id,
                "segment_index": segment_index,
            })
            next_id += 1
            cursor = next_cursor

    result = frozen + future
    if _json_hash(result[: cutoff + 1]) != before_hash:
        shutil.rmtree(backup, ignore_errors=True)
        raise ValueError("frozen sentence snapshot changed; migration stopped")
    sentences_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "resplit_progress.json").write_text(
        json.dumps({"original_count": len(sentences), "frozen_through": cutoff, "progress_floor": (cutoff + 1) / len(sentences)}, indent=2),
        encoding="utf-8",
    )
    return {"backup_id": backup_id, "cutoff": cutoff, "old_count": len(sentences), "new_count": len(result), "frozen_hash": before_hash}


def rollback_resplit_session(folder: Path, backup_id: str) -> dict[str, Any]:
    backup = folder / "_resplit_backups" / backup_id
    if not backup.is_dir():
        raise ValueError("backup not found")
    snapshot_root = backup / "session"
    if not snapshot_root.is_dir() or not (backup / "manifest.json").is_file():
        raise ValueError("backup is incomplete")
    for child in folder.iterdir():
        if child.name == "_resplit_backups":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    for source in sorted(snapshot_root.rglob("*")):
        target = folder / source.relative_to(snapshot_root)
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return {"backup_id": backup_id, "restored": True}
