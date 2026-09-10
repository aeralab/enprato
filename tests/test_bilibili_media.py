from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import db, main, url_import_jobs
from backend.app.bilibili import prepare_bilibili_video
from backend.app.bilibili_media import kick_bilibili_media, media_payload, wait_media, write_media_state
from job_wait import wait_ready
from test_ingest_bilibili import BV_URL, FakeHTTP

ASR_SENTENCE = [{"id": 0, "start": 0, "end": 2, "text": "Hello from asr."}]
CAPTION = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello from asr.\n"


def _fake_ingest(_url, folder: Path, **_kwargs):
    playback = folder / "playback.m4a"
    playback.write_bytes(b"m4a" * 400)
    return playback, playback, CAPTION


def _fake_codec(_src, kind="a"):
    return "h264" if kind == "v" else "aac"


class PrepareBilibiliVideoTests(unittest.TestCase):
    def test_uses_existing_playback_and_does_not_download_audio(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "playback.m4a").write_bytes(b"m4a" * 400)
        fake = FakeHTTP()
        downloaded: list[str] = []

        def capture(urls, dest):
            downloaded.extend(urls)
            dest.write_bytes(b"v" * 800)

        def fake_merge(video, audio, dest):
            self.assertEqual(audio.name, "playback.m4a")
            self.assertTrue(audio.is_file())
            self.assertEqual(video.name, "dash_video.m4s")
            dest.write_bytes(b"merged" * 400)

        with patch("backend.app.bilibili.urllib.request.urlopen", side_effect=fake.urlopen), patch(
            "backend.app.bilibili.download_with_backups", side_effect=capture
        ), patch("backend.app.bilibili.merge_dash", side_effect=fake_merge), patch(
            "backend.app.bilibili.stream_codec", side_effect=_fake_codec
        ):
            result = prepare_bilibili_video(BV_URL, folder)
        self.assertFalse(result["skipped"])
        self.assertTrue((folder / "source.mp4").is_file())
        self.assertFalse((folder / "source.mp4.tmp").exists())
        self.assertFalse((folder / "dash_video.m4s").exists())
        self.assertTrue(any("/v.m4s" in url for url in downloaded))
        self.assertFalse(any("/a.m4s" in url for url in downloaded))

    def test_skips_when_source_mp4_already_complete(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "playback.m4a").write_bytes(b"m4a" * 400)
        (folder / "source.mp4").write_bytes(b"mp4" * 400)
        with patch("backend.app.bilibili.stream_codec", side_effect=_fake_codec), patch(
            "backend.app.bilibili.download_with_backups"
        ) as download:
            result = prepare_bilibili_video(BV_URL, folder)
        self.assertTrue(result["skipped"])
        download.assert_not_called()

    def test_merge_tmp_forces_mp4_muxer(self):
        from backend.app.bilibili import merge_dash

        folder = Path(tempfile.mkdtemp())
        video = folder / "dash_video.m4s"
        audio = folder / "playback.m4a"
        dest = folder / "source.mp4.tmp"
        video.write_bytes(b"v" * 400)
        audio.write_bytes(b"a" * 400)
        dest.write_bytes(b"merged" * 400)

        def fake_run(args, timeout=None):
            self.assertIn("-f", args)
            self.assertEqual(args[args.index("-f") + 1], "mp4")
            self.assertEqual(args[-1], str(dest))

        with patch("backend.app.bilibili.run_ffmpeg", side_effect=fake_run):
            merge_dash(video, audio, dest)


class BilibiliMediaJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old = {
            "db": db.DB_PATH,
            "data": main.DATA,
            "auth": os.environ.get("ENPRATO_REQUIRE_AUTH"),
            "secure": os.environ.get("ENPRATO_COOKIE_SECURE"),
            "asr": os.environ.get("ENPRATO_ASR_BACKEND"),
            "dashscope": os.environ.get("DASHSCOPE_API_KEY"),
        }
        db.DB_PATH = Path(self.tmp.name) / "media.sqlite3"
        main.DATA = Path(self.tmp.name) / "sessions"
        main.DATA.mkdir()
        db.migrate()
        os.environ["ENPRATO_REQUIRE_AUTH"] = "1"
        os.environ["ENPRATO_COOKIE_SECURE"] = "0"
        os.environ["ENPRATO_ASR_BACKEND"] = "whisper"
        os.environ.pop("DASHSCOPE_API_KEY", None)

    def tearDown(self):
        try:
            url_import_jobs.wait_idle(20)
        except Exception:
            pass
        db.DB_PATH, main.DATA = self.old["db"], self.old["data"]
        if self.old["auth"] is None:
            os.environ.pop("ENPRATO_REQUIRE_AUTH", None)
        else:
            os.environ["ENPRATO_REQUIRE_AUTH"] = self.old["auth"]
        if self.old["secure"] is None:
            os.environ.pop("ENPRATO_COOKIE_SECURE", None)
        else:
            os.environ["ENPRATO_COOKIE_SECURE"] = self.old["secure"]
        if self.old["asr"] is None:
            os.environ.pop("ENPRATO_ASR_BACKEND", None)
        else:
            os.environ["ENPRATO_ASR_BACKEND"] = self.old["asr"]
        if self.old.get("dashscope") is None:
            os.environ.pop("DASHSCOPE_API_KEY", None)
        else:
            os.environ["DASHSCOPE_API_KEY"] = self.old["dashscope"]
        self.tmp.cleanup()

    def _client(self, email: str) -> TestClient:
        client = TestClient(main.app)
        client.post("/api/auth/register", json={"email": email, "password": "password123"})
        return client

    def test_ready_does_not_wait_for_video_and_starts_media_after(self):
        client = self._client("media-ready@example.com")
        gate = threading.Event()
        holder: dict = {}

        def fake_prepare(_url, folder):
            job = url_import_jobs.get_job(holder["job_id"])
            holder["status"] = job["status"] if job else None
            holder["source_during"] = (folder / "source.mp4").exists()
            gate.wait(3)
            (folder / "source.mp4").write_bytes(b"mp4" * 400)
            return {"skipped": False, "t_video_download_ms": 5, "t_video_merge_ms": 5, "elapsed_ms": 10}

        with patch.object(main, "ingest_url", new=_fake_ingest), patch(
            "backend.app.ingest.ingest_url", new=_fake_ingest
        ), patch.object(
            main, "transcribe_sentences", return_value=ASR_SENTENCE
        ), patch("backend.app.bilibili_media.prepare_bilibili_video", side_effect=fake_prepare), patch(
            "backend.app.bilibili_media.source_mp4_complete",
            side_effect=lambda folder: (folder / "source.mp4").is_file(),
        ):
            res = client.post("/api/prepare-url", json={"url": BV_URL, "create_new_session": True})
            holder["job_id"] = res.json()["job_id"]
            detail = wait_ready(client, res)
            session_id = detail["session_id"]
            self.assertFalse(detail.get("has_video"))
            self.assertFalse((main.DATA / session_id / "source.mp4").exists())
            self.assertEqual(client.get("/api/auth/me").json()["user"]["trial"]["used"], 0)
            media = client.get("/api/session/" + session_id + "/media")
            self.assertEqual(media.status_code, 200)
            self.assertEqual(media.json()["status"], "preparing")
            self.assertEqual(media.json()["session_id"], session_id)
            gate.set()
            wait_media(session_id, 4)
        self.assertEqual(holder["status"], "ready")
        self.assertFalse(holder["source_during"])
        job = url_import_jobs.get_job(holder["job_id"])
        self.assertEqual(job["status"], "ready")
        self.assertTrue((main.DATA / session_id / "source.mp4").is_file())

    def test_media_failure_keeps_ready_and_quota(self):
        client = self._client("media-fail@example.com")

        def boom(_url, _folder):
            raise RuntimeError("cdn down")

        with patch.object(main, "ingest_url", new=_fake_ingest), patch(
            "backend.app.ingest.ingest_url", new=_fake_ingest
        ), patch.object(
            main, "transcribe_sentences", return_value=ASR_SENTENCE
        ), patch("backend.app.bilibili_media.prepare_bilibili_video", side_effect=boom), patch(
            "backend.app.url_import_jobs.fail_job"
        ) as fail_job:
            res = client.post("/api/prepare-url", json={"url": BV_URL, "create_new_session": True})
            detail = wait_ready(client, res)
            wait_media(detail["session_id"], 4)
        fail_job.assert_not_called()
        job = url_import_jobs.get_job(res.json()["job_id"])
        self.assertEqual(job["status"], "ready")
        self.assertEqual(client.get("/api/auth/me").json()["user"]["trial"]["used"], 0)
        media = client.get("/api/session/" + detail["session_id"] + "/media").json()
        self.assertFalse(media["has_video"])
        self.assertEqual(media["status"], "failed")

    def test_duplicate_does_not_start_second_media_task(self):
        client = self._client("media-dup@example.com")
        calls: list[str] = []
        hold = threading.Event()

        def slow_prepare(_url, folder):
            calls.append("prepare")
            hold.wait(3)
            (folder / "source.mp4").write_bytes(b"mp4" * 400)
            return {"skipped": False, "t_video_download_ms": 1, "t_video_merge_ms": 1, "elapsed_ms": 2}

        with patch.object(main, "ingest_url", new=_fake_ingest), patch(
            "backend.app.ingest.ingest_url", new=_fake_ingest
        ), patch.object(
            main, "transcribe_sentences", return_value=ASR_SENTENCE
        ), patch("backend.app.bilibili_media.prepare_bilibili_video", side_effect=slow_prepare), patch(
            "backend.app.bilibili_media.source_mp4_complete", return_value=False
        ):
            first = client.post("/api/prepare-url", json={"url": BV_URL})
            detail = wait_ready(client, first)
            session_id = detail["session_id"]
            second = client.post("/api/prepare-url", json={"url": BV_URL})
            kick_bilibili_media(
                url=BV_URL,
                folder=main.DATA / session_id,
                session_id=session_id,
                job_id=first.json()["job_id"],
            )
            hold.set()
            wait_media(session_id, 4)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json().get("session_id"), session_id)
        self.assertEqual(calls, ["prepare"])

    def test_media_endpoint_hides_other_users_session(self):
        owner = self._client("media-owner@example.com")
        other = self._client("media-other@example.com")
        with patch.object(main, "ingest_url", new=_fake_ingest), patch(
            "backend.app.ingest.ingest_url", new=_fake_ingest
        ), patch.object(
            main, "transcribe_sentences", return_value=ASR_SENTENCE
        ), patch("backend.app.bilibili_media.kick_bilibili_media"):
            res = owner.post("/api/prepare-url", json={"url": BV_URL, "create_new_session": True})
            detail = wait_ready(owner, res)
        hidden = other.get("/api/session/" + detail["session_id"] + "/media")
        self.assertEqual(hidden.status_code, 404)
        missing = owner.get("/api/session/does-not-exist/media")
        self.assertEqual(missing.status_code, 404)


class MediaPayloadTests(unittest.TestCase):
    def test_payload_uses_marker_without_db(self):
        folder = Path(tempfile.mkdtemp())
        write_media_state(folder, "preparing")
        payload = media_payload(folder, "abc123")
        self.assertEqual(payload["session_id"], "abc123")
        self.assertFalse(payload["has_video"])
        self.assertEqual(payload["status"], "preparing")
        write_media_state(folder, "failed", error_kind="cdn")
        self.assertEqual(media_payload(folder, "abc123")["status"], "failed")


class KickGuardTests(unittest.TestCase):
    def test_kick_returns_without_waiting_prepare(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "playback.m4a").write_bytes(b"m4a" * 400)
        hold = threading.Event()

        def slow(_url, _folder):
            hold.wait(2)
            return {"skipped": False, "t_video_download_ms": 1, "t_video_merge_ms": 1, "elapsed_ms": 2}

        t0 = time.monotonic()
        with patch("backend.app.bilibili_media.prepare_bilibili_video", side_effect=slow), patch(
            "backend.app.bilibili_media.source_mp4_complete", return_value=False
        ):
            kick_bilibili_media(url=BV_URL, folder=folder, session_id="kick-fast", job_id="job-fast")
        self.assertLess(time.monotonic() - t0, 0.8)
        hold.set()
        wait_media("kick-fast", 3)

    def test_kick_skips_when_source_already_complete(self):
        folder = Path(tempfile.mkdtemp())
        with patch("backend.app.bilibili_media.source_mp4_complete", return_value=True), patch(
            "backend.app.bilibili_media.prepare_bilibili_video"
        ) as prepare:
            kick_bilibili_media(url=BV_URL, folder=folder, session_id="kick-skip", job_id="job-skip")
        prepare.assert_not_called()


class VideoTimingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old = {
            "db": db.DB_PATH,
            "data": main.DATA,
            "auth": os.environ.get("ENPRATO_REQUIRE_AUTH"),
            "secure": os.environ.get("ENPRATO_COOKIE_SECURE"),
            "asr": os.environ.get("ENPRATO_ASR_BACKEND"),
            "dashscope": os.environ.get("DASHSCOPE_API_KEY"),
        }
        db.DB_PATH = Path(self.tmp.name) / "timing.sqlite3"
        main.DATA = Path(self.tmp.name) / "sessions"
        main.DATA.mkdir()
        db.migrate()
        os.environ["ENPRATO_REQUIRE_AUTH"] = "1"
        os.environ["ENPRATO_COOKIE_SECURE"] = "0"
        os.environ["ENPRATO_ASR_BACKEND"] = "whisper"
        os.environ.pop("DASHSCOPE_API_KEY", None)

    def tearDown(self):
        try:
            url_import_jobs.wait_idle(20)
        except Exception:
            pass
        db.DB_PATH, main.DATA = self.old["db"], self.old["data"]
        if self.old["auth"] is None:
            os.environ.pop("ENPRATO_REQUIRE_AUTH", None)
        else:
            os.environ["ENPRATO_REQUIRE_AUTH"] = self.old["auth"]
        if self.old["secure"] is None:
            os.environ.pop("ENPRATO_COOKIE_SECURE", None)
        else:
            os.environ["ENPRATO_COOKIE_SECURE"] = self.old["secure"]
        if self.old["asr"] is None:
            os.environ.pop("ENPRATO_ASR_BACKEND", None)
        else:
            os.environ["ENPRATO_ASR_BACKEND"] = self.old["asr"]
        if self.old.get("dashscope") is None:
            os.environ.pop("DASHSCOPE_API_KEY", None)
        else:
            os.environ["DASHSCOPE_API_KEY"] = self.old["dashscope"]
        self.tmp.cleanup()

    def test_video_task_starts_after_session_ready(self):
        client = TestClient(main.app)
        client.post("/api/auth/register", json={"email": "media-timing@example.com", "password": "password123"})
        stages: list[tuple[str, int | None]] = []

        def capture(_host, stage, elapsed_ms=None, **_fields):
            stages.append((stage, elapsed_ms))

        def fake_prepare(_url, folder):
            (folder / "source.mp4").write_bytes(b"mp4" * 400)
            return {"skipped": False, "t_video_download_ms": 3, "t_video_merge_ms": 4, "elapsed_ms": 7}

        with patch.object(main, "ingest_url", new=_fake_ingest), patch(
            "backend.app.ingest.ingest_url", new=_fake_ingest
        ), patch.object(main, "transcribe_sentences", return_value=ASR_SENTENCE), patch(
            "backend.app.bilibili_media.prepare_bilibili_video", side_effect=fake_prepare
        ), patch(
            "backend.app.bilibili_media.source_mp4_complete",
            side_effect=lambda folder: (folder / "source.mp4").is_file(),
        ), patch("backend.app.url_import_jobs.log_url_import_stage", side_effect=capture), patch(
            "backend.app.bilibili_media.log_url_import_stage", side_effect=capture
        ):
            res = client.post("/api/prepare-url", json={"url": BV_URL, "create_new_session": True})
            detail = wait_ready(client, res)
            wait_media(detail["session_id"], 4)
        names = [item[0] for item in stages]
        self.assertIn("T_session_ready", names)
        self.assertIn("T_video_task_start", names)
        ready_ms = next(ms for name, ms in stages if name == "T_session_ready")
        start_ms = next(ms for name, ms in stages if name == "T_video_task_start")
        self.assertIsNotNone(ready_ms)
        self.assertIsNotNone(start_ms)
        self.assertGreaterEqual(start_ms, ready_ms)
