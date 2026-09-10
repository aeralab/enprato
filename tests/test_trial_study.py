from __future__ import annotations

import json
import os
import tempfile
import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import db, main
from backend.app.auth import hash_password
from backend.app.store import write_meta
from job_wait import wait_job, wait_ready

ROOT = Path(__file__).resolve().parents[1]
APP_SRC = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
API_SRC = (ROOT / "frontend" / "src" / "api.ts").read_text(encoding="utf-8")
IPAD_SRC = (ROOT / "backend" / "app" / "ipad_studio.py").read_text(encoding="utf-8")
CAPTION = "WEBVTT\n\n00:00:00.000 --> 00:00:04.000\nA newly imported sentence, with a natural split, for testing.\n"


def _isolate_env():
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    old = {
        "db": db.DB_PATH,
        "data": main.DATA,
        "auth": os.environ.get("ENPRATO_REQUIRE_AUTH"),
        "secure": os.environ.get("ENPRATO_COOKIE_SECURE"),
        "asr": os.environ.get("ENPRATO_ASR_BACKEND"),
    }
    db.DB_PATH = Path(tmp.name) / "trial-study.sqlite3"
    main.DATA = Path(tmp.name) / "sessions"
    main.DATA.mkdir()
    db.migrate()
    os.environ["ENPRATO_REQUIRE_AUTH"] = "1"
    os.environ["ENPRATO_COOKIE_SECURE"] = "0"
    os.environ["ENPRATO_ASR_BACKEND"] = "whisper"
    return tmp, old


def _restore_env(tmp, old):
    db.DB_PATH, main.DATA = old["db"], old["data"]
    for key, env_name in (
        ("auth", "ENPRATO_REQUIRE_AUTH"),
        ("secure", "ENPRATO_COOKIE_SECURE"),
        ("asr", "ENPRATO_ASR_BACKEND"),
    ):
        if old[key] is None:
            os.environ.pop(env_name, None)
        else:
            os.environ[env_name] = old[key]
    tmp.cleanup()


def _ok_ingest(_url, folder: Path, **_kwargs):
    audio = folder / "audio.wav"
    audio.write_bytes(b"RIFF")
    (folder / "source.mp4").write_bytes(b"mp4")
    return folder / "source.mp4", audio, CAPTION


class TrialStudyTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.old = _isolate_env()
        self.client = TestClient(main.app)
        self.client.post("/api/auth/register", json={"email": "study@example.com", "password": "password123"})
        self.user_id = self.client.get("/api/auth/me").json()["user"]["id"]

    def tearDown(self):
        _restore_env(self.tmp, self.old)

    def _used(self) -> int:
        return int(self.client.get("/api/auth/me").json()["user"]["trial"]["used"])

    def _make_session(self, suffix: str = "") -> str:
        sid = (suffix or uuid.uuid4().hex)[:12]
        folder = main.DATA / sid
        folder.mkdir(parents=True, exist_ok=True)
        sentences = [{"text": "Hello there friend.", "start": 0.0, "end": 2.0}]
        (folder / "sentences.json").write_text(json.dumps(sentences), encoding="utf-8")
        write_meta(folder, title=sid, source_kind="file", phase="listen", index=0, drafts={})
        (folder / "audio.wav").write_bytes(b"RIFF")
        (folder / "source.mp4").write_bytes(b"mp4")
        db.register_learning_session(sid, self.user_id)
        return sid

    def _beat(self, sid: str, seconds: int):
        return self.client.post(f"/api/session/{sid}/study-heartbeat", json={"active_seconds": seconds})

    def _add(self, sid: str, total: int):
        left = total
        last = None
        while left > 0:
            chunk = min(20, left)
            last = self._beat(sid, chunk)
            self.assertEqual(last.status_code, 200, last.text)
            left -= chunk
        return last

    def _import(self, url: str, create_new: bool = True):
        with patch.object(main, "ingest_url", side_effect=_ok_ingest), patch.object(
            main, "fetch_media_title", return_value="title"
        ):
            res = self.client.post("/api/prepare-url", json={"url": url, "create_new_session": create_new})
            self.assertEqual(res.status_code, 200, res.text)
            return wait_ready(self.client, res)

    def test_import_counts_stay_zero(self):
        self._import("https://example.com/one.mp4")
        self.assertEqual(self._used(), 0)
        for i in range(4):
            self._import(f"https://example.com/v{i}.mp4")
        self.assertEqual(self._used(), 0)
        for i in range(15):
            self._import(f"https://example.com/more{i}.mp4")
        self.assertEqual(self._used(), 0)

    def test_import_failure_and_duplicate_keep_quota(self):
        with patch.object(main, "ingest_url", side_effect=RuntimeError("download failed")):
            res = self.client.post("/api/prepare-url", json={"url": "https://example.com/bad.mp4"})
            job, _ = wait_job(self.client, res.json()["job_id"])
        self.assertEqual(job["status"], "failed")
        self.assertEqual(self._used(), 0)
        first = self._import("https://example.com/dup.mp4", create_new=False)
        again = self.client.post("/api/prepare-url", json={"url": "https://example.com/dup.mp4"})
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json().get("session_id"), first["session_id"])
        self.assertEqual(self._used(), 0)

    def test_active_seconds_threshold_and_idempotent_consume(self):
        a = self._make_session("sessaaaaaaaa")
        self._add(a, 299)
        self.assertEqual(self._used(), 0)
        self.assertFalse(self._beat(a, 0).json()["trial_consumed"])
        self._add(a, 1)
        self.assertEqual(self._used(), 1)
        body = self._beat(a, 0).json()
        self.assertTrue(body["trial_consumed"])
        self._add(a, 200)
        self.assertEqual(self._used(), 1)
        self._add(a, 20)
        self._add(a, 20)
        self.assertEqual(self._used(), 1)

    def test_second_session_increments_and_five_cap(self):
        ids = [self._make_session(f"sess{i:08d}xx") for i in range(5)]
        for sid in ids:
            self._add(sid, 300)
        self.assertEqual(self._used(), 5)
        sixth = self._make_session("sesssixthxxx")
        blocked = self._beat(sixth, 1)
        self.assertEqual(blocked.status_code, 402)
        self.assertIn("免费深度学习的 5 个素材已用完", blocked.json()["detail"])
        self.assertEqual(self._used(), 5)

    def test_consumed_sessions_remain_open_after_cap(self):
        ids = [self._make_session(f"done{i:08d}xx") for i in range(5)]
        for sid in ids:
            self._add(sid, 300)
        sixth = self._make_session("opensixthxxx")
        a = ids[0]
        patch_a = self.client.patch(f"/api/session/{a}", json={"drafts": {"0": "hello there"}, "index": 0})
        self.assertEqual(patch_a.status_code, 200, patch_a.text)
        with patch.object(main, "convert_to_wav", side_effect=lambda raw, wav: Path(wav).write_bytes(b"RIFF")), patch.object(
            main, "probe_duration", return_value=1.0
        ), patch.object(
            main,
            "transcribe_speech_detailed",
            return_value={"text": "hello", "segment_count": 1, "last_end": 1.0, "retried": False, "retry_reason": ""},
        ):
            stt_ok = self.client.post(
                "/api/stt",
                files={"audio": ("clip.webm", b"x" * 400, "audio/webm")},
                data={"session_id": a, "context": "", "target": "Hello"},
            )
        self.assertEqual(stt_ok.status_code, 200, stt_ok.text)
        with patch.object(main, "convert_to_wav", side_effect=lambda raw, wav: Path(wav).write_bytes(b"RIFF")), patch.object(
            main, "score_shadowing", return_value={"score": 88, "words": []}
        ):
            score_ok = self.client.post("/api/score", files={"audio": ("clip.webm", b"x" * 400, "audio/webm")}, data={"session_id": a})
        self.assertEqual(score_ok.status_code, 200, score_ok.text)
        opened = self.client.get(f"/api/session/{sixth}")
        self.assertEqual(opened.status_code, 200)
        play = self.client.get(f"/api/session/{sixth}/video")
        self.assertEqual(play.status_code, 200)
        self.assertEqual(self.client.patch(f"/api/session/{sixth}", json={"index": 0, "phase": "listen"}).status_code, 200)
        deep = self.client.patch(f"/api/session/{sixth}", json={"drafts": {"0": "typed"}, "index": 0})
        self.assertEqual(deep.status_code, 402)
        stt6 = self.client.post(
            "/api/stt",
            files={"audio": ("clip.webm", b"x" * 400, "audio/webm")},
            data={"session_id": sixth, "context": "", "target": "Hello"},
        )
        self.assertEqual(stt6.status_code, 402)
        score6 = self.client.post("/api/score", files={"audio": ("clip.webm", b"x" * 400, "audio/webm")}, data={"session_id": sixth})
        self.assertEqual(score6.status_code, 402)
        self.assertEqual(self._beat(sixth, 5).status_code, 402)

    def test_heartbeat_binding_and_validation(self):
        a = self._make_session("bindaaaaaaa")
        b = self._make_session("bindbbbbbbb")
        self.assertEqual(self._beat(a, 10).json()["active_study_seconds"], 10)
        self.assertEqual(self._beat(b, 7).json()["active_study_seconds"], 7)
        self.assertEqual(self._beat(a, 2).json()["active_study_seconds"], 12)
        self.assertEqual(self._beat(b, 1).json()["active_study_seconds"], 8)
        self.assertEqual(self._beat(a, 21).status_code, 400)
        self.assertEqual(self._beat(a, -1).status_code, 400)
        other = TestClient(main.app)
        other.post("/api/auth/register", json={"email": "other@example.com", "password": "password123"})
        hidden = other.post(f"/api/session/{a}/study-heartbeat", json={"active_seconds": 5})
        self.assertEqual(hidden.status_code, 404)

    def test_migration_idempotent_and_non_negative(self):
        user = db.create_user("migrate@example.com", hash_password("password123"))
        self.assertTrue(db.consume_trial(user["id"], "prepare:oldsession"))
        self.assertEqual(db.trial_status(user["id"])["used"], 1)
        first = db.refund_historical_prepare_trials()
        self.assertGreaterEqual(first["refunded"], 1)
        self.assertEqual(db.trial_status(user["id"])["used"], 0)
        second = db.refund_historical_prepare_trials()
        self.assertEqual(second["refunded"], 0)
        self.assertEqual(db.trial_status(user["id"])["used"], 0)
        self.assertTrue(db.consume_trial(user["id"], "study:keepme"))
        self.assertEqual(db.trial_status(user["id"])["used"], 1)
        db.refund_historical_prepare_trials()
        self.assertEqual(db.trial_status(user["id"])["used"], 1)
        db.refund_trial(user["id"], "study:keepme")
        db.refund_trial(user["id"], "study:keepme")
        self.assertGreaterEqual(db.trial_status(user["id"])["used"], 0)
        db.refund_historical_prepare_trials()
        self.assertEqual(db.trial_status(user["id"])["used"], 0)

    def test_paid_member_unrestricted(self):
        ids = [self._make_session(f"mem{i:09d}xx") for i in range(5)]
        for sid in ids:
            self._add(sid, 300)
        sixth = self._make_session("membsixthxxx")
        conn = db.connect()
        try:
            plan = conn.execute("SELECT id FROM plans WHERE code='monthly_30d'").fetchone()
            now = db.iso()
            later = db.iso(db.utc_now() + timedelta(days=30))
            conn.execute(
                "INSERT INTO memberships(user_id,plan_id,starts_at,expires_at,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (self.user_id, plan[0], now, later, "active", now, now),
            )
        finally:
            conn.close()
        beat = self._beat(sixth, 20)
        self.assertEqual(beat.status_code, 200, beat.text)
        draft = self.client.patch(f"/api/session/{sixth}", json={"drafts": {"0": "member text"}, "index": 0})
        self.assertEqual(draft.status_code, 200, draft.text)


class TrialStudyContractTests(unittest.TestCase):
    def test_frontend_copy_and_active_window(self):
        self.assertIn("STUDY_ACTIVE_WINDOW_MS = 20_000", APP_SRC)
        self.assertNotIn("免费导入次数", APP_SRC)
        self.assertNotIn("免费学习素材", APP_SRC)
        self.assertIn("已学习素材", APP_SRC)
        self.assertIn("可免费深度学习 5 个素材", APP_SRC)
        self.assertIn("postStudyHeartbeat", APP_SRC)
        self.assertIn("/study-heartbeat", API_SRC)
        self.assertIn("visibilityState", APP_SRC)
        self.assertIn("!video.paused", APP_SRC)
        self.assertIn("STUDY_ACTIVE_WINDOW_MS = 20000", IPAD_SRC)
        self.assertIn("/study-heartbeat", IPAD_SRC)
        self.assertIn("visibilityState", IPAD_SRC)
        self.assertNotRegex(APP_SRC, r"STUDY_ACTIVE_WINDOW_MS = 60")
        self.assertNotRegex(IPAD_SRC, r"STUDY_ACTIVE_WINDOW_MS = 60")
