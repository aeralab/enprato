import unittest

from backend.app.aliyun_asr import (
    aliyun_transcription_to_sentences,
    transcription_has_sentence_timestamps,
    transcription_has_word_timestamps,
)


SAMPLE = {
    "file_url": "https://example.invalid/audio.m4a",
    "properties": {
        "audio_format": "aac",
        "original_duration_in_milliseconds": 3834,
    },
    "transcripts": [
        {
            "channel_id": 0,
            "text": "Hello world, this is a dictation sample.",
            "sentences": [
                {
                    "begin_time": 100,
                    "end_time": 3820,
                    "text": "Hello world, this is a dictation sample.",
                    "words": [
                        {"begin_time": 100, "end_time": 596, "text": "Hello ", "punctuation": ""},
                        {"begin_time": 596, "end_time": 844, "text": "world", "punctuation": ", "},
                        {"begin_time": 844, "end_time": 1200, "text": "this", "punctuation": ""},
                        {"begin_time": 1200, "end_time": 1500, "text": "is", "punctuation": ""},
                        {"begin_time": 1500, "end_time": 1800, "text": "a", "punctuation": ""},
                        {"begin_time": 1800, "end_time": 2400, "text": "dictation", "punctuation": ""},
                        {"begin_time": 2400, "end_time": 3820, "text": "sample", "punctuation": "."},
                    ],
                }
            ],
        }
    ],
}


class AliyunAsrAdapterTests(unittest.TestCase):
    def test_word_and_sentence_timestamps_detected(self):
        self.assertTrue(transcription_has_word_timestamps(SAMPLE))
        self.assertTrue(transcription_has_sentence_timestamps(SAMPLE))

    def test_maps_to_enprato_sentence_schema(self):
        sentences = aliyun_transcription_to_sentences(SAMPLE)
        self.assertGreaterEqual(len(sentences), 1)
        first = sentences[0]
        self.assertIn("id", first)
        self.assertIn("start", first)
        self.assertIn("end", first)
        self.assertIn("text", first)
        self.assertLess(first["start"], first["end"])
        self.assertIn("Hello", first["text"])
        self.assertAlmostEqual(first["start"], 0.1, places=3)

    def test_sentence_only_fallback_without_words(self):
        payload = {
            "transcripts": [
                {
                    "sentences": [
                        {
                            "begin_time": 1000,
                            "end_time": 4000,
                            "text": "Hello world.",
                            "words": [],
                        }
                    ]
                }
            ]
        }
        self.assertFalse(transcription_has_word_timestamps(payload))
        sentences = aliyun_transcription_to_sentences(payload)
        self.assertEqual(len(sentences), 1)
        self.assertEqual(sentences[0]["text"], "Hello world.")
        self.assertAlmostEqual(sentences[0]["start"], 1.0, places=3)
        self.assertAlmostEqual(sentences[0]["end"], 4.0, places=3)

    def test_empty_payload(self):
        self.assertEqual(aliyun_transcription_to_sentences({}), [])
        self.assertEqual(aliyun_transcription_to_sentences({"transcripts": []}), [])
