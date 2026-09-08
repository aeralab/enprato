import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import db, main
from backend.app.store import apply_draft_snapshot


def _isolate():
    tmp = tempfile.TemporaryDirectory()
    old = {
        "db": db.DB_PATH,
        "data": main.DATA,
        "auth": os.environ.get("ENPRATO_REQUIRE_AUTH"),
        "secure": os.environ.get("ENPRATO_COOKIE_SECURE"),
    }
    db.DB_PATH = Path(tmp.name) / "session-iso.sqlite3"
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


def _seed_session(session_id: str, sentences: list[dict], drafts: dict | None = None, index: int = 0):
    folder = main.DATA / session_id
    folder.mkdir()
    (folder / "sentences.json").write_text(json.dumps(sentences), encoding="utf-8")
    (folder / "source.mp4").write_bytes(b"mp4")
    (folder / "audio.wav").write_bytes(b"RIFF")
    (folder / "meta.json").write_text(
        json.dumps(
            {
                "title": session_id,
                "phase": "listen",
                "index": index,
                "drafts": drafts or {},
            }
        ),
        encoding="utf-8",
    )
    db.register_learning_session(session_id, "lan-local")
    return folder


A_SENTENCES = [
    {"id": 0, "start": 0, "end": 2, "text": "Alpha sentence one."},
    {"id": 1, "start": 2, "end": 4, "text": "Alpha sentence two."},
    {"id": 2, "start": 4, "end": 6, "text": "Alpha sentence three."},
]
B_SENTENCES = [
    {"id": 0, "start": 0, "end": 2, "text": "Bravo brand new line."},
    {"id": 1, "start": 2, "end": 4, "text": "Bravo second line."},
]


class ApplyDraftSnapshotTests(unittest.TestCase):
    def test_full_empty_snapshot_clears_poisoned_keys(self):
        existing = {"0": "from A", "1": "from A too", "2": "still A"}
        incoming = {"0": "", "1": ""}
        out = apply_draft_snapshot(existing, incoming)
        self.assertEqual(out.get("0"), "")
        self.assertEqual(out.get("1"), "")
        self.assertEqual(out.get("2"), "still A")

    def test_full_range_empty_snapshot_clears_all_covered_keys(self):
        existing = {"0": "from A", "1": "from A too"}
        incoming = {"0": "", "1": ""}
        out = apply_draft_snapshot(existing, incoming)
        self.assertEqual(out["0"], "")
        self.assertEqual(out["1"], "")


class SessionLearningIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.old = _isolate()
        self.client = TestClient(main.app)
        _seed_session("sess-a", A_SENTENCES, drafts={"0": "typed in A", "1": "also A"}, index=1)
        _seed_session("sess-b", B_SENTENCES, drafts={}, index=0)

    def tearDown(self):
        _restore(self.tmp, self.old)

    def test_new_session_does_not_inherit_other_session_drafts(self):
        a = self.client.get("/api/session/sess-a")
        b = self.client.get("/api/session/sess-b")
        self.assertEqual(a.status_code, 200, a.text)
        self.assertEqual(b.status_code, 200, b.text)
        self.assertEqual(a.json()["drafts"].get("0"), "typed in A")
        self.assertEqual(a.json()["index"], 1)
        self.assertFalse(any(str(v).strip() for v in b.json()["drafts"].values()))
        self.assertEqual(b.json()["sentences"][0]["text"], "Bravo brand new line.")
        self.assertNotEqual(b.json()["sentences"][0]["text"], a.json()["sentences"][0]["text"])

    def test_patch_a_does_not_change_b(self):
        res = self.client.patch(
            "/api/session/sess-a",
            json={"phase": "dictate", "index": 2, "drafts": {"0": "typed in A", "1": "also A", "2": "third A"}},
        )
        self.assertEqual(res.status_code, 200, res.text)
        b = self.client.get("/api/session/sess-b").json()
        self.assertFalse(any(str(v).strip() for v in b["drafts"].values()))
        self.assertNotIn("typed in A", json.dumps(b["drafts"]))

    def test_empty_full_snapshot_on_b_clears_poison_and_keeps_a(self):
        poisoned = main.DATA / "sess-b" / "meta.json"
        meta = json.loads(poisoned.read_text(encoding="utf-8"))
        meta["drafts"] = {"0": "typed in A", "1": "also A"}
        poisoned.write_text(json.dumps(meta), encoding="utf-8")
        res = self.client.patch(
            "/api/session/sess-b",
            json={"phase": "listen", "index": 0, "drafts": {"0": "", "1": ""}},
        )
        self.assertEqual(res.status_code, 200, res.text)
        b = self.client.get("/api/session/sess-b").json()
        self.assertFalse(any(str(v).strip() for v in b["drafts"].values()))
        a = self.client.get("/api/session/sess-a").json()
        self.assertEqual(a["drafts"].get("0"), "typed in A")

    def test_b_independent_then_a_restored(self):
        self.client.patch(
            "/api/session/sess-b",
            json={"phase": "dictate", "index": 0, "drafts": {"0": "only B", "1": ""}},
        )
        a = self.client.get("/api/session/sess-a").json()
        b = self.client.get("/api/session/sess-b").json()
        self.assertEqual(a["drafts"].get("0"), "typed in A")
        self.assertEqual(b["drafts"].get("0"), "only B")
        self.assertNotIn("only B", json.dumps(a["drafts"]))
        self.assertNotIn("typed in A", json.dumps(b["drafts"]))

    def test_two_users_cannot_share_session_progress(self):
        from backend.app.auth import hash_password

        os.environ["ENPRATO_REQUIRE_AUTH"] = "1"
        db.migrate()
        ua = db.create_user("iso-a@example.com", hash_password("password123"))
        ub = db.create_user("iso-b@example.com", hash_password("password123"))
        sid = "shared-looking"
        db.register_learning_session(sid, ua["id"])
        folder = main.DATA / sid
        folder.mkdir()
        (folder / "sentences.json").write_text(json.dumps(A_SENTENCES), encoding="utf-8")
        (folder / "source.mp4").write_bytes(b"mp4")
        (folder / "meta.json").write_text(json.dumps({"title": "x", "drafts": {"0": "owner draft"}}), encoding="utf-8")
        client_a, client_b = TestClient(main.app), TestClient(main.app)
        self.assertEqual(
            client_a.post("/api/auth/login", json={"email": "iso-a@example.com", "password": "password123"}).status_code,
            200,
        )
        self.assertEqual(
            client_b.post("/api/auth/login", json={"email": "iso-b@example.com", "password": "password123"}).status_code,
            200,
        )
        self.assertEqual(client_a.get(f"/api/session/{sid}").status_code, 200)
        self.assertIn(client_b.get(f"/api/session/{sid}").status_code, (401, 403, 404))
        self.assertIn(
            client_b.patch(f"/api/session/{sid}", json={"phase": "dictate", "index": 0, "drafts": {"0": "intruder"}}).status_code,
            (401, 403, 404),
        )
        kept = client_a.get(f"/api/session/{sid}").json()
        self.assertEqual(kept["drafts"].get("0"), "owner draft")


class FrontendSessionStateContractTests(unittest.TestCase):
    def test_cache_key_includes_user_and_session(self):
        self.assertEqual(_drafts_cache_key("user-a", "sess-x"), "enprato.drafts.user-a.sess-x")
        self.assertEqual(_drafts_cache_key("", "sess-x"), "enprato.drafts.lan-local.sess-x")
        self.assertNotEqual(_drafts_cache_key("user-a", "sess-x"), _drafts_cache_key("user-b", "sess-x"))

    def test_full_snapshot_pads_empty_slots(self):
        snap = _full_draft_snapshot({0: "A"}, 3)
        self.assertEqual(snap, {0: "A", 1: "", 2: ""})

    def test_load_gate_ignores_stale_token(self):
        gate = _LoadGate()
        token_a = gate.bump()
        token_b = gate.bump()
        self.assertFalse(gate.is_current(token_a))
        self.assertTrue(gate.is_current(token_b))

    def test_progress_body_is_snapshotted_not_live(self):
        live = {0: "first"}
        body = {"drafts": {str(k): str(v) for k, v in dict(live).items()}}
        live[0] = "CHANGED"
        self.assertEqual(body["drafts"]["0"], "first")


def _drafts_cache_key(user_id: str, session_id: str) -> str:
    owner = (user_id or "").strip() or "lan-local"
    return f"enprato.drafts.{owner}.{(session_id or '').strip()}"


def _full_draft_snapshot(drafts: dict[int, str], sentence_count: int) -> dict[int, str]:
    return {i: str(drafts.get(i, "")) for i in range(max(0, sentence_count))}


class _LoadGate:
    def __init__(self) -> None:
        self.gen = 0

    def bump(self) -> int:
        self.gen += 1
        return self.gen

    def is_current(self, token: int) -> bool:
        return token == self.gen
