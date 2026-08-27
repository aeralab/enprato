from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import ssl
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from .media import ensure_playback_audio, extract_wav, find_ffmpeg, is_browser_video, make_browser_mp4, media_has_audio, run_ffmpeg, stream_codec

VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".m4v", ".mov", ".avi"}
AUDIO_EXTS = {".m4a", ".mp3", ".opus", ".ogg", ".wav", ".aac"}
SUB_EXTS = {".vtt", ".srt"}

YT_DLP_CANDIDATES = [
    os.environ.get("YT_DLP_PATH", ""),
    r"C:\Users\Administrator\.agent-reach-venv\Scripts\yt-dlp.exe",
]


def ytdlp_cmd() -> list[str]:
    try:
        import yt_dlp  # noqa: F401

        return [sys.executable, "-m", "yt_dlp"]
    except ImportError:
        pass
    for candidate in YT_DLP_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return [candidate]
    found = shutil.which("yt-dlp")
    if found:
        return [found]
    raise RuntimeError("未找到 yt-dlp。请先安装：pip install yt-dlp")


def parse_bilibili_bvid(url: str) -> str | None:
    match = re.search(r"(BV[0-9A-Za-z]+)", url, re.I)
    return match.group(1) if match else None


def is_garbled_title(title: str) -> bool:
    text = (title or "").strip()
    if not text:
        return True
    if "\ufffd" in text:
        return True
    latin1ish = sum(1 for ch in text if 128 <= ord(ch) <= 255)
    if latin1ish >= max(3, len(text) // 3):
        return True
    if text.startswith("http") or "bilibili.com/video" in text:
        return True
    return False


def _http_get(url: str, timeout: int = 45) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com/",
        },
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read()


def download_http_file(url: str, dest: Path, min_bytes: int = 2000) -> bool:
    if url.startswith("//"):
        url = "https:" + url
    try:
        data = _http_get(url)
        if len(data) < min_bytes:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest.is_file() and dest.stat().st_size >= min_bytes
    except Exception:
        return False


def fetch_bilibili_view(bvid: str) -> dict[str, str] | None:
    try:
        raw = _http_get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
        payload = json.loads(raw.decode("utf-8"))
        if payload.get("code") != 0:
            return None
        data = payload.get("data") or {}
        title = str(data.get("title") or "").strip()
        pic = str(data.get("pic") or "").strip()
        if not title and not pic:
            return None
        return {"title": title, "pic": pic}
    except Exception:
        return None


def fetch_bilibili_thumbnail(url: str, dest: Path) -> bool:
    bvid = parse_bilibili_bvid(url)
    if not bvid:
        return False
    info = fetch_bilibili_view(bvid)
    if not info or not info.get("pic"):
        return False
    return download_http_file(info["pic"], dest)


def fetch_media_title(url: str) -> str | None:
    try:
        url = validate_media_url(url)
    except ValueError:
        return None
    bvid = parse_bilibili_bvid(url)
    if bvid:
        info = fetch_bilibili_view(bvid)
        if info and info.get("title"):
            return info["title"]
    try:
        ytdlp = ytdlp_cmd()
        completed = subprocess.run(
            [*ytdlp, "--no-playlist", "--no-warnings", "-j", "--skip-download", url],
            capture_output=True,
            timeout=90,
        )
        if completed.returncode != 0:
            return None
        payload = json.loads(completed.stdout.decode("utf-8", errors="replace"))
        title = str(payload.get("title") or "").strip()
        return title if title else None
    except Exception:
        return None


def fetch_url_thumbnail(url: str, dest: Path) -> bool:
    """拉平台封面：B站优先官方图，其它走 yt-dlp。"""
    try:
        url = validate_media_url(url)
    except ValueError:
        return False
    if "bilibili.com" in url.lower():
        if fetch_bilibili_thumbnail(url, dest):
            return True
    folder = dest.parent
    prefix = "_ytdlp_thumb"
    for old in folder.glob(f"{prefix}*"):
        try:
            old.unlink()
        except OSError:
            pass
    try:
        ytdlp = ytdlp_cmd()
        ffmpeg = find_ffmpeg()
        ffmpeg_dir = str(Path(ffmpeg).parent)
        out = str(folder / prefix)
        cmd = [
            *ytdlp,
            "--no-playlist",
            "--no-warnings",
            "--skip-download",
            "--write-thumbnail",
            "--convert-thumbnails",
            "jpg",
            "--ffmpeg-location",
            ffmpeg_dir,
            "-o",
            out + ".%(ext)s",
            url,
        ]
        cookies = os.environ.get("ENPRATO_COOKIES", "").strip()
        if cookies:
            cmd.extend(["--cookies", cookies])
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if completed.returncode != 0:
            return False
        picked: Path | None = None
        for path in sorted(folder.glob(f"{prefix}*"), key=lambda p: p.stat().st_size, reverse=True):
            if path.is_file() and path.stat().st_size > 2000:
                picked = path
                break
        if picked is None:
            return False
        if picked.suffix.lower() == ".jpg":
            picked.replace(dest)
        else:
            run_ffmpeg(["-i", str(picked), "-frames:v", "1", "-q:v", "3", str(dest)])
            try:
                picked.unlink()
            except OSError:
                pass
        for old in folder.glob(f"{prefix}*"):
            try:
                old.unlink()
            except OSError:
                pass
        return dest.is_file() and dest.stat().st_size > 2000
    except Exception:
        return False


def adopt_downloaded_thumbnail(folder: Path) -> bool:
    """导入链接时 yt-dlp 常顺带下载 source.jpg，收拢为 thumb.jpg。"""
    dest = folder / "thumb.jpg"
    candidates: list[Path] = []
    for path in folder.iterdir():
        if not path.is_file():
            continue
        name = path.name.lower()
        if path.suffix.lower() not in {".jpg", ".jpeg", ".webp", ".png"}:
            continue
        if name == "thumb.jpg":
            continue
        if name.startswith("source") or "thumb" in name:
            candidates.append(path)
    if not candidates:
        return False
    best = max(candidates, key=lambda p: p.stat().st_size)
    if best.stat().st_size < 2000:
        return False
    if best.suffix.lower() in {".jpg", ".jpeg"}:
        best.replace(dest)
        return True
    try:
        run_ffmpeg(["-i", str(best), "-frames:v", "1", "-q:v", "3", str(dest)])
        return dest.is_file() and dest.stat().st_size > 2000
    except Exception:
        return False


def validate_media_url(raw: str) -> str:
    url = raw.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("请粘贴 http/https 视频链接")
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    # 微信视频号 / 公众号页：yt-dlp 无法拉取，引导本地上传
    if (
        "weixin.qq.com" in host
        or "channels.weixin.qq.com" in host
        or path.startswith("/sph/")
        or "finder-preview" in path
    ):
        raise ValueError("微信视频号链接无法在线拉取，请先下载到本机，再拖入或点击上传")
    return url


def ingest_url(url: str, folder: Path) -> tuple[Path, Path, str | None]:
    """Fetch playable media + optional English captions. Returns (video_or_audio, wav, captions_text)."""
    url = validate_media_url(url)
    folder.mkdir(parents=True, exist_ok=True)
    ffmpeg = find_ffmpeg()
    ffmpeg_dir = str(Path(ffmpeg).parent)
    ytdlp = ytdlp_cmd()
    out_tmpl = str(folder / "source.%(ext)s")
    cookies = os.environ.get("ENPRATO_COOKIES", "").strip()

    base = [
        *ytdlp,
        "--no-playlist",
        "--no-warnings",
        "--restrict-filenames",
        "--newline",
        "--ffmpeg-location",
        ffmpeg_dir,
        "--merge-output-format",
        "mp4",
        "--write-thumbnail",
        "--convert-thumbnails",
        "jpg",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        "en.*,en",
        "--convert-subs",
        "vtt",
        "-o",
        out_tmpl,
    ]
    if cookies:
        base.extend(["--cookies", cookies])

    video_fmt = (
        "bv*[vcodec^=avc1][height<=720]+ba[ext=m4a]/"
        "bv*[ext=mp4][height<=720]+ba/"
        "b[ext=mp4][height<=720]/"
        "bv*+ba/b"
    )
    try:
        _run(base + ["-f", video_fmt, url])
    except RuntimeError:
        try:
            _run(base + ["-f", "bestvideo+bestaudio/best", url])
        except RuntimeError:
            _run(base + ["-f", "ba/bestaudio/b", url])

    media = _ensure_playable(folder)
    if media is None:
        raise RuntimeError("链接能打开，但没有拿到可播放的音视频（可能有版权保护或地区限制）")

    audio = folder / "audio.wav"
    extract_wav(media, audio)
    ensure_playback_audio(folder, media)
    captions = _read_captions(folder)
    adopt_downloaded_thumbnail(folder)
    return media, audio, captions


def find_session_media(folder: Path) -> Path | None:
    return _ensure_playable(folder)


def _ensure_playable(folder: Path) -> Path | None:
    merged = folder / "playable.mp4"
    if merged.is_file() and media_has_audio(merged) and is_browser_video(merged):
        return merged
    picked = _pick_media(folder)
    if picked is None:
        return None
    if picked.suffix.lower() in VIDEO_EXTS and media_has_audio(picked) and is_browser_video(picked):
        return picked
    audios = [
        p
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS and p.name not in {"audio.wav", "playback.m4a"}
    ]
    if picked.suffix.lower() in VIDEO_EXTS and audios:
        audio = max(audios, key=lambda p: p.stat().st_size)
        try:
            run_ffmpeg(
                [
                    "-i",
                    str(picked),
                    "-i",
                    str(audio),
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-shortest",
                    "-movflags",
                    "+faststart",
                    str(merged),
                ]
            )
            if merged.is_file() and media_has_audio(merged) and is_browser_video(merged):
                return merged
        except Exception:
            pass
    wav = folder / "audio.wav"
    if picked.suffix.lower() in VIDEO_EXTS and wav.is_file() and not media_has_audio(picked):
        try:
            run_ffmpeg(
                [
                    "-i",
                    str(picked),
                    "-i",
                    str(wav),
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-shortest",
                    "-movflags",
                    "+faststart",
                    str(merged),
                ]
            )
            if merged.is_file() and media_has_audio(merged) and is_browser_video(merged):
                return merged
        except Exception:
            pass
    if picked.suffix.lower() in VIDEO_EXTS and media_has_audio(picked) and not is_browser_video(picked):
        try:
            return make_browser_mp4(picked, merged)
        except Exception:
            return picked
    return picked


def _run(cmd: list[str]) -> None:
    completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        lines = [line for line in detail.splitlines() if line.strip()]
        raise RuntimeError(lines[-1] if lines else "yt-dlp 失败")


def _pick_media(folder: Path) -> Path | None:
    files = [p for p in folder.iterdir() if p.is_file() and p.name != "playable.mp4"]
    videos = [p for p in files if p.suffix.lower() in VIDEO_EXTS]
    if videos:
        with_audio = [p for p in videos if media_has_audio(p)]
        pool = with_audio or videos
        h264 = [p for p in pool if stream_codec(p, "v") in {"h264", "avc1"}]
        return max(h264 or pool, key=lambda p: p.stat().st_size)
    audios = [p for p in files if p.suffix.lower() in AUDIO_EXTS and p.name != "audio.wav"]
    if audios:
        return max(audios, key=lambda p: p.stat().st_size)
    wav = folder / "audio.wav"
    return wav if wav.is_file() else None


def _read_captions(folder: Path) -> str | None:
    subs = [p for p in folder.iterdir() if p.suffix.lower() in SUB_EXTS]
    if not subs:
        return None

    def rank(path: Path) -> tuple[int, int]:
        name = path.name.lower()
        score = 0
        if ".en" in name or name.endswith(".en.vtt") or name.endswith(".en.srt"):
            score += 4
        if "en-us" in name or "en-gb" in name:
            score += 3
        if "auto" in name or "orig" in name:
            score += 1
        if path.suffix.lower() == ".vtt":
            score += 1
        return (score, path.stat().st_size)

    best = max(subs, key=rank)
    text = best.read_text(encoding="utf-8", errors="replace")
    return text if text.strip() else None
