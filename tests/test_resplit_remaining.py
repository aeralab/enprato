import json
import tempfile
import unittest
from pathlib import Path

from backend.app.resplit import resplit_remaining_session, rollback_resplit_session


class ResplitRemainingTests(unittest.TestCase):
    def test_only_future_segments_change_and_rollback_restores_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            sentences = [
                {"id": i, "start": float(i), "end": float(i + 1), "text": f"frozen sentence {i}"}
                for i in range(100)
            ]
            long_text = "And, you know, there are many things I am critical about with Silicon Valley, but one thing that I think is good about it is this encouragement of learning from mistakes and trying again."
            sentences[40] = {"id": 40, "start": 40.0, "end": 60.0, "text": long_text}
            sentences[50] = {"id": 50, "start": 70.0, "end": 71.0, "text": "A short sentence stays unchanged."}
            meta = {"index": 37, "phase": "dictate", "drafts": {"0": "done", "37": "done"}, "score": {"content": 86}, "history": [{"index": 37}]}
            (folder / "sentences.json").write_text(json.dumps(sentences), encoding="utf-8")
            (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            before_frozen = json.loads((folder / "sentences.json").read_text(encoding="utf-8"))[:38]
            before_meta = (folder / "meta.json").read_bytes()

            result = resplit_remaining_session(folder)
            after = json.loads((folder / "sentences.json").read_text(encoding="utf-8"))
            self.assertEqual(result["cutoff"], 37)
            self.assertEqual(after[:38], before_frozen)
            self.assertEqual((folder / "meta.json").read_bytes(), before_meta)
            self.assertGreater(len(after), 100)
            self.assertEqual(after[-1]["text"], "frozen sentence 99")
            self.assertTrue(any(item.get("parent_id") == "resplit-40" for item in after[38:]))
            self.assertEqual(after[38 + len(after[38:]) - len(after[38:])]["start"], 38.0)
            manifest = json.loads((folder / "_resplit_backups" / result["backup_id"] / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["session_id"], folder.name)
            self.assertEqual(manifest["cutoff"], 37)
            self.assertEqual(manifest["frozen_through_index"], 37)
            self.assertAlmostEqual(manifest["progress_before"], 0.38)
            self.assertIn("sentences.json", manifest["files"])
            self.assertIn("meta.json", manifest["files"])
            self.assertEqual(json.loads((folder / "resplit_progress.json").read_text(encoding="utf-8"))["progress_floor"], 0.38)
            future_hash = json.dumps(after[38:], sort_keys=True)
            second = resplit_remaining_session(folder)
            after_second = json.loads((folder / "sentences.json").read_text(encoding="utf-8"))
            self.assertEqual(future_hash, json.dumps(after_second[38:], sort_keys=True))
            self.assertEqual(second["cutoff"], 37)

            rollback_resplit_session(folder, result["backup_id"])
            self.assertEqual(json.loads((folder / "sentences.json").read_text(encoding="utf-8")), sentences)
            self.assertEqual((folder / "meta.json").read_bytes(), before_meta)

    def test_future_draft_blocks_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "sentences.json").write_text(json.dumps([{"id": i, "start": i, "end": i + 1, "text": "sentence"} for i in range(4)]), encoding="utf-8")
            (folder / "meta.json").write_text(json.dumps({"index": 1, "drafts": {"2": "already started"}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "future sentences already contain drafts"):
                resplit_remaining_session(folder)


if __name__ == "__main__":
    unittest.main()
