import json
import os
import secrets
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import db, main, sms, url_import_jobs, wechat_oauth


def _isolate():
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    old = {
        "db": db.DB_PATH,
        "data": main.DATA,
        "auth": os.environ.get("ENPRATO_REQUIRE_AUTH"),
        "secure": os.environ.get("ENPRATO_COOKIE_SECURE"),
        "mock": os.environ.get("ENPRATO_ALLOW_MOCK_PAY"),
        "sms": os.environ.get("ENPRATO_SMS_PROVIDER"),
        "dev_sms": os.environ.get("ENPRATO_ALLOW_DEV_SMS"),
        "env": os.environ.get("ENPRATO_ENV"),
        "web_id": os.environ.get("WECHAT_WEB_APP_ID"),
        "web_secret": os.environ.get("WECHAT_WEB_APP_SECRET"),
        "oa_id": os.environ.get("WECHAT_OA_APP_ID"),
        "oa_secret": os.environ.get("WECHAT_OA_APP_SECRET"),
    }
    db.DB_PATH = Path(tmp.name) / "auth.sqlite3"
    main.DATA = Path(tmp.name) / "sessions"
    main.DATA.mkdir()
    db.migrate()
    os.environ["ENPRATO_REQUIRE_AUTH"] = "1"
    os.environ["ENPRATO_COOKIE_SECURE"] = "0"
    os.environ["ENPRATO_ALLOW_MOCK_PAY"] = "0"
    return tmp, old


def _restore(tmp, old):
    try:
        url_import_jobs.wait_idle(20)
    except Exception:
        pass
    db.DB_PATH, main.DATA = old["db"], old["data"]
    mapping = {
        "auth": "ENPRATO_REQUIRE_AUTH",
        "secure": "ENPRATO_COOKIE_SECURE",
        "mock": "ENPRATO_ALLOW_MOCK_PAY",
        "sms": "ENPRATO_SMS_PROVIDER",
        "dev_sms": "ENPRATO_ALLOW_DEV_SMS",
        "env": "ENPRATO_ENV",
        "web_id": "WECHAT_WEB_APP_ID",
        "web_secret": "WECHAT_WEB_APP_SECRET",
        "oa_id": "WECHAT_OA_APP_ID",
        "oa_secret": "WECHAT_OA_APP_SECRET",
    }
    for key, name in mapping.items():
        if old[key] is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old[key]
    tmp.cleanup()


def _enable_dev_sms():
    os.environ["ENPRATO_SMS_PROVIDER"] = "dev"
    os.environ["ENPRATO_ALLOW_DEV_SMS"] = "1"
    os.environ["ENPRATO_ENV"] = "development"


def _wechat_login(client: TestClient, unionid: str, openid: str, appid: str = "wxweb") -> TestClient:
    state = "web.test-state-" + unionid
    client.cookies.set("enprato_oauth_state", state)
    with patch.object(wechat_oauth, "exchange_code", return_value={"appid": appid, "openid": openid, "unionid": unionid}):
        res = client.get("/api/auth/wechat/callback", params={"code": "ok", "state": state}, follow_redirects=False)
    assert res.status_code == 302, res.text
    return client


def _send_code(client: TestClient, phone: str, code: int = 123456, headers: dict | None = None):
    with patch("backend.app.main.secrets.randbelow", return_value=code):
        return client.post("/api/auth/phone/send", json={"phone": phone}, headers=headers or {})


def _owned_session(client: TestClient, session_id: str, user_id: str) -> None:
    db.register_learning_session(session_id, user_id)
    folder = main.DATA / session_id
    folder.mkdir()
    (folder / "sentences.json").write_text(
        json.dumps([{"id": 0, "start": 0, "end": 1, "text": "hello world today"}]),
        encoding="utf-8",
    )
    (folder / "meta.json").write_text(json.dumps({"title": "A lesson", "drafts": {}, "index": 0}), encoding="utf-8")
    (folder / "source.mp4").write_bytes(b"not-video")
    (folder / "audio.wav").write_bytes(b"RIFF")


class WechatIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.old = _isolate()
        os.environ["WECHAT_WEB_APP_ID"] = "wx-web-test"
        os.environ["WECHAT_WEB_APP_SECRET"] = "web-secret-test"

    def tearDown(self):
        _restore(self.tmp, self.old)

    def test_first_wechat_login_creates_user_and_quota(self):
        client = _wechat_login(TestClient(main.app), "union-a", "openid-a")
        me = client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        user = me.json()["user"]
        self.assertTrue(user["id"])
        self.assertEqual(user["trial"]["limit"], 5)
        self.assertEqual(user["trial"]["remaining"], 5)
        self.assertEqual(user["login_label"], "微信账号")

    def test_same_wechat_identity_reuses_user(self):
        first = _wechat_login(TestClient(main.app), "union-same", "openid-1")
        user_id = first.get("/api/auth/me").json()["user"]["id"]
        first.post("/api/auth/logout")
        second = _wechat_login(TestClient(main.app), "union-same", "openid-2")
        self.assertEqual(second.get("/api/auth/me").json()["user"]["id"], user_id)

    def test_different_wechat_identities_are_different_users(self):
        a = _wechat_login(TestClient(main.app), "union-a", "oa")
        b = _wechat_login(TestClient(main.app), "union-b", "ob")
        self.assertNotEqual(a.get("/api/auth/me").json()["user"]["id"], b.get("/api/auth/me").json()["user"]["id"])

    def test_start_redirects_to_qrconnect_on_pc(self):
        client = TestClient(main.app)
        res = client.get("/api/auth/wechat/start", follow_redirects=False)
        self.assertEqual(res.status_code, 302)
        self.assertIn("open.weixin.qq.com/connect/qrconnect", res.headers["location"])

    def test_start_unconfigured_does_not_fake_success(self):
        os.environ.pop("WECHAT_WEB_APP_ID", None)
        os.environ.pop("WECHAT_WEB_APP_SECRET", None)
        res = TestClient(main.app).get("/api/auth/wechat/start", follow_redirects=False)
        self.assertEqual(res.status_code, 302)
        self.assertIn("auth_error=wechat_unconfigured", res.headers["location"])


class PhoneIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.old = _isolate()
        _enable_dev_sms()

    def tearDown(self):
        _restore(self.tmp, self.old)

    def test_phone_verify_creates_user(self):
        client = TestClient(main.app)
        sent = _send_code(client, "13800138000")
        self.assertEqual(sent.status_code, 200)
        self.assertNotIn("dev_code", sent.json())
        self.assertNotIn("code", sent.json())
        challenge = sent.json()["challenge_id"]
        res = client.post("/api/auth/phone/verify", json={"phone": "13800138000", "challenge_id": challenge, "code": "123456"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["trial"]["remaining"], 5)
        me = client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["user"]["id"], res.json()["id"])

    def test_same_phone_reuses_user(self):
        client = TestClient(main.app)
        first = _send_code(client, "13900139000")
        verify = client.post("/api/auth/phone/verify", json={"phone": "13900139000", "challenge_id": first.json()["challenge_id"], "code": "123456"})
        user_id = verify.json()["id"]
        client.post("/api/auth/logout")
        conn = db.connect()
        try:
            conn.execute("UPDATE sms_challenges SET created_at='2000-01-01T00:00:00Z'")
        finally:
            conn.close()
        second = _send_code(client, "13900139000")
        self.assertEqual(second.status_code, 200, second.text)
        again = client.post("/api/auth/phone/verify", json={"phone": "13900139000", "challenge_id": second.json()["challenge_id"], "code": "123456"})
        self.assertEqual(again.json()["id"], user_id)

    def test_different_phones_are_different_users(self):
        a = db.login_or_create_identities([("phone", "13700137001")])
        b = db.login_or_create_identities([("phone", "13700137002")])
        self.assertNotEqual(a, b)

    def test_wrong_code_rejected(self):
        client = TestClient(main.app)
        sent = _send_code(client, "13600136000")
        bad = client.post("/api/auth/phone/verify", json={"phone": "13600136000", "challenge_id": sent.json()["challenge_id"], "code": "000000"})
        self.assertEqual(bad.status_code, 401)
        self.assertEqual(bad.json()["detail"], "验证码错误或已失效")
        self.assertEqual(client.get("/api/auth/me").status_code, 401)

    def test_expired_code_rejected(self):
        client = TestClient(main.app)
        sent = _send_code(client, "13500135000")
        conn = db.connect()
        try:
            conn.execute("UPDATE sms_challenges SET expires_at='2000-01-01T00:00:00Z'")
        finally:
            conn.close()
        res = client.post("/api/auth/phone/verify", json={"phone": "13500135000", "challenge_id": sent.json()["challenge_id"], "code": "123456"})
        self.assertEqual(res.status_code, 401)

    def test_code_cannot_be_reused(self):
        client = TestClient(main.app)
        sent = _send_code(client, "13400134000")
        payload = {"phone": "13400134000", "challenge_id": sent.json()["challenge_id"], "code": "123456"}
        self.assertEqual(client.post("/api/auth/phone/verify", json=payload).status_code, 200)
        client.post("/api/auth/logout")
        self.assertEqual(client.post("/api/auth/phone/verify", json=payload).status_code, 401)

    def test_send_rate_limited(self):
        client = TestClient(main.app)
        self.assertEqual(_send_code(client, "13300133000").status_code, 200)
        again = _send_code(client, "13300133000")
        self.assertEqual(again.status_code, 429)

    def test_trusted_proxy_sms_limit_uses_x_real_ip(self):
        client = TestClient(main.app, client=("127.0.0.1", 50000))
        sent = _send_code(client, "13300133001", headers={"X-Real-IP": "203.0.113.10"})
        self.assertEqual(sent.status_code, 200, sent.text)
        conn = db.connect()
        try:
            row = conn.execute("SELECT request_ip FROM sms_challenges WHERE phone=?", ("13300133001",)).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["request_ip"], "203.0.113.10")

    def test_trusted_proxy_different_real_ips_use_separate_buckets(self):
        client = TestClient(main.app, client=("127.0.0.1", 50000))
        first = _send_code(client, "13300133002", headers={"X-Real-IP": "203.0.113.11"})
        second = _send_code(client, "13300133003", headers={"X-Real-IP": "203.0.113.12"})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)

    def test_untrusted_peer_cannot_spoof_xff_to_bypass_ip_limit(self):
        client = TestClient(main.app, client=("203.0.113.50", 50000))
        first = _send_code(client, "13300133004", headers={"X-Forwarded-For": "198.51.100.1"})
        second = _send_code(client, "13300133005", headers={"X-Forwarded-For": "198.51.100.2"})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 429)
        conn = db.connect()
        try:
            row = conn.execute("SELECT request_ip FROM sms_challenges WHERE phone=?", ("13300133004",)).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["request_ip"], "203.0.113.50")

    def test_malformed_forwarded_for_does_not_500(self):
        client = TestClient(main.app, client=("127.0.0.1", 50000))
        res = _send_code(client, "13300133006", headers={"X-Forwarded-For": "???, not-an-ip"})
        self.assertEqual(res.status_code, 200, res.text)
        conn = db.connect()
        try:
            row = conn.execute("SELECT request_ip FROM sms_challenges WHERE phone=?", ("13300133006",)).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["request_ip"], "127.0.0.1")

    def test_production_dev_sms_does_not_leak_code(self):
        os.environ["ENPRATO_ENV"] = "production"
        os.environ["ENPRATO_ALLOW_DEV_SMS"] = "1"
        os.environ["ENPRATO_SMS_PROVIDER"] = "dev"
        client = TestClient(main.app)
        res = _send_code(client, "13200132000")
        self.assertEqual(res.status_code, 503)
        self.assertNotIn("dev_code", res.text)
        self.assertNotIn("123456", res.text)


class IsolationAndQuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.old = _isolate()
        _enable_dev_sms()
        os.environ["WECHAT_WEB_APP_ID"] = "wx-web-test"
        os.environ["WECHAT_WEB_APP_SECRET"] = "web-secret-test"

    def tearDown(self):
        _restore(self.tmp, self.old)

    def test_wechat_user_cannot_see_phone_user_session_or_progress(self):
        wechat = _wechat_login(TestClient(main.app), "union-owner", "oid-owner")
        phone = TestClient(main.app)
        sent = _send_code(phone, "13100131000")
        phone.post("/api/auth/phone/verify", json={"phone": "13100131000", "challenge_id": sent.json()["challenge_id"], "code": "123456"})
        wechat_id = wechat.get("/api/auth/me").json()["user"]["id"]
        sid = "wechat-lesson"
        _owned_session(wechat, sid, wechat_id)
        wechat.patch("/api/session/" + sid, json={"index": 0, "drafts": {"0": "hello world today"}, "phase": "dictate"})
        self.assertTrue(any(item["session_id"] == sid for item in wechat.get("/api/sessions").json()["sessions"]))
        self.assertFalse(any(item["session_id"] == sid for item in phone.get("/api/sessions").json()["sessions"]))
        self.assertEqual(phone.get("/api/session/" + sid).status_code, 404)
        self.assertGreaterEqual(wechat.get("/api/progress").json().get("days_learned", 0), 1)
        self.assertEqual(phone.get("/api/progress").json().get("days_learned", 0), 0)
        self.assertEqual(wechat.get("/api/auth/me").json()["user"]["trial"]["remaining"], 5)
        self.assertEqual(phone.get("/api/auth/me").json()["user"]["trial"]["remaining"], 5)

    def test_anonymous_still_401(self):
        client = TestClient(main.app)
        self.assertEqual(client.get("/api/sessions").status_code, 401)
        self.assertEqual(client.get("/api/progress").status_code, 401)
        self.assertEqual(client.post("/api/prepare-url", json={"url": "https://example.com/a.mp4"}).status_code, 401)


class PhoneChallengeLimitTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.old = _isolate()

    def tearDown(self):
        _restore(self.tmp, self.old)

    def _create(self, phone: str, ip: str) -> bool:
        return db.create_phone_challenge(phone, "hash", ip, secrets.token_urlsafe(12))

    def _backdate_minutes(self, minutes: int) -> None:
        when = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = db.connect()
        try:
            conn.execute("UPDATE sms_challenges SET created_at=?", (when,))
        finally:
            conn.close()

    def test_same_phone_one_per_minute(self):
        self.assertTrue(self._create("13000000001", "203.0.113.1"))
        self.assertFalse(self._create("13000000001", "203.0.113.2"))

    def test_same_ip_one_per_minute(self):
        self.assertTrue(self._create("13000000011", "203.0.113.3"))
        self.assertFalse(self._create("13000000012", "203.0.113.3"))

    def test_same_phone_five_per_hour(self):
        for _ in range(5):
            self.assertTrue(self._create("13000000021", "203.0.113.4"))
            self._backdate_minutes(2)
        self.assertFalse(self._create("13000000021", "203.0.113.5"))

    def test_same_ip_twenty_per_hour(self):
        for index in range(20):
            self.assertTrue(self._create(f"1300001{index:04d}", "203.0.113.6"))
            self._backdate_minutes(2)
        self.assertFalse(self._create("13000019999", "203.0.113.6"))


class LocalAndMockTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.old = _isolate()
        os.environ.pop("ENPRATO_REQUIRE_AUTH", None)
        os.environ["ENPRATO_ALLOW_MOCK_PAY"] = "0"

    def tearDown(self):
        _restore(self.tmp, self.old)

    def test_lan_local_still_works(self):
        client = TestClient(main.app)
        me = client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertIsNone(me.json()["user"])
        self.assertEqual(client.get("/api/sessions").status_code, 200)
        self.assertEqual(client.get("/api/progress").status_code, 200)

    def test_mock_pay_still_disabled(self):
        from backend.app.license import checkout_license
        with self.assertRaises(ValueError):
            checkout_license(main.DATA, "monthly")


class SmsConfigTests(unittest.TestCase):
    def test_tencent_incomplete_is_not_ready(self):
        old = {name: os.environ.get(name) for name in ("ENPRATO_SMS_PROVIDER", "ENPRATO_ENV", "ENPRATO_ALLOW_DEV_SMS")}
        try:
            os.environ["ENPRATO_SMS_PROVIDER"] = "tencent"
            os.environ["ENPRATO_ENV"] = "production"
            os.environ["ENPRATO_ALLOW_DEV_SMS"] = "0"
            self.assertFalse(sms.sms_send_ready())
        finally:
            for name, value in old.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
