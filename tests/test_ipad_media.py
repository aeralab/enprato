import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import ingest, media


TMP_ROOT = Path(__file__).resolve().parents[1] / ".tmp" / "tests"


class IpadMediaTests(unittest.TestCase):
    def test_large_mp4_is_not_treated_as_ipad_ready(self):
        with patch("backend.app.media.stream_codec", side_effect=["h264", "aac"]), patch(
            "backend.app.media.stream_dimensions", return_value=(1920, 1080)
        ):
            self.assertFalse(media.is_ipad_media(Path("source.mp4")))

    def test_reuses_ipad_compatible_mp4(self):
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TMP_ROOT) as tmp:
            folder = Path(tmp)
            source = folder / "source.mp4"
            source.write_bytes(b"mp4")
            with patch("backend.app.ingest.is_ipad_media", return_value=True), patch(
                "backend.app.ingest.media_has_audio", return_value=True
            ), patch("backend.app.ingest.make_browser_mp4") as make_browser_mp4:
                self.assertEqual(ingest.find_session_media(folder), source)
                make_browser_mp4.assert_not_called()

    def test_transcodes_non_ipad_video_to_playable_mp4(self):
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TMP_ROOT) as tmp:
            folder = Path(tmp)
            source = folder / "source.webm"
            source.write_bytes(b"webm")

            def fake_make_browser_mp4(src: Path, dest: Path) -> Path:
                self.assertEqual(src, source)
                dest.write_bytes(b"playable mp4")
                return dest

            with patch("backend.app.ingest.is_ipad_media", return_value=False), patch(
                "backend.app.ingest.media_has_audio", return_value=True
            ), patch("backend.app.ingest.stream_codec", return_value="vp9"), patch(
                "backend.app.ingest.make_browser_mp4", side_effect=fake_make_browser_mp4
            ):
                media = ingest.find_session_media(folder)
                self.assertEqual(media, folder / "playable.mp4")
                self.assertTrue(media.is_file())


if __name__ == "__main__":
    unittest.main()
