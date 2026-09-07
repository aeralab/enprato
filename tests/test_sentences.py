import unittest
from types import SimpleNamespace

from backend.app.sentences import (
    HARD_DISPLAY_UNITS,
    _display_units,
    parse_srt,
    split_long_text,
    split_timed_sentences,
    words_to_sentences,
)


class SentenceSplitTests(unittest.TestCase):
    def assert_recombined(self, text: str):
        parts = split_long_text(text)
        self.assertGreaterEqual(len(parts), 1)
        self.assertEqual(" ".join(parts), " ".join(text.split()))
        self.assertTrue(all(len(part.split()) >= 5 for part in parts))
        self.assertTrue(all(_display_units(part) <= HARD_DISPLAY_UNITS for part in parts))
        return parts

    def test_short_one_and_two_line_text_is_unchanged(self):
        self.assertEqual(split_long_text("This is a short sentence."), ["This is a short sentence."])
        self.assertEqual(split_long_text("This is a comfortable sentence for dictation."), ["This is a comfortable sentence for dictation."])

    def test_long_comma_sentence_uses_natural_boundaries(self):
        text = "Although I had been studying English for several years, I still found it difficult to understand native speakers when they spoke quickly, especially when I was watching movies or listening to podcasts."
        parts = self.assert_recombined(text)
        self.assertGreater(len(parts), 1)
        self.assertTrue(parts[0].endswith(","))

    def test_real_long_clause_splits_before_but(self):
        text = "And, you know, there's a lot of things I'm actually very critical about with Silicon Valley, but one thing that I think is good about it is this encouragement of, like, you know, it doesn't matter if all the experts are against you."
        parts = self.assert_recombined(text)
        self.assertGreaterEqual(len(parts), 2)
        self.assertIn("Silicon Valley,", parts[0])
        self.assertTrue(parts[1].lower().startswith("but one thing"))
        self.assertLessEqual(len(parts[0].split()), 29)
        self.assertLessEqual(len(parts[1].split()), 29)

    def test_long_text_without_punctuation_is_bounded(self):
        text = "This is a very long sentence without punctuation that should still be divided into comfortable chunks for a learner to listen to and write down carefully"
        self.assertGreater(len(self.assert_recombined(text)), 1)

    def test_names_numbers_abbreviations_quotes_questions_and_exclamations(self):
        text = 'Dr. Smith from New York City said, "In 2026, the U.S. team will visit London," and everyone shouted, "Really?"'
        self.assert_recombined(text)

    def test_timed_split_preserves_audio_range(self):
        items = split_timed_sentences([{"start": 10.0, "end": 20.0, "text": "One very long sentence, with several natural pauses, should become multiple listening units for practice, especially when the learner is working with unfamiliar vocabulary."}])
        self.assertGreater(len(items), 1)
        self.assertEqual(items[0]["start"], 10.0)
        self.assertEqual(items[-1]["end"], 20.0)
        for previous, current in zip(items, items[1:]):
            self.assertLessEqual(previous["end"], current["start"])
            self.assertGreater(previous["end"], previous["start"])
        self.assertEqual(" ".join(item["text"] for item in items), "One very long sentence, with several natural pauses, should become multiple listening units for practice, especially when the learner is working with unfamiliar vocabulary.")

    def test_srt_long_cue_is_split_and_timeline_is_contiguous(self):
        raw = "1\n00:00:01,000 --> 00:00:12,000\nAlthough this subtitle cue is long, it should be split at a natural boundary so the learner can dictate it comfortably.\n"
        items = parse_srt(raw)
        self.assertGreater(len(items), 1)
        self.assertEqual(items[0]["start"], 1.0)
        self.assertEqual(items[-1]["end"], 12.0)
        self.assertEqual(" ".join(item["text"] for item in items), "Although this subtitle cue is long, it should be split at a natural boundary so the learner can dictate it comfortably.")

    def test_word_timestamps_are_used_for_asr_split(self):
        raw = "One long ASR segment, with enough words to require a natural split, keeps the exact word timing for every listening unit."
        words = []
        cursor = 30.0
        for token in raw.split():
            token_text = (" " if words else "") + token
            start = cursor
            cursor += 0.4
            words.append(SimpleNamespace(word=token_text, start=start, end=cursor))
        items = words_to_sentences(words, pause=10.0, max_dur=60.0)
        self.assertGreater(len(items), 1)
        self.assertEqual(items[0]["start"], 30.0)
        self.assertAlmostEqual(items[-1]["end"], words[-1].end)
        self.assertEqual(" ".join(item["text"] for item in items), raw)


if __name__ == "__main__":
    unittest.main()
