import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from backend.app import asr


def _silence_wav(seconds: float = 1.0) -> Path:
    tmp = Path(tempfile.mkdtemp()) / "clip.wav"
    with wave.open(str(tmp), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * int(16000 * seconds))
    return tmp


class PromptLeakageTests(unittest.TestCase):
    def test_no_extra_sentences_period_is_leakage(self):
        hit = asr.detect_prompt_leakage("No extra sentences.", "And often we see behaviors")
        self.assertTrue(hit["is_leakage"])

    def test_no_extra_sentences_without_period_is_leakage(self):
        hit = asr.detect_prompt_leakage("No extra sentences", "Come on.")
        self.assertTrue(hit["is_leakage"])

    def test_english_dictation_is_leakage(self):
        hit = asr.detect_prompt_leakage("English dictation.", "Come on.")
        self.assertTrue(hit["is_leakage"])

    def test_correct_spelling_is_leakage(self):
        hit = asr.detect_prompt_leakage("Correct spelling.", "Thank you.")
        self.assertTrue(hit["is_leakage"])

    def test_expected_to_be_expected_is_contamination(self):
        hit = asr.detect_prompt_leakage(
            "Expected to be expected.",
            "That is to be expected.",
        )
        self.assertTrue(hit["is_leakage"])

    def test_exact_target_that_is_to_be_expected_passes(self):
        hit = asr.detect_prompt_leakage(
            "That is to be expected.",
            "That is to be expected.",
        )
        self.assertFalse(hit["is_leakage"])

    def test_come_on_passes(self):
        hit = asr.detect_prompt_leakage("Come on.", "Come on.")
        self.assertFalse(hit["is_leakage"])
        hit_other_target = asr.detect_prompt_leakage("Come on.", "That is to be expected.")
        self.assertFalse(hit_other_target["is_leakage"])

    def test_thank_you_passes(self):
        hit = asr.detect_prompt_leakage("Thank you.", "Thank you.")
        self.assertFalse(hit["is_leakage"])

    def test_normal_long_sentence_passes(self):
        spoken = "And often we see behaviors that surprise us in the moment."
        hit = asr.detect_prompt_leakage(spoken, spoken)
        self.assertFalse(hit["is_leakage"])
        hit_mismatch = asr.detect_prompt_leakage(spoken, "That is to be expected.")
        self.assertFalse(hit_mismatch["is_leakage"])

    def test_compact_prompt_has_no_command_labels_or_full_target(self):
        target = "That is to be expected."
        prompt = asr._build_prompt(target=target, context="", compact=True)
        lowered = prompt.lower()
        self.assertNotIn("no extra sentences", lowered)
        self.assertNotIn("expected:", lowered)
        self.assertNotIn("english dictation", lowered)
        self.assertNotIn("correct spelling", lowered)
        self.assertNotIn(target.lower(), lowered)


class AsrPipelineTests(unittest.TestCase):
    def test_long_audio_low_coverage_gets_one_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "twenty-seconds.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(b"\x00\x00" * 320000)

            calls = []

            def fake_run(*_args, **kwargs):
                calls.append(kwargs["vad"])
                if len(calls) == 1:
                    return {"text": "only the first clause", "segment_count": 1, "last_end": 6.0}
                return {"text": "the complete long utterance after fallback", "segment_count": 2, "last_end": 19.2}

            with patch.object(asr, "get_model", return_value=object()), patch.object(asr, "_warm_model_once"), patch.object(asr, "_run_transcribe_detailed", side_effect=fake_run):
                result = asr.transcribe_speech_detailed(path, target="a long expected sentence with many words for testing", fast=True)

            self.assertEqual(calls, [False, True])
            self.assertTrue(result["retried"])
            self.assertEqual(result["retry_reason"], "low_coverage")
            self.assertEqual(result["last_end"], 19.2)
            self.assertEqual(result["text"], "The complete long utterance after fallback")
            self.assertEqual(result["prompt_guard_result"], "ok")

    def test_prompt_leakage_fallback_returns_clean_text(self):
        path = _silence_wav(1.0)
        calls = []

        def fake_run(_model, _audio, prompt, **kwargs):
            calls.append({"prompt": prompt, "vad": kwargs.get("vad"), "hotwords": kwargs.get("hotwords")})
            if len(calls) == 1:
                return {"text": "No extra sentences.", "segment_count": 1, "last_end": 0.8}
            return {"text": "That is to be expected.", "segment_count": 1, "last_end": 0.9}

        with patch.object(asr, "get_model", return_value=object()), patch.object(asr, "_warm_model_once"), patch.object(asr, "_run_transcribe_detailed", side_effect=fake_run):
            result = asr.transcribe_speech_detailed(path, target="That is to be expected.", fast=True)

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["prompt"], "")
        self.assertEqual(calls[1]["prompt"], "")
        self.assertFalse(calls[1]["hotwords"])
        self.assertEqual(result["text"], "That is to be expected.")
        self.assertEqual(result["prompt_guard_result"], "fallback_success")
        self.assertTrue(result["retried"])
        self.assertEqual(result["retry_reason"], "prompt_leakage")
        self.assertFalse(result.get("code"))

    def test_prompt_leakage_fallback_failure_does_not_return_garbage(self):
        path = _silence_wav(1.0)
        calls = []

        def fake_run(*_args, **_kwargs):
            calls.append(1)
            return {"text": "No extra sentences.", "segment_count": 1, "last_end": 0.8}

        with patch.object(asr, "get_model", return_value=object()), patch.object(asr, "_warm_model_once"), patch.object(asr, "_run_transcribe_detailed", side_effect=fake_run):
            result = asr.transcribe_speech_detailed(path, target="That is to be expected.", fast=True)

        self.assertEqual(len(calls), 2)
        self.assertEqual(result["text"], "")
        self.assertEqual(result["code"], "prompt_leakage")
        self.assertEqual(result["prompt_guard_result"], "fallback_failed")
        self.assertNotIn("no extra sentences", result["text"].lower())


if __name__ == "__main__":
    unittest.main()
