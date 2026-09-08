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

    def test_format_unavailable_falls_back(self):
        calls = []

        def fake_run(cmd, timeout=None):
            calls.append(cmd)
            if len(calls) < 2:
                raise RuntimeError("Requested format is not available")

        with patch.object(ingest, "_run", side_effect=fake_run):
            ingest._ytdlp_fetch(["yt-dlp"], "https://www.youtube.com/watch?v=abc")
        self.assertEqual(len(calls), 2)


class FriendlyErrorTests(unittest.TestCase):
    def test_bilibili_412_asks_for_local_upload(self):
        msg = ingest._friendly_ytdlp_error(
            "https://www.bilibili.com/video/BV1CMjq6nEu1/",
            "ERROR: [BiliBili] Unable to download webpage: HTTP Error 412: Precondition Failed",
        )
        self.assertIn("保存到本地", msg)
        self.assertIn("上传", msg)
        self.assertNotIn("ENPRATO_COOKIES", msg)

    def test_timeout_asks_for_local_upload(self):
        msg = ingest._friendly_ytdlp_error("https://www.youtube.com/watch?v=abc", "下载超时，已停止等待")
        self.assertIn("保存到本地", msg)


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
        self.assertEqual(list(main.DATA.iterdir()), [])

    def test_download_timeout_returns_400(self):
        client = TestClient(main.app)
        err = ingest._friendly_ytdlp_error("https://www.youtube.com/watch?v=abc", "下载超时，已停止等待")
        with patch.object(main, "ingest_url", side_effect=RuntimeError(err)):
            res = client.post("/api/prepare-url", json={"url": "https://www.youtube.com/watch?v=abc"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("保存到本地", res.json()["detail"])
        self.assertEqual(list(main.DATA.iterdir()), [])

    def test_bilibili_412_returns_local_upload_fallback(self):
        client = TestClient(main.app)
        url = "https://www.bilibili.com/video/BV1CMjq6nEu1/"
        err = ingest._friendly_ytdlp_error(url, "HTTP Error 412: Precondition Failed")
        with patch.object(main, "ingest_url", side_effect=RuntimeError(err)):
            res = client.post("/api/prepare-url", json={"url": url})
        self.assertEqual(res.status_code, 400)
        self.assertIn("保存到本地", res.json()["detail"])
        self.assertIn("上传", res.json()["detail"])

    def test_ffmpeg_failure_returns_400(self):
        client = TestClient(main.app)
        with patch.object(main, "ingest_url", side_effect=RuntimeError("ffmpeg 执行失败")):
            res = client.post("/api/prepare-url", json={"url": "https://www.youtube.com/watch?v=abc"})
        self.assertEqual(res.status_code, 400)
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
