from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
ACCESS = (ROOT / "frontend" / "src" / "studioAccess.ts").read_text(encoding="utf-8")


class HomeBootDoesNotAutoOpenStudioTests(unittest.TestCase):
    def test_boot_does_not_restore_last_session(self):
        self.assertNotIn("if (last && rows.some((row) => row.session_id === last))", APP)
        self.assertNotIn("const last = localStorage.getItem(LAST_SESSION_KEY);", APP)

    def test_boot_does_not_wait_for_import_job(self):
        self.assertNotIn("waitForImportJob", APP)
        self.assertNotIn("fetchActiveImportJob", APP)
        self.assertNotIn("IMPORT_JOB_KEY", APP)

    def test_guest_home_hides_history_and_shows_login(self):
        self.assertIn("登录后查看你的课程", APP)
        self.assertIn("请先登录后再开始听写", APP)
        self.assertIn("极短的时间、极低的成本、极高效的方法", APP)
        self.assertNotIn("if (requireAuth && !user) {\n    return (\n      <AuthScreen", APP)

    def test_boot_still_loads_history_only(self):
        self.assertIn("const rows = await listSessions();", APP)
        self.assertIn("setHistory(rows);", APP)

    def test_enter_studio_requires_explicit_action_and_gate(self):
        self.assertIn('from "./studioAccess"', APP)
        self.assertIn("canEnterDictation(", APP)
        self.assertGreaterEqual(APP.count("studioLocked("), 4)
        self.assertIn("function start()", APP)
        self.assertIn("async function resume(", APP)
        self.assertIn("openDetail(detail)", APP)

    def test_access_helper_blocks_exhausted_trial(self):
        self.assertIn("sessionCanDeepStudy === true", ACCESS)
        self.assertIn("trial?.remaining", ACCESS)
        self.assertIn("membership?.status === \"active\"", ACCESS)
        self.assertIn("DICTATION_LOCKED_MESSAGE", ACCESS)


if __name__ == "__main__":
    unittest.main()
