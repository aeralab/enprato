import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from backend.app import db, main
from backend.app.auth import hash_password
from backend.app.progress import (
    _rebuild_daily,
    _streak,
    _word_eval,
    complete_session,
    progress_for_user,
    record_new_dictations,
)
from backend.app.resplit import resplit_remaining_session, rollback_resplit_session


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "progress.sqlite3"
        db.migrate()
        self.user = db.create_user("progress@example.com", hash_password("password123"))
        self.other = db.create_user("other@example.com", hash_password("password123"))
        self.root = Path(self.tmp.name) / "sessions"
        self.root.mkdir()

    def tearDown(self):
        db.DB_PATH = self.old_db
        self.tmp.cleanup()

    def _session(self, session_id: str, user_id: str, drafts: dict | None = None, extra_sentences: list | None = None):
        db.register_learning_session(session_id, user_id)
        folder = self.root / session_id
        folder.mkdir()
        sentences = [
            {"id": 0, "start": 0, "end": 2, "text": "hello world today"},
            {"id": 1, "start": 2, "end": 4, "text": "this is practice"},
        ]
        if extra_sentences:
            sentences.extend(extra_sentences)
        (folder / "sentences.json").write_text(json.dumps(sentences), encoding="utf-8")
        (folder / "meta.json").write_text(json.dumps({
            "title": "Test lesson",
            "source_kind": "file",
            "created_at": "2026-09-03T10:00:00Z",
            "drafts": drafts or {},
            "index": 0,
        }), encoding="utf-8")
        return folder

    def _count_events(self, owner: str) -> int:
        conn = db.connect()
        try:
            return int(conn.execute("SELECT COUNT(*) FROM learning_events WHERE owner_key=?", (owner,)).fetchone()[0])
        finally:
            conn.close()

    def test_first_learning_is_day_one(self):
        folder = self._session("first", self.user["id"])
        created = record_new_dictations(self.user, "first", folder, {}, {"0": "hello world today"})
        summary = progress_for_user(self.user)
        self.assertEqual(created, 1)
        self.assertTrue(summary["day_one"])
        self.assertEqual(summary["days_learned"], 1)
        self.assertFalse(summary["comparison"]["has_history"])
        self.assertIsNone(summary["comparison"]["accuracy_delta"])
        self.assertEqual(summary["today"]["dictation_words"], 3)

    def test_old_session_history_is_not_backfilled(self):
        folder = self._session("old", self.user["id"], drafts={"0": "hello world today"})
        created = record_new_dictations(
            self.user,
            "old",
            folder,
            {"0": "hello world today"},
            {"0": "hello world today", "1": ""},
        )
        self.assertEqual(created, 0)
        self.assertEqual(progress_for_user(self.user)["days_learned"], 0)

    def test_old_session_new_work_is_recorded(self):
        folder = self._session("old2", self.user["id"], drafts={"0": "hello world today"})
        created = record_new_dictations(
            self.user,
            "old2",
            folder,
            {"0": "hello world today"},
            {"0": "hello world today", "1": "this is practice"},
        )
        self.assertEqual(created, 1)
        summary = progress_for_user(self.user)
        self.assertEqual(summary["today"]["completed_units"], 1)
        self.assertEqual(summary["total_dictation_words"], 3)

    def test_refresh_and_duplicate_complete_do_not_double_count(self):
        folder = self._session("dup", self.user["id"])
        first = record_new_dictations(self.user, "dup", folder, {}, {"0": "hello world today"})
        second = record_new_dictations(self.user, "dup", folder, {"0": "hello world today"}, {"0": "hello world today"})
        third = record_new_dictations(self.user, "dup", folder, {}, {"0": "hello world today"})
        complete_session(self.user, "dup", self.root, 900)
        complete_session(self.user, "dup", self.root, 900)
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(third, 0)
        self.assertEqual(self._count_events(self.user["id"]), 1)

    def test_reconnect_and_restart_idempotency(self):
        folder = self._session("re", self.user["id"])
        record_new_dictations(self.user, "re", folder, {}, {"0": "hello world today"})
        record_new_dictations(self.user, "re", folder, {}, {"0": "hello world today"})
        self.assertEqual(self._count_events(self.user["id"]), 1)

    def test_accuracy_is_word_weighted_across_sessions(self):
        a = self._session("a", self.user["id"])
        b = self._session("b", self.user["id"])
        record_new_dictations(self.user, "a", a, {}, {"0": "hello"})  # 1/3
        record_new_dictations(self.user, "b", b, {}, {"0": "hello world today"})  # 3/3
        summary = progress_for_user(self.user)
        self.assertEqual(summary["today"]["evaluated_words"], 6)
        self.assertEqual(summary["today"]["correct_words"], 4)
        self.assertEqual(summary["today"]["accuracy"], round(100 * 4 / 6, 1))

    def test_word_eval_helper(self):
        stats = _word_eval("hello world today", "hello world today")
        self.assertEqual(stats["evaluated_words"], 3)
        self.assertEqual(stats["correct_words"], 3)

    def test_active_day_and_streak(self):
        today = date.today()
        dates = [(today - timedelta(days=i)).isoformat() for i in range(2, -1, -1)]
        self.assertEqual(_streak(dates, today), (3, 3))
        self.assertEqual(_streak([(today - timedelta(days=4)).isoformat()], today), (0, 1))
        folder = self._session("streak", self.user["id"])
        record_new_dictations(self.user, "streak", folder, {}, {"0": "hello world today"})
        summary = progress_for_user(self.user)
        self.assertEqual(summary["current_streak"], 1)
        self.assertEqual(summary["longest_streak"], 1)

    def test_splitter_and_resplit_do_not_create_events(self):
        extra = [{"id": 2, "start": 4, "end": 20, "text": "Because the weather was unusually cold this morning we decided to stay inside and finish our homework together after breakfast."}]
        folder = self._session("split", self.user["id"], extra_sentences=extra)
        before = self._count_events(self.user["id"])
        result = resplit_remaining_session(folder)
        after_split = self._count_events(self.user["id"])
        rollback_resplit_session(folder, result["backup_id"])
        after_rollback = self._count_events(self.user["id"])
        self.assertEqual(before, 0)
        self.assertEqual(after_split, 0)
        self.assertEqual(after_rollback, 0)

    def test_user_isolation_and_progress_api(self):
        folder = self._session("owned", self.user["id"])
        record_new_dictations(self.user, "owned", folder, {}, {"0": "hello world today"})
        self.assertEqual(progress_for_user(self.other)["days_learned"], 0)
        old_db, old_data = db.DB_PATH, main.DATA
        try:
            main.DATA = self.root
            client_a, client_b = TestClient(main.app), TestClient(main.app)
            self.assertEqual(client_a.post("/api/auth/login", json={"email": "progress@example.com", "password": "password123"}).status_code, 200)
            self.assertEqual(client_b.post("/api/auth/login", json={"email": "other@example.com", "password": "password123"}).status_code, 200)
            mine = client_a.get("/api/progress").json()
            theirs = client_b.get("/api/progress").json()
            self.assertEqual(mine["days_learned"], 1)
            self.assertEqual(theirs["days_learned"], 0)
            self.assertEqual(client_a.get("/api/progress?days=7").status_code, 200)
            self.assertEqual(client_a.get("/api/progress?days=30").status_code, 200)
        finally:
            main.DATA = old_data
            db.DB_PATH = old_db

    def test_seven_and_thirty_day_windows(self):
        folder = self._session("win", self.user["id"])
        record_new_dictations(self.user, "win", folder, {}, {"0": "hello world today"})
        conn = db.connect()
        try:
            old = (datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=10)).isoformat()
            conn.execute("UPDATE learning_events SET learning_date=? WHERE session_id='win'", (old,))
            conn.execute("DELETE FROM learning_daily WHERE owner_key=?", (self.user["id"],))
            _rebuild_daily(conn, self.user["id"], old)
        finally:
            conn.close()
        seven = progress_for_user(self.user, 7)
        thirty = progress_for_user(self.user, 30)
        self.assertEqual(len(seven["records"]), 0)
        self.assertEqual(len(thirty["records"]), 1)


if __name__ == "__main__":
    unittest.main()
