import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import db, main
from backend.app.auth import hash_password


class P0ApiTests(unittest.TestCase):
    def test_session_access_logout_and_remote_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_db, old_data = db.DB_PATH, main.DATA
            old_auth = os.environ.get("ENPRATO_REQUIRE_AUTH")
            try:
                # 模拟官网强制登录：禁止仅因目录存在而匿名放行
                os.environ["ENPRATO_REQUIRE_AUTH"] = "1"
                db.DB_PATH = Path(tmp) / "api.sqlite3"
                main.DATA = Path(tmp) / "sessions"
                main.DATA.mkdir()
                db.migrate()
                a = db.create_user("api-a@example.com", hash_password("password123"))
                b = db.create_user("api-b@example.com", hash_password("password123"))
                sid = "owned-session"
                db.register_learning_session(sid, a["id"])
                folder = main.DATA / sid
                folder.mkdir()
                (folder / "sentences.json").write_text(json.dumps([{"start": 0, "end": 1, "text": "hello"}]), encoding="utf-8")
                (folder / "meta.json").write_text(json.dumps({"title": "test"}), encoding="utf-8")
                (folder / "source.mp4").write_bytes(b"not-video")
                client_a, client_b, anon = TestClient(main.app), TestClient(main.app), TestClient(main.app)
                self.assertEqual(client_a.post("/api/auth/login", json={"email": "api-a@example.com", "password": "password123"}).status_code, 200)
                self.assertEqual(client_b.post("/api/auth/login", json={"email": "api-b@example.com", "password": "password123"}).status_code, 200)
                self.assertEqual(anon.get("/api/session/" + sid).status_code, 401)
                self.assertIn(client_b.get("/api/session/" + sid).status_code, (401, 403, 404))
                self.assertIn(client_b.get("/api/session/" + sid + "/video").status_code, (401, 403, 404))
                self.assertIn(client_b.get("/api/session/" + sid + "/audio").status_code, (401, 403, 404))
                self.assertIn(client_b.get("/api/session/" + sid + "/thumb").status_code, (401, 403, 404))
                self.assertEqual(client_a.get("/api/session/" + sid).status_code, 200)
                token = client_a.post("/api/remote-token/" + sid).json()["token"]
                self.assertEqual(anon.get("/api/session/" + sid + "?token=" + token).status_code, 200)
                self.assertEqual(anon.get("/api/session/other?token=" + token).status_code, 401)
                expired = client_a.post("/api/remote-token/" + sid).json()["token"]
                conn = db.connect()
                conn.execute("UPDATE remote_tokens SET expires_at='2000-01-01T00:00:00Z' WHERE token_hash=?", (db.hash_token(expired),))
                conn.close()
                self.assertEqual(anon.get("/api/session/" + sid + "?token=" + expired).status_code, 401)
                db.revoke_remote_token(token)
                self.assertEqual(anon.get("/api/session/" + sid + "?token=" + token).status_code, 401)
                client_a.post("/api/auth/logout")
                self.assertEqual(client_a.get("/api/session/" + sid).status_code, 401)
                client_a.post("/api/auth/login", json={"email": "api-a@example.com", "password": "password123"})
                self.assertEqual(client_a.get("/api/session/" + sid).status_code, 200)
            finally:
                db.DB_PATH, main.DATA = old_db, old_data
                if old_auth is None:
                    os.environ.pop("ENPRATO_REQUIRE_AUTH", None)
                else:
                    os.environ["ENPRATO_REQUIRE_AUTH"] = old_auth

    def test_concurrent_trial_quota_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = db.DB_PATH
            try:
                db.DB_PATH = Path(tmp) / "quota.sqlite3"
                db.migrate()
                user = db.create_user("parallel@example.com", "hash")
                with ThreadPoolExecutor(max_workers=10) as pool:
                    results = list(pool.map(lambda i: db.consume_trial(user["id"], "parallel-" + str(i)), range(10)))
                self.assertEqual(sum(results), 5)
                self.assertEqual(db.trial_status(user["id"])["remaining"], 0)
            finally:
                db.DB_PATH = old
