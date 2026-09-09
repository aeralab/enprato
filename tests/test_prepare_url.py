import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import db, main, url_import_jobs
from job_wait import wait_ready


class PrepareUrlIsolationTests(unittest.TestCase):
    def test_create_new_session_does_not_touch_reused_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_db, old_data = db.DB_PATH, main.DATA
            old_auth = os.environ.get("ENPRATO_REQUIRE_AUTH")
            old_asr = os.environ.get("ENPRATO_ASR_BACKEND")
            try:
                os.environ.pop("ENPRATO_REQUIRE_AUTH", None)
                os.environ["ENPRATO_ASR_BACKEND"] = "whisper"
                db.DB_PATH = Path(tmp) / "sessions.sqlite3"
                main.DATA = Path(tmp) / "sessions"
                main.DATA.mkdir()
                db.migrate()
                old_id = "old-session"
                db.register_learning_session(old_id, "lan-local")
                old_folder = main.DATA / old_id
                old_folder.mkdir()
                old_sentences = [{"id": 0, "start": 0, "end": 1, "text": "old segment"}]
                (old_folder / "sentences.json").write_text(json.dumps(old_sentences), encoding="utf-8")
                (old_folder / "meta.json").write_text(json.dumps({"title": "old", "source_url": "https://example.com/video"}), encoding="utf-8")
                (old_folder / "source.mp4").write_bytes(b"old")
                (old_folder / "audio.wav").write_bytes(b"old")

                def fake_ingest(_url, folder, **_kwargs):
                    audio = folder / "audio.wav"
                    audio.write_bytes(b"new")
                    return None, audio, "WEBVTT\n\n00:00:00.000 --> 00:00:04.000\nA newly imported sentence, with a natural split, for testing.\n"

                with patch.object(main, "require_member_or_trial"), patch.object(main, "ingest_url", side_effect=fake_ingest), patch.object(main, "fetch_media_title", return_value="new"):
                    client = TestClient(main.app)
                    reused = client.post("/api/prepare-url", json={"url": "https://example.com/video"})
                    created = client.post("/api/prepare-url", json={"url": "https://example.com/video", "create_new_session": True})
                    created_detail = wait_ready(client, created)

                self.assertEqual(reused.status_code, 200)
                self.assertEqual(reused.json()["session_id"], old_id)
                self.assertEqual(created.status_code, 200)
                new_id = created_detail["session_id"]
                self.assertNotEqual(new_id, old_id)
                self.assertEqual(created_detail["index"], 0)
                self.assertEqual(json.loads((old_folder / "sentences.json").read_text(encoding="utf-8")), old_sentences)
                self.assertEqual((old_folder / "audio.wav").read_bytes(), b"old")
                self.assertTrue((main.DATA / new_id / "sentences.json").is_file())
            finally:
                try:
                    url_import_jobs.wait_idle(20)
                except Exception:
                    pass
                db.DB_PATH, main.DATA = old_db, old_data
                if old_auth is None:
                    os.environ.pop("ENPRATO_REQUIRE_AUTH", None)
                else:
                    os.environ["ENPRATO_REQUIRE_AUTH"] = old_auth
                if old_asr is None:
                    os.environ.pop("ENPRATO_ASR_BACKEND", None)
                else:
                    os.environ["ENPRATO_ASR_BACKEND"] = old_asr


if __name__ == "__main__":
    unittest.main()
