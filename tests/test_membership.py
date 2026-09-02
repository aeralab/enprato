import os
import tempfile
import unittest
import base64
import time
from pathlib import Path

from backend.app.payment import WechatPayProvider

from backend.app import db
from backend.app.auth import hash_password, verify_password


class MembershipTests(unittest.TestCase):
    def test_account_trial_quota_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = db.DB_PATH
            try:
                db.DB_PATH = Path(tmp) / "quota.sqlite3"
                db.migrate()
                user = db.create_user("quota@example.com", "hash")
                self.assertEqual(db.trial_status(user["id"])["remaining"], 5)
                self.assertTrue(db.consume_trial(user["id"], "task-1"))
                self.assertTrue(db.consume_trial(user["id"], "task-1"))
                self.assertEqual(db.trial_status(user["id"])["used"], 1)
                for i in range(2, 6):
                    self.assertTrue(db.consume_trial(user["id"], "task-" + str(i)))
                self.assertFalse(db.consume_trial(user["id"], "task-6"))
                self.assertEqual(db.trial_status(user["id"])["remaining"], 0)
            finally:
                db.DB_PATH = old

    def test_session_owner_and_remote_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = db.DB_PATH
            try:
                db.DB_PATH = Path(tmp) / "owner.sqlite3"
                db.migrate()
                a = db.create_user("a@example.com", "hash")
                b = db.create_user("b@example.com", "hash")
                db.register_learning_session("session-a", a["id"])
                self.assertTrue(db.owns_learning_session("session-a", a["id"]))
                self.assertFalse(db.owns_learning_session("session-a", b["id"]))
                token = db.create_remote_token("session-a", a["id"])
                self.assertEqual(db.remote_token_owner(token, "session-a"), a["id"])
                self.assertIsNone(db.remote_token_owner(token, "other-session"))
            finally:
                db.DB_PATH = old

    def test_password_round_trip(self):
        encoded = hash_password("correct horse battery staple")
        self.assertTrue(verify_password("correct horse battery staple", encoded))
        self.assertFalse(verify_password("wrong", encoded))

    def test_wechat_signature_failure_is_rejected(self):
        provider = object.__new__(WechatPayProvider)
        provider.serial = "merchant"
        provider.platform_serial = "serial"
        class Cert:
            class Key:
                def verify(self, *args): raise ValueError("bad signature")
            def public_key(self): return self.Key()
        provider.platform_cert = Cert()
        with self.assertRaises(ValueError):
            provider.verify_and_decode_notify(headers={"Wechatpay-Timestamp": str(int(time.time())), "Wechatpay-Nonce": "n", "Wechatpay-Signature": base64.b64encode(b"bad").decode(), "Wechatpay-Serial": "serial"}, body=b"{}")

    def test_order_payment_idempotency_amount_and_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = db.DB_PATH
            try:
                db.DB_PATH = Path(tmp) / "test.sqlite3"
                db.migrate()
                user = db.create_user("pay@example.com", "hash")
                order = db.create_order(user["id"], "monthly_30d", "mock")
                self.assertEqual(order["amount_fen"], 1990)
                with self.assertRaises(ValueError):
                    db.complete_payment(provider="mock", event_id="bad", payload_hash="x", order_no=order["order_no"], trade_no="bad", amount_fen=1, payment_status="SUCCESS")
                self.assertEqual(db.get_order(order["order_no"], user["id"])["status"], "pending")
                self.assertEqual(db.complete_payment(provider="mock", event_id="ok", payload_hash="x", order_no=order["order_no"], trade_no="t1", amount_fen=1990, payment_status="SUCCESS"), "paid")
                first = db.membership_status(user["id"])
                self.assertEqual(first["status"], "active")
                self.assertEqual(db.complete_payment(provider="mock", event_id="ok", payload_hash="x", order_no=order["order_no"], trade_no="t1", amount_fen=1990, payment_status="SUCCESS"), "duplicate")
                self.assertEqual(db.membership_status(user["id"])["expires_at"], first["expires_at"])
                order2 = db.create_order(user["id"], "monthly_30d", "mock")
                db.complete_payment(provider="mock", event_id="ok2", payload_hash="y", order_no=order2["order_no"], trade_no="t2", amount_fen=1990, payment_status="SUCCESS")
                self.assertGreater(db.membership_status(user["id"])["expires_at"], first["expires_at"])
            finally:
                db.DB_PATH = old

    def test_membership_expiry_and_dev_grant(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = db.DB_PATH
            old_flag = os.environ.get("ENPRATO_ENABLE_DEV_MEMBERSHIP")
            try:
                db.DB_PATH = Path(tmp) / "test.sqlite3"
                os.environ["ENPRATO_ENABLE_DEV_MEMBERSHIP"] = "1"
                db.migrate()
                user = db.create_user("a@example.com", "hash")
                self.assertEqual(db.membership_status(user["id"])["status"], "none")
                granted = db.grant_dev_membership(user["id"])
                self.assertEqual(granted["status"], "active")
                self.assertTrue(granted["expires_at"])
            finally:
                db.DB_PATH = old
                if old_flag is None:
                    os.environ.pop("ENPRATO_ENABLE_DEV_MEMBERSHIP", None)
                else:
                    os.environ["ENPRATO_ENABLE_DEV_MEMBERSHIP"] = old_flag


if __name__ == "__main__":
    unittest.main()
