import io
import json
import os
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from backend.app.bilibili import (
    BilibiliIngestError,
    _BilibiliRedirectHandler,
    _ResolvedRedirect,
    download_with_backups,
    fetch_view,
    is_allowed_bilibili_media_url,
    parse_bilibili_page,
    pick_dash_audio,
    pick_dash_video,
    subtitle_json_to_vtt,
)
from backend.app.ingest import classify_ingest_error, ingest_url, is_retryable_ytdlp_error, ytdlp_cmd_variants
from backend.app.store import find_session_id_by_url

BV_URL = "https://www.bilibili.com/video/BV1CMjq6nEu1/"
B23_URL = "https://b23.tv/abcdef"
PAGE2_URL = "https://www.bilibili.com/video/BV1CMjq6nEu1/?p=2"


def _view_payload(cid1=111, cid2=222, title="Official Title"):
    return {
        "code": 0,
        "message": "OK",
        "data": {
            "bvid": "BV1CMjq6nEu1",
            "aid": 123,
            "cid": cid1,
            "title": title,
            "duration": 120,
            "pic": "https://i0.hdslb.com/bfs/archive/cover.jpg",
            "pages": [
                {"cid": cid1, "page": 1, "duration": 60, "part": "P1"},
                {"cid": cid2, "page": 2, "duration": 60, "part": "P2"},
            ],
            "subtitle": {"list": []},
        },
    }


def _playurl_dash(video_url="https://upos-sz-mirrorcos.bilivideo.com/v.m4s", audio_url="https://upos-sz-mirrorcos.bilivideo.com/a.m4s", backup=None):
    video = {
        "id": 32,
        "height": 480,
        "codecs": "avc1.64001F",
        "bandwidth": 800000,
        "baseUrl": video_url,
    }
    if backup:
        video["backupUrl"] = [backup]
    return {
        "code": 0,
        "message": "OK",
        "data": {
            "quality": 32,
            "dash": {
                "video": [
                    video,
                    {"id": 64, "height": 720, "codecs": "avc1.64001F", "bandwidth": 1600000, "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/v720.m4s"},
                    {"id": 80, "height": 1080, "codecs": "avc1.64001F", "bandwidth": 3000000, "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/v1080.m4s"},
                ],
                "audio": [
                    {"id": 30280, "codecs": "mp4a.40.2", "bandwidth": 128000, "baseUrl": audio_url},
                ],
            },
        },
    }


class FakeResp:
    def __init__(self, data: bytes, url: str = "", status: int = 200):
        self._buf = data
        self.status = status
        self.headers = {}
        self.url = url

    def read(self, n: int = -1):
        if n is None or n < 0:
            out, self._buf = self._buf, b""
            return out
        chunk, self._buf = self._buf[:n], self._buf[n:]
        return chunk

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHTTP:
    def __init__(self, view=None, play=None, media=b"x" * 800, fail_first_cdn=None):
        self.requested = []
        self.view = view if view is not None else _view_payload()
        self.play = play if play is not None else _playurl_dash()
        self.media = media
        self.fail_first_cdn = fail_first_cdn
        self.cdn_hits = []

    def urlopen(self, req, timeout=None, context=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        self.requested.append(url)
        if "www.bilibili.com/video" in url or "m.bilibili.com/video" in url:
            raise urllib.error.HTTPError(url, 412, "Precondition Failed", Message(), io.BytesIO(b""))
        if "web-interface/view" in url:
            return FakeResp(json.dumps(self.view).encode("utf-8"), url)
        if "player/playurl" in url:
            return FakeResp(json.dumps(self.play).encode("utf-8"), url)
        if "player/v2" in url:
            return FakeResp(json.dumps({"code": 0, "data": {"subtitle": {"subtitles": []}}}).encode("utf-8"), url)
        if "bilivideo.com" in url or "hdslb.com" in url:
            self.cdn_hits.append(url)
            if self.fail_first_cdn and url == self.fail_first_cdn:
                raise urllib.error.URLError("cdn down")
            return FakeResp(self.media, url, status=200)
        raise AssertionError("unexpected url " + url)


class BilibiliLegacyTests(unittest.TestCase):
    def test_412_is_retryable_for_bilibili(self):
        err = "ERROR: [BiliBili] 1CMjq6nEu1: Unable to download webpage: HTTP Error 412: Precondition Failed"
        self.assertTrue(is_retryable_ytdlp_error("https://www.bilibili.com/video/BV1CMjq6nEu1/", err))
        self.assertFalse(is_retryable_ytdlp_error("https://www.youtube.com/watch?v=abc", err))

    def test_bilibili_variants_include_direct_proxy(self):
        base = ["yt-dlp", "--no-playlist"]
        variants = ytdlp_cmd_variants(base, "https://www.bilibili.com/video/BV1CMjq6nEu1/?spm_id_from=333.337")
        self.assertTrue(any(cmd[i : i + 2] == ["--proxy", ""] for cmd in variants for i in range(len(cmd) - 1)))
        self.assertGreaterEqual(len(variants), 2)

    def test_find_session_by_bvid_ignores_tracking_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "oldbili"
            folder.mkdir()
            (folder / "sentences.json").write_text("[]", encoding="utf-8")
            (folder / "meta.json").write_text(
                json.dumps(
                    {
                        "title": "old",
                        "source_url": "https://www.bilibili.com/video/BV1CMjq6nEu1/?spm_id_from=333.337.search-card.all.click",
                    }
                ),
                encoding="utf-8",
            )
            found = find_session_id_by_url(root, "https://www.bilibili.com/video/BV1CMjq6nEu1/")
            self.assertEqual(found, "oldbili")


class BilibiliNativePathTests(unittest.TestCase):
    def test_page_query_selects_second_cid(self):
        fake = FakeHTTP()
        with patch("backend.app.bilibili.urllib.request.urlopen", side_effect=fake.urlopen):
            view = fetch_view(PAGE2_URL)
        self.assertEqual(view["cid"], 222)
        self.assertEqual(view["page"], 2)
        self.assertTrue(any("web-interface/view" in url for url in fake.requested))
        self.assertFalse(any("/video/" in url and "api.bilibili.com" not in url for url in fake.requested))

    def test_default_page_is_p1(self):
        fake = FakeHTTP()
        with patch("backend.app.bilibili.urllib.request.urlopen", side_effect=fake.urlopen):
            view = fetch_view(BV_URL)
        self.assertEqual(view["cid"], 111)
        self.assertEqual(parse_bilibili_page(BV_URL), 1)

    def test_pick_dash_prefers_480_avc_not_1080(self):
        streams = (_playurl_dash()["data"]["dash"]["video"])
        picked = pick_dash_video(streams)
        self.assertEqual(picked["height"], 480)
        self.assertIn("avc", picked["codecs"])

    def test_pick_dash_audio_prefers_aac(self):
        streams = [
            {"codecs": "ec-3", "bandwidth": 400000, "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/e.m4s"},
            {"codecs": "mp4a.40.2", "bandwidth": 128000, "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/a.m4s"},
        ]
        picked = pick_dash_audio(streams)
        self.assertIn("mp4a", picked["codecs"])

    def test_pick_dash_audio_prefers_64k_over_132k(self):
        streams = [
            {"codecs": "mp4a.40.2", "bandwidth": 132000, "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/hi.m4s"},
            {"codecs": "mp4a.40.2", "bandwidth": 64000, "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/lo.m4s"},
        ]
        picked = pick_dash_audio(streams)
        self.assertEqual(picked["bandwidth"], 64000)

    def test_ssrf_blocks_private_media_hosts(self):
        self.assertFalse(is_allowed_bilibili_media_url("http://127.0.0.1/secret"))
        self.assertFalse(is_allowed_bilibili_media_url("http://192.168.1.4/v.m4s"))
        self.assertFalse(is_allowed_bilibili_media_url("file:///tmp/x"))
        self.assertFalse(is_allowed_bilibili_media_url("https://example.com/v.m4s"))
        self.assertTrue(is_allowed_bilibili_media_url("https://upos-sz-mirrorcos.bilivideo.com/v.m4s"))

    def test_redirect_handler_does_not_follow_video_html(self):
        handler = _BilibiliRedirectHandler()
        req = type("Req", (), {"full_url": B23_URL})()
        with self.assertRaises(_ResolvedRedirect) as ctx:
            handler.redirect_request(req, None, 302, "Found", {}, BV_URL)
        self.assertIn("BV1CMjq6nEu1", ctx.exception.url)
        with self.assertRaises(BilibiliIngestError):
            handler.redirect_request(req, None, 302, "Found", {}, "http://127.0.0.1/admin")

    def test_backup_cdn_used_when_first_fails(self):
        primary = "https://upos-sz-mirrorcos.bilivideo.com/bad.m4s"
        backup = "https://upos-sz-mirrorbd.bilivideo.com/ok.m4s"
        fake = FakeHTTP(fail_first_cdn=primary)
        dest = Path(tempfile.mkdtemp()) / "part.m4s"
        with patch("backend.app.bilibili.urllib.request.urlopen", side_effect=fake.urlopen):
            used = download_with_backups([primary, backup], dest)
        self.assertEqual(used, backup)
        self.assertGreater(dest.stat().st_size, 200)

    def test_english_subtitle_converts_to_vtt(self):
        vtt = subtitle_json_to_vtt({"body": [{"from": 0, "to": 1.5, "content": "Hello world"}]})
        self.assertIn("WEBVTT", vtt)
        self.assertIn("Hello world", vtt)
        self.assertIn("-->", vtt)

    def test_classify_native_error_kinds(self):
        self.assertEqual(classify_ingest_error("bilibili_view_failed: missing cid"), "bilibili_view_failed")
        self.assertEqual(classify_ingest_error("bilibili_playurl_failed: dash streams missing"), "bilibili_playurl_failed")
        self.assertEqual(classify_ingest_error("bilibili_media_download_failed: cdn host not allowed"), "bilibili_media_download_failed")
        self.assertEqual(classify_ingest_error("bilibili_merge_failed: ffmpeg merge failed"), "bilibili_merge_failed")


class BilibiliIngestIntegrationTests(unittest.TestCase):
    def _ingest(self, url, fake, env="production"):
        folder = Path(tempfile.mkdtemp())

        def fake_remux(src, dest):
            dest.write_bytes(b"m4a" * 400)

        def forbid_merge(*_args, **_kwargs):
            raise AssertionError("source.mp4 merge must not run on the session-ready path")

        old_env = os.environ.get("ENPRATO_ENV")
        os.environ["ENPRATO_ENV"] = env
        ytdlp_calls = []
        try:
            with patch("backend.app.bilibili.urllib.request.urlopen", side_effect=fake.urlopen), patch(
                "backend.app.bilibili.remux_dash_audio", side_effect=fake_remux
            ), patch("backend.app.bilibili.merge_dash", side_effect=forbid_merge), patch(
                "backend.app.ingest.extract_wav", side_effect=lambda src, dest, sr=16000: dest.write_bytes(b"RIFF" + b"\x00" * 100)
            ), patch("backend.app.ingest.ensure_playback_audio", side_effect=lambda folder, media=None: folder / "playback.m4a"), patch(
                "backend.app.ingest.fetch_bilibili_thumbnail", return_value=True
            ), patch(
                "backend.app.ingest.ytdlp_cmd", side_effect=lambda: ytdlp_calls.append("ytdlp") or ["yt-dlp"]
            ):
                result = ingest_url(url, folder)
        finally:
            if old_env is None:
                os.environ.pop("ENPRATO_ENV", None)
            else:
                os.environ["ENPRATO_ENV"] = old_env
        return result, folder, fake, ytdlp_calls

    def test_bv_url_uses_official_api_and_ignores_html_412(self):
        fake = FakeHTTP()
        (media, audio, captions), folder, fake, ytdlp_calls = self._ingest(BV_URL, fake)
        self.assertEqual(media.name, "playback.m4a")
        self.assertEqual(audio.name, "playback.m4a")
        self.assertIsNone(captions)
        self.assertEqual(ytdlp_calls, [])
        self.assertTrue(any("web-interface/view" in u for u in fake.requested))
        self.assertTrue(any("player/playurl" in u for u in fake.requested))
        self.assertTrue(any("bilivideo.com" in u and "/a.m4s" in u for u in fake.requested))
        self.assertFalse(any("/v.m4s" in u for u in fake.requested))
        self.assertFalse(any("www.bilibili.com/video" in u for u in fake.requested))
        self.assertFalse((folder / "source.mp4").exists())
        self.assertEqual(json.loads((folder / "import_meta.json").read_text(encoding="utf-8"))["title"], "Official Title")

    def test_b23_resolves_then_imports(self):
        fake = FakeHTTP()

        def resolve(url):
            self.assertTrue(url.startswith("https://b23.tv/"))
            return BV_URL

        with patch("backend.app.bilibili.resolve_bilibili_url", side_effect=resolve):
            (media, _audio, _captions), _folder, fake, ytdlp_calls = self._ingest(B23_URL, fake)
        self.assertEqual(media.name, "playback.m4a")
        self.assertEqual(ytdlp_calls, [])

    def test_missing_english_captions_does_not_block_media(self):
        fake = FakeHTTP()
        (_media, audio, captions), folder, _fake, _calls = self._ingest(BV_URL, fake)
        self.assertIsNone(captions)
        self.assertTrue(audio.is_file())
        self.assertEqual(json.loads((folder / "import_meta.json").read_text(encoding="utf-8"))["subtitle_status"], "unavailable")

    def test_english_captions_are_used(self):
        view = _view_payload()
        view["data"]["subtitle"] = {
            "list": [{"lan": "en", "subtitle_url": "https://aisubtitle.hdslb.com/bfs/subtitle/en.json"}]
        }
        fake = FakeHTTP(view=view)
        original = fake.urlopen

        def urlopen(req, timeout=None, context=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "aisubtitle.hdslb.com" in url:
                fake.requested.append(url)
                body = json.dumps({"body": [{"from": 0, "to": 2, "content": "Hello from official sub"}]}).encode()
                return FakeResp(body, url)
            return original(req, timeout=timeout, context=context)

        fake.urlopen = urlopen
        (_media, audio, captions), folder, _fake, ytdlp_calls = self._ingest(BV_URL, fake)
        self.assertIsNotNone(captions)
        self.assertIn("Hello from official sub", captions)
        self.assertTrue((folder / "playback.m4a").is_file())
        self.assertFalse((folder / "source.mp4").exists())
        self.assertEqual(ytdlp_calls, [])
        self.assertTrue((folder / "source.en.vtt").is_file())
        self.assertFalse(any("/v.m4s" in u for u in fake.requested))

    def test_youtube_still_uses_ytdlp(self):
        folder = Path(tempfile.mkdtemp())
        calls = []

        def fake_run(cmd, timeout=None):
            calls.append(list(cmd))
            if "--write-subs" in cmd or "--write-auto-subs" in cmd:
                raise RuntimeError("no subtitle")
            if "-f" in cmd:
                (folder / "source.mp4").write_bytes(b"mp4")
                return
            raise AssertionError(str(cmd))

        with patch("backend.app.ingest.ytdlp_cmd", return_value=["yt-dlp"]), patch(
            "backend.app.ingest.find_ffmpeg", return_value="ffmpeg"
        ), patch("backend.app.ingest.extract_wav"), patch(
            "backend.app.ingest.ensure_playback_audio"
        ), patch(
            "backend.app.ingest._ensure_playable", side_effect=lambda f: f / "source.mp4"
        ), patch(
            "backend.app.ingest.adopt_downloaded_thumbnail", return_value=True
        ), patch(
            "backend.app.ingest._run", side_effect=fake_run
        ), patch(
            "backend.app.ingest._run_capture",
            return_value=json.dumps({"title": "YT", "subtitles": {}, "automatic_captions": {}}),
        ):
            ingest_url("https://www.youtube.com/watch?v=abc", folder)
        self.assertTrue(any("-f" in cmd for cmd in calls))


if __name__ == "__main__":
    unittest.main()
