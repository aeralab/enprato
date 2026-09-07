import unittest

from backend.app.store import resume_sentence_index


class ResumeIndexTests(unittest.TestCase):
    def test_stale_pointer_jumps_to_first_unfinished(self):
        drafts = {i: "done" for i in range(87)}
        self.assertEqual(resume_sentence_index(100, drafts, 0), 87)
        self.assertEqual(resume_sentence_index(100, drafts, 11), 87)

    def test_keeps_current_unfinished_sentence(self):
        drafts = {i: "done" for i in range(87)}
        self.assertEqual(resume_sentence_index(100, drafts, 87), 87)

    def test_stays_on_in_progress_filled_sentence(self):
        drafts = {i: "done" for i in range(88)}
        self.assertEqual(resume_sentence_index(100, drafts, 87), 87)

    def test_keeps_jump_ahead_without_filling_gaps(self):
        drafts = {i: "done" for i in range(10)}
        self.assertEqual(resume_sentence_index(100, drafts, 50), 50)

    def test_all_complete_lands_on_last(self):
        drafts = {i: "done" for i in range(12)}
        self.assertEqual(resume_sentence_index(12, drafts, 0), 11)

    def test_empty_session_stays_at_start(self):
        self.assertEqual(resume_sentence_index(12, {}, 0), 0)


if __name__ == "__main__":
    unittest.main()
