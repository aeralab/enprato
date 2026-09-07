import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import db, main, stt_jobs
from backend.app.auth import hash_password


def _tiny_wav() -> bytes:
    import struct

    samples = b"\x00\x00" * 1600
    return b"".join(
        [
            b"RIFF",
            struct.pack("<I", 36 + len(samples)),
            b"WAVEfmt ",
            struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16),
            b"data",
            struct.pack("<I", len(samples)),
            samples,
        ]
    )


ASR_OK = {"text": "hello world", "segment_count": 1, "last_end": 1.0, "retried": False, "retry_reason": ""}


class RemoteSttTests(unittest.TestCase):
    def setUp(self) -> None:
        stt_jobs.clear()
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._old_db, self._old_data = db.DB_PATH, main.DATA
        db.DB_PATH = tmp / "stt.sqlite3"
        main.DATA = tmp / "sessions"
        main.DATA.mkdir()
        db.migrate()
        self.user = db.create_user("stt@example.com", hash_password("password123"))
        self.sid = "stt-session"
        db.register_learning_session(self.sid, self.user["id"])
        folder = main.DATA / self.sid
        folder.mkdir()
        (folder / "sentences.json").write_text(
            json.dumps([{"id": 0, "start": 0, "end": 2, "text": "hello world"}]),
            encoding="utf-8",
        )
        (folder / "meta.json").write_text(json.dumps({"title": "t", "drafts": {}, "index": 0}), encoding="utf-8")
        self.client = TestClient(main.app)
        self.assertEqual(
            self.client.post("/api/auth/login", json={"email": "stt@example.com", "password": "password123"}).status_code,
            200,
        )

    def tearDown(self) -> None:
        stt_jobs.clear()
        db.DB_PATH, main.DATA = self._old_db, self._old_data
        self._tmp.cleanup()

    def _bin(self, body: bytes, headers: dict | None = None, method: str = "POST"):
        hdrs = {"Content-Type": "audio/wav"}
        if headers:
            hdrs.update(headers)
        url = f"/api/session/{self.sid}/remote-stt-bin?index=0&mode=insert"
        if method == "PUT":
            return self.client.put(url, content=body, headers=hdrs)
        return self.client.post(url, content=body, headers=hdrs)

    def test_remote_stt_returns_request_id_header(self):
        with patch("backend.app.main.convert_to_wav"), patch(
            "backend.app.main.probe_duration", return_value=1.0
        ), patch(
            "backend.app.main.transcribe_speech_detailed",
            return_value=ASR_OK,
        ), patch("backend.app.main.require_member_or_trial"):
            res = self.client.post(
                f"/api/session/{self.sid}/remote-stt",
                files={"audio": ("clip.wav", _tiny_wav(), "audio/wav")},
                data={"index": "0", "mode": "insert"},
            )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.headers.get("X-Request-ID"))
        self.assertEqual(res.headers.get("X-Request-ID"), res.json().get("request_id"))
        self.assertTrue(str(res.json().get("text") or "").strip())
        with patch("backend.app.main.convert_to_wav"), patch(
            "backend.app.main.probe_duration", return_value=1.0
        ), patch(
            "backend.app.main.transcribe_speech_detailed",
            return_value=ASR_OK,
        ), patch("backend.app.main.require_member_or_trial"):
            bin_res = self._bin(_tiny_wav(), method="PUT")
        self.assertEqual(bin_res.status_code, 200, bin_res.text)
        self.assertTrue(bin_res.headers.get("X-Request-ID"))
        self.assertEqual(bin_res.headers.get("X-Request-ID"), bin_res.json().get("request_id"))

    def test_bin_echoes_client_request_id(self):
        cid = "stt_test_echo_1"
        with patch("backend.app.main.convert_to_wav"), patch(
            "backend.app.main.probe_duration", return_value=1.0
        ), patch("backend.app.main.transcribe_speech_detailed", return_value=ASR_OK), patch(
            "backend.app.main.require_member_or_trial"
        ):
            res = self._bin(_tiny_wav(), headers={"X-Client-Request-ID": cid})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.headers.get("X-Client-Request-ID"), cid)
        self.assertEqual(res.json().get("client_request_id"), cid)
        self.assertTrue(res.headers.get("X-Request-ID"))

    def test_empty_blob_is_400(self):
        with patch("backend.app.main.require_member_or_trial"):
            res = self._bin(b"", headers={"X-Client-Request-ID": "stt_empty"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.headers.get("X-Client-Request-ID"), "stt_empty")
        self.assertTrue(res.headers.get("X-Request-ID"))

    def test_invalid_mime_is_400(self):
        with patch("backend.app.main.require_member_or_trial"):
            res = self._bin(_tiny_wav(), headers={"Content-Type": "application/json", "X-Client-Request-ID": "stt_mime"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("无效", res.text)

    def test_corrupt_audio_is_400(self):
        with patch("backend.app.main.convert_to_wav", side_effect=RuntimeError("bad wav")), patch(
            "backend.app.main.probe_duration", return_value=0.0
        ), patch("backend.app.main.require_member_or_trial"):
            res = self._bin(_tiny_wav(), headers={"X-Client-Request-ID": "stt_corrupt"})
        self.assertEqual(res.status_code, 400)

    def test_too_large_is_413(self):
        with patch("backend.app.main.require_member_or_trial"):
            res = self._bin(b"x" * (25 * 1024 * 1024 + 201), headers={"X-Client-Request-ID": "stt_huge"})
        self.assertEqual(res.status_code, 413)

    def test_duplicate_client_request_id_does_not_rerun_asr(self):
        calls: list[int] = []

        def fake_asr(*_a, **_k):
            calls.append(1)
            return ASR_OK

        cid = "stt_idem_1"
        with patch("backend.app.main.convert_to_wav"), patch(
            "backend.app.main.probe_duration", return_value=1.0
        ), patch("backend.app.main.transcribe_speech_detailed", side_effect=fake_asr), patch(
            "backend.app.main.require_member_or_trial"
        ):
            first = self._bin(_tiny_wav(), headers={"X-Client-Request-ID": cid})
            second = self._bin(_tiny_wav(), headers={"X-Client-Request-ID": cid})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json().get("text"), second.json().get("text"))
        self.assertEqual(len(calls), 1)

    def test_inflight_same_id_is_409(self):
        cid = "stt_inflight_1"
        stt_jobs.begin(self.user["id"], cid)
        with patch("backend.app.main.transcribe_speech_detailed") as asr, patch("backend.app.main.require_member_or_trial"):
            res = self._bin(_tiny_wav(), headers={"X-Client-Request-ID": cid})
        self.assertEqual(res.status_code, 409)
        asr.assert_not_called()

    def test_asr_exception_is_500(self):
        with patch("backend.app.main.convert_to_wav"), patch(
            "backend.app.main.probe_duration", return_value=1.0
        ), patch("backend.app.main.transcribe_speech_detailed", side_effect=RuntimeError("boom")), patch(
            "backend.app.main.require_member_or_trial"
        ):
            res = self._bin(_tiny_wav(), headers={"X-Client-Request-ID": "stt_500"})
        self.assertEqual(res.status_code, 500)
        self.assertTrue(res.headers.get("X-Request-ID"))
        self.assertEqual(res.headers.get("X-Client-Request-ID"), "stt_500")

    def test_asr_timeout_is_504(self):
        async def boom(*_a, **_k):
            raise asyncio.TimeoutError()

        with patch("backend.app.main.convert_to_wav"), patch(
            "backend.app.main.probe_duration", return_value=1.0
        ), patch("backend.app.main.asyncio.wait_for", side_effect=boom), patch(
            "backend.app.main.require_member_or_trial"
        ):
            res = self._bin(_tiny_wav(), headers={"X-Client-Request-ID": "stt_timeout"})
        self.assertEqual(res.status_code, 504)

    def test_prompt_leakage_fallback_success_keeps_client_request_id(self):
        calls: list[int] = []
        cid = "stt_leak_ok_1"
        folder = main.DATA / self.sid
        (folder / "sentences.json").write_text(
            json.dumps([{"id": 0, "start": 0, "end": 2, "text": "That is to be expected."}]),
            encoding="utf-8",
        )

        def fake_asr(*_a, **_k):
            calls.append(1)
            return {
                "text": "That is to be expected.",
                "code": "",
                "prompt_guard_result": "fallback_success",
                "retried": True,
                "retry_reason": "prompt_leakage",
                "segment_count": 1,
                "last_end": 1.0,
                "fast": True,
                "prompt_mode": "compact",
                "compact_prompt_enabled": True,
                "expected_target_len": 24,
                "expected_target_preview": "That is to be expected.",
            }

        with patch("backend.app.main.convert_to_wav"), patch(
            "backend.app.main.probe_duration", return_value=1.0
        ), patch("backend.app.main.transcribe_speech_detailed", side_effect=fake_asr), patch(
            "backend.app.main.require_member_or_trial"
        ):
            res = self._bin(_tiny_wav(), headers={"X-Client-Request-ID": cid})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(res.headers.get("X-Client-Request-ID"), cid)
        self.assertEqual(res.json().get("client_request_id"), cid)
        self.assertEqual(res.json().get("request_id"), res.headers.get("X-Request-ID"))
        self.assertEqual(res.json().get("text"), "That is to be expected.")

    def test_prompt_leakage_fallback_failure_does_not_return_garbage(self):
        cid = "stt_leak_fail_1"
        with patch("backend.app.main.convert_to_wav"), patch(
            "backend.app.main.probe_duration", return_value=1.0
        ), patch(
            "backend.app.main.transcribe_speech_detailed",
            return_value={
                "text": "",
                "code": "prompt_leakage",
                "prompt_guard_result": "fallback_failed",
                "retried": True,
                "retry_reason": "prompt_leakage",
                "segment_count": 1,
                "last_end": 0.4,
                "fast": True,
                "prompt_mode": "compact",
                "compact_prompt_enabled": True,
            },
        ), patch("backend.app.main.require_member_or_trial"):
            res = self._bin(_tiny_wav(), headers={"X-Client-Request-ID": cid})
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body.get("code"), "prompt_leakage")
        self.assertEqual(body.get("text"), "")
        self.assertEqual(body.get("client_request_id"), cid)
        self.assertNotIn("No extra sentences", res.text)
        self.assertIn("这次没有听清", body.get("message", ""))

    def test_failed_job_can_retry_same_client_id(self):
        cid = "stt_retry_after_fail"
        with patch("backend.app.main.convert_to_wav"), patch(
            "backend.app.main.probe_duration", return_value=1.0
        ), patch("backend.app.main.transcribe_speech_detailed", side_effect=RuntimeError("boom")), patch(
            "backend.app.main.require_member_or_trial"
        ):
            first = self._bin(_tiny_wav(), headers={"X-Client-Request-ID": cid})
        self.assertEqual(first.status_code, 500)
        with patch("backend.app.main.convert_to_wav"), patch(
            "backend.app.main.probe_duration", return_value=1.0
        ), patch("backend.app.main.transcribe_speech_detailed", return_value=ASR_OK), patch(
            "backend.app.main.require_member_or_trial"
        ):
            second = self._bin(_tiny_wav(), headers={"X-Client-Request-ID": cid})
        self.assertEqual(second.status_code, 200, second.text)


if __name__ == "__main__":
    unittest.main()
