from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

FFMPEG_CANDIDATES = [
    os.environ.get("FFMPEG_PATH", ""),
    r"F:\softinstall\ffmpeg\bin\ffmpeg.exe",
    r"C:\ffmpeg\ffmpeg\bin\ffmpeg.exe",
    "ffmpeg",
]


def find_ffmpeg() -> str:
    for candidate in FFMPEG_CANDIDATES:
        if not candidate:
            continue
        if candidate == "ffmpeg":
            found = shutil.which("ffmpeg")
            if found:
                return found
            continue
        if Path(candidate).is_file():
            return candidate
    raise RuntimeError("未找到 ffmpeg，请安装或设置环境变量 FFMPEG_PATH")


def run_ffmpeg(args: list[str]) -> None:
    cmd = [find_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", *args]
    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "ffmpeg 执行失败")


def probe_duration(src: Path) -> float:
    try:
        cmd = [
            find_ffprobe(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(src),
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True)
        if completed.returncode != 0:
            return 0.0
        return max(0.0, float((completed.stdout or "").strip()))
    except Exception:
        return 0.0


def extract_video_thumbnail(src: Path, dest: Path, at: float = 1.0, width: int = 320) -> None:
    """从视频截取一帧作为封面（无画面轨时跳过）。"""
    if not stream_codec(src, "v"):
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    duration = probe_duration(src)
    seek = max(0.5, at)
    if duration > 4:
        seek = min(max(3.0, duration * 0.08), duration - 1.5)
    run_ffmpeg(
        [
            "-ss",
            str(seek),
            "-i",
            str(src),
            "-vf",
            f"thumbnail=300,scale={width}:-2",
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(dest),
        ]
    )


def extract_wav(src: Path, dest: Path, sr: int = 16000) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-i",
            str(src),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sr),
            "-c:a",
            "pcm_s16le",
            str(dest),
        ]
    )


def find_ffprobe() -> str:
    ffmpeg = find_ffmpeg()
    probe = Path(ffmpeg).with_name("ffprobe.exe" if ffmpeg.lower().endswith(".exe") else "ffprobe")
    if probe.is_file():
        return str(probe)
    found = shutil.which("ffprobe")
    if found:
        return found
    raise RuntimeError("未找到 ffprobe")


def convert_to_wav(src: Path, dest: Path, sr: int = 16000) -> None:
    if src.suffix.lower() == ".wav":
        try:
            import wave

            with wave.open(str(src), "rb") as wf:
                ready = wf.getnchannels() == 1 and wf.getframerate() == sr and wf.getsampwidth() == 2
            if ready:
                dest.parent.mkdir(parents=True, exist_ok=True)
                if src.resolve() != dest.resolve():
                    shutil.copyfile(src, dest)
                return
        except Exception:
            pass
    extract_wav(src, dest, sr=sr)


def stream_codec(src: Path, kind: str = "a") -> str:
    try:
        cmd = [
            find_ffprobe(),
            "-v",
            "error",
            "-select_streams",
            f"{kind}:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "default=nw=1:nk=1",
            str(src),
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True)
        return (completed.stdout or "").strip().lower()
    except Exception:
        return ""


BROWSER_VIDEO_CODECS = {"h264", "avc1", "vp8", "vp9", "theora"}
BROWSER_AUDIO_CODECS = {"aac", "mp3", "opus", "vorbis"}


def is_browser_video(src: Path) -> bool:
    codec = stream_codec(src, "v")
    if not codec:
        return True
    return codec in BROWSER_VIDEO_CODECS


def is_browser_audio(src: Path) -> bool:
    codec = stream_codec(src, "a")
    if not codec:
        return False
    return codec in BROWSER_AUDIO_CODECS


def is_browser_media(src: Path) -> bool:
    return is_browser_video(src) and media_has_audio(src) and is_browser_audio(src)

def make_browser_mp4(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 1000 and is_browser_media(dest):
        return dest
    tmp = dest.with_name(f"{dest.stem}.{os.getpid()}.tmp{dest.suffix}")
    video_attempts = [
        ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p"],
    ]
    try:
        last_error = None
        for video_args in video_attempts:
            try:
                run_ffmpeg(
                    [
                        "-i",
                        str(src),
                        *video_args,
                        "-c:a",
                        "aac",
                        "-b:a",
                        "160k",
                        "-ac",
                        "2",
                        "-movflags",
                        "+faststart",
                        str(tmp),
                    ]
                )
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                if tmp.is_file():
                    tmp.unlink(missing_ok=True)
        if last_error:
            raise last_error
        if tmp.is_file() and tmp.stat().st_size > 1000:
            tmp.replace(dest)
    finally:
        if tmp.is_file() and tmp != dest:
            tmp.unlink(missing_ok=True)
    return dest


_PLAYBACK_LOCK = threading.Lock()


def extract_playback_audio(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 1000:
        return dest
    tmp = dest.with_name(f"{dest.stem}.{os.getpid()}.tmp{dest.suffix}")
    try:
        try:
            run_ffmpeg(["-i", str(src), "-vn", "-c:a", "copy", str(tmp)])
        except Exception:
            run_ffmpeg(["-i", str(src), "-vn", "-c:a", "aac", "-b:a", "192k", str(tmp)])
        if tmp.is_file() and tmp.stat().st_size > 1000:
            tmp.replace(dest)
    finally:
        if tmp.is_file() and tmp != dest:
            tmp.unlink(missing_ok=True)
    return dest


def ensure_playback_audio(folder: Path, media: Path | None = None) -> Path | None:
    dest = folder / "playback.m4a"
    with _PLAYBACK_LOCK:
        if dest.is_file() and dest.stat().st_size > 1000:
            return dest
        candidates: list[Path] = []
        if media and media.is_file():
            candidates.append(media)
        for name in ("source.mp4", "source.webm", "source.mkv", "audio.wav"):
            path = folder / name
            if path.is_file() and path not in candidates:
                candidates.append(path)
        for src in candidates:
            if src.suffix.lower() in {".wav", ".m4a", ".mp3", ".aac", ".opus", ".ogg"} or media_has_audio(src):
                try:
                    return extract_playback_audio(src, dest)
                except Exception:
                    continue
        return dest if dest.is_file() else None


def media_has_audio(src: Path) -> bool:
    try:
        cmd = [
            find_ffprobe(),
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(src),
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True)
        return "audio" in (completed.stdout or "").lower()
    except Exception:
        return False
