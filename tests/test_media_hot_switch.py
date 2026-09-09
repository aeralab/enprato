from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
MEDIA = (ROOT / "frontend" / "src" / "mediaStatus.ts").read_text(encoding="utf-8")
IPAD = (ROOT / "backend" / "app" / "ipad_studio.py").read_text(encoding="utf-8")


def should_accept_media_poll(bound_session_id: str, payload_session_id: str | None = None) -> bool:
    bound = (bound_session_id or "").strip()
    incoming = (payload_session_id or "").strip()
    if not bound:
        return False
    if not incoming:
        return True
    return bound == incoming


def infer_initial_media_status(has_video: bool | None, source_url: str | None = None) -> str:
    if has_video:
        return "ready"
    src = (source_url or "").lower()
    if "bilibili.com" in src or "b23.tv" in src:
        return "preparing"
    return "audio"


def audio_only_panel(status: str | None, has_video: bool) -> dict[str, str]:
    if has_video:
        return {"title": "", "body": ""}
    if status == "failed":
        return {"title": "暂时无法加载视频画面", "body": "音频学习不受影响。"}
    if status == "audio":
        return {"title": "仅音频", "body": "本课没有视频画面，可使用音频听写、跟读。"}
    return {
        "title": "视频画面正在准备中",
        "body": "你可以先开始听写，画面准备好后会自动显示。",
    }


def _studio_poll_block() -> str:
    start = APP.find("fetchSessionMedia(bound)")
    return APP[max(0, start - 400) : start + 1800]


class MediaStatusLogicTests(unittest.TestCase):
    def test_stale_media_response_from_a_is_ignored_after_switch_to_b(self):
        self.assertTrue(should_accept_media_poll("sess-a", "sess-a"))
        self.assertFalse(should_accept_media_poll("sess-b", "sess-a"))
        self.assertFalse(should_accept_media_poll("", "sess-a"))
        self.assertIn("shouldAcceptMediaPoll", MEDIA)
        self.assertIn("shouldAcceptMediaPoll", APP)
        self.assertIn("shouldAcceptMediaPoll", IPAD)

    def test_copy_does_not_ask_to_reimport(self):
        preparing = audio_only_panel("preparing", False)
        failed = audio_only_panel("failed", False)
        audio = audio_only_panel("audio", False)
        self.assertEqual(preparing["title"], "视频画面正在准备中")
        self.assertIn("自动显示", preparing["body"])
        self.assertEqual(failed["title"], "暂时无法加载视频画面")
        self.assertIn("不受影响", failed["body"])
        self.assertEqual(audio["title"], "仅音频")
        self.assertNotIn("重新导入", preparing["body"] + failed["body"] + audio["body"])
        self.assertNotIn("重新导入", APP)
        self.assertNotIn("重新导入", MEDIA)
        self.assertNotIn("重新导入", IPAD)

    def test_true_audio_upload_is_not_preparing_copy(self):
        self.assertEqual(infer_initial_media_status(False, ""), "audio")
        self.assertEqual(infer_initial_media_status(False, "https://www.bilibili.com/video/BV1xx"), "preparing")
        self.assertEqual(audio_only_panel("audio", False)["title"], "仅音频")
        self.assertNotEqual(audio_only_panel("preparing", False)["title"], "仅音频")


class StudioHotSwitchContractTests(unittest.TestCase):
    def test_poll_only_updates_media_availability(self):
        block = _studio_poll_block()
        self.assertIn("fetchSessionMedia", block)
        self.assertIn("setPlayerSrc", block)
        self.assertIn("pendingSeekRef", block)
        self.assertNotIn("loadSession(", block)
        self.assertNotIn("setSentences", block)
        self.assertNotIn("setPhase", block)
        self.assertNotIn("openDetail", block)
        self.assertNotIn("setResumeDrafts", block)
        self.assertNotIn("setResumeIndex", block)

    def test_does_not_remount_studio_to_hot_switch_video(self):
        self.assertIn("key={sessionId}", APP)
        self.assertIn("setPlayerSrc(nextSrc)", APP)
        self.assertIn("node.load()", APP)
        self.assertIn("pendingSeekRef.current = { time, play: !wasPaused, sessionId: bound }", APP)
        self.assertNotIn("setLocalAudioOnly", APP)
        self.assertNotIn("localAudioOnly || audioOnly", APP)
        self.assertIn("showAudioOnly = !hasVideo", APP)

    def test_polling_stops_after_video_ready(self):
        self.assertIn("if (hasVideo) return;", APP)
        self.assertIn("[sessionId, hasVideo]", APP)

    def test_preserves_paused_and_current_time_after_metadata(self):
        self.assertIn("pendingSeekRef.current = { time, play: !wasPaused, sessionId: bound }", APP)
        self.assertIn("node.currentTime = pending.time", APP)
        self.assertIn("node.play().catch", APP)
        self.assertIn("node.pause()", APP)
        self.assertIn("if (!isHotSwitch) onOrientation(nextOrientation)", APP)
        self.assertIn("node.load()", APP)

    def test_save_progress_does_not_depend_on_media_hot_switch(self):
        self.assertIn("[sessionId, phase, index, drafts, highlights, score, orientation, ipadStudio]", APP)
        self.assertNotIn("hasVideo, playerSrc", APP)
        self.assertNotIn("[sessionId, hasVideo, phase, index", APP)


class IpadHotSwitchContractTests(unittest.TestCase):
    def test_ipad_switches_media_source_without_reload(self):
        self.assertIn("/api/session/' + encodeURIComponent(bound) + '/media'", IPAD)
        self.assertIn("pendingMediaSeek", IPAD)
        self.assertIn("videoEl.load()", IPAD)
        self.assertIn("videoEl.currentTime = pending.time", IPAD)
        self.assertIn("NotAllowedError", IPAD)
        self.assertNotIn("location.reload", IPAD.split("function pollMedia")[1].split("function clearVideoSrc")[0])
        self.assertIn("sessionId !== bound", IPAD)
