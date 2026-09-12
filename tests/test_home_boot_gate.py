from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
CSS = (ROOT / "frontend" / "src" / "index.css").read_text(encoding="utf-8")
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

    def test_guest_pay_cta_waits_for_plan_click(self):
        self.assertIn('useState<"pay" | "trial" | null>(null)', APP)
        self.assertIn('setGuestPrompt("pay")', APP)
        self.assertIn('setGuestPrompt("trial")', APP)
        self.assertIn('guestPrompt === "pay"', APP)
        self.assertIn('guestPrompt === "trial"', APP)
        self.assertIn("请先登录后再开通会员", APP)
        self.assertIn("请登录", APP)

    def test_logout_is_visible_in_topbar(self):
        self.assertIn('className="account-logout"', APP)
        self.assertIn("退出登录", APP)
        self.assertGreaterEqual(APP.count("退出登录"), 2)
        self.assertIn("logoutAccount()", APP)
        self.assertIn("setGuestLoginOpen(true)", APP)
        self.assertIn("account-popover", CSS)
        self.assertIn("z-index: 80", CSS)

    def test_cheers_for_register_and_plans(self):
        self.assertIn("终于等到你", APP)
        self.assertIn("真是太棒啦！", APP)
        self.assertIn("掌握一门语言哦。", APP)
        self.assertIn("你一定行！", APP)
        self.assertIn("请一定要挑战成功哦，", APP)
        self.assertIn("舍得投资自己", APP)
        self.assertIn("你真的太棒啦！", APP)
        self.assertIn("学会三门语言再走吧！", APP)
        self.assertIn("cheer-go", APP)
        self.assertIn("cheer-heart", APP)
        self.assertIn("/cheer-heart.png", APP)
        self.assertIn("Noto Sans SC", CSS)
        self.assertIn("/fonts/NotoSansSC-SemiBold.woff2", CSS)
        self.assertIn("PingFang SC", CSS)
        self.assertIn("white-space: nowrap", CSS)
        self.assertIn("height: 1.5em", CSS)
        self.assertIn("color: #1a1a1a", CSS)
        self.assertIn("cheer-backdrop", CSS)
        self.assertNotIn("124, 63, 22", CSS)
        self.assertIn('welcome: ["哇！终于等到你，", "好棒啊！"]', APP)
        self.assertIn('payMonthly: ["哇，你这么美，还这么上进！", "真是太棒啦！"]', APP)
        self.assertIn('payYearly: ["哇，你这么美，还这么舍得投资自己，", "你真的太棒啦！"]', APP)
        self.assertIn("firstRegister", APP)
        self.assertIn("payCheer", APP)
        self.assertIn("paidCheer", APP)
        self.assertIn("setGroupInviteOpen(true)", APP)

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
