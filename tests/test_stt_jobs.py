import unittest

from backend.app import stt_jobs


class SttJobsTests(unittest.TestCase):
    def setUp(self) -> None:
        stt_jobs.clear()

    def tearDown(self) -> None:
        stt_jobs.clear()

    def test_begin_then_finish_replays_same_result(self):
        action, cached = stt_jobs.begin("u1", "stt_abc")
        self.assertEqual(action, "start")
        self.assertIsNone(cached)
        inflight, _ = stt_jobs.begin("u1", "stt_abc")
        self.assertEqual(inflight, "inflight")
        stt_jobs.finish("u1", "stt_abc", {"text": "hello"})
        replay, result = stt_jobs.begin("u1", "stt_abc")
        self.assertEqual(replay, "replay")
        self.assertEqual(result, {"text": "hello"})

    def test_fail_allows_new_start(self):
        stt_jobs.begin("u1", "stt_abc")
        stt_jobs.fail("u1", "stt_abc")
        action, cached = stt_jobs.begin("u1", "stt_abc")
        self.assertEqual(action, "start")
        self.assertIsNone(cached)

    def test_empty_id_never_locks(self):
        self.assertEqual(stt_jobs.begin("u1", "")[0], "start")
        self.assertEqual(stt_jobs.begin("u1", "")[0], "start")
