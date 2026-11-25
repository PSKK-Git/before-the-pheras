"""SQLite persistence: sessions, participants, positions (3 tables)."""
import json
import os
import secrets
import sqlite3
import string
import time
from contextlib import contextmanager

DB_PATH = os.getenv("DATABASE_URL", "sqlite:///./pheras.db").replace("sqlite:///", "")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  id TEXT PRIMARY KEY, join_code TEXT UNIQUE NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS participants(
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL, language TEXT NOT NULL, is_seeded INTEGER NOT NULL DEFAULT 0,
  token TEXT NOT NULL,
  topic_idx INTEGER NOT NULL DEFAULT 0,      -- interview progress
  followup_used INTEGER NOT NULL DEFAULT 0,  -- at most one follow-up per topic
  pending_followup TEXT,                     -- follow-up question text, if asked
  answer_log TEXT NOT NULL DEFAULT '{}');    -- answer_id -> response (idempotency)
CREATE TABLE IF NOT EXISTS positions(
  id TEXT PRIMARY KEY, participant_id TEXT NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  topic TEXT NOT NULL, transcript TEXT NOT NULL DEFAULT '',
  stance_key TEXT, stance_scale INTEGER, importance INTEGER, flexibility INTEGER,
  conditions TEXT NOT NULL DEFAULT '[]', summary_native TEXT, summary_english TEXT,
  supporting_quote TEXT, confidence REAL, needs_clarification INTEGER NOT NULL DEFAULT 0,
  review_status TEXT NOT NULL DEFAULT 'pending', approved_at REAL, created_at REAL NOT NULL,
  discussed INTEGER NOT NULL DEFAULT 0,
  UNIQUE(participant_id, topic));
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)


def _id() -> str:
    return secrets.token_urlsafe(9)


def create_session() -> dict:
    sid = _id()
    alphabet = string.ascii_uppercase.replace("O", "").replace("I", "") + "23456789"
    with conn() as c:
        while True:
            code = "".join(secrets.choice(alphabet) for _ in range(5))
            if not c.execute("SELECT 1 FROM sessions WHERE join_code=?", (code,)).fetchone():
                break
        c.execute("INSERT INTO sessions VALUES(?,?,?)", (sid, code, time.time()))
    return {"session_id": sid, "join_code": code}


def join_session(code: str, language: str, seeded: bool = False):
    """Returns participant dict, None if code unknown, or 'full'."""
    with conn() as c:
        s = c.execute("SELECT * FROM sessions WHERE join_code=?", (code.upper().strip(),)).fetchone()
        if not s:
            return None
        roles = [r["role"] for r in c.execute("SELECT role FROM participants WHERE session_id=?", (s["id"],))]
        if len(roles) >= 2:
            return "full"
        role = "A" if "A" not in roles else "B"
        pid, token = _id(), secrets.token_urlsafe(24)
        c.execute(
            "INSERT INTO participants(id,session_id,role,language,is_seeded,token) VALUES(?,?,?,?,?,?)",
            (pid, s["id"], role, language, int(seeded), token),
        )
        return {"session_id": s["id"], "participant_id": pid, "role": role, "language": language, "token": token}


def participant_by_token(token: str):
    if not token:
        return None
    with conn() as c:
        r = c.execute("SELECT * FROM participants WHERE token=?", (token,)).fetchone()
        return dict(r) if r else None


def get_participant(pid: str):
    with conn() as c:
        r = c.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None


def update_participant(pid: str, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE participants SET {cols} WHERE id=?", (*fields.values(), pid))


def session_participants(session_id: str):
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM participants WHERE session_id=? ORDER BY role", (session_id,))]


def get_position(pos_id: str):
    with conn() as c:
        return _pos(c.execute("SELECT * FROM positions WHERE id=?", (pos_id,)).fetchone())


def position_for(pid: str, topic: str):
    with conn() as c:
        return _pos(c.execute("SELECT * FROM positions WHERE participant_id=? AND topic=?", (pid, topic)).fetchone())


def positions_of(pid: str, status: str | None = None):
    q, a = "SELECT * FROM positions WHERE participant_id=?", [pid]
    if status:
        q += " AND review_status=?"
        a.append(status)
    with conn() as c:
        return [_pos(r) for r in c.execute(q, a)]


def _norm(fields: dict) -> dict:
    if "conditions" in fields and not isinstance(fields["conditions"], str):
        fields["conditions"] = json.dumps(fields["conditions"], ensure_ascii=False)
    for k in ("needs_clarification", "discussed"):
        if k in fields:
            fields[k] = int(bool(fields[k]))
    return fields


def upsert_position(pid: str, topic: str, **fields):
    fields = _norm(fields)
    with conn() as c:
        row = c.execute("SELECT id FROM positions WHERE participant_id=? AND topic=?", (pid, topic)).fetchone()
        if row:
            pos_id = row["id"]
            if fields:
                cols = ", ".join(f"{k}=?" for k in fields)
                c.execute(f"UPDATE positions SET {cols} WHERE id=?", (*fields.values(), pos_id))
        else:
            pos_id = _id()
            fields = {"id": pos_id, "participant_id": pid, "topic": topic, "created_at": time.time(), **fields}
            c.execute(
                f"INSERT INTO positions({','.join(fields)}) VALUES({','.join('?' * len(fields))})",
                tuple(fields.values()),
            )
    return get_position(pos_id)


def update_position(pos_id: str, **fields):
    fields = _norm(fields)
    cols = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE positions SET {cols} WHERE id=?", (*fields.values(), pos_id))
    return get_position(pos_id)


def delete_session(session_id: str):
    with conn() as c:  # ON DELETE CASCADE removes participants and positions
        c.execute("DELETE FROM sessions WHERE id=?", (session_id,))


def session_exists(session_id: str) -> bool:
    with conn() as c:
        return bool(c.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone())


def ping() -> bool:
    try:
        with conn() as c:
            c.execute("SELECT 1")
        return True
    except Exception:
        return False


def _pos(r):
    if not r:
        return None
    d = dict(r)
    d["conditions"] = json.loads(d.get("conditions") or "[]")
    d["needs_clarification"] = bool(d["needs_clarification"])
    d["discussed"] = bool(d["discussed"])
    return d
