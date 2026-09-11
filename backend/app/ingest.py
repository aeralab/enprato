from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import ssl
import time
import urllib.request
import logging
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

from .bilibili import BilibiliIngestError, ingest_bilibili
from .media import ensure_playback_audio, extract_wav, find_ffmpeg, is_ipad_media, make_browser_mp4, media_has_audio, run_ffmpeg, stream_codec

logger = logging.getLogger(__name__)
_stage_log = logging.getLogger("enprato")

VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".m4v", ".mov", ".avi"}
AUDIO_EXTS = {".m4a", ".mp3", ".opus", ".ogg", ".wav", ".aac"}
SUB_EXTS = {".vtt", ".srt"}
IMPORT_META_NAME = "import_meta.json"
YTDLP_TIMEOUT_SEC = int(os.environ.get("ENPRATO_YTDLP_TIMEOUT", "600"))
SUBTITLE_TIMEOUT_SEC = 90
METADATA_TIMEOUT_SEC = 90
LOCAL_UPLOAD_HINT = "暂时无法直接读取该视频链接。你可以先将视频保存到本地，再上传到 Enprato 学习。"
MANUAL_EN_LANGS = ("en", "en-US", "en-GB")
AUTO_EN_LANGS = ("en", "en-US")
MEDIA_FORMATS = (
    "bv*[vcodec^=avc1][height<=480]+ba[ext=m4a]/bv*[ext=mp4][height<=480]+ba/b[height<=480][ext=mp4]",
    "bv*[vcodec^=avc1][height<=720]+ba[ext=m4a]/bv*[ext=mp4][height<=720]+ba/b[ext=mp4][height<=720]",
    "ba[ext=m4a]/bestaudio[ext=m4a]/ba/bestaudio",
)

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


def ytdlp_network_args() -> list[str]:
    return ["--socket-timeout", "20", "--retries", "1", "--fragment-retries", "1"]


def parse_bilibili_bvid(url: str) -> str | None:
    match = re.search(r"(BV[0-9A-Za-z]+)", url, re.I)
    return match.group(1) if match else None


def same_media_url(left: str, right: str) -> bool:
    a = (left or "").strip()
    b = (right or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    ba, bb = parse_bilibili_bvid(a), parse_bilibili_bvid(b)
    return bool(ba and bb and ba.lower() == bb.lower())


def is_bilibili_url(url: str) -> bool:
    text = (url or "").lower()
    return "bilibili.com" in text or "b23.tv" in text


def is_youtube_url(url: str) -> bool:
    host = (urlparse(url or "").netloc or "").lower()
    return "youtube.com" in host or host.endswith("youtu.be") or host == "youtu.be"


def url_preview(url: str) -> str:
    try:
        parsed = urlparse(url or "")
    except Exception:
        return "unknown"
    host = (parsed.netloc or "").split("@")[-1].lower()
    if is_bilibili_url(url):
        bvid = parse_bilibili_bvid(url)
        return f"bilibili:{bvid}" if bvid else f"bilibili:{host or 'unknown'}"
    if is_youtube_url(url):
        vid = (parse_qs(parsed.query).get("v") or [""])[0].strip()
        if not vid and "youtu.be" in host:
            vid = (parsed.path or "").strip("/").split("/")[0]
        return f"youtube:{vid}" if vid else f"youtube:{host or 'unknown'}"
    return host or "unknown"


def is_retryable_ytdlp_error(url: str, detail: str) -> bool:
    if not is_bilibili_url(url):
        return False
    text = (detail or "").strip()
    low = text.lower()
    return "412" in text or "precondition failed" in low


def ytdlp_cmd_variants(base: list[str], url: str) -> list[list[str]]:
    variants = [list(base)]
    if not is_bilibili_url(url):
        return variants
    if ["--proxy", ""] not in [base[i : i + 2] for i in range(len(base) - 1)]:
        variants.append([*base, "--proxy", ""])
    return variants


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
            [*ytdlp, "--no-playlist", "--no-warnings", *ytdlp_network_args(), "-j", "--skip-download", url],
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
            *ytdlp_network_args(),
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


def url_host_family(url: str) -> str:
    if is_bilibili_url(url):
        return "bilibili.com"
    if is_youtube_url(url):
        return "youtube.com"
    try:
        host = (urlparse(url or "").netloc or "").split("@")[-1].lower()
    except Exception:
        return "unknown"
    return host or "unknown"


def format_attempt_label(fmt: str) -> str:
    if "height<=480" in (fmt or ""):
        return "480"
    if "height<=720" in (fmt or ""):
        return "720"
    return "audio"


def log_url_import_stage(host: str, stage: str, elapsed_ms: int | None = None, **fields: object) -> None:
    parts = [f"url_import_stage host={host} stage={stage}"]
    if elapsed_ms is not None:
        parts.append(f"elapsed_ms={elapsed_ms}")
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    _stage_log.info(" ".join(parts))


def read_import_title(folder: Path) -> str | None:
    path = folder / IMPORT_META_NAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    title = str(payload.get("title") or "").strip()
    return title if title and not is_garbled_title(title) else None


def read_subtitle_status(folder: Path) -> str:
    path = folder / IMPORT_META_NAME
    if not path.is_file():
        return "unknown"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return "unknown"
    return str(payload.get("subtitle_status") or "unknown")


def _write_import_meta(folder: Path, **fields: object) -> None:
    path = folder / IMPORT_META_NAME
    current: dict[str, object] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                current = loaded
        except Exception:
            current = {}
    for key, value in fields.items():
        if value is None:
            continue
        current[key] = value
    path.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")


def _usable_en_lang(key: str) -> bool:
    text = (key or "").lower()
    if not text or "live" in text:
        return False
    if "en-en" in text:
        return False
    return text == "en" or text.startswith("en-") or text.startswith("en_")


def _first_english_lang(mapping: dict | None, prefer: tuple[str, ...]) -> str | None:
    keys = [str(key) for key in (mapping or {})]
    lower = {key.lower(): key for key in keys}
    for pref in prefer:
        found = lower.get(pref.lower())
        if found:
            return found
    for key in keys:
        if _usable_en_lang(key):
            return key
    return None


def pick_english_sub_lang(info: dict | None) -> tuple[str | None, bool]:
    payload = info or {}
    manual = _first_english_lang(payload.get("subtitles") or {}, MANUAL_EN_LANGS)
    if manual:
        return manual, False
    auto = _first_english_lang(payload.get("automatic_captions") or {}, AUTO_EN_LANGS)
    if auto:
        return auto, True
    return None, False


def _bili_ytdlp_fallback_allowed() -> bool:
    return (os.environ.get("ENPRATO_ENV") or "").strip().lower() != "production"


def ingest_url(
    url: str,
    folder: Path,
    on_stage: Callable[..., None] | None = None,
    job_id: str | None = None,
    session_id: str | None = None,
) -> tuple[Path, Path, str | None]:
    """Fetch playable media + optional English captions. Returns (video_or_audio, wav, captions_text)."""
    url = validate_media_url(url)
    host = url_host_family(url)
    started = time.monotonic()
    folder.mkdir(parents=True, exist_ok=True)
    audio = folder / "audio.wav"
    if is_bilibili_url(url):
        return _ingest_bilibili_url(url, folder, audio, started, on_stage=on_stage, job_id=job_id, session_id=session_id)
    return _ingest_url_ytdlp(url, folder, audio, host, started, on_stage=on_stage)


def _ingest_bilibili_url(
    url: str,
    folder: Path,
    audio: Path,
    started: float,
    on_stage: Callable[..., None] | None = None,
    job_id: str | None = None,
    session_id: str | None = None,
) -> tuple[Path, Path, str | None]:
    host = "bilibili.com"
    if on_stage:
        on_stage("metadata")
    log_url_import_stage(host, "metadata_start", path="official_api")
    try:
        media_started = time.monotonic()
        if on_stage:
            on_stage("downloading")
        media, captions, view = ingest_bilibili(url, folder)
        if captions:
            (folder / "source.en.vtt").write_text(captions, encoding="utf-8")
        playable = _ensure_playable(folder)
        if playable is not None:
            media = playable
        title = str(view.get("title") or "").strip()
        if title:
            _write_import_meta(folder, title=title, duration=view.get("duration"))
        _write_import_meta(folder, subtitle_status="ok" if captions else "unavailable")
        log_url_import_stage(
            host,
            "T_metadata",
            elapsed_ms=int(view.get("t_metadata_ms") or _elapsed_ms(media_started)),
            path="official_api",
            job_id=job_id,
            session_id=session_id,
        )
        log_url_import_stage(
            host,
            "T_subtitle",
            elapsed_ms=int(view.get("t_subtitle_ms") or 0),
            captions="1" if captions else "0",
            path="official_api",
            job_id=job_id,
            session_id=session_id,
        )
        log_url_import_stage(
            host,
            "T_audio_download",
            elapsed_ms=int(view.get("t_audio_download_ms") or 0),
            path="official_api",
            job_id=job_id,
            session_id=session_id,
        )
        log_url_import_stage(
            host,
            "media_download",
            elapsed_ms=_elapsed_ms(media_started),
            path="official_api",
            captions="1" if captions else "0",
            audio_only="1" if view.get("audio_only") else "0",
            job_id=job_id,
            session_id=session_id,
        )
    except BilibiliIngestError as exc:
        log_url_import_stage(host, "media_download", error_kind=exc.kind, path="official_api")
        if _bili_ytdlp_fallback_allowed():
            log_url_import_stage(host, "media_download", recovered="ytdlp_fallback", error_kind=exc.kind)
            return _ingest_url_ytdlp(url, folder, audio, host, started, on_stage=on_stage)
        raise RuntimeError(public_url_import_error(exc)) from exc
    playback = folder / "playback.m4a"
    if on_stage:
        on_stage("processing_audio")
    extract_started = time.monotonic()
    try:
        if playback.is_file() and playback.stat().st_size >= 200:
            ensure_playback_audio(folder, playback)
            asr_audio = playback
        else:
            extract_wav(media, audio)
            ensure_playback_audio(folder, media)
            asr_audio = audio
        log_url_import_stage(
            host,
            "T_audio_prepare",
            elapsed_ms=int(view.get("t_audio_prepare_ms") or _elapsed_ms(extract_started)),
            skipped="0",
            job_id=job_id,
            session_id=session_id,
        )
    except Exception as exc:
        log_url_import_stage(
            host,
            "audio_extract",
            elapsed_ms=_elapsed_ms(extract_started),
            error_kind="ffmpeg_failure",
        )
        raise RuntimeError(public_url_import_error(exc)) from exc
    if captions:
        log_url_import_stage(host, "audio_extract", elapsed_ms=0, skipped="captions")
    try:
        fetch_bilibili_thumbnail(url, folder / "thumb.jpg")
    except Exception:
        pass
    adopt_downloaded_thumbnail(folder)
    log_url_import_stage(host, "ingest_total", elapsed_ms=_elapsed_ms(started), path="official_api")
    return media, asr_audio, captions


def _ingest_url_ytdlp(
    url: str,
    folder: Path,
    audio: Path,
    host: str,
    started: float,
    on_stage: Callable[..., None] | None = None,
) -> tuple[Path, Path, str | None]:
    ffmpeg = find_ffmpeg()
    ffmpeg_dir = str(Path(ffmpeg).parent)
    ytdlp = ytdlp_cmd()
    cookies = os.environ.get("ENPRATO_COOKIES", "").strip()
    base = _ytdlp_common_base(ytdlp, ffmpeg_dir, url, cookies)

    meta_started = time.monotonic()
    if on_stage:
        on_stage("metadata")
    log_url_import_stage(host, "metadata_start")
    info = _fetch_url_metadata(base, url, folder)
    log_url_import_stage(host, "metadata", elapsed_ms=_elapsed_ms(meta_started))

    sub_started = time.monotonic()
    log_url_import_stage(host, "subtitle_start")
    captions = _fetch_english_captions(base, url, folder, info)
    log_url_import_stage(
        host,
        "subtitle",
        elapsed_ms=_elapsed_ms(sub_started),
        captions="1" if captions else "0",
        subtitle_status=read_subtitle_status(folder),
    )

    try:
        media_started = time.monotonic()
        if on_stage:
            on_stage("downloading")
        log_url_import_stage(host, "media_download_start")
        _download_with_retries(_media_cmd_base(base, folder), url)
        log_url_import_stage(host, "media_download", elapsed_ms=_elapsed_ms(media_started))
    except RuntimeError as exc:
        kind = classify_ingest_error(str(exc))
        log_url_import_stage(
            host,
            "media_download",
            elapsed_ms=_elapsed_ms(media_started),
            error_kind=kind,
            exception=type(exc).__name__,
        )
        raise RuntimeError(public_url_import_error(exc)) from exc

    media = _ensure_playable(folder)
    if media is None:
        raise RuntimeError("链接能打开，但没有拿到可播放的音视频（可能有版权保护或地区限制）")

    if captions:
        log_url_import_stage(host, "audio_extract", elapsed_ms=0, skipped="captions")
    else:
        extract_started = time.monotonic()
        if on_stage:
            on_stage("processing_audio")
        log_url_import_stage(host, "audio_extract_start")
        try:
            extract_wav(media, audio)
            ensure_playback_audio(folder, media)
        except Exception as exc:
            log_url_import_stage(
                host,
                "audio_extract",
                elapsed_ms=_elapsed_ms(extract_started),
                error_kind="ffmpeg_failure",
                exception=type(exc).__name__,
            )
            raise RuntimeError(public_url_import_error(exc)) from exc
        log_url_import_stage(host, "audio_extract", elapsed_ms=_elapsed_ms(extract_started))

    adopt_downloaded_thumbnail(folder)
    log_url_import_stage(host, "ingest_total", elapsed_ms=_elapsed_ms(started))
    return media, audio, captions


def _ytdlp_common_base(ytdlp: list[str], ffmpeg_dir: str, url: str, cookies: str) -> list[str]:
    base = [
        *ytdlp,
        "--no-playlist",
        "--no-warnings",
        "--restrict-filenames",
        "--no-progress",
        *ytdlp_network_args(),
        "--ffmpeg-location",
        ffmpeg_dir,
    ]
    if cookies:
        base.extend(["--cookies", cookies])
    if is_bilibili_url(url):
        base.extend(
            [
                "--user-agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "--add-header",
                "Referer:https://www.bilibili.com",
            ]
        )
    return base


def _media_cmd_base(base: list[str], folder: Path) -> list[str]:
    return [
        *base,
        "--merge-output-format",
        "mp4",
        "--write-thumbnail",
        "--convert-thumbnails",
        "jpg",
        "-o",
        str(folder / "source.%(ext)s"),
    ]


def _fetch_url_metadata(base: list[str], url: str, folder: Path) -> dict:
    host = url_host_family(url)
    if is_bilibili_url(url):
        bvid = parse_bilibili_bvid(url)
        if bvid:
            view = fetch_bilibili_view(bvid)
            if view and view.get("title"):
                _write_import_meta(folder, title=view["title"])
        return {}
    cmd = [*base, "--skip-download", "-j", url]
    try:
        raw = _run_capture(cmd, timeout=METADATA_TIMEOUT_SEC)
        payload = json.loads(raw)
    except Exception as exc:
        kind = classify_ytdlp_error(str(exc))
        if kind == "subtitle_rate_limited":
            _write_import_meta(folder, subtitle_status="rate_limited")
        log_url_import_stage(host, "metadata", error_kind=kind, recovered="1")
        return {}
    if not isinstance(payload, dict):
        return {}
    title = str(payload.get("title") or "").strip()
    if title and not is_garbled_title(title):
        _write_import_meta(folder, title=title)
    duration = payload.get("duration")
    if isinstance(duration, (int, float)):
        _write_import_meta(folder, duration=int(duration))
    return {
        "title": payload.get("title"),
        "duration": payload.get("duration"),
        "subtitles": payload.get("subtitles") or {},
        "automatic_captions": payload.get("automatic_captions") or {},
    }


def _fetch_english_captions(base: list[str], url: str, folder: Path, info: dict | None) -> str | None:
    host = url_host_family(url)
    if read_subtitle_status(folder) == "rate_limited":
        return None
    lang, is_auto = pick_english_sub_lang(info)
    if not lang and info:
        _write_import_meta(folder, subtitle_status="unavailable")
        log_url_import_stage(host, "subtitle", subtitle_status="unavailable")
        return None
    if not lang:
        lang, is_auto = "en", False
    out_tmpl = str(folder / "source.%(ext)s")
    cmd = [*base, "--skip-download", "--convert-subs", "vtt", "-o", out_tmpl]
    if is_auto:
        cmd.extend(["--write-auto-subs", "--sub-langs", lang])
    else:
        cmd.extend(["--write-subs", "--sub-langs", lang])
    last = ""
    variants = ytdlp_cmd_variants(cmd, url)
    for i, variant in enumerate(variants):
        try:
            _run([*variant, url], timeout=SUBTITLE_TIMEOUT_SEC)
            last = ""
            break
        except RuntimeError as exc:
            last = str(exc)
            kind = classify_ytdlp_error(last)
            if kind == "subtitle_rate_limited" or _is_http_429(last):
                _write_import_meta(folder, subtitle_status="rate_limited")
                log_url_import_stage(host, "subtitle", error_kind="subtitle_rate_limited", recovered="1")
                return None
            if kind in {"timeout", "network_unreachable"}:
                _write_import_meta(folder, subtitle_status="unavailable")
                log_url_import_stage(host, "subtitle", error_kind=kind, recovered="1")
                return None
            if is_retryable_ytdlp_error(url, last) and i < len(variants) - 1:
                time.sleep(0.8)
                continue
            break
    if last:
        _write_import_meta(folder, subtitle_status="unavailable")
        log_url_import_stage(host, "subtitle", error_kind=classify_ytdlp_error(last), recovered="1")
        return None
    captions = _read_captions(folder)
    _write_import_meta(folder, subtitle_status="ok" if captions else "unavailable")
    return captions


def find_session_media(folder: Path) -> Path | None:
    return _ensure_playable(folder)


def _ensure_playable(folder: Path) -> Path | None:
    merged = folder / "playable.mp4"
    if merged.is_file() and is_ipad_media(merged):
        return merged
    picked = _pick_media(folder)
    if picked is None:
        return None
    if picked.suffix.lower() in VIDEO_EXTS and is_ipad_media(picked):
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
            if merged.is_file() and is_ipad_media(merged):
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
            if merged.is_file() and is_ipad_media(merged):
                return merged
        except Exception:
            pass
    if picked.suffix.lower() in VIDEO_EXTS and media_has_audio(picked) and not is_ipad_media(picked):
        try:
            return make_browser_mp4(picked, merged)
        except Exception:
            return picked
    return picked


def _download_with_retries(base: list[str], url: str) -> None:
    last = ""
    variants = ytdlp_cmd_variants(base, url)
    for i, cmd in enumerate(variants):
        try:
            _ytdlp_fetch(cmd, url)
            return
        except RuntimeError as exc:
            last = str(exc)
            if not is_retryable_ytdlp_error(url, last):
                raise
            if i < len(variants) - 1:
                time.sleep(0.8)
    raise RuntimeError(last or "yt-dlp 拉取失败")


def _ytdlp_fetch(base: list[str], url: str) -> None:
    last = ""
    host = url_host_family(url)
    for fmt in MEDIA_FORMATS:
        label = format_attempt_label(fmt)
        started = time.monotonic()
        log_url_import_stage(host, "media_download", format_attempt=label, status="start")
        try:
            _run(base + ["-f", fmt, url])
            log_url_import_stage(
                host,
                "media_download",
                format_attempt=label,
                elapsed_ms=_elapsed_ms(started),
                status="ok",
            )
            return
        except RuntimeError as exc:
            last = str(exc)
            kind = classify_ytdlp_error(last)
            log_url_import_stage(
                host,
                "media_download",
                format_attempt=label,
                elapsed_ms=_elapsed_ms(started),
                status="fail",
                error_kind=kind,
            )
            if kind in {"http_412", "timeout", "network_unreachable", "subtitle_rate_limited"}:
                raise
            if _is_http_429(last):
                raise
    raise RuntimeError(last or "yt-dlp 失败")


def _is_timeout_error(detail: str) -> bool:
    text = detail or ""
    low = text.lower()
    return "超时" in text or "timed out" in low or "timeoutexpired" in low


def _is_network_unreachable(detail: str) -> bool:
    low = (detail or "").lower()
    return (
        "network is unreachable" in low
        or "errno 101" in low
        or "errno 51" in low
        or "failed to establish a new connection" in low
        or "no route to host" in low
        or "connection refused" in low
        or "errno 111" in low
        or "errno 113" in low
        or "name or service not known" in low
        or "temporary failure in name resolution" in low
        or "nodename nor servname" in low
        or "connect call failed" in low
    )


def _is_http_429(detail: str) -> bool:
    text = detail or ""
    low = text.lower()
    return "429" in text or "too many requests" in low


def _is_subtitle_related(detail: str) -> bool:
    low = (detail or "").lower()
    return "subtitle" in low or "subtitles" in low or "caption" in low


def classify_ytdlp_error(detail: str) -> str:
    text = detail or ""
    low = text.lower()
    if _is_subtitle_related(text) and _is_http_429(text):
        return "subtitle_rate_limited"
    if _is_subtitle_related(text):
        return "subtitle_unavailable"
    if "412" in text or "precondition failed" in low:
        return "http_412"
    if _is_timeout_error(text):
        return "timeout"
    if _is_network_unreachable(text):
        return "network_unreachable"
    if "sign in to confirm" in low or "not a bot" in low:
        return "ytdlp_blocked"
    if "geo-restricted" in low or "deleted" in low:
        return "ytdlp_unavailable"
    return "ytdlp_failure"


def classify_ingest_error(detail: str) -> str:
    text = detail or ""
    low = text.lower()
    for kind in (
        "bilibili_view_failed",
        "bilibili_playurl_failed",
        "bilibili_media_download_failed",
        "bilibili_merge_failed",
    ):
        if kind in text:
            return kind
    if "ffmpeg" in low:
        return "ffmpeg_failure"
    if "语音识别" in text or "分出句子" in text or "asr" in low:
        return "asr_failure"
    return classify_ytdlp_error(text)


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _friendly_ytdlp_error(url: str, detail: str) -> str:
    del url
    del detail
    return LOCAL_UPLOAD_HINT


def public_url_import_error(exc: BaseException | str) -> str:
    text = str(exc or "").strip()
    if text[:5] in {"400: ", "402: ", "404: "}:
        text = text[5:].strip()
    if "无法从视频中分出句子" in text or "语音识别时间过长" in text:
        return text
    if "微信视频号" in text or "请粘贴 http" in text:
        return text
    return LOCAL_UPLOAD_HINT


def _run(cmd: list[str], timeout: int | None = None) -> None:
    _run_completed(cmd, timeout=timeout)


def _run_capture(cmd: list[str], timeout: int | None = None) -> str:
    completed = _run_completed(cmd, timeout=timeout)
    return completed.stdout or ""


def _run_completed(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    limit = YTDLP_TIMEOUT_SEC if timeout is None else timeout
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=limit,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("下载超时，已停止等待") from exc
    if completed.returncode != 0:
        raise RuntimeError(_compact_cmd_error(completed.stderr, completed.stdout))
    return completed


def _compact_cmd_error(stderr: str | None, stdout: str | None) -> str:
    detail = (stderr or stdout or "").strip()
    lines = [line.strip() for line in detail.splitlines() if line.strip() and "traceback" not in line.lower()]
    if not lines:
        return "yt-dlp 失败"
    return " | ".join(lines[-8:])


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
