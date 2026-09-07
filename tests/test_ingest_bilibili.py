import json
import tempfile
import unittest
from pathlib import Path

from backend.app.ingest import is_retryable_ytdlp_error, ytdlp_cmd_variants
from backend.app.store import find_session_id_by_url


class BilibiliIngestTests(unittest.TestCase):
    def test_412_is_retryable_for_bilibili(self):
        err = "ERROR: [BiliBili] 1CMjq6nEu1: Unable to download webpage: HTTP Error 412: Precondition Failed"
        self.assertTrue(is_retryable_ytdlp_error("https://www.bilibili.com/video/BV1CMjq6nEu1/", err))
        self.assertFalse(is_retryable_ytdlp_error("https://www.youtube.com/watch?v=abc", err))

    def test_bilibili_variants_include_direct_proxy(self):
        base = ["yt-dlp", "--no-playlist"]
        variants = ytdlp_cmd_variants(base, "https://www.bilibili.com/video/BV1CMjq6nEu1/?spm_id_from=333.337")
        self.assertTrue(any(cmd[i : i + 2] == ["--proxy", ""] for cmd in variants for i in range(len(cmd) - 1)))
        self.assertGreaterEqual(len(variants), 2)

    def test_find_session_by_bvid_ignores_tracking_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "oldbili"
            folder.mkdir()
            (folder / "sentences.json").write_text("[]", encoding="utf-8")
            (folder / "meta.json").write_text(
                json.dumps(
                    {
                        "title": "old",
                        "source_url": "https://www.bilibili.com/video/BV1CMjq6nEu1/?spm_id_from=333.337.search-card.all.click",
                    }
                ),
                encoding="utf-8",
            )
            found = find_session_id_by_url(root, "https://www.bilibili.com/video/BV1CMjq6nEu1/")
            self.assertEqual(found, "oldbili")


if __name__ == "__main__":
    unittest.main()
