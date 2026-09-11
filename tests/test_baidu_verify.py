from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFY_NAME = "baidu_verify_codeva-whZAtsrhf6.html"
PUBLIC = ROOT / "frontend" / "public" / VERIFY_NAME


class BaiduVerifyFileTests(unittest.TestCase):
    def test_public_file_keeps_baidu_name_and_is_not_spa_index(self):
        self.assertTrue(PUBLIC.is_file(), f"missing {PUBLIC}")
        self.assertEqual(PUBLIC.name, VERIFY_NAME)
        data = PUBLIC.read_bytes()
        self.assertGreater(len(data), 0)
        self.assertLess(len(data), 200)
        lowered = data.lower()
        self.assertNotIn(b"<!doctype html", lowered)
        self.assertNotIn(b"<div id=\"root\">", lowered)
        self.assertNotIn(b"enprato", lowered)

    def test_vite_public_dir_is_default(self):
        config = (ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
        self.assertNotIn("publicDir", config)
