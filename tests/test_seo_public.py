from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "frontend" / "public"
INDEX = ROOT / "frontend" / "index.html"


class PublicSeoFilesTests(unittest.TestCase):
    def test_sitemap_lists_only_public_home(self):
        text = (PUBLIC / "sitemap.xml").read_text(encoding="utf-8")
        self.assertIn('<?xml version="1.0" encoding="UTF-8"?>', text)
        self.assertIn("http://www.sitemaps.org/schemas/sitemap/0.9", text)
        self.assertIn("<loc>https://enprato.site/</loc>", text)
        self.assertNotIn("www.enprato.site", text)
        self.assertNotIn("/api/", text)
        self.assertNotIn("/ipad", text)
        self.assertNotIn("/remote", text)
        self.assertEqual(text.count("<loc>"), 1)
        self.assertNotIn("<!doctype html", text.lower())
        self.assertNotIn('id="root"', text)

    def test_robots_allows_public_and_points_to_sitemap(self):
        text = (PUBLIC / "robots.txt").read_text(encoding="utf-8")
        self.assertIn("User-agent: *", text)
        self.assertIn("Allow: /", text)
        self.assertIn("Disallow: /api/", text)
        self.assertIn("Sitemap: https://enprato.site/sitemap.xml", text)
        self.assertNotIn("www.enprato.site", text)

    def test_home_head_has_title_description_and_canonical(self):
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn("<title>Enprato - 高效学习外语</title>", html)
        self.assertIn('name="description"', html)
        self.assertIn("Enprato", html)
        self.assertIn("外语学习", html)
        self.assertIn("英语学习", html)
        self.assertIn("听写", html)
        self.assertIn("真实内容", html)
        self.assertIn('<link rel="canonical" href="https://enprato.site/" />', html)
        self.assertNotIn("www.enprato.site", html)
