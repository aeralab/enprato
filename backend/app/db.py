from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("ENPRATO_DATABASE_PATH", str(ROOT / "data" / "enprato.sqlite3")))
MIGRATIONS = ROOT / "migrations"


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(dt: datetime | None = None) -> str:
    return (dt or utc_now()).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


DEEP_STUDY_SECONDS = 300
STUDY_HEARTBEAT_MAX_SECONDS = 20
DEEP_STUDY_DONE_MESSAGE = "免费深度学习的 5 个素材已用完，开通会员后可继续学习新的素材。"


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column in cols:
        return
    try:
        conn.execute(ddl)
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            raise


def ensure_learning_session_study_columns(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(
        conn,
        "learning_sessions",
        "active_study_seconds",
        "ALTER TABLE learning_sessions ADD COLUMN active_study_seconds INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        conn,
        "learning_sessions",
        "trial_consumed",
        "ALTER TABLE learning_sessions ADD COLUMN trial_consumed INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        conn,
        "learning_sessions",
        "last_study_at",
        "ALTER TABLE learning_sessions ADD COLUMN last_study_at TEXT",
    )


def migrate(path: Path | None = None) -> None:
    conn = connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        for file in sorted(MIGRATIONS.glob("*.sql")):
            if file.name in applied:
                continue
            try:
                conn.executescript(file.read_text(encoding="utf-8"))
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
            conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (file.name, iso()))
        ensure_learning_session_study_columns(conn)
    finally:
        conn.close()
    refund_historical_prepare_trials(path)


def ensure_legacy_sessions(data_root: Path) -> None:
    migrate()
    conn = connect()
    try:
        for folder in data_root.iterdir() if data_root.is_dir() else []:
            if not folder.is_dir() or folder.name.startswith("_"):
                continue
            stamp = iso(datetime.fromtimestamp(folder.stat().st_mtime, UTC))
            conn.execute(
                "INSERT OR IGNORE INTO learning_sessions(session_id, created_at, updated_at) VALUES (?, ?, ?)",
                (folder.name, stamp, stamp),
            )
    finally:
        conn.close()


def register_learning_session(session_id, user_id):
    conn = connect()
    try:
        now = iso()
        owner = None if not user_id or user_id == "lan-local" else user_id
        conn.execute(
            "INSERT INTO learning_sessions(session_id, owner_user_id, created_at, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET owner_user_id=excluded.owner_user_id, updated_at=excluded.updated_at",
            (session_id, owner, now, now),
        )
    finally:
        conn.close()


def owns_learning_session(session_id, user_id):
    conn = connect()
    try:
        row = conn.execute("SELECT owner_user_id FROM learning_sessions WHERE session_id=?", (session_id,)).fetchone()
        return bool(row and row[0] == user_id)
    finally:
        conn.close()


def create_user(email: str | None, password_hash: str | None) -> dict[str, Any]:
    user_id = uuid.uuid4().hex
    now = iso()
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO users(id,email,password_hash,created_at) VALUES(?,?,?,?)",
            (user_id, email, password_hash, now),
        )
        conn.execute(
            "INSERT INTO usage_quotas(user_id, trial_limit, trial_used, updated_at) VALUES(?,?,?,?)",
            (user_id, 5, 0, now),
        )
        conn.execute("COMMIT")
        return {"id": user_id, "email": email, "status": "active", "created_at": now}
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def normalize_phone(phone: str) -> str:
    value = "".join(ch for ch in phone.strip() if ch.isdigit() or ch == "+")
    if value.startswith("+86"): value = value[3:]
    elif value.startswith("0086"): value = value[4:]
    if len(value) != 11 or not value.startswith("1") or not value.isdigit(): raise ValueError("invalid phone")
    return value


def create_phone_challenge(phone: str, code_hash: str, request_ip: str, challenge_id: str, minutes: int = 5) -> bool:
    conn = connect()
    try:
        now = utc_now()
        cutoff = iso(now - timedelta(minutes=1))
        hour = iso(now - timedelta(hours=1))
        if conn.execute("SELECT 1 FROM sms_challenges WHERE phone=? AND created_at>?", (phone, cutoff)).fetchone():
            return False
        if conn.execute("SELECT 1 FROM sms_challenges WHERE request_ip=? AND created_at>?", (request_ip, cutoff)).fetchone():
            return False
        if conn.execute("SELECT COUNT(*) FROM sms_challenges WHERE phone=? AND created_at>?", (phone, hour)).fetchone()[0] >= 5:
            return False
        if conn.execute("SELECT COUNT(*) FROM sms_challenges WHERE request_ip=? AND created_at>?", (request_ip, hour)).fetchone()[0] >= 20:
            return False
        conn.execute(
            "INSERT INTO sms_challenges(id,phone,code_hash,request_ip,created_at,expires_at) VALUES(?,?,?,?,?,?)",
            (challenge_id, phone, code_hash, request_ip, iso(now), iso(now + timedelta(minutes=minutes))),
        )
        return True
    finally:
        conn.close()


def delete_phone_challenge(challenge_id: str) -> None:
    conn = connect()
    try:
        conn.execute("DELETE FROM sms_challenges WHERE id=?", (challenge_id,))
    finally:
        conn.close()


def _login_or_create_identities(conn: sqlite3.Connection, pairs: list[tuple[str, str]]) -> str:
    if not pairs:
        raise ValueError("missing identity")
    found: list[str] = []
    for provider, provider_user_id in pairs:
        row = conn.execute(
            "SELECT user_id FROM user_identities WHERE provider=? AND provider_user_id=?",
            (provider, provider_user_id),
        ).fetchone()
        if row:
            uid = str(row["user_id"])
            if uid not in found:
                found.append(uid)
    now = iso()
    if found:
        user_id = found[0]
    else:
        user_id = uuid.uuid4().hex
        conn.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(?,?,?,?)", (user_id, None, None, now))
        conn.execute(
            "INSERT INTO usage_quotas(user_id,trial_limit,trial_used,updated_at) VALUES(?,?,?,?)",
            (user_id, 5, 0, now),
        )
    conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (now, user_id))
    for provider, provider_user_id in pairs:
        existing = conn.execute(
            "SELECT user_id FROM user_identities WHERE provider=? AND provider_user_id=?",
            (provider, provider_user_id),
        ).fetchone()
        if existing:
            continue
        conn.execute(
            "INSERT INTO user_identities(user_id,provider,provider_user_id,verified_at,created_at) VALUES(?,?,?,?,?)",
            (user_id, provider, provider_user_id, now, now),
        )
    return user_id


def login_or_create_identities(pairs: list[tuple[str, str]]) -> str:
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        user_id = _login_or_create_identities(conn, pairs)
        conn.execute("COMMIT")
        return user_id
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def login_label(user_id: str, email: str | None = None) -> str:
    if email:
        return email
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT provider, provider_user_id FROM user_identities WHERE user_id=? ORDER BY id",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        if row["provider"] == "phone":
            phone = str(row["provider_user_id"])
            if len(phone) >= 7:
                return phone[:3] + "****" + phone[-4:]
            return "手机号账号"
        if row["provider"] in {"wechat", "wechat_openid"}:
            return "微信账号"
    return "已登录"


def consume_phone_challenge(challenge_id: str, phone: str, code_hash: str) -> str | None:
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM sms_challenges WHERE id=? AND phone=? AND consumed_at IS NULL AND expires_at>?",
            (challenge_id, phone, iso()),
        ).fetchone()
        if not row:
            conn.execute("ROLLBACK")
            return None
        attempts = int(row["attempts"] or 0)
        if attempts >= 5:
            conn.execute("ROLLBACK")
            return None
        if not secrets.compare_digest(str(row["code_hash"]), code_hash):
            conn.execute("UPDATE sms_challenges SET attempts=attempts+1 WHERE id=?", (challenge_id,))
            if attempts + 1 >= 5:
                conn.execute("UPDATE sms_challenges SET consumed_at=? WHERE id=?", (iso(), challenge_id))
            conn.execute("COMMIT")
            return None
        conn.execute("UPDATE sms_challenges SET consumed_at=? WHERE id=?", (iso(), challenge_id))
        user_id = _login_or_create_identities(conn, [("phone", phone)])
        conn.execute("COMMIT")
        return user_id
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def trial_status(user_id: str) -> dict[str, int]:
    conn = connect()
    try:
        row = conn.execute("SELECT trial_limit, trial_used FROM usage_quotas WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            now = iso()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO usage_quotas(user_id,trial_limit,trial_used,updated_at) VALUES(?,?,?,?)",
                    (user_id, 5, 0, now),
                )
            except sqlite3.IntegrityError:
                # 用户不存在（如局域网伪用户）时只返回默认额度，不写库
                return {"limit": 5, "used": 0, "remaining": 5}
            return {"limit": 5, "used": 0, "remaining": 5}
        limit, used = int(row["trial_limit"]), int(row["trial_used"])
        return {"limit": limit, "used": used, "remaining": max(0, limit - used)}
    finally:
        conn.close()


def _quota_row(conn: sqlite3.Connection, user_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT trial_limit, trial_used FROM usage_quotas WHERE user_id=?", (user_id,)).fetchone()
    if row:
        return row
    conn.execute(
        "INSERT INTO usage_quotas(user_id,trial_limit,trial_used,updated_at) VALUES(?,?,?,?)",
        (user_id, 5, 0, iso()),
    )
    row = conn.execute("SELECT trial_limit, trial_used FROM usage_quotas WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        raise RuntimeError("usage_quotas missing")
    return row


def _membership_active_on_conn(conn: sqlite3.Connection, user_id: str) -> bool:
    row = conn.execute(
        "SELECT expires_at, status FROM memberships WHERE user_id=? ORDER BY expires_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if not row:
        return False
    return str(row["expires_at"] or "") > iso() and row["status"] == "active"


def _consume_trial_on_conn(conn: sqlite3.Connection, user_id: str, request_key: str) -> bool:
    event_id = f"{user_id}:{request_key}"
    row = _quota_row(conn, user_id)
    if conn.execute("SELECT 1 FROM payment_events WHERE provider='trial' AND event_id=?", (event_id,)).fetchone():
        return True
    if int(row["trial_used"]) >= int(row["trial_limit"]):
        return False
    now = iso()
    conn.execute("UPDATE usage_quotas SET trial_used=trial_used+1,updated_at=? WHERE user_id=?", (now, user_id))
    conn.execute(
        "INSERT INTO payment_events(provider,event_id,order_no,payload_hash,verified_at,processed_at,result) VALUES(?,?,?,?,?,?,?)",
        ("trial", event_id, request_key, "trial", now, now, "consumed"),
    )
    return True


def consume_trial(user_id: str, request_key: str) -> bool:
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        ok = _consume_trial_on_conn(conn, user_id, request_key)
        if ok:
            conn.execute("COMMIT")
        else:
            conn.execute("ROLLBACK")
        return ok
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def refund_trial(user_id: str, request_key: str) -> None:
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        event_id = f"{user_id}:{request_key}"
        row = conn.execute("SELECT id FROM payment_events WHERE provider='trial' AND event_id=? AND result='consumed'", (event_id,)).fetchone()
        if row:
            now = iso()
            conn.execute("UPDATE usage_quotas SET trial_used=MAX(0,trial_used-1),updated_at=? WHERE user_id=?", (now, user_id))
            conn.execute("UPDATE payment_events SET result='refunded',processed_at=? WHERE id=?", (now, row["id"]))
        conn.execute("COMMIT")
    except Exception:
        try: conn.execute("ROLLBACK")
        except Exception: pass
        raise
    finally:
        conn.close()


def _recount_study_trial_used(conn: sqlite3.Connection, user_id: str, now: str) -> int:
    used = int(
        conn.execute(
            "SELECT COUNT(*) FROM payment_events WHERE provider='trial' AND result='consumed' AND event_id LIKE ?",
            (f"{user_id}:study:%",),
        ).fetchone()[0]
    )
    conn.execute(
        "UPDATE usage_quotas SET trial_used=?, updated_at=? WHERE user_id=?",
        (used, now, user_id),
    )
    return used


def refund_historical_prepare_trials(path: Path | None = None) -> dict[str, int]:
    """Idempotent: refund leftover prepare: trial rows and recount study: usage."""
    conn = connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = iso()
        cur = conn.execute(
            "UPDATE payment_events SET result='refunded', processed_at=? "
            "WHERE provider='trial' AND result='consumed' AND instr(event_id, ':prepare:') > 0",
            (now,),
        )
        refunded = int(cur.rowcount or 0)
        for row in conn.execute("SELECT user_id FROM usage_quotas").fetchall():
            _recount_study_trial_used(conn, str(row["user_id"]), now)
        conn.execute("COMMIT")
        return {"refunded": refunded}
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def can_deep_study(user_id: str, session_id: str, conn: sqlite3.Connection | None = None) -> bool:
    own = conn is None
    if own:
        conn = connect()
    try:
        if _membership_active_on_conn(conn, user_id):
            return True
        row = conn.execute(
            "SELECT trial_consumed FROM learning_sessions WHERE session_id=? AND owner_user_id=?",
            (session_id, user_id),
        ).fetchone()
        if row and int(row["trial_consumed"] or 0):
            return True
        quota = _quota_row(conn, user_id)
        return int(quota["trial_used"]) < int(quota["trial_limit"])
    finally:
        if own:
            conn.close()


def apply_study_heartbeat(user_id: str, session_id: str, active_seconds: int) -> dict[str, Any]:
    try:
        seconds = int(active_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid active_seconds") from exc
    if seconds < 0 or seconds > STUDY_HEARTBEAT_MAX_SECONDS:
        raise ValueError("invalid active_seconds")
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT owner_user_id, active_study_seconds, trial_consumed FROM learning_sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if not row or str(row["owner_user_id"] or "") != user_id:
            conn.execute("ROLLBACK")
            raise LookupError("session")
        if not can_deep_study(user_id, session_id, conn):
            conn.execute("ROLLBACK")
            raise PermissionError("deep_study")
        now = iso()
        if seconds:
            conn.execute(
                "UPDATE learning_sessions SET active_study_seconds=active_study_seconds+?, last_study_at=?, updated_at=? "
                "WHERE session_id=? AND owner_user_id=?",
                (seconds, now, now, session_id, user_id),
            )
        row = conn.execute(
            "SELECT active_study_seconds, trial_consumed FROM learning_sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        total = int(row["active_study_seconds"] or 0)
        consumed = int(row["trial_consumed"] or 0)
        member = _membership_active_on_conn(conn, user_id)
        quota = _quota_row(conn, user_id)
        if (
            not member
            and not consumed
            and total >= DEEP_STUDY_SECONDS
            and int(quota["trial_used"]) < int(quota["trial_limit"])
        ):
            if _consume_trial_on_conn(conn, user_id, "study:" + session_id):
                conn.execute(
                    "UPDATE learning_sessions SET trial_consumed=1, updated_at=? WHERE session_id=?",
                    (now, session_id),
                )
                consumed = 1
        conn.execute("COMMIT")
        quota = trial_status(user_id)
        return {
            "session_id": session_id,
            "active_study_seconds": total,
            "trial_consumed": bool(consumed),
            "trial": quota,
        }
    except (LookupError, PermissionError, ValueError):
        raise
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def create_remote_token(session_id: str, user_id: str, minutes: int = 30) -> str:
    if not owns_learning_session(session_id, user_id):
        raise PermissionError("session ownership required")
    raw = secrets.token_urlsafe(32)
    now = utc_now()
    conn = connect()
    try:
        conn.execute("INSERT INTO remote_tokens(token_hash,session_id,owner_user_id,created_at,expires_at) VALUES(?,?,?,?,?)", (hash_token(raw), session_id, user_id, iso(now), iso(now + timedelta(minutes=minutes))))
    finally:
        conn.close()
    return raw


def revoke_remote_token(raw: str) -> None:
    conn = connect()
    try:
        conn.execute("UPDATE remote_tokens SET revoked_at=? WHERE token_hash=?", (iso(), hash_token(raw)))
    finally:
        conn.close()


def remote_token_owner(raw: str, session_id: str) -> str | None:
    if not raw:
        return None
    conn = connect()
    try:
        row = conn.execute("SELECT owner_user_id FROM remote_tokens WHERE token_hash=? AND session_id=? AND revoked_at IS NULL AND expires_at>?", (hash_token(raw), session_id, iso())).fetchone()
        return str(row["owner_user_id"]) if row else None
    finally:
        conn.close()


def user_by_id(user_id: str) -> sqlite3.Row | None:
    conn = connect()
    try:
        return conn.execute("SELECT * FROM users WHERE id=? AND status='active'", (user_id,)).fetchone()
    finally:
        conn.close()


def find_user(email: str) -> sqlite3.Row | None:
    conn = connect()
    try:
        return conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    finally:
        conn.close()


def create_auth_session(user_id: str, days: int = 30) -> str:
    raw = secrets.token_urlsafe(32)
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
            (hash_token(raw), user_id, iso(), iso(utc_now() + timedelta(days=days))),
        )
    finally:
        conn.close()
    return raw


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def user_for_token(raw: str) -> sqlite3.Row | None:
    conn = connect()
    try:
        return conn.execute(
            "SELECT u.* FROM auth_sessions s JOIN users u ON u.id=s.user_id "
            "WHERE s.token_hash=? AND s.expires_at>? AND u.status='active'",
            (hash_token(raw), iso()),
        ).fetchone()
    finally:
        conn.close()


def delete_auth_session(raw: str) -> None:
    conn = connect()
    try:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (hash_token(raw),))
    finally:
        conn.close()


def membership_status(user_id: str) -> dict[str, Any]:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT m.*, p.code, p.name FROM memberships m JOIN plans p ON p.id=m.plan_id "
            "WHERE m.user_id=? ORDER BY m.expires_at DESC LIMIT 1", (user_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"status": "none", "active": False, "expires_at": ""}
    active = row["expires_at"] > iso() and row["status"] == "active"
    return {"status": "active" if active else "expired", "active": active, "expires_at": row["expires_at"], "plan": row["code"], "plan_name": row["name"]}


def grant_dev_membership(user_id: str, days: int = 30) -> dict[str, Any]:
    if os.environ.get("ENPRATO_ENABLE_DEV_MEMBERSHIP", "").lower() not in {"1", "true", "yes"}:
        raise PermissionError("development membership is disabled")
    conn = connect()
    try:
        plan = conn.execute("SELECT id FROM plans WHERE code='monthly_30d'").fetchone()
        now = utc_now()
        current = conn.execute("SELECT expires_at FROM memberships WHERE user_id=? AND status='active' ORDER BY expires_at DESC LIMIT 1", (user_id,)).fetchone()
        start = max(now, parse_time(current[0])) if current else now
        expires = start + timedelta(days=days)
        conn.execute("UPDATE memberships SET status='expired', updated_at=? WHERE user_id=? AND status='active'", (iso(now), user_id))
        conn.execute("INSERT INTO memberships(user_id,plan_id,starts_at,expires_at,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (user_id, plan[0], iso(start), iso(expires), "active", iso(now), iso(now)))
    finally:
        conn.close()
    return membership_status(user_id)


def create_order(user_id: str, plan_code: str, provider: str) -> dict[str, Any]:
    conn=connect()
    try:
        plan=conn.execute("SELECT * FROM plans WHERE code=? AND active=1",(plan_code,)).fetchone()
        if not plan: raise ValueError("plan not found")
        now_dt=utc_now();now=iso(now_dt);expires=iso(now_dt+timedelta(minutes=15))
        order_no="EN"+now_dt.strftime("%y%m%d%H%M%S")+secrets.token_hex(4)
        conn.execute("INSERT INTO orders(order_no,user_id,plan_id,amount_fen,status,provider,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)",(order_no,user_id,plan["id"],plan["price_fen"],"pending",provider,now,expires))
        return {"id":order_no,"order_no":order_no,"plan":plan["code"],"amount_fen":plan["price_fen"],"currency":"CNY","status":"pending","created_at":now,"expires_at":expires}
    finally: conn.close()


def get_order(order_no: str, user_id: str) -> dict[str, Any] | None:
    conn=connect()
    try:
        row=conn.execute("SELECT o.*,p.code plan_code,p.name plan_name FROM orders o JOIN plans p ON p.id=o.plan_id WHERE o.order_no=? AND o.user_id=?",(order_no,user_id)).fetchone()
        if not row:return None
        result=dict(row)
        if result["status"]=="pending" and result.get("expires_at") and parse_time(result["expires_at"])<=utc_now():
            conn.execute("UPDATE orders SET status='closed' WHERE order_no=? AND status='pending'",(order_no,));result["status"]="closed"
        return result
    finally:conn.close()


def complete_payment(*,provider:str,event_id:str,payload_hash:str,order_no:str,trade_no:str,amount_fen:int,payment_status:str,merchant_id:str|None=None,app_id:str|None=None)->str:
    conn=connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT id FROM payment_events WHERE provider=? AND event_id=?",(provider,event_id)).fetchone():
            conn.execute("COMMIT");return "duplicate"
        order=conn.execute("SELECT * FROM orders WHERE order_no=?",(order_no,)).fetchone()
        if not order:raise ValueError("order not found")
        if int(order["amount_fen"])!=int(amount_fen) or int(amount_fen)!=1990:raise ValueError("payment amount mismatch")
        if merchant_id is not None and merchant_id!=os.environ.get("WECHATPAY_MCH_ID","").strip():raise ValueError("merchant mismatch")
        if app_id is not None and app_id!=os.environ.get("WECHATPAY_APP_ID","").strip():raise ValueError("app mismatch")
        now=iso()
        if payment_status!="SUCCESS":
            conn.execute("INSERT INTO payment_events(provider,event_id,order_no,payload_hash,verified_at,processed_at,result) VALUES(?,?,?,?,?,?,?)",(provider,event_id,order_no,payload_hash,now,now,"ignored"));conn.execute("COMMIT");return "ignored"
        if order["status"]=="paid":
            conn.execute("INSERT INTO payment_events(provider,event_id,order_no,payload_hash,verified_at,processed_at,result) VALUES(?,?,?,?,?,?,?)",(provider,event_id,order_no,payload_hash,now,now,"already_paid"));conn.execute("COMMIT");return "already_paid"
        conn.execute("INSERT INTO payment_events(provider,event_id,order_no,payload_hash,verified_at,processed_at,result) VALUES(?,?,?,?,?,?,?)",(provider,event_id,order_no,payload_hash,now,now,"paid"))
        conn.execute("UPDATE orders SET status='paid',provider_trade_no=?,paid_at=? WHERE order_no=?",(trade_no,now,order_no))
        current=conn.execute("SELECT expires_at FROM memberships WHERE user_id=? AND status='active' ORDER BY expires_at DESC LIMIT 1",(order["user_id"],)).fetchone()
        paid_at=parse_time(now);start_time=max(paid_at,parse_time(current[0])) if current else paid_at;expires=start_time+timedelta(days=30)
        conn.execute("UPDATE memberships SET status='expired',updated_at=? WHERE user_id=? AND status='active'",(now,order["user_id"]))
        conn.execute("INSERT INTO memberships(user_id,plan_id,starts_at,started_at,expires_at,status,source_order_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(order["user_id"],order["plan_id"],iso(start_time),iso(start_time),iso(expires),"active",order_no,now,now))
        subscription=conn.execute("SELECT id FROM subscriptions WHERE user_id=? AND provider='wechat' ORDER BY id DESC LIMIT 1",(order["user_id"],)).fetchone()
        if subscription:conn.execute("UPDATE subscriptions SET mode='one_time',status='active',current_period_start=?,current_period_end=?,auto_renew=0,updated_at=? WHERE id=?",(iso(start_time),iso(expires),now,subscription["id"]))
        else:conn.execute("INSERT INTO subscriptions(user_id,provider,mode,status,current_period_start,current_period_end,auto_renew,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(order["user_id"],"wechat","one_time","active",iso(start_time),iso(expires),0,now,now))
        conn.execute("COMMIT");return "paid"
    except Exception:
        try:conn.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:conn.close()
