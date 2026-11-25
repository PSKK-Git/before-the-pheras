"""Before the Pheras - FastAPI app + routes."""
import json
import os
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

try:  # optional .env support without adding a dependency (loaded before the modules read env)
    for _line in (Path(__file__).parent.parent / ".env").read_text().splitlines():
        if _line.strip() and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())
except Exception:
    pass

import compare  # noqa: E402
import db  # noqa: E402
import extract  # noqa: E402
import flags  # noqa: E402
import sarvam  # noqa: E402

db.init()
app = FastAPI(title="Before the Pheras")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

incident = {"nudgebee_signal": None, "self_heal": False, "at": None}   # content-free


# --- breaker -> incident wiring ---------------------------------------------------------------

def _on_open():
    detail = {"breaker_state": "open", "error_counts": dict(sarvam.error_counts), "p95_ms": sarvam.p95_latency_ms()}
    incident["at"] = time.time()
    if os.getenv("SELF_HEAL_FALLBACK") == "1":   # ladder step 4, labelled honestly in README
        flags.ops_override(True)
        incident["self_heal"] = True
    threading.Thread(
        target=lambda: incident.update(nudgebee_signal=flags.notify_nudgebee("sarvam_breaker_open", detail)),
        daemon=True,
    ).start()


def _on_close():
    flags.ops_override(False)
    incident.update(self_heal=False)


sarvam.breaker.on_open = _on_open
sarvam.breaker.on_close = _on_close


# --- helpers ----------------------------------------------------------------------------------

def me(token: str | None, session_id: str | None = None) -> dict:
    p = db.participant_by_token(token or "")
    if not p or (session_id and p["session_id"] != session_id):
        raise HTTPException(401, "invalid participant token")
    return p


def topic_list():
    return flags.topics()


_qcache: dict = {}


def localized(text: str, lang: str, i18n: dict | None = None) -> str:
    if lang == "en-IN":
        return text
    if i18n and i18n.get(lang):
        return i18n[lang]
    key = (text, lang)
    if key not in _qcache:
        try:
            _qcache[key] = sarvam.translate(text, "en-IN", lang)
        except Exception:
            return text   # English fallback; never block the interview on translation
    return _qcache[key]


def current_question(p: dict) -> dict | None:
    ts = topic_list()
    if p["topic_idx"] >= len(ts):
        return None
    t = ts[p["topic_idx"]]
    if p["pending_followup"]:
        text, kind = p["pending_followup"], "followup"
    else:
        text, kind = localized(t["question"], p["language"], t.get("question_i18n")), "question"
    return {"topic": t["id"], "index": p["topic_idx"], "total": len(ts), "kind": kind, "text": text,
            "stance_options": t["stance_options"]}


def public_view(pos: dict) -> dict:
    """What a partner may see: approved summaries only. No transcript, no quote."""
    return {k: pos[k] for k in ("id", "topic", "stance_key", "stance_scale", "importance", "flexibility",
                                 "summary_native", "summary_english", "conditions", "approved_at")}


# --- sessions ---------------------------------------------------------------------------------

class JoinIn(BaseModel):
    join_code: str
    language: str = "en-IN"


@app.post("/sessions")
def create_session():
    return db.create_session()


@app.post("/sessions/join")
def join(body: JoinIn):
    if body.language not in flags.LANGS:
        raise HTTPException(400, "unsupported language")
    r = db.join_session(body.join_code, body.language)
    if r is None:
        raise HTTPException(404, "unknown code")
    if r == "full":
        raise HTTPException(409, "session already has two participants")
    return r


@app.get("/config")
def config():
    f = flags.get_all()
    ready_ = sarvam.configured() and sarvam.breaker.current() != "open"
    return {
        "voice_mode": f["voice_mode"],
        "text_fallback": f["text_fallback"],
        "tts_replies": f["tts_replies"],
        "enabled_languages": [{"code": c, "name": flags.LANGS[c]} for c in f["enabled_languages"]],
        "topics": [{"id": t["id"], "question": t["question"]} for t in f["interview_topics"]],
        "source": f["source"],
        "override_active": f["override_active"],
        "voice_available": bool(f["voice_mode"] and ready_),
        "sarvam": "ok" if ready_ else ("degraded" if sarvam.configured() else "unconfigured"),
    }


@app.get("/sessions/{session_id}/me")
def my_state(session_id: str, x_participant_token: str | None = Header(None)):
    p = me(x_participant_token, session_id)
    with db.conn() as c:
        code = c.execute("SELECT join_code FROM sessions WHERE id=?", (session_id,)).fetchone()["join_code"]
    others = [o for o in db.session_participants(session_id) if o["id"] != p["id"]]
    return {
        "role": p["role"], "language": p["language"], "join_code": code,
        "question": current_question(p),
        "partner_joined": bool(others),
        "partner_synthetic": bool(others and others[0]["is_seeded"]),
        "positions": db.positions_of(p["id"]),   # own data only, all statuses
        "done": p["topic_idx"] >= len(topic_list()),
    }


# --- interview --------------------------------------------------------------------------------

@app.post("/answer")
async def answer(
    answer_id: str = Form(...),
    text: str | None = Form(None),
    redo_topic: str | None = Form(None),
    audio: UploadFile | None = File(None),
    x_participant_token: str | None = Header(None),
):
    p = me(x_participant_token)
    log = json.loads(p["answer_log"])
    if answer_id in log:                       # idempotent: repeat returns same result, no advance
        return log[answer_id]

    ts = topic_list()
    if redo_topic:                             # user re-answers a topic (state saved first)
        idx = next((i for i, t in enumerate(ts) if t["id"] == redo_topic), None)
        if idx is None:
            raise HTTPException(400, "unknown topic")
        db.update_participant(p["id"], topic_idx=idx, followup_used=0, pending_followup=None)
        db.upsert_position(p["id"], redo_topic, transcript="", review_status="pending", approved_at=None)
        p = db.get_participant(p["id"])
    if p["topic_idx"] >= len(ts):
        return {"done": True}
    topic = ts[p["topic_idx"]]
    f = flags.get_all()

    # 1) transcript: voice (Sarvam STT) or text
    if audio is not None:
        if not f["voice_mode"]:
            raise HTTPException(403, detail={"fallback": "text", "reason": "voice_mode_off"})
        data = await audio.read()
        try:
            transcript = sarvam.transcribe(data, audio.filename or "a.webm", audio.content_type or "audio/webm", p["language"])
        except sarvam.SarvamError:
            # question state is already saved; nothing is lost. Client continues in text on the same question.
            raise HTTPException(503, detail={"fallback": "text", "reason": "voice_unavailable"})
        finally:
            del data                            # audio is never stored
        if not transcript:
            raise HTTPException(422, detail={"fallback": "retry", "reason": "empty_transcript"})
    else:
        if not (text or "").strip():
            raise HTTPException(400, "empty answer")
        if not f["text_fallback"] and f["voice_mode"]:
            raise HTTPException(403, detail={"fallback": "voice", "reason": "text_disabled"})
        transcript = text.strip()[:2000]

    # 2) accumulate transcript against this topic
    pos = db.position_for(p["id"], topic["id"])
    full = ((pos["transcript"] + "\n") if pos and pos["transcript"] else "") + transcript
    db.upsert_position(p["id"], topic["id"], transcript=full, review_status="pending")

    # 3) at most ONE follow-up (separate interviewer call)
    if not p["followup_used"]:
        fu = extract.needs_followup(topic, full, p["language"])
        if fu:
            fu = localized(fu, p["language"]) if fu == topic.get("followup") else fu
            db.update_participant(p["id"], followup_used=1, pending_followup=fu)
            return _remember(p, log, answer_id, {"transcript": transcript, "followup": fu, "topic": topic["id"]})

    # 4) extraction (separate call), stored as PENDING until the person approves
    ex = extract.extract(topic, full, p["language"])
    position = db.upsert_position(p["id"], topic["id"], review_status="pending", approved_at=None, **ex)
    db.update_participant(p["id"], topic_idx=p["topic_idx"] + 1, followup_used=0, pending_followup=None)
    return _remember(p, log, answer_id, {"transcript": transcript, "position": position, "topic": topic["id"],
                                         "stance_options": topic["stance_options"]})


def _remember(p, log, answer_id, resp):
    log[answer_id] = resp
    db.update_participant(p["id"], answer_log=json.dumps(log, ensure_ascii=False))
    return resp


# --- review -----------------------------------------------------------------------------------

def _own_position(pos_id: str, token: str | None):
    p = db.participant_by_token(token or "")
    pos = db.get_position(pos_id)
    if not p or not pos or pos["participant_id"] != p["id"]:
        raise HTTPException(404, "position not found")      # also hides other people's ids
    return p, pos


class PatchIn(BaseModel):
    summary_native: str | None = None
    summary_english: str | None = None
    stance_key: str | None = None
    importance: int | None = None
    flexibility: int | None = None


@app.patch("/positions/{pos_id}")
def patch_position(pos_id: str, body: PatchIn, x_participant_token: str | None = Header(None)):
    p, pos = _own_position(pos_id, x_participant_token)
    upd = {k: v for k, v in body.model_dump().items() if v is not None}
    if "stance_key" in upd:
        topic = next(t for t in topic_list() if t["id"] == pos["topic"])
        opts = {o["key"]: o["scale"] for o in topic["stance_options"]}
        if upd["stance_key"] not in opts:
            raise HTTPException(400, "invalid stance")
        upd["stance_scale"] = opts[upd["stance_key"]]
        upd.setdefault("importance", pos["importance"] or 3)
        upd.setdefault("flexibility", pos["flexibility"] or 3)
        upd.update(needs_clarification=False, confidence=max(pos["confidence"] or 0, 0.8))
    for k in ("importance", "flexibility"):
        if k in upd and not 1 <= upd[k] <= 5:
            raise HTTPException(400, f"{k} must be 1-5")
    upd.update(review_status="pending", approved_at=None)   # any edit requires fresh approval
    return db.update_position(pos_id, **upd)


@app.post("/positions/{pos_id}/{action}")
def review_action(pos_id: str, action: str, x_participant_token: str | None = Header(None)):
    p, pos = _own_position(pos_id, x_participant_token)
    if action == "approve":
        if pos["stance_key"] is None or pos["needs_clarification"]:
            raise HTTPException(400, "pick a stance (edit) before approving")
        return db.update_position(pos_id, review_status="approved", approved_at=time.time())
    if action == "reject":
        return db.update_position(pos_id, review_status="rejected", approved_at=None)
    if action == "revoke":
        return db.update_position(pos_id, review_status="pending", approved_at=None)
    raise HTTPException(404)


# --- conversation map -------------------------------------------------------------------------

_tcache: dict = {}
_scache: dict = {}


def _their_text(pos: dict, their_lang: str, my_lang: str) -> dict:
    if their_lang == my_lang:
        return {"text": pos["summary_native"] or pos["summary_english"], "translated": False}
    if my_lang == "en-IN":
        return {"text": pos["summary_english"], "translated": True}
    key = (pos["id"], pos["approved_at"], my_lang)
    if key not in _tcache:
        try:
            _tcache[key] = sarvam.translate(pos["summary_english"], "en-IN", my_lang)
        except Exception:
            return {"text": pos["summary_english"], "translated": False,
                    "note": "Translation unavailable; showing English"}
    return {"text": _tcache[key], "translated": True}


@app.get("/sessions/{session_id}/map")
def conversation_map(session_id: str, x_participant_token: str | None = Header(None)):
    p = me(x_participant_token, session_id)
    others = [o for o in db.session_participants(session_id) if o["id"] != p["id"]]
    other = others[0] if others else None
    out = []
    for t in topic_list():
        mine = db.position_for(p["id"], t["id"])
        mine = mine if mine and mine["review_status"] == "approved" else None
        theirs = db.position_for(other["id"], t["id"]) if other else None
        theirs = theirs if theirs and theirs["review_status"] == "approved" else None   # approved only
        row = {"topic": t["id"], "question": localized(t["question"], p["language"], t.get("question_i18n")),
               "mine": mine, "theirs": None, "state": "waiting_for_you" if not mine else "waiting_for_partner",
               "comparison": None, "suggested_question": None, "discussed": bool(mine and mine["discussed"])}
        if theirs:
            row["theirs"] = {**public_view(theirs), **_their_text(theirs, other["language"], p["language"])}
        if mine and theirs:
            row["state"] = "both"
            row["comparison"] = compare.compare_topic(t["id"], mine, theirs)
            ck = (mine["id"], mine["approved_at"], theirs["id"], theirs["approved_at"])
            if ck not in _scache:
                a, b = ((mine, theirs) if p["role"] == "A" else (theirs, mine))
                _scache[ck] = extract.suggest_question(t, a["summary_english"], b["summary_english"])
            row["suggested_question"] = _scache[ck]
        out.append(row)
    return {"language": p["language"], "partner_synthetic": bool(other and other["is_seeded"]),
            "badge": "Only approved answers are shown", "topics": out,
            "note": "A conversation starter, not a compatibility score."}


@app.post("/sessions/{session_id}/topics/{topic_id}/discussed")
def mark_discussed(session_id: str, topic_id: str, x_participant_token: str | None = Header(None)):
    p = me(x_participant_token, session_id)
    pos = db.position_for(p["id"], topic_id)
    if not pos:
        raise HTTPException(404)
    return db.update_position(pos["id"], discussed=not pos["discussed"])


class TtsIn(BaseModel):
    text: str
    language: str = "en-IN"


@app.post("/tts")
def speak(body: TtsIn, x_participant_token: str | None = Header(None)):
    me(x_participant_token)
    if not flags.get_all()["tts_replies"]:
        raise HTTPException(403, "tts disabled")
    try:
        return Response(sarvam.tts(body.text, body.language), media_type="audio/wav")
    except sarvam.SarvamError:
        raise HTTPException(503, "tts unavailable")


@app.delete("/sessions/{session_id}")
def delete_session(session_id: str, x_participant_token: str | None = Header(None)):
    me(x_participant_token, session_id)
    db.delete_session(session_id)
    return {"deleted": True}


# --- health -----------------------------------------------------------------------------------

@app.get("/ready")
def ready():
    state = sarvam.breaker.current()
    flags.get_all()
    return {
        "db": db.ping(),
        "sarvam": ("degraded" if state != "closed" else "ok") if sarvam.configured() else "unconfigured",
        "flagsmith": flags.status["flagsmith"],
        "breaker_state": state,
        "error_counts": dict(sarvam.error_counts),
        "p95_latency": sarvam.p95_latency_ms(),
        "simulated_failure": sarvam.simulate["on"],
        "incident": incident,
    }


# --- demo-only endpoints (guarded) ------------------------------------------------------------

def _demo_guard():
    if os.getenv("ALLOW_DEMO_ADMIN") != "1":
        raise HTTPException(404)


class SimIn(BaseModel):
    on: bool
    scope: str = "voice"   # "voice" = STT+TTS only; "all" = every Sarvam call


@app.post("/admin/simulate-sarvam-failure")
def simulate_failure(body: SimIn):
    """DEMO ONLY. Makes Sarvam calls fail (clearly simulated), then drives real probe calls through the
    real retry+breaker path so the breaker opens, /ready degrades and the incident signal fires."""
    _demo_guard()
    sarvam.simulate.update(on=body.on, scope=body.scope if body.scope in ("voice", "all") else "voice")
    if body.on:
        for _ in range(sarvam.FAIL_THRESHOLD):
            try:
                sarvam.tts("probe", "en-IN")
            except sarvam.SarvamError:
                pass
    else:
        sarvam.breaker.success()   # recovery: close breaker; on_close clears the local override
        incident.update(nudgebee_signal=None, at=None)
    return ready()


@app.post("/admin/seed-partner-b")
def seed_partner_b(session_id: str, x_participant_token: str | None = Header(None)):
    """DEMO ONLY: add a clearly-labelled SYNTHETIC partner B with pre-approved positions."""
    _demo_guard()
    me(x_participant_token, session_id)
    with db.conn() as c:
        code = c.execute("SELECT join_code FROM sessions WHERE id=?", (session_id,)).fetchone()["join_code"]
    r = db.join_session(code, "hi-IN", seeded=True)
    if not isinstance(r, dict):
        raise HTTPException(409, "partner slot not available")
    now = time.time()
    persona = {
        "living_arrangement": ("with_parents", 1, 5, 1,
            "मुझे शादी के बाद माता-पिता के साथ संयुक्त परिवार में रहना पसंद है।",
            "I would prefer to live with my parents in a joint household after marriage.",
            "Living with my parents matters a lot to me."),
        "money_and_family": ("support_parents_first", 1, 4, 2,
            "मेरे लिए माता-पिता की ज़रूरतों का ध्यान रखना पहली प्राथमिकता है।",
            "Taking care of my parents' needs comes first for me.",
            "My parents' needs come first."),
        "career_relocation": ("stay_put", 1, 5, 1,
            "परिवार के पास रहना मेरे लिए बहुत मायने रखता है, इसलिए दूसरे शहर जाना मुश्किल होगा।",
            "Staying close to family matters a great deal to me, so moving to another city would be hard.",
            "Being near family matters a great deal."),
    }
    for tid, (key, scale, imp, flex, nat, en, quote) in persona.items():
        db.upsert_position(r["participant_id"], tid, stance_key=key, stance_scale=scale, importance=imp,
                           flexibility=flex, conditions=[], summary_native=nat, summary_english=en,
                           supporting_quote=quote, confidence=0.9, needs_clarification=False,
                           review_status="approved", approved_at=now, transcript="(synthetic persona)")
    db.update_participant(r["participant_id"], topic_idx=len(persona))
    return {"seeded": True, "synthetic": True}


# optional: serve the built frontend from the same origin
_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="web")
