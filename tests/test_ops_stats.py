from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import db, main


class OpsStatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old_db = db.DB_PATH
        self.old_token = os.environ.get("ENPRATO_OPS_TOKEN")
        db.DB_PATH = Path(self.tmp.name) / "ops.sqlite3"
        db.migrate()
        os.environ["ENPRATO_OPS_TOKEN"] = "ops-token-for-tests-only"
        self.client = TestClient(main.app, raise_server_exceptions=True)

    def tearDown(self):
        db.DB_PATH = self.old_db
        if self.old_token is None:
            os.environ.pop("ENPRATO_OPS_TOKEN", None)
        else:
            os.environ["ENPRATO_OPS_TOKEN"] = self.old_token
        self.tmp.cleanup()

    def test_missing_or_wrong_token_is_not_found(self):
        self.assertEqual(self.client.get("/api/ops").status_code, 404)
        self.assertEqual(self.client.get("/api/ops?k=wrong-token-value").status_code, 404)
        self.assertEqual(self.client.get("/api/ops/stats").status_code, 404)

    def test_token_shows_counts_and_hides_from_guests(self):
        user = db.create_user("ops@example.com", "hash")
        order = db.create_order(user["id"], "monthly_30d", "mock")
        db.complete_payment(
            provider="mock",
            event_id="ops-pay",
            payload_hash="ops",
            order_no=order["order_no"],
            trade_no="t-ops",
            amount_fen=1990,
            payment_status="SUCCESS",
        )
        raw = db.create_auth_session(user["id"])
        db.user_for_token(raw)
        page = self.client.get("/api/ops?k=ops-token-for-tests-only", follow_redirects=False)
        self.assertEqual(page.status_code, 303)
        self.assertIn("enprato_ops", page.headers.get("set-cookie", ""))
        stats = self.client.get("/api/ops/stats?k=ops-token-for-tests-only")
        self.assertEqual(stats.status_code, 200)
        self.assertEqual(stats.headers.get("x-robots-tag"), "noindex, nofollow")
        body = stats.json()
        self.assertEqual(body["registered_users"], 1)
        self.assertEqual(body["paid_users"], 1)
        self.assertEqual(body["paid_amount_yuan"], 19.9)
        self.assertEqual(body["online_users"], 1)
        self.assertEqual(body["timezone"], "Asia/Shanghai")
        self.assertEqual(len(body["daily"]), 30)
        self.assertEqual(body["daily"][-1]["date"], db.shanghai_today())
        self.assertEqual(body["daily"][-1]["registered"], 1)
        self.assertEqual(body["daily"][-1]["paid_amount_yuan"], 19.9)
        self.assertEqual(body["today_registered"], 1)
        self.assertEqual(body["today_paid_amount_yuan"], 19.9)
        self.assertEqual(sum(day["registered"] for day in body["daily"]), 1)
        self.assertNotIn("email", body)
        self.assertNotIn("ops@example.com", stats.text)
        html = self.client.get("/api/ops?k=ops-token-for-tests-only", follow_redirects=True)
        self.assertEqual(html.status_code, 200)
        self.assertIn("注册人数", html.text)
        self.assertIn("每日注册", html.text)
        self.assertIn("每日付费金额", html.text)
        self.assertIn("regChart", html.text)
        self.assertIn("chart-svg", html.text)
        self.assertIn("noindex", html.text)

    def test_daily_series_uses_shanghai_calendar_days(self):
        from datetime import datetime, timedelta, timezone

        user = db.create_user("ops-day@example.com", "hash")
        today = datetime.fromisoformat(db.shanghai_today())
        past = (today - timedelta(days=3)).date()
        stamp = datetime(past.year, past.month, past.day, 4, 0, 0, tzinfo=timezone.utc)
        conn = db.connect()
        try:
            conn.execute("UPDATE users SET created_at=? WHERE id=?", (db.iso(stamp), user["id"]))
        finally:
            conn.close()
        stats = self.client.get("/api/ops/stats?k=ops-token-for-tests-only")
        body = stats.json()
        day = next(item for item in body["daily"] if item["date"] == past.isoformat())
        self.assertEqual(day["registered"], 1)
        self.assertEqual(body["daily"][-1]["registered"], 0)
        self.assertNotIn("ops-day@example.com", stats.text)

    def test_unset_token_hides_page(self):
        os.environ.pop("ENPRATO_OPS_TOKEN", None)
        self.assertEqual(self.client.get("/api/ops?k=ops-token-for-tests-only").status_code, 404)


if __name__ == "__main__":
    unittest.main()
