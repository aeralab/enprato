import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import db, main, url_import_jobs
from backend.app.auth import cookie_secure, hash_password, verify_password
from backend.app.license import checkout_license
from job_wait import wait_job, wait_ready


def _isolate_env():
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    old = {
        "db": db.DB_PATH,
        "data": main.DATA,
        "auth": os.environ.get("ENPRATO_REQUIRE_AUTH"),
        "secure": os.environ.get("ENPRATO_COOKIE_SECURE"),
        "mock": os.environ.get("ENPRATO_ALLOW_MOCK_PAY"),
        "asr": os.environ.get("ENPRATO_ASR_BACKEND"),
    }
    db.DB_PATH = Path(tmp.name) / "auth.sqlite3"
    main.DATA = Path(tmp.name) / "sessions"
    main.DATA.mkdir()
    db.migrate()
    os.environ["ENPRATO_ASR_BACKEND"] = "whisper"
    return tmp, old


def _restore_env(tmp, old):
    try:
        url_import_jobs.wait_idle(20)
    except Exception:
        pass
    db.DB_PATH, main.DATA = old["db"], old["data"]
    for key, env_name in (
        ("auth", "ENPRATO_REQUIRE_AUTH"),
        ("secure", "ENPRATO_COOKIE_SECURE"),
        ("mock", "ENPRATO_ALLOW_MOCK_PAY"),
        ("asr", "ENPRATO_ASR_BACKEND"),
    ):
        if old[key] is None:
            os.environ.pop(env_name, None)
        else:
            os.environ[env_name] = old[key]
    tmp.cleanup()


def _cookie_header(response) -> str:
    value = response.headers.get("set-cookie") or ""
    extra = getattr(response.headers, "getlist", None)
    if callable(extra):
        value = "; ".join(extra("set-cookie") or [value])
    return value


class AuthCookieTests(unittest.TestCase):
    def test_verify_password_rejects_missing_hash(self):
        self.assertFalse(verify_password("whatever", None))  # type: ignore[arg-type]
        self.assertFalse(verify_password("whatever", ""))

    def test_cookie_secure_follows_env_and_auth_required(self):
        old_auth = os.environ.get("ENPRATO_REQUIRE_AUTH")
        old_secure = os.environ.get("ENPRATO_COOKIE_SECURE")
        try:
            os.environ.pop("ENPRATO_REQUIRE_AUTH", None)
            os.environ["ENPRATO_COOKIE_SECURE"] = "0"
            self.assertFalse(cookie_secure())
            os.environ["ENPRATO_COOKIE_SECURE"] = "1"
            self.assertTrue(cookie_secure())
            os.environ.pop("ENPRATO_COOKIE_SECURE", None)
            os.environ["ENPRATO_REQUIRE_AUTH"] = "1"
            self.assertFalse(cookie_secure())
            os.environ["ENPRATO_COOKIE_SECURE"] = "1"
            self.assertTrue(cookie_secure())
            os.environ["ENPRATO_COOKIE_SECURE"] = "0"
            os.environ["ENPRATO_REQUIRE_AUTH"] = "1"
            self.assertFalse(cookie_secure())
            dummy = type("Req", (), {"headers": {"x-forwarded-proto": "https"}, "url": type("U", (), {"scheme": "http"})()})()
            os.environ.pop("ENPRATO_COOKIE_SECURE", None)
            self.assertTrue(cookie_secure(dummy))
        finally:
            if old_auth is None:
                os.environ.pop("ENPRATO_REQUIRE_AUTH", None)
            else:
                os.environ["ENPRATO_REQUIRE_AUTH"] = old_auth
            if old_secure is None:
                os.environ.pop("ENPRATO_COOKIE_SECURE", None)
            else:
                os.environ["ENPRATO_COOKIE_SECURE"] = old_secure


class AuthFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.old = _isolate_env()
        os.environ["ENPRATO_REQUIRE_AUTH"] = "1"
        os.environ["ENPRATO_COOKIE_SECURE"] = "0"

    def tearDown(self):
        _restore_env(self.tmp, self.old)

    def test_register_sets_httponly_cookie_and_me(self):
        client = TestClient(main.app)
        res = client.post("/api/auth/register", json={"email": "a@example.com", "password": "password123"})
        self.assertEqual(res.status_code, 200)
        cookie = _cookie_header(res).lower()
        self.assertIn("enprato_session=", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        me = client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["user"]["email"], "a@example.com")
        self.assertEqual(me.json()["user"]["trial"]["remaining"], 5)

    def test_secure_cookie_flag_when_enabled(self):
        os.environ["ENPRATO_COOKIE_SECURE"] = "1"
        client = TestClient(main.app)
        res = client.post("/api/auth/register", json={"email": "secure@example.com", "password": "password123"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("secure", _cookie_header(res).lower())

    def test_duplicate_email_returns_conflict(self):
        client = TestClient(main.app)
        payload = {"email": "dup@example.com", "password": "password123"}
        self.assertEqual(client.post("/api/auth/register", json=payload).status_code, 200)
        again = client.post("/api/auth/register", json=payload)
        self.assertEqual(again.status_code, 409)
        self.assertIn("邮箱已注册", str(again.json()["detail"]))

    def test_wrong_password_is_generic_401(self):
        client = TestClient(main.app)
        client.post("/api/auth/register", json={"email": "pw@example.com", "password": "password123"})
        client.post("/api/auth/logout")
        bad = client.post("/api/auth/login", json={"email": "pw@example.com", "password": "wrongpass"})
        self.assertEqual(bad.status_code, 401)
        detail = str(bad.json()["detail"])
        self.assertEqual(detail, "邮箱或密码错误")
        self.assertNotIn("hash", detail.lower())
        self.assertNotIn("password_hash", detail)
        missing = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "password123"})
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.json()["detail"], "邮箱或密码错误")

    def test_logout_then_me_is_401(self):
        client = TestClient(main.app)
        client.post("/api/auth/register", json={"email": "out@example.com", "password": "password123"})
        self.assertEqual(client.post("/api/auth/logout").status_code, 200)
        self.assertEqual(client.get("/api/auth/me").status_code, 401)

    def test_same_email_login_after_logout(self):
        client = TestClient(main.app)
        payload = {"email": "back@example.com", "password": "password123"}
        self.assertEqual(client.post("/api/auth/register", json=payload).status_code, 200)
        self.assertEqual(client.post("/api/auth/logout").status_code, 200)
        login = client.post("/api/auth/login", json=payload)
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["email"], "back@example.com")
        self.assertNotIn("password", login.json())
        self.assertNotIn("password_hash", login.json())
        me = client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["user"]["email"], "back@example.com")

    def test_invalid_email_and_short_password_rejected(self):
        client = TestClient(main.app)
        bad_email = client.post("/api/auth/register", json={"email": "not-an-email", "password": "password123"})
        self.assertEqual(bad_email.status_code, 400)
        short = client.post("/api/auth/register", json={"email": "ok@example.com", "password": "short"})
        self.assertEqual(short.status_code, 400)

    def test_register_conflict_then_login_matches_email_gate(self):
        client = TestClient(main.app)
        payload = {"email": "combo@example.com", "password": "password123"}
        self.assertEqual(client.post("/api/auth/register", json=payload).status_code, 200)
        client.post("/api/auth/logout")
        conflict = client.post("/api/auth/register", json=payload)
        self.assertEqual(conflict.status_code, 409)
        login = client.post("/api/auth/login", json=payload)
        self.assertEqual(login.status_code, 200)
        self.assertEqual(client.get("/api/auth/me").json()["user"]["email"], "combo@example.com")

    def test_expired_session_is_401(self):
        client = TestClient(main.app)
        client.post("/api/auth/register", json={"email": "exp@example.com", "password": "password123"})
        raw = client.cookies.get("enprato_session")
        self.assertTrue(raw)
        conn = db.connect()
        try:
            conn.execute("UPDATE auth_sessions SET expires_at='2000-01-01T00:00:00Z'")
        finally:
            conn.close()
        self.assertEqual(client.get("/api/auth/me").status_code, 401)


class AuthRequiredApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.old = _isolate_env()
        os.environ["ENPRATO_REQUIRE_AUTH"] = "1"
        os.environ["ENPRATO_COOKIE_SECURE"] = "0"

    def tearDown(self):
        _restore_env(self.tmp, self.old)

    def test_anonymous_private_apis_are_401(self):
        client = TestClient(main.app)
        self.assertEqual(client.get("/api/sessions").status_code, 401)
        self.assertEqual(client.post("/api/prepare-url", json={"url": "https://example.com/video.mp4"}).status_code, 401)
        self.assertEqual(client.get("/api/import-status/x").status_code, 401)
        self.assertEqual(client.get("/api/import-jobs/active").status_code, 401)
        self.assertEqual(client.get("/api/progress").status_code, 401)
        self.assertEqual(client.get("/api/license").status_code, 401)
        self.assertEqual(client.post("/api/license/activate", json={"key": "ENP-x.y"}).status_code, 401)
        self.assertEqual(client.post("/api/license/checkout", json={"plan": "monthly"}).status_code, 401)

    def test_logged_in_cannot_activate_global_license(self):
        client = TestClient(main.app)
        client.post("/api/auth/register", json={"email": "lic@example.com", "password": "password123"})
        res = client.post("/api/license/activate", json={"key": "ENP-x.y"})
        self.assertIn(res.status_code, (403, 400))
        checkout = client.post("/api/license/checkout", json={"plan": "monthly"})
        self.assertIn(checkout.status_code, (403, 400))


class UserIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.old = _isolate_env()
        os.environ["ENPRATO_REQUIRE_AUTH"] = "1"
        os.environ["ENPRATO_COOKIE_SECURE"] = "0"
        self.a = TestClient(main.app)
        self.b = TestClient(main.app)
        self.a.post("/api/auth/register", json={"email": "owner-a@example.com", "password": "password123"})
        self.b.post("/api/auth/register", json={"email": "owner-b@example.com", "password": "password123"})

    def tearDown(self):
        _restore_env(self.tmp, self.old)

    def _owned_session(self, client: TestClient, session_id: str, user_id: str) -> None:
        db.register_learning_session(session_id, user_id)
        folder = main.DATA / session_id
        folder.mkdir()
        (folder / "sentences.json").write_text(json.dumps([{"id": 0, "start": 0, "end": 1, "text": "hello world today"}]), encoding="utf-8")
        (folder / "meta.json").write_text(json.dumps({"title": "A lesson", "drafts": {}, "index": 0}), encoding="utf-8")
        (folder / "source.mp4").write_bytes(b"not-video")
        (folder / "audio.wav").write_bytes(b"RIFF")

    def test_b_cannot_see_or_touch_a_session(self):
        a_id = self.a.get("/api/auth/me").json()["user"]["id"]
        sid = "session-of-a"
        self._owned_session(self.a, sid, a_id)
        mine = self.a.get("/api/sessions").json()["sessions"]
        self.assertTrue(any(item["session_id"] == sid for item in mine))
        theirs = self.b.get("/api/sessions").json()["sessions"]
        self.assertFalse(any(item["session_id"] == sid for item in theirs))
        self.assertEqual(self.b.get("/api/session/" + sid).status_code, 404)
        patch = self.b.patch("/api/session/" + sid, json={"index": 0, "drafts": {"0": "hello world today"}, "phase": "dictate"})
        self.assertEqual(patch.status_code, 404)
        self.assertEqual(self.b.get("/api/session/" + sid + "/video").status_code, 404)
        self.assertEqual(self.b.get("/api/session/" + sid + "/media").status_code, 404)

    def test_progress_is_per_user(self):
        a_id = self.a.get("/api/auth/me").json()["user"]["id"]
        sid = "progress-a"
        self._owned_session(self.a, sid, a_id)
        self.a.patch("/api/session/" + sid, json={"index": 0, "drafts": {"0": "hello world today"}, "phase": "dictate"})
        mine = self.a.get("/api/progress")
        theirs = self.b.get("/api/progress")
        self.assertEqual(mine.status_code, 200)
        self.assertEqual(theirs.status_code, 200)
        self.assertGreaterEqual(mine.json().get("days_learned", 0), 1)
        self.assertEqual(theirs.json().get("days_learned", 0), 0)


class AccountTrialTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.old = _isolate_env()
        os.environ["ENPRATO_REQUIRE_AUTH"] = "1"
        os.environ["ENPRATO_COOKIE_SECURE"] = "0"

    def tearDown(self):
        _restore_env(self.tmp, self.old)

    def _login(self, email: str) -> TestClient:
        client = TestClient(main.app)
        client.post("/api/auth/register", json={"email": email, "password": "password123"})
        return client

    def _ok_ingest(self, _url, folder: Path, **_kwargs):
        audio = folder / "audio.wav"
        audio.write_bytes(b"RIFF")
        (folder / "source.mp4").write_bytes(b"mp4")
        captions = (
            "WEBVTT\n\n00:00:00.000 --> 00:00:04.000\n"
            "A newly imported sentence, with a natural split, for testing.\n"
        )
        return folder / "source.mp4", audio, captions

    def test_accounts_have_independent_quotas(self):
        a = self._login("trial-a@example.com")
        b = self._login("trial-b@example.com")
        self.assertEqual(a.get("/api/auth/me").json()["user"]["trial"]["remaining"], 5)
        self.assertEqual(b.get("/api/auth/me").json()["user"]["trial"]["remaining"], 5)

    def test_sixth_successful_import_is_rejected(self):
        client = self._login("cap@example.com")
        with patch.object(main, "ingest_url", side_effect=self._ok_ingest), patch.object(main, "fetch_media_title", return_value="title"):
            for i in range(5):
                res = client.post("/api/prepare-url", json={"url": f"https://example.com/v{i}.mp4", "create_new_session": True})
                self.assertEqual(res.status_code, 200, res.text)
                wait_ready(client, res)
            sixth = client.post("/api/prepare-url", json={"url": "https://example.com/v6.mp4", "create_new_session": True})
        self.assertEqual(sixth.status_code, 402)
        self.assertEqual(client.get("/api/auth/me").json()["user"]["trial"]["used"], 5)

    def test_failed_import_does_not_consume_quota(self):
        client = self._login("fail@example.com")
        with patch.object(main, "ingest_url", side_effect=RuntimeError("download failed")):
            res = client.post("/api/prepare-url", json={"url": "https://example.com/bad.mp4"})
            self.assertEqual(res.status_code, 200, res.text)
            job, _ = wait_job(client, res.json()["job_id"])
        self.assertEqual(job["status"], "failed")
        self.assertEqual(client.get("/api/auth/me").json()["user"]["trial"]["used"], 0)

    def test_empty_transcript_does_not_consume_quota(self):
        client = self._login("silent@example.com")

        def ingest(_url, folder, **_kwargs):
            source, wav, _captions = self._ok_ingest(_url, folder)
            return source, wav, ""

        with patch.object(main, "ingest_url", side_effect=ingest), patch.object(main, "fetch_media_title", return_value="silent"), patch.object(main, "transcribe_sentences", return_value=[]):
            res = client.post("/api/prepare-url", json={"url": "https://example.com/silent.mp4", "create_new_session": True})
            self.assertEqual(res.status_code, 200, res.text)
            job, _ = wait_job(client, res.json()["job_id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("分出句子", job["message"])
        self.assertEqual(client.get("/api/auth/me").json()["user"]["trial"]["used"], 0)

    def test_idempotent_prepare_key_does_not_double_charge(self):
        user = db.create_user("idem@example.com", hash_password("password123"))
        self.assertTrue(db.consume_trial(user["id"], "prepare:abc"))
        self.assertTrue(db.consume_trial(user["id"], "prepare:abc"))
        self.assertEqual(db.trial_status(user["id"])["used"], 1)

    def test_concurrent_imports_cap_at_five(self):
        user = db.create_user("race@example.com", hash_password("password123"))
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(lambda i: db.consume_trial(user["id"], "race-" + str(i)), range(10)))
        self.assertEqual(sum(results), 5)
        self.assertEqual(db.trial_status(user["id"])["remaining"], 0)


class LocalAnonymousModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.old = _isolate_env()
        os.environ.pop("ENPRATO_REQUIRE_AUTH", None)
        os.environ["ENPRATO_COOKIE_SECURE"] = "0"

    def tearDown(self):
        _restore_env(self.tmp, self.old)

    def test_lan_local_sessions_and_license_still_work(self):
        client = TestClient(main.app)
        me = client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertIsNone(me.json()["user"])
        self.assertFalse(me.json().get("require_auth"))
        self.assertEqual(client.get("/api/sessions").status_code, 200)
        self.assertEqual(client.get("/api/progress").status_code, 200)
        self.assertEqual(client.get("/api/license").status_code, 200)


class MockPayGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.old = _isolate_env()
        os.environ["ENPRATO_ALLOW_MOCK_PAY"] = "0"

    def tearDown(self):
        _restore_env(self.tmp, self.old)

    def test_mock_checkout_disabled(self):
        with self.assertRaises(ValueError):
            checkout_license(main.DATA, "monthly")
        os.environ.pop("ENPRATO_REQUIRE_AUTH", None)
        client = TestClient(main.app)
        res = client.post("/api/license/checkout", json={"plan": "monthly"})
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
