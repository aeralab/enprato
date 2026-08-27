from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from .media import find_ffmpeg

_lock = threading.Lock()
_proc: subprocess.Popen[bytes] | None = None


def _ffplay() -> str:
    ffmpeg = find_ffmpeg()
    name = "ffplay.exe" if ffmpeg.lower().endswith(".exe") else "ffplay"
    candidate = Path(ffmpeg).with_name(name)
    if candidate.is_file():
        return str(candidate)
    raise RuntimeError("未找到 ffplay，无法走系统音箱播放")


def stop_speaker() -> None:
    global _proc
    with _lock:
        proc = _proc
        _proc = None
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1.5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


def play_speaker(path: Path, start: float, end: float, volume: float = 1.0) -> None:
    """Play a clip through the Windows default output (e.g. Realtek Digital Output)."""
    if not path.is_file():
        raise FileNotFoundError(str(path))
    start = max(0.0, float(start))
    end = max(start + 0.05, float(end))
    duration = end - start
    vol = int(max(0, min(100, round(float(volume) * 100))))
    stop_speaker()
    cmd = [
        _ffplay(),
        "-nodisp",
        "-autoexit",
        "-vn",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-volume",
        str(vol),
        "-loglevel",
        "quiet",
        str(path),
    ]
    global _proc
    with _lock:
        _proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
