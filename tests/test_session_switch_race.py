import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import db, main


def _isolate():
    tmp = tempfile.TemporaryDirectory()
    old = {
        "db": db.DB_PATH,
        "data": main.DATA,
        "auth": os.environ.get("ENPRATO_REQUIRE_AUTH"),
        "secure": os.environ.get("ENPRATO_COOKIE_SECURE"),
    }
    db.DB_PATH = Path(tmp.name) / "switch-race.sqlite3"
    main.DATA = Path(tmp.name) / "sessions"
    main.DATA.mkdir()
    db.migrate()
    os.environ.pop("ENPRATO_REQUIRE_AUTH", None)
    os.environ["ENPRATO_COOKIE_SECURE"] = "0"
    return tmp, old


def _restore(tmp, old):
    db.DB_PATH, main.DATA = old["db"], old["data"]
    if old["auth"] is None:
        os.environ.pop("ENPRATO_REQUIRE_AUTH", None)
    else:
        os.environ["ENPRATO_REQUIRE_AUTH"] = old["auth"]
    if old["secure"] is None:
        os.environ.pop("ENPRATO_COOKIE_SECURE", None)
    else:
        os.environ["ENPRATO_COOKIE_SECURE"] = old["secure"]
    tmp.cleanup()


def _sentences(prefix: str, count: int) -> list[dict]:
    return [{"id": i, "start": i * 2, "end": i * 2 + 2, "text": f"{prefix} {i}."} for i in range(count)]


def _drafts(prefix: str, count: int) -> dict[str, str]:
    return {str(i): f"{prefix} {i}" for i in range(count)}


def _filled(drafts: dict) -> int:
    return sum(1 for value in drafts.values() if str(value or "").strip())


def _seed(session_id: str, sentences: list[dict], drafts: dict, index: int) -> None:
    folder = main.DATA / session_id
    folder.mkdir()
    (folder / "sentences.json").write_text(json.dumps(sentences), encoding="utf-8")
    (folder / "source.mp4").write_bytes(b"mp4")
    (folder / "audio.wav").write_bytes(b"RIFF")
    (folder / "meta.json").write_text(
        json.dumps({"title": session_id, "phase": "listen", "index": index, "drafts": drafts}),
        encoding="utf-8",
    )
    db.register_learning_session(session_id, "lan-local")


A_DRAFTS = _drafts("typed A", 20)
C_DRAFTS = _drafts("typed C", 3)


class SessionSwitchRaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.old = _isolate()
        self.client = TestClient(main.app)
        _seed("sess-a", _sentences("Alpha", 24), A_DRAFTS, 19)
        _seed("sess-c", _sentences("Charlie", 8), C_DRAFTS, 2)

    def tearDown(self):
        _restore(self.tmp, self.old)

    def test_c_a_c_keeps_independent_counts(self):
        c0 = self.client.get("/api/session/sess-c").json()
        a0 = self.client.get("/api/session/sess-a").json()
        self.assertEqual(_filled(c0["drafts"]), 3)
        self.assertEqual(_filled(a0["drafts"]), 20)

        self.assertEqual(
            self.client.patch(
                "/api/session/sess-c",
                json={
                    "phase": "dictate",
                    "index": 2,
                    "drafts": C_DRAFTS,
                    "source_session_id": "sess-c",
                    "save_reason": "switch-session",
                },
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.patch(
                "/api/session/sess-a",
                json={
                    "phase": "dictate",
                    "index": 19,
                    "drafts": A_DRAFTS,
                    "source_session_id": "sess-a",
                    "save_reason": "switch-session",
                },
            ).status_code,
            200,
        )
        delayed_a = self.client.patch(
            "/api/session/sess-a",
            json={
                "phase": "dictate",
                "index": 19,
                "drafts": A_DRAFTS,
                "source_session_id": "sess-a",
                "save_reason": "unmount-flush",
            },
        )
        self.assertEqual(delayed_a.status_code, 200)
        c1 = self.client.get("/api/session/sess-c").json()
        a1 = self.client.get("/api/session/sess-a").json()
        self.assertEqual(_filled(c1["drafts"]), 3)
        self.assertEqual(_filled(a1["drafts"]), 20)

        self.assertEqual(
            self.client.patch(
                "/api/session/sess-c",
                json={
                    "phase": "listen",
                    "index": 2,
                    "drafts": C_DRAFTS,
                    "source_session_id": "sess-c",
                    "save_reason": "reload",
                },
            ).status_code,
            200,
        )
        c2 = self.client.get("/api/session/sess-c").json()
        a2 = self.client.get("/api/session/sess-a").json()
        self.assertEqual(_filled(c2["drafts"]), 3)
        self.assertEqual(_filled(a2["drafts"]), 20)

    def test_mismatch_source_session_is_rejected_and_does_not_write_c(self):
        res = self.client.patch(
            "/api/session/sess-c",
            json={
                "phase": "dictate",
                "index": 19,
                "drafts": A_DRAFTS,
                "source_session_id": "sess-a",
                "save_reason": "unmount-flush",
            },
        )
        self.assertEqual(res.status_code, 400, res.text)
        c = self.client.get("/api/session/sess-c").json()
        a = self.client.get("/api/session/sess-a").json()
        self.assertEqual(_filled(c["drafts"]), 3)
        self.assertEqual(_filled(a["drafts"]), 20)
        self.assertNotIn("typed A 0", json.dumps(c["drafts"]))

    def test_delayed_a_promise_still_patches_a_not_c(self):
        self.client.patch(
            "/api/session/sess-c",
            json={"phase": "listen", "index": 0, "drafts": C_DRAFTS, "source_session_id": "sess-c"},
        )
        delayed = self.client.patch(
            "/api/session/sess-a",
            json={
                "phase": "dictate",
                "index": 19,
                "drafts": A_DRAFTS,
                "source_session_id": "sess-a",
                "save_reason": "late-a",
            },
        )
        self.assertEqual(delayed.status_code, 200)
        c = self.client.get("/api/session/sess-c").json()
        a = self.client.get("/api/session/sess-a").json()
        self.assertEqual(_filled(c["drafts"]), 3)
        self.assertEqual(_filled(a["drafts"]), 20)
