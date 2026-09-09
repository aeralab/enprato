import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.aliyun_asr import aliyun_transcription_to_sentences
from backend.app.dashscope_asr import (
    AliyunASRError,
    AliyunASRResult,
    language_hints,
    transcribe_url_import,
)


class DashscopeConfigTests(unittest.TestCase):
    def test_language_hints_default_is_not_english_only(self):
        old = os.environ.get("DASHSCOPE_LANGUAGE_HINTS")
        try:
            os.environ.pop("DASHSCOPE_LANGUAGE_HINTS", None)
            self.assertEqual(language_hints(), ["zh", "en"])
            os.environ["DASHSCOPE_LANGUAGE_HINTS"] = "auto"
            self.assertEqual(language_hints(), ["zh", "en"])
            os.environ["DASHSCOPE_LANGUAGE_HINTS"] = "ja"
            self.assertEqual(language_hints(), ["ja"])
            self.assertEqual(language_hints(["fr", "de"]), ["fr", "de"])
        finally:
            if old is None:
                os.environ.pop("DASHSCOPE_LANGUAGE_HINTS", None)
            else:
                os.environ["DASHSCOPE_LANGUAGE_HINTS"] = old

    def test_transcribe_url_import_falls_back_when_aliyun_raises(self):
        folder = Path(tempfile.mkdtemp())
        audio = folder / "playback.m4a"
        audio.write_bytes(b"m4a" * 400)
        extracted: list[str] = []

        def fail(_self, _path, hints=None):
            raise AliyunASRError("aliyun_submit", "http=500")

        def whisper(_path):
            return [{"id": 0, "start": 0, "end": 1, "text": "fallback"}]

        def extract(src, dest):
            extracted.append(dest.name)
            dest.write_bytes(b"RIFF")

        old_backend = os.environ.get("ENPRATO_ASR_BACKEND")
        old_key = os.environ.get("DASHSCOPE_API_KEY")
        try:
            os.environ["ENPRATO_ASR_BACKEND"] = "aliyun"
            os.environ["DASHSCOPE_API_KEY"] = "test-not-a-real-key"
            with patch("backend.app.dashscope_asr.AliyunASRBackend.transcribe", fail):
                sentences = transcribe_url_import(
                    audio,
                    folder=folder,
                    job_id="job1",
                    session_id="sess1",
                    host="bilibili.com",
                    whisper_fn=whisper,
                    extract_wav_fn=extract,
                )
        finally:
            if old_backend is None:
                os.environ.pop("ENPRATO_ASR_BACKEND", None)
            else:
                os.environ["ENPRATO_ASR_BACKEND"] = old_backend
            if old_key is None:
                os.environ.pop("DASHSCOPE_API_KEY", None)
            else:
                os.environ["DASHSCOPE_API_KEY"] = old_key
        self.assertEqual(sentences[0]["text"], "fallback")
        self.assertEqual(extracted, ["audio.wav"])

    def test_adapter_result_is_enprato_schema(self):
        payload = {
            "transcripts": [
                {
                    "sentences": [
                        {
                            "begin_time": 100,
                            "end_time": 1500,
                            "text": "Hello world.",
                            "words": [
                                {"begin_time": 100, "end_time": 700, "text": "Hello", "punctuation": " "},
                                {"begin_time": 700, "end_time": 1500, "text": "world", "punctuation": "."},
                            ],
                        }
                    ]
                }
            ]
        }
        result = AliyunASRResult(sentences=aliyun_transcription_to_sentences(payload))
        self.assertEqual(result.sentences[0]["id"], 0)
        self.assertIn("start", result.sentences[0])
        self.assertIn("text", result.sentences[0])
