from __future__ import annotations

import ipaddress
import json
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from .media import run_ffmpeg, stream_codec

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
REFERER = "https://www.bilibili.com/"
VIEW_API = "https://api.bilibili.com/x/web-interface/view"
PLAYURL_API = "https://api.bilibili.com/x/player/playurl"
PLAYER_API = "https://api.bilibili.com/x/player/v2"
API_TIMEOUT_SEC = 20
MEDIA_TIMEOUT_SEC = 180
REDIRECT_TIMEOUT_SEC = 15
CHUNK_SIZE = 64 * 1024

API_HOSTS = {"api.bilibili.com"}
REDIRECT_HOSTS = {
    "b23.tv",
    "www.b23.tv",
    "b23.bilibili.com",
    "bilibili.com",
    "www.bilibili.com",
    "m.bilibili.com",
}
MEDIA_HOST_SUFFIXES = ("bilivideo.com", "bilivideo.cn", "hdslb.com")
EN_SUB_LANGS = ("en", "en-us", "en-gb", "ai-en", "en-en")


class BilibiliIngestError(RuntimeError):
    def __init__(self, kind: str, message: str = ""):
        self.kind = kind
        super().__init__(f"{kind}: {message}" if message else kind)


class _ResolvedRedirect(Exception):
    def __init__(self, url: str):
        self.url = url


def parse_bilibili_bvid(url: str) -> str | None:
    match = re.search(r"(BV[0-9A-Za-z]+)", url or "", re.I)
    return match.group(1) if match else None


def parse_bilibili_aid(url: str) -> str | None:
    text = url or ""
    match = re.search(r"(?:[?&]aid=|/video/av)(\d+)", text, re.I)
    return match.group(1) if match else None


def parse_bilibili_page(url: str) -> int:
    try:
        raw = (parse_qs(urlparse(url or "").query).get("p") or ["1"])[0]
        page = int(raw)
    except (TypeError, ValueError):
        return 1
    return page if page >= 1 else 1


def is_bilibili_video_html_url(url: str) -> bool:
    parsed = urlparse(url or "")
    host = _hostname(parsed.netloc)
    path = (parsed.path or "").lower()
    if host not in {"www.bilibili.com", "m.bilibili.com", "bilibili.com"}:
        return False
    return "/video/" in path


def _hostname(netloc: str) -> str:
    host = (netloc or "").split("@")[-1].lower()
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end > 0 else host
    return host.split(":")[0]


def _is_private_ip(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_private or ipaddress.ip_address(value).is_loopback or ipaddress.ip_address(value).is_link_local
    except ValueError:
        return False


def _host_resolves_private(host: str) -> bool:
    if _is_private_ip(host):
        return True
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    return any(_is_private_ip(item[4][0]) for item in infos if item[4])


def _suffix_allowed(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)


def is_allowed_bilibili_api_url(url: str) -> bool:
    parsed = urlparse(url or "")
    if parsed.scheme != "https":
        return False
    host = _hostname(parsed.netloc)
    return host in API_HOSTS and not _is_private_ip(host)


def is_allowed_bilibili_redirect_url(url: str) -> bool:
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"}:
        return False
    host = _hostname(parsed.netloc)
    if host in REDIRECT_HOSTS or _suffix_allowed(host, ("bilibili.com", "b23.tv")):
        return not _is_private_ip(host)
    return False


def is_allowed_bilibili_media_url(url: str) -> bool:
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"}:
        return False
    host = _hostname(parsed.netloc)
    if not host or _is_private_ip(host):
        return False
    if host in API_HOSTS:
        return False
    return _suffix_allowed(host, MEDIA_HOST_SUFFIXES)


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def _headers() -> dict[str, str]:
    return {"User-Agent": UA, "Referer": REFERER}


class _BilibiliRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urljoin(req.full_url, newurl)
        if is_bilibili_video_html_url(target) and (parse_bilibili_bvid(target) or parse_bilibili_aid(target)):
            raise _ResolvedRedirect(target)
        if not is_allowed_bilibili_redirect_url(target):
            raise BilibiliIngestError("bilibili_view_failed", "unsafe redirect")
        return super().redirect_request(req, fp, code, msg, headers, target)


def resolve_bilibili_url(url: str) -> str:
    current = (url or "").strip()
    if parse_bilibili_bvid(current) or parse_bilibili_aid(current):
        if is_bilibili_video_html_url(current) or "bilibili.com" in current.lower():
            return current
    parsed = urlparse(current)
    host = _hostname(parsed.netloc)
    if host not in {"b23.tv", "www.b23.tv"} and not host.endswith(".b23.tv") and host != "b23.bilibili.com":
        return current
    if not is_allowed_bilibili_redirect_url(current):
        raise BilibiliIngestError("bilibili_view_failed", "unsupported short link")
    opener = urllib.request.build_opener(_BilibiliRedirectHandler())
    req = urllib.request.Request(current, headers=_headers(), method="GET")
    try:
        with opener.open(req, timeout=REDIRECT_TIMEOUT_SEC) as resp:
            final = resp.geturl()
    except _ResolvedRedirect as exc:
        return exc.url
    except urllib.error.HTTPError as exc:
        location = exc.headers.get("Location") if exc.headers else ""
        if location:
            target = urljoin(current, location)
            if parse_bilibili_bvid(target) or parse_bilibili_aid(target):
                return target
        raise BilibiliIngestError("bilibili_view_failed", f"short link HTTP {exc.code}") from exc
    except BilibiliIngestError:
        raise
    except Exception as exc:
        raise BilibiliIngestError("bilibili_view_failed", "short link resolve failed") from exc
    if parse_bilibili_bvid(final) or parse_bilibili_aid(final):
        return final
    raise BilibiliIngestError("bilibili_view_failed", "short link did not resolve to a video")


def http_json(url: str, timeout: int = API_TIMEOUT_SEC, kind: str = "bilibili_view_failed") -> dict:
    if not is_allowed_bilibili_api_url(url):
        raise BilibiliIngestError(kind, "api host not allowed")
    req = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            raw = resp.read()
    except Exception as exc:
        raise BilibiliIngestError(kind, "api request failed") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise BilibiliIngestError(kind, "api json invalid") from exc
    if not isinstance(payload, dict):
        raise BilibiliIngestError(kind, "api json invalid")
    return payload


def fetch_view(url: str) -> dict:
    resolved = resolve_bilibili_url(url)
    bvid = parse_bilibili_bvid(resolved)
    aid = parse_bilibili_aid(resolved)
    if bvid:
        api = f"{VIEW_API}?bvid={bvid}"
    elif aid:
        api = f"{VIEW_API}?aid={aid}"
    else:
        raise BilibiliIngestError("bilibili_view_failed", "missing bvid/aid")
    payload = http_json(api)
    if payload.get("code") != 0:
        raise BilibiliIngestError("bilibili_view_failed", str(payload.get("message") or "view code"))
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise BilibiliIngestError("bilibili_view_failed", "view data missing")
    pages = data.get("pages") if isinstance(data.get("pages"), list) else []
    page = parse_bilibili_page(resolved)
    chosen = None
    if pages:
        index = min(page, len(pages)) - 1
        if index < 0:
            index = 0
        item = pages[index]
        if isinstance(item, dict):
            chosen = item
    cid = (chosen or {}).get("cid") if chosen else data.get("cid")
    if not cid:
        raise BilibiliIngestError("bilibili_view_failed", "missing cid")
    return {
        "url": resolved,
        "bvid": str(data.get("bvid") or bvid or ""),
        "aid": str(data.get("aid") or aid or ""),
        "cid": int(cid),
        "page": page,
        "title": str(data.get("title") or "").strip(),
        "duration": int(data.get("duration") or (chosen or {}).get("duration") or 0),
        "pic": str(data.get("pic") or "").strip(),
        "subtitle": data.get("subtitle") if isinstance(data.get("subtitle"), dict) else {},
        "pages": pages,
    }


def fetch_playurl(bvid: str, cid: int, qn: int = 32) -> dict:
    if not bvid or not cid:
        raise BilibiliIngestError("bilibili_playurl_failed", "missing bvid/cid")
    api = f"{PLAYURL_API}?bvid={bvid}&cid={cid}&qn={qn}&fnval=16&fourk=0"
    payload = http_json(api, kind="bilibili_playurl_failed")
    if payload.get("code") != 0:
        raise BilibiliIngestError("bilibili_playurl_failed", str(payload.get("message") or "playurl code"))
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise BilibiliIngestError("bilibili_playurl_failed", "playurl data missing")
    return data


def _stream_urls(stream: dict) -> list[str]:
    urls: list[str] = []
    for key in ("baseUrl", "base_url", "url"):
        value = stream.get(key)
        if isinstance(value, str) and value.strip():
            urls.append(value.strip())
    for key in ("backupUrl", "backup_url"):
        raw = stream.get(key) or []
        if isinstance(raw, str) and raw.strip():
            urls.append(raw.strip())
        elif isinstance(raw, list):
            urls.extend(str(item).strip() for item in raw if str(item).strip())
    seen: set[str] = set()
    unique: list[str] = []
    for item in urls:
        if item.startswith("//"):
            item = "https:" + item
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def pick_dash_video(streams: list[dict]) -> dict | None:
    scored: list[tuple[int, int, dict]] = []
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        height = int(stream.get("height") or 0)
        if height > 720:
            continue
        codecs = str(stream.get("codecs") or "").lower()
        bandwidth = int(stream.get("bandwidth") or 0)
        score = 0
        if 1 <= height <= 480:
            score += 200
        elif height <= 720:
            score += 80
        if "avc" in codecs:
            score += 60
        score -= abs((height or 480) - 480)
        scored.append((score, -bandwidth, stream))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return scored[0][2]


def pick_dash_audio(streams: list[dict]) -> dict | None:
    scored: list[tuple[int, int, dict]] = []
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        codecs = str(stream.get("codecs") or "").lower()
        bandwidth = int(stream.get("bandwidth") or 0)
        score = 0
        if "mp4a" in codecs or "aac" in codecs:
            score += 50
        target = 64000
        score -= abs((bandwidth or target) - target) // 1000
        scored.append((score, -bandwidth, stream))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return scored[0][2]


def download_media_url(url: str, dest: Path, timeout: int = MEDIA_TIMEOUT_SEC) -> None:
    if not is_allowed_bilibili_media_url(url):
        raise BilibiliIngestError("bilibili_media_download_failed", "cdn host not allowed")
    if _host_resolves_private(_hostname(urlparse(url).netloc)):
        raise BilibiliIngestError("bilibili_media_download_failed", "cdn host not allowed")
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            with dest.open("wb") as handle:
                while True:
                    chunk = resp.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
    except BilibiliIngestError:
        raise
    except Exception as exc:
        raise BilibiliIngestError("bilibili_media_download_failed", "cdn download failed") from exc
    if not dest.is_file() or dest.stat().st_size < 200:
        raise BilibiliIngestError("bilibili_media_download_failed", "cdn file too small")


def download_with_backups(urls: list[str], dest: Path) -> str:
    last: Exception | None = None
    for url in urls:
        try:
            download_media_url(url, dest)
            return url
        except BilibiliIngestError as exc:
            last = exc
            if dest.exists():
                try:
                    dest.unlink()
                except OSError:
                    pass
            continue
    raise last or BilibiliIngestError("bilibili_media_download_failed", "no cdn url")


def remux_dash_audio(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_ffmpeg(["-i", str(src), "-vn", "-c:a", "copy", "-movflags", "+faststart", str(dest)])
    except Exception:
        run_ffmpeg(
            ["-i", str(src), "-vn", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dest)]
        )
    if not dest.is_file() or dest.stat().st_size < 200:
        raise BilibiliIngestError("bilibili_media_download_failed", "audio remux missing")


def merge_dash(video: Path, audio: Path, dest: Path) -> None:
    try:
        run_ffmpeg(
            [
                "-i",
                str(video),
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
                "-f",
                "mp4",
                str(dest),
            ]
        )
    except Exception as exc:
        raise BilibiliIngestError("bilibili_merge_failed", "ffmpeg merge failed") from exc
    if not dest.is_file() or dest.stat().st_size < 200:
        raise BilibiliIngestError("bilibili_merge_failed", "merged file missing")


def _english_subtitle_url(payload: dict | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    items = payload.get("list") or payload.get("subtitles") or []
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        lan = str(item.get("lan") or item.get("lang") or "").lower()
        if lan not in EN_SUB_LANGS and not lan.startswith("en"):
            continue
        href = str(item.get("subtitle_url") or item.get("url") or "").strip()
        if href.startswith("//"):
            href = "https:" + href
        if href:
            return href
    return None


def _seconds_to_vtt(value: float) -> str:
    total = max(0.0, float(value))
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    seconds = total % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"


def subtitle_json_to_vtt(payload: dict) -> str:
    body = payload.get("body") if isinstance(payload, dict) else None
    if not isinstance(body, list):
        return ""
    lines = ["WEBVTT", ""]
    for cue in body:
        if not isinstance(cue, dict):
            continue
        text = str(cue.get("content") or "").strip()
        if not text:
            continue
        start = _seconds_to_vtt(float(cue.get("from") or 0))
        end = _seconds_to_vtt(float(cue.get("to") or 0))
        lines.append(f"{start} --> {end}")
        lines.append(text.replace("\n", " "))
        lines.append("")
    return "\n".join(lines).strip() + ("\n" if body else "")


def fetch_english_captions(view: dict) -> str | None:
    href = _english_subtitle_url(view.get("subtitle") if isinstance(view, dict) else None)
    if not href:
        bvid = str((view or {}).get("bvid") or "")
        cid = int((view or {}).get("cid") or 0)
        if bvid and cid:
            try:
                payload = http_json(f"{PLAYER_API}?bvid={bvid}&cid={cid}")
                data = payload.get("data") if payload.get("code") == 0 else None
                subtitle = (data or {}).get("subtitle") if isinstance(data, dict) else None
                href = _english_subtitle_url(subtitle if isinstance(subtitle, dict) else None)
            except BilibiliIngestError:
                href = None
    if not href or not is_allowed_bilibili_media_url(href):
        return None
    try:
        req = urllib.request.Request(href, headers=_headers())
        with urllib.request.urlopen(req, timeout=API_TIMEOUT_SEC, context=_ssl_context()) as resp:
            raw = resp.read()
        payload = json.loads(raw.decode("utf-8"))
        vtt = subtitle_json_to_vtt(payload if isinstance(payload, dict) else {})
        return vtt if "-->" in vtt else None
    except Exception:
        return None


def ingest_bilibili(url: str, folder: Path, *, include_video: bool = False) -> tuple[Path, str | None, dict]:
    """Download DASH audio for playback/ASR. Video merge is off the session-ready path."""
    started = time.monotonic()
    t0 = time.monotonic()
    view = fetch_view(url)
    play = fetch_playurl(view["bvid"], view["cid"])
    view["t_metadata_ms"] = int((time.monotonic() - t0) * 1000)
    captions_box: dict[str, str | None] = {"text": None}
    t1 = time.monotonic()

    def _load_captions() -> None:
        try:
            captions_box["text"] = fetch_english_captions(view)
        except Exception:
            captions_box["text"] = None

    cap_thread = threading.Thread(target=_load_captions, name="bili-captions", daemon=True)
    cap_thread.start()
    folder.mkdir(parents=True, exist_ok=True)
    playback = folder / "playback.m4a"
    dash = play.get("dash") if isinstance(play.get("dash"), dict) else None
    if dash:
        audio = pick_dash_audio(dash.get("audio") or [])
        if not audio:
            raise BilibiliIngestError("bilibili_playurl_failed", "dash audio missing")
        audio_part = folder / "dash_audio.m4s"
        t2 = time.monotonic()
        download_with_backups(_stream_urls(audio), audio_part)
        view["t_audio_download_ms"] = int((time.monotonic() - t2) * 1000)
        t3 = time.monotonic()
        remux_dash_audio(audio_part, playback)
        view["t_audio_prepare_ms"] = int((time.monotonic() - t3) * 1000)
        try:
            if audio_part.exists():
                audio_part.unlink()
        except OSError:
            pass
        dest = playback
        if include_video:
            video = pick_dash_video(dash.get("video") or [])
            if not video:
                raise BilibiliIngestError("bilibili_playurl_failed", "dash video missing")
            video_part = folder / "dash_video.m4s"
            merged = folder / "source.mp4"
            try:
                download_with_backups(_stream_urls(video), video_part)
                merge_dash(video_part, playback, merged)
                dest = merged
            finally:
                try:
                    if video_part.exists():
                        video_part.unlink()
                except OSError:
                    pass
    else:
        durl = play.get("durl") if isinstance(play.get("durl"), list) else []
        if not durl or not isinstance(durl[0], dict):
            raise BilibiliIngestError("bilibili_playurl_failed", "no playable stream")
        dest = folder / "source.mp4"
        t2 = time.monotonic()
        download_with_backups(_stream_urls(durl[0]), dest)
        view["t_audio_download_ms"] = int((time.monotonic() - t2) * 1000)
        t3 = time.monotonic()
        remux_dash_audio(dest, playback)
        view["t_audio_prepare_ms"] = int((time.monotonic() - t3) * 1000)
        dest = playback
    cap_thread.join(timeout=API_TIMEOUT_SEC)
    captions = captions_box["text"]
    view["t_subtitle_ms"] = int((time.monotonic() - t1) * 1000)
    view["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    view["captions"] = bool(captions)
    view["audio_only"] = not include_video
    return dest, captions, view


def source_mp4_complete(folder: Path) -> bool:
    path = folder / "source.mp4"
    if not path.is_file() or path.stat().st_size < 1000:
        return False
    return bool(stream_codec(path, "v")) and bool(stream_codec(path, "a"))


def _confirm_av_streams(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < 1000:
        raise BilibiliIngestError("bilibili_merge_failed", "merged file missing")
    if not stream_codec(path, "v") or not stream_codec(path, "a"):
        raise BilibiliIngestError("bilibili_merge_failed", "merged streams missing")


def prepare_bilibili_video(url: str, folder: Path) -> dict:
    """Download video DASH only and merge with existing playback.m4a. Never downloads audio."""
    started = time.monotonic()
    playback = folder / "playback.m4a"
    if not playback.is_file() or playback.stat().st_size < 200:
        raise BilibiliIngestError("bilibili_audio_missing", "playback.m4a missing")
    if source_mp4_complete(folder):
        return {
            "skipped": True,
            "t_video_download_ms": 0,
            "t_video_merge_ms": 0,
            "elapsed_ms": 0,
        }
    folder.mkdir(parents=True, exist_ok=True)
    view = fetch_view(url)
    play = fetch_playurl(view["bvid"], view["cid"])
    dash = play.get("dash") if isinstance(play.get("dash"), dict) else None
    dest = folder / "source.mp4"
    tmp = folder / "source.mp4.tmp"
    video_part = folder / "dash_video.m4s"
    t_download = 0.0
    t_merge = 0.0
    try:
        if tmp.exists():
            tmp.unlink()
        if dash:
            video = pick_dash_video(dash.get("video") or [])
            if not video:
                raise BilibiliIngestError("bilibili_playurl_failed", "dash video missing")
            t0 = time.monotonic()
            download_with_backups(_stream_urls(video), video_part)
            t_download = time.monotonic() - t0
            t1 = time.monotonic()
            merge_dash(video_part, playback, tmp)
            t_merge = time.monotonic() - t1
        else:
            raise BilibiliIngestError("bilibili_playurl_failed", "dash video missing")
        _confirm_av_streams(tmp)
        tmp.replace(dest)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise
    finally:
        try:
            if video_part.exists():
                video_part.unlink()
        except OSError:
            pass
    return {
        "skipped": False,
        "t_video_download_ms": int(t_download * 1000),
        "t_video_merge_ms": int(t_merge * 1000),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
