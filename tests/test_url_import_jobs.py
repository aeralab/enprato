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
from job_wait import wait_job, wait_ready

CAPTION = "WEBVTT\n\n00:00:00.000 --> 00:00:04.000\nA newly imported sentence, with a natural split, for testing.\n"
ASR_SENTENCE = [{"id": 0, "start": 0, "end": 2, "text": "Hello from asr."}]


def _ok_media(folder: Path, captions: str | None):
    audio = folder / "audio.wav"
    audio.write_bytes(b"RIFF")
    (folder / "source.mp4").write_bytes(b"mp4")
    return folder / "source.mp4", audio, captions


class AsyncUrlImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old = {
            "db": db.DB_PATH,
            "data": main.DATA,
            "auth": os.environ.get("ENPRATO_REQUIRE_AUTH"),
            "secure": os.environ.get("ENPRATO_COOKIE_SECURE"),
        }
        db.DB_PATH = Path(self.tmp.name) / "jobs.sqlite3"
        main.DATA = Path(self.tmp.name) / "sessions"
        main.DATA.mkdir()
        db.migrate()
        os.environ["ENPRATO_REQUIRE_AUTH"] = "1"
        os.environ["ENPRATO_COOKIE_SECURE"] = "0"

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
        self.tmp.cleanup()

    def _client(self, email: str) -> TestClient:
        client = TestClient(main.app)
        client.post("/api/auth/register", json={"email": email, "password": "password123"})
        return client

    def test_post_returns_job_without_waiting_asr(self):
        client = self._client("fast@example.com")
        release = threading.Event()

        def hang(_audio):
            release.wait(3)
            return ASR_SENTENCE

        def fake_ingest(_url, folder, **_kwargs):
            return _ok_media(folder, None)

        with patch.object(main, "ingest_url", side_effect=fake_ingest), patch.object(
            main, "transcribe_sentences", side_effect=hang
        ):
            t0 = time.monotonic()
            res = client.post("/api/prepare-url", json={"url": "https://www.youtube.com/watch?v=slow"})
            elapsed = time.monotonic() - t0
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(res.json()["status"], "processing")
            self.assertTrue(res.json()["job_id"])
            self.assertLess(elapsed, 0.4)
            release.set()
            detail = wait_ready(client, res)
        self.assertEqual(detail["sentences"][0]["text"], "Hello from asr.")

    def test_job_reaches_ready_and_session_opens(self):
        client = self._client("ready@example.com")

        def fake_ingest(_url, folder, **_kwargs):
            time.sleep(0.08)
            return _ok_media(folder, None)

        def slow_asr(_audio):
            time.sleep(0.08)
            return ASR_SENTENCE

        with patch.object(main, "ingest_url", side_effect=fake_ingest), patch.object(
            main, "transcribe_sentences", side_effect=slow_asr
        ):
            res = client.post("/api/prepare-url", json={"url": "https://www.youtube.com/watch?v=ok"})
            job, stages = wait_job(client, res.json()["job_id"])
            detail = client.get("/api/session/" + job["session_id"])
        self.assertEqual(job["status"], "ready")
        self.assertEqual(detail.status_code, 200)
        self.assertTrue(detail.json()["sentences"])
        self.assertIn("transcribing", stages)
        self.assertIn("ready", stages)

    def test_failed_job_refunds_quota(self):
        client = self._client("failjob@example.com")
        with patch.object(main, "ingest_url", side_effect=RuntimeError("yt-dlp boom")):
            res = client.post("/api/prepare-url", json={"url": "https://www.youtube.com/watch?v=bad"})
            job, _ = wait_job(client, res.json()["job_id"])
        self.assertEqual(job["status"], "failed")
        self.assertTrue(job.get("error_kind"))
        self.assertNotIn("yt-dlp", job["message"])
        self.assertEqual(client.get("/api/auth/me").json()["user"]["trial"]["used"], 0)

    def test_user_cannot_read_other_job(self):
        a = self._client("owner-a@example.com")
        b = self._client("owner-b@example.com")
        hold = threading.Event()

        def hang(_audio):
            hold.wait(3)
            return ASR_SENTENCE

        def fake_ingest(_url, folder, **_kwargs):
            return _ok_media(folder, None)

        with patch.object(main, "ingest_url", side_effect=fake_ingest), patch.object(
            main, "transcribe_sentences", side_effect=hang
        ):
            res = a.post("/api/prepare-url", json={"url": "https://www.youtube.com/watch?v=priv"})
            job_id = res.json()["job_id"]
            denied = b.get("/api/import-status/" + job_id)
            self.assertEqual(denied.status_code, 404)
            hold.set()
            wait_job(a, job_id)

    def test_second_job_queued_while_asr_runs(self):
        client = self._client("queue@example.com")
        started = threading.Event()
        release = threading.Event()

        def slow_asr(_audio):
            started.set()
            self.assertTrue(release.wait(5))
            return ASR_SENTENCE

        def fake_ingest(_url, folder, **_kwargs):
            return _ok_media(folder, None)

        with patch.object(main, "ingest_url", side_effect=fake_ingest), patch.object(
            main, "transcribe_sentences", side_effect=slow_asr
        ):
            first = client.post("/api/prepare-url", json={"url": "https://www.youtube.com/watch?v=one"})
            self.assertTrue(started.wait(3))
            second = client.post("/api/prepare-url", json={"url": "https://www.youtube.com/watch?v=two"})
            st = client.get("/api/import-status/" + second.json()["job_id"]).json()
            self.assertEqual(st["status"], "queued")
            self.assertEqual(st["stage"], "queued")
            self.assertIn("排队", st["message"])
            release.set()
            wait_job(client, first.json()["job_id"])
            wait_job(client, second.json()["job_id"])

    def test_polling_does_not_create_jobs(self):
        client = self._client("poll@example.com")

        def fake_ingest(_url, folder, **_kwargs):
            return _ok_media(folder, CAPTION)

        with patch.object(main, "ingest_url", side_effect=fake_ingest):
            res = client.post("/api/prepare-url", json={"url": "https://www.youtube.com/watch?v=poll"})
            job_id = res.json()["job_id"]
            for _ in range(5):
                self.assertEqual(client.get("/api/import-status/" + job_id).status_code, 200)
            wait_job(client, job_id)
        conn = db.connect()
        try:
            n = conn.execute("SELECT COUNT(*) FROM import_jobs").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n, 1)

    def test_job_survives_client_refresh(self):
        client = self._client("refresh@example.com")

        def fake_ingest(_url, folder, **_kwargs):
            return _ok_media(folder, CAPTION)

        with patch.object(main, "ingest_url", side_effect=fake_ingest):
            res = client.post("/api/prepare-url", json={"url": "https://www.youtube.com/watch?v=keep"})
            job_id = res.json()["job_id"]
            again = client.get("/api/import-jobs/active")
            self.assertEqual(again.status_code, 200)
            self.assertEqual(again.json()["job"]["job_id"], job_id)
            wait_job(client, job_id)
            self.assertIsNone(client.get("/api/import-jobs/active").json()["job"])

    def test_duplicate_post_reuses_job(self):
        client = self._client("dup@example.com")
        hold = threading.Event()

        def hang(_audio):
            hold.wait(3)
            return ASR_SENTENCE

        def fake_ingest(_url, folder, **_kwargs):
            return _ok_media(folder, None)

        url = "https://www.youtube.com/watch?v=same"
        with patch.object(main, "ingest_url", side_effect=fake_ingest), patch.object(
            main, "transcribe_sentences", side_effect=hang
        ):
            a = client.post("/api/prepare-url", json={"url": url})
            b = client.post("/api/prepare-url", json={"url": url})
            self.assertEqual(a.json()["job_id"], b.json()["job_id"])
            hold.set()
            wait_job(client, a.json()["job_id"])
        conn = db.connect()
        try:
            n = conn.execute("SELECT COUNT(*) FROM import_jobs").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n, 1)

    def test_stale_jobs_marked_failed_on_recover(self):
        client = self._client("stale@example.com")
        user_id = client.get("/api/auth/me").json()["user"]["id"]
        session_id = "stalesession1"
        db.register_learning_session(session_id, user_id)
        (main.DATA / session_id).mkdir()
        db.consume_trial(user_id, "prepare:" + session_id)
        now = db.iso()
        conn = db.connect()
        try:
            conn.execute(
                "INSERT INTO import_jobs(job_id,user_id,session_id,source_url,url_key,status,stage,error_kind,error_message,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "stalejob1234567",
                    user_id,
                    session_id,
                    "https://www.youtube.com/watch?v=stale",
                    "youtube:stale",
                    "processing",
                    "transcribing",
                    None,
                    None,
                    now,
                    now,
                ),
            )
        finally:
            conn.close()
        n = url_import_jobs.recover_stale_jobs()
        self.assertGreaterEqual(n, 1)
        stored = url_import_jobs.get_job("stalejob1234567")
        self.assertEqual(stored["status"], "failed")
        self.assertEqual(stored["error_kind"], "interrupted")
        self.assertEqual(client.get("/api/auth/me").json()["user"]["trial"]["used"], 0)
        self.assertFalse((main.DATA / session_id).exists())

    def test_bilibili_url_uses_ingest_url(self):
        client = self._client("bili@example.com")
        seen: list[str] = []

        def fake_ingest(url, folder, **_kwargs):
            seen.append(url)
            return _ok_media(folder, CAPTION)

        with patch.object(main, "ingest_url", side_effect=fake_ingest):
            res = client.post("/api/prepare-url", json={"url": "https://www.bilibili.com/video/BV1CMjq6nEu1/"})
            detail = wait_ready(client, res)
        self.assertTrue(seen)
        self.assertIn("BV1CMjq6nEu1", seen[0])
        self.assertTrue(detail["sentences"])

    def test_local_mp4_prepare_stays_synchronous(self):
        client = self._client("file@example.com")

        def fake_extract(_media, audio):
            Path(audio).write_bytes(b"RIFF")

        with patch.object(main, "extract_wav", side_effect=fake_extract), patch.object(
            main, "ensure_playback_audio"
        ), patch.object(main, "transcribe_sentences", return_value=ASR_SENTENCE):
            res = client.post("/api/prepare", files={"video": ("clip.mp4", b"mp4bytes", "video/mp4")})
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertNotIn("job_id", body)
        self.assertEqual(body["sentences"][0]["text"], "Hello from asr.")
