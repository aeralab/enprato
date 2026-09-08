import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import db, ingest, main


def _isolate_env():
    tmp = tempfile.TemporaryDirectory()
    old = {
        "db": db.DB_PATH,
        "data": main.DATA,
        "auth": os.environ.get("ENPRATO_REQUIRE_AUTH"),
        "secure": os.environ.get("ENPRATO_COOKIE_SECURE"),
    }
    db.DB_PATH = Path(tmp.name) / "url-ingest.sqlite3"
    main.DATA = Path(tmp.name) / "sessions"
    main.DATA.mkdir()
    db.migrate()
    os.environ.pop("ENPRATO_REQUIRE_AUTH", None)
    os.environ["ENPRATO_COOKIE_SECURE"] = "0"
    return tmp, old


def _restore_env(tmp, old):
    db.DB_PATH, main.DATA = old["db"], old["data"]
    if old["auth"] is None:
        os.environ.pop("ENPRATO_REQUIRE_AUTH", None)
    else:
        os.environ["ENPRATO_REQUIRE_AUTH"] = old["auth"]
    if old["secure"] is None:
        os.environ.pop("ENPRATO_COOKIE_SECURE", None)
    else:
        os.environ["ENPRATO_COOKIE_SECURE"] = old["secure"]
    tmp.cleanup()


def _ok_media(folder: Path, captions: str | None):
    audio = folder / "audio.wav"
    audio.write_bytes(b"RIFF")
    (folder / "source.mp4").write_bytes(b"mp4")
    return folder / "source.mp4", audio, captions


CAPTION = "WEBVTT\n\n00:00:00.000 --> 00:00:04.000\nA newly imported sentence, with a natural split, for testing.\n"


SUBTITLE_429 = "ERROR: Unable to download video subtitles for 'en-en-GB': HTTP Error 429: Too Many Requests"


def _cmd_text(cmd) -> str:
    return " ".join(str(part) for part in cmd)


def _is_media_cmd(cmd) -> bool:
    return "-f" in cmd and "--skip-download" not in cmd


def _is_subtitle_cmd(cmd) -> bool:
    return "--write-subs" in cmd or "--write-auto-subs" in cmd


class SubtitlePolicyTests(unittest.TestCase):
    def test_prefers_manual_english_over_auto(self):
        lang, is_auto = ingest.pick_english_sub_lang(
            {"subtitles": {"en": [{}], "fr": [{}]}, "automatic_captions": {"en": [{}]}}
        )
        self.assertEqual(lang, "en")
        self.assertFalse(is_auto)

    def test_uses_auto_english_when_no_manual(self):
        lang, is_auto = ingest.pick_english_sub_lang(
            {"subtitles": {"zh-Hans": [{}]}, "automatic_captions": {"en": [{}], "en-US": [{}]}}
        )
        self.assertEqual(lang, "en")
        self.assertTrue(is_auto)

    def test_skips_malformed_en_en_gb_key(self):
        lang, is_auto = ingest.pick_english_sub_lang(
            {"subtitles": {}, "automatic_captions": {"en-en-GB": [{}], "en": [{}]}}
        )
        self.assertEqual(lang, "en")
        self.assertTrue(is_auto)

    def test_no_english_returns_none(self):
        lang, is_auto = ingest.pick_english_sub_lang({"subtitles": {"ja": [{}]}, "automatic_captions": {}})
        self.assertIsNone(lang)
        self.assertFalse(is_auto)

    def test_source_does_not_use_wide_sub_langs(self):
        text = Path(ingest.__file__).read_text(encoding="utf-8")
        self.assertNotIn("en.*,en", text)
        self.assertNotIn("--sub-langs\",\n        \"en.*", text)


class FormatAndPreviewTests(unittest.TestCase):
    def test_format_selectors_never_download_uncapped_bestvideo(self):
        joined = " ".join(ingest.MEDIA_FORMATS)
        self.assertNotIn("bestvideo+bestaudio", joined)
        self.assertNotIn("bv*+ba/b", joined)
        parts = [part for fmt in ingest.MEDIA_FORMATS for part in fmt.split("/")]
        self.assertNotIn("best", parts)
        for fmt in ingest.MEDIA_FORMATS[:-1]:
            self.assertIn("height<=", fmt)

    def test_audio_only_is_last_resort(self):
        self.assertTrue(ingest.MEDIA_FORMATS[-1].startswith("ba"))

    def test_url_preview_does_not_include_query(self):
        preview = ingest.url_preview("https://www.youtube.com/watch?v=dQw4w9wgGcQ&list=secret-token")
        self.assertEqual(preview, "youtube:dQw4w9wgGcQ")
        self.assertNotIn("secret", preview)
        self.assertNotIn("list=", preview)
        bili = ingest.url_preview("https://www.bilibili.com/video/BV1CMjq6nEu1/?spm_id_from=333")
        self.assertEqual(bili, "bilibili:BV1CMjq6nEu1")


class YtdlpTimeoutTests(unittest.TestCase):
    def test_hung_subprocess_is_killed(self):
        started = time.monotonic()
        with self.assertRaises(RuntimeError) as ctx:
            ingest._run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)
        self.assertLess(time.monotonic() - started, 8)
        self.assertIn("超时", str(ctx.exception))

    def test_timeout_does_not_try_next_format(self):
        calls = []

        def fake_run(cmd, timeout=None):
            calls.append(cmd)
            raise RuntimeError("下载超时，已停止等待")

        with patch.object(ingest, "_run", side_effect=fake_run):
            with self.assertRaises(RuntimeError):
                ingest._ytdlp_fetch(["yt-dlp"], "https://www.youtube.com/watch?v=abc")
        self.assertEqual(len(calls), 1)

    def test_network_unreachable_does_not_try_next_format(self):
        calls = []
        err = (
            "ERROR: [youtube] gpMe8ADa2_E: Unable to download API page: "
            "HTTPSConnection(host='www.youtube.com', port=443): "
            "Failed to establish a new connection: [Errno 101] Network is unreachable"
        )

        def fake_run(cmd, timeout=None):
            calls.append(cmd)
            raise RuntimeError(err)

        with patch.object(ingest, "_run", side_effect=fake_run):
            with self.assertRaises(RuntimeError):
                ingest._ytdlp_fetch(["yt-dlp"], "https://www.youtube.com/watch?v=gpMe8ADa2_E")
        self.assertEqual(len(calls), 1)

    def test_format_unavailable_falls_back(self):
        calls = []

        def fake_run(cmd, timeout=None):
            calls.append(cmd)
            if len(calls) < 2:
                raise RuntimeError("Requested format is not available")

        with patch.object(ingest, "_run", side_effect=fake_run):
            ingest._ytdlp_fetch(["yt-dlp"], "https://www.youtube.com/watch?v=abc")
        self.assertEqual(len(calls), 2)

    def test_subtitle_429_does_not_try_next_format(self):
        calls = []

        def fake_run(cmd, timeout=None):
            calls.append(cmd)
            raise RuntimeError(SUBTITLE_429)

        with patch.object(ingest, "_run", side_effect=fake_run):
            with self.assertRaises(RuntimeError):
                ingest._ytdlp_fetch(["yt-dlp"], "https://www.youtube.com/watch?v=abc")
        self.assertEqual(len(calls), 1)


class FriendlyErrorTests(unittest.TestCase):
    def _assert_safe_user_error(self, msg: str):
        self.assertEqual(msg, ingest.LOCAL_UPLOAD_HINT)
        low = msg.lower()
        self.assertNotIn("errno", low)
        self.assertNotIn("101", msg)
        self.assertNotIn("youtube", low)
        self.assertNotIn("b站", msg)
        self.assertNotIn("bilibili", low)
        self.assertNotIn("412", msg)
        self.assertNotIn("traceback", low)
        self.assertNotIn("yt-dlp", low)
        self.assertNotIn("www.", low)

    def test_youtube_network_unreachable_is_classified(self):
        err = (
            "ERROR: [youtube] gpMe8ADa2_E: Unable to download API page: "
            "HTTPSConnection(host='www.youtube.com', port=443): "
            "Failed to establish a new connection: [Errno 101] Network is unreachable"
        )
        self.assertEqual(ingest.classify_ytdlp_error(err), "network_unreachable")
        self._assert_safe_user_error(ingest.public_url_import_error(err))

    def test_bilibili_412_asks_for_local_upload(self):
        err = "ERROR: [BiliBili] Unable to download webpage: HTTP Error 412: Precondition Failed"
        self.assertEqual(ingest.classify_ytdlp_error(err), "http_412")
        msg = ingest._friendly_ytdlp_error("https://www.bilibili.com/video/BV1CMjq6nEu1/", err)
        self._assert_safe_user_error(msg)
        self.assertNotIn("ENPRATO_COOKIES", msg)

    def test_timeout_asks_for_local_upload(self):
        msg = ingest._friendly_ytdlp_error("https://www.youtube.com/watch?v=abc", "下载超时，已停止等待")
        self._assert_safe_user_error(msg)

    def test_subtitle_429_is_rate_limited_not_fatal_kind(self):
        self.assertEqual(ingest.classify_ytdlp_error(SUBTITLE_429), "subtitle_rate_limited")
        self.assertEqual(
            ingest.classify_ytdlp_error("ERROR: Unable to download video subtitles for 'en': HTTP Error 404"),
            "subtitle_unavailable",
        )


class IngestDecoupleTests(unittest.TestCase):
    def _run_ingest(self, url, folder, fake_run, info=None):
        capture = json.dumps(
            info
            if info is not None
            else {"title": "From Meta", "subtitles": {}, "automatic_captions": {}}
        )
        with patch.object(ingest, "ytdlp_cmd", return_value=["yt-dlp"]), patch.object(
            ingest, "find_ffmpeg", return_value="ffmpeg"
        ), patch.object(ingest, "extract_wav") as extract, patch.object(
            ingest, "ensure_playback_audio"
        ) as playback, patch.object(
            ingest, "_ensure_playable", side_effect=lambda f: f / "source.mp4"
        ), patch.object(ingest, "adopt_downloaded_thumbnail", return_value=True), patch.object(
            ingest, "fetch_bilibili_view", return_value={"title": "Bili Title", "pic": ""}
        ), patch.object(ingest, "_run", side_effect=fake_run), patch.object(
            ingest, "_run_capture", return_value=capture
        ):
            result = ingest.ingest_url(url, folder)
        return result, extract, playback

    def test_manual_captions_skip_asr_extract(self):
        folder = Path(tempfile.mkdtemp())
        calls = []

        def fake_run(cmd, timeout=None):
            calls.append(list(cmd))
            if _is_subtitle_cmd(cmd):
                self.assertIn("--write-subs", cmd)
                self.assertNotIn("--write-auto-subs", cmd)
                self.assertIn("en", cmd)
                (folder / "source.en.vtt").write_text(CAPTION, encoding="utf-8")
                return
            if _is_media_cmd(cmd):
                self.assertNotIn("--write-subs", cmd)
                self.assertNotIn("--write-auto-subs", cmd)
                (folder / "source.mp4").write_bytes(b"mp4")
                return
            raise AssertionError(_cmd_text(cmd))

        (media, audio, captions), extract, playback = self._run_ingest(
            "https://www.youtube.com/watch?v=manual",
            folder,
            fake_run,
            {"title": "From Meta", "subtitles": {"en": [{}]}, "automatic_captions": {"en": [{}]}},
        )
        self.assertIsNotNone(captions)
        self.assertIn("newly imported", captions)
        extract.assert_not_called()
        playback.assert_not_called()
        self.assertEqual(sum(1 for cmd in calls if _is_media_cmd(cmd)), 1)
        self.assertEqual(ingest.read_import_title(folder), "From Meta")
        self.assertTrue(media.name.endswith(".mp4"))
        self.assertEqual(audio.name, "audio.wav")

    def test_auto_captions_use_write_auto_subs(self):
        folder = Path(tempfile.mkdtemp())

        def fake_run(cmd, timeout=None):
            if _is_subtitle_cmd(cmd):
                self.assertIn("--write-auto-subs", cmd)
                self.assertNotIn("--write-subs", cmd)
                (folder / "source.en.vtt").write_text(CAPTION, encoding="utf-8")
                return
            if _is_media_cmd(cmd):
                (folder / "source.mp4").write_bytes(b"mp4")
                return
            raise AssertionError(_cmd_text(cmd))

        (_media, _audio, captions), extract, _playback = self._run_ingest(
            "https://www.youtube.com/watch?v=auto",
            folder,
            fake_run,
            {"title": "Auto", "subtitles": {}, "automatic_captions": {"en": [{}]}},
        )
        self.assertIsNotNone(captions)
        extract.assert_not_called()

    def test_subtitle_429_falls_back_to_single_media_download(self):
        folder = Path(tempfile.mkdtemp())
        calls = []

        def fake_run(cmd, timeout=None):
            calls.append(list(cmd))
            if _is_subtitle_cmd(cmd):
                raise RuntimeError(SUBTITLE_429)
            if _is_media_cmd(cmd):
                (folder / "source.mp4").write_bytes(b"mp4")
                return
            raise AssertionError(_cmd_text(cmd))

        (_media, _audio, captions), extract, playback = self._run_ingest(
            "https://www.youtube.com/watch?v=rate",
            folder,
            fake_run,
            {"title": "Rate", "subtitles": {"en": [{}]}, "automatic_captions": {"en-GB": [{}]}},
        )
        self.assertIsNone(captions)
        extract.assert_called_once()
        playback.assert_called_once()
        self.assertEqual(ingest.read_subtitle_status(folder), "rate_limited")
        self.assertEqual(sum(1 for cmd in calls if _is_subtitle_cmd(cmd)), 1)
        self.assertEqual(sum(1 for cmd in calls if _is_media_cmd(cmd)), 1)
        media_cmds = [cmd for cmd in calls if _is_media_cmd(cmd)]
        self.assertIn("height<=480", media_cmds[0][media_cmds[0].index("-f") + 1])
        self.assertTrue(all(not _is_subtitle_cmd(cmd) for cmd in media_cmds))

    def test_no_english_skips_subtitle_request(self):
        folder = Path(tempfile.mkdtemp())
        calls = []

        def fake_run(cmd, timeout=None):
            calls.append(list(cmd))
            if _is_media_cmd(cmd):
                (folder / "source.mp4").write_bytes(b"mp4")
                return
            raise AssertionError("unexpected " + _cmd_text(cmd))

        (_media, _audio, captions), extract, _playback = self._run_ingest(
            "https://www.youtube.com/watch?v=nosub",
            folder,
            fake_run,
            {"title": "No", "subtitles": {"ja": [{}]}, "automatic_captions": {"es": [{}]}},
        )
        self.assertIsNone(captions)
        extract.assert_called_once()
        self.assertEqual(sum(1 for cmd in calls if _is_subtitle_cmd(cmd)), 0)
        self.assertEqual(sum(1 for cmd in calls if _is_media_cmd(cmd)), 1)

    def test_bilibili_without_english_downloads_480_and_extracts(self):
        folder = Path(tempfile.mkdtemp())
        calls = []

        def fake_run(cmd, timeout=None):
            calls.append(list(cmd))
            if _is_subtitle_cmd(cmd):
                raise RuntimeError("WARNING: no subtitle")
            if _is_media_cmd(cmd):
                fmt = cmd[cmd.index("-f") + 1]
                self.assertIn("height<=480", fmt)
                (folder / "source.mp4").write_bytes(b"mp4")
                return
            raise AssertionError(_cmd_text(cmd))

        (_media, _audio, captions), extract, _playback = self._run_ingest(
            "https://www.bilibili.com/video/BV1JUtj6QENd/",
            folder,
            fake_run,
        )
        self.assertIsNone(captions)
        extract.assert_called_once()
        self.assertEqual(ingest.read_import_title(folder), "Bili Title")
        self.assertEqual(sum(1 for cmd in calls if _is_media_cmd(cmd)), 1)

    def test_480_unavailable_falls_back_to_720_once(self):
        folder = Path(tempfile.mkdtemp())
        formats = []

        def fake_run(cmd, timeout=None):
            if _is_subtitle_cmd(cmd):
                raise RuntimeError("no subtitle")
            if _is_media_cmd(cmd):
                fmt = cmd[cmd.index("-f") + 1]
                formats.append(ingest.format_attempt_label(fmt))
                if ingest.format_attempt_label(fmt) == "480":
                    raise RuntimeError("Requested format is not available")
                (folder / "source.mp4").write_bytes(b"mp4")
                return
            raise AssertionError(_cmd_text(cmd))

        self._run_ingest("https://www.youtube.com/watch?v=fmt", folder, fake_run)
        self.assertEqual(formats, ["480", "720"])

    def test_media_success_does_not_redownload_after_subtitle_failure(self):
        folder = Path(tempfile.mkdtemp())
        order = []

        def fake_run(cmd, timeout=None):
            if _is_subtitle_cmd(cmd):
                order.append("subtitle")
                raise RuntimeError(SUBTITLE_429)
            if _is_media_cmd(cmd):
                order.append("media")
                (folder / "source.mp4").write_bytes(b"mp4")
                return
            raise AssertionError(_cmd_text(cmd))

        self._run_ingest(
            "https://www.youtube.com/watch?v=once",
            folder,
            fake_run,
            {"title": "Once", "subtitles": {"en": [{}]}, "automatic_captions": {}},
        )
        self.assertEqual(order, ["subtitle", "media"])


class PrepareUrlFailureTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.old = _isolate_env()

    def tearDown(self):
        _restore_env(self.tmp, self.old)

    def test_ytdlp_failure_returns_400_and_cleans_session(self):
        client = TestClient(main.app)
        with patch.object(main, "ingest_url", side_effect=RuntimeError("yt-dlp 拉取失败")):
            res = client.post("/api/prepare-url", json={"url": "https://www.youtube.com/watch?v=abc"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["detail"], ingest.LOCAL_UPLOAD_HINT)
        self.assertNotIn("yt-dlp", res.json()["detail"])
        self.assertEqual(list(main.DATA.iterdir()), [])

    def test_youtube_network_unreachable_returns_generic_fallback(self):
        client = TestClient(main.app)
        err = (
            "ERROR: [youtube] gpMe8ADa2_E: Unable to download API page: "
            "HTTPSConnection(host='www.youtube.com', port=443): "
            "Failed to establish a new connection: [Errno 101] Network is unreachable"
        )
        with patch.object(main, "ingest_url", side_effect=RuntimeError(err)):
            res = client.post("/api/prepare-url", json={"url": "https://www.youtube.com/watch?v=gpMe8ADa2_E"})
        self.assertEqual(res.status_code, 400)
        detail = res.json()["detail"]
        self.assertEqual(detail, ingest.LOCAL_UPLOAD_HINT)
        self.assertNotIn("Errno", detail)
        self.assertNotIn("101", detail)
        self.assertNotIn("youtube", detail.lower())
        self.assertEqual(list(main.DATA.iterdir()), [])

    def test_download_timeout_returns_400(self):
        client = TestClient(main.app)
        err = ingest._friendly_ytdlp_error("https://www.youtube.com/watch?v=abc", "下载超时，已停止等待")
        with patch.object(main, "ingest_url", side_effect=RuntimeError(err)):
            res = client.post("/api/prepare-url", json={"url": "https://www.youtube.com/watch?v=abc"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["detail"], ingest.LOCAL_UPLOAD_HINT)
        self.assertEqual(list(main.DATA.iterdir()), [])

    def test_bilibili_412_returns_local_upload_fallback(self):
        client = TestClient(main.app)
        url = "https://www.bilibili.com/video/BV1CMjq6nEu1/"
        err = ingest._friendly_ytdlp_error(url, "HTTP Error 412: Precondition Failed")
        with patch.object(main, "ingest_url", side_effect=RuntimeError(err)):
            res = client.post("/api/prepare-url", json={"url": url})
        self.assertEqual(res.status_code, 400)
        detail = res.json()["detail"]
        self.assertEqual(detail, ingest.LOCAL_UPLOAD_HINT)
        self.assertNotIn("412", detail)
        self.assertNotIn("B站", detail)

    def test_ffmpeg_failure_returns_400(self):
        client = TestClient(main.app)
        with patch.object(main, "ingest_url", side_effect=RuntimeError("ffmpeg 执行失败")):
            res = client.post("/api/prepare-url", json={"url": "https://www.youtube.com/watch?v=abc"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["detail"], ingest.LOCAL_UPLOAD_HINT)
        self.assertEqual(list(main.DATA.iterdir()), [])

    def test_asr_failure_returns_400_and_does_not_leave_pending_session(self):
        client = TestClient(main.app)

        def fake_ingest(_url, folder):
            return _ok_media(folder, None)

        with patch.object(main, "ingest_url", side_effect=fake_ingest), patch.object(
            main, "fetch_media_title", return_value="title"
        ), patch.object(main, "transcribe_sentences", return_value=[]):
            res = client.post("/api/prepare-url", json={"url": "https://www.youtube.com/watch?v=abc"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("分出句子", res.json()["detail"])
        self.assertEqual(list(main.DATA.iterdir()), [])

    def test_captions_skip_asr(self):
        client = TestClient(main.app)

        def fake_ingest(_url, folder):
            return _ok_media(folder, CAPTION)

        with patch.object(main, "ingest_url", side_effect=fake_ingest), patch.object(
            main, "fetch_media_title", return_value="titled"
        ), patch.object(main, "transcribe_sentences") as asr:
            res = client.post("/api/prepare-url", json={"url": "https://www.youtube.com/watch?v=short"})
        self.assertEqual(res.status_code, 200, res.text)
        asr.assert_not_called()
        self.assertTrue((main.DATA / res.json()["session_id"] / "sentences.json").is_file())

    def test_no_captions_uses_asr(self):
        client = TestClient(main.app)

        def fake_ingest(_url, folder):
            return _ok_media(folder, None)

        with patch.object(main, "ingest_url", side_effect=fake_ingest), patch.object(
            main, "fetch_media_title", return_value="long"
        ), patch.object(
            main,
            "transcribe_sentences",
            return_value=[{"id": 0, "start": 0, "end": 2, "text": "Hello from asr."}],
        ):
            res = client.post("/api/prepare-url", json={"url": "https://www.youtube.com/watch?v=long"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["sentences"][0]["text"], "Hello from asr.")

    def test_asr_timeout_returns_400(self):
        client = TestClient(main.app)

        def fake_ingest(_url, folder):
            return _ok_media(folder, None)

        def hang(_audio):
            time.sleep(2)
            return [{"id": 0, "start": 0, "end": 1, "text": "too late"}]

        with patch.object(main, "ASR_IMPORT_TIMEOUT_SEC", 0.2), patch.object(
            main, "ingest_url", side_effect=fake_ingest
        ), patch.object(main, "fetch_media_title", return_value="title"), patch.object(
            main, "transcribe_sentences", side_effect=hang
        ):
            res = client.post("/api/prepare-url", json={"url": "https://www.youtube.com/watch?v=abc"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("语音识别", res.json()["detail"])
        self.assertEqual(list(main.DATA.iterdir()), [])

    def test_subtitle_429_ingest_falls_back_to_asr_session(self):
        client = TestClient(main.app)

        def fake_ingest(_url, folder):
            (folder / "import_meta.json").write_text(
                json.dumps({"title": "Rate Limited", "subtitle_status": "rate_limited"}),
                encoding="utf-8",
            )
            return _ok_media(folder, None)

        with patch.object(main, "ingest_url", side_effect=fake_ingest), patch.object(
            main, "fetch_media_title"
        ) as title_fn, patch.object(
            main,
            "transcribe_sentences",
            return_value=[{"id": 0, "start": 0, "end": 2, "text": "Recovered by asr."}],
        ):
            res = client.post("/api/prepare-url", json={"url": "https://www.youtube.com/watch?v=rate"})
        self.assertEqual(res.status_code, 200, res.text)
        title_fn.assert_not_called()
        self.assertEqual(res.json()["sentences"][0]["text"], "Recovered by asr.")
        self.assertEqual(res.json()["title"], "Rate Limited")

    def test_caption_parse_failure_falls_back_to_asr(self):
        client = TestClient(main.app)

        def fake_ingest(_url, folder):
            return _ok_media(folder, "WEBVTT\n\nthis has no timestamp cues")

        with patch.object(main, "ingest_url", side_effect=fake_ingest), patch.object(
            main, "fetch_media_title", return_value="parsed"
        ), patch.object(
            main,
            "transcribe_sentences",
            return_value=[{"id": 0, "start": 0, "end": 2, "text": "Parsed fallback."}],
        ):
            res = client.post("/api/prepare-url", json={"url": "https://www.youtube.com/watch?v=parse"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["sentences"][0]["text"], "Parsed fallback.")

    def test_ytdlp_download_failure_still_fails_import(self):
        client = TestClient(main.app)
        with patch.object(main, "ingest_url", side_effect=RuntimeError("Requested format is not available")):
            res = client.post("/api/prepare-url", json={"url": "https://www.youtube.com/watch?v=fail"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["detail"], ingest.LOCAL_UPLOAD_HINT)


class FfmpegTimeoutTests(unittest.TestCase):
    def test_run_ffmpeg_times_out(self):
        from backend.app import media as media_mod

        with patch("backend.app.media.find_ffmpeg", return_value=sys.executable), patch(
            "backend.app.media.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=1)
        ):
            with self.assertRaises(RuntimeError) as ctx:
                media_mod.run_ffmpeg(["-i", "x"])
        self.assertIn("超时", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
