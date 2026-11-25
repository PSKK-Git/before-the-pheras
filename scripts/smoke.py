"""End-to-end smoke test on the TEXT path. Synthetic data only.

    ALLOW_DEMO_ADMIN=1 uvicorn main:app --port 8000      # in backend/
    python scripts/smoke.py [http://localhost:8000]

Checks: session/join, 3 topics with at most one follow-up, extraction -> edit -> approve, privacy
(partner cannot see pending), seeded synthetic partner, deterministic map, idempotent /answer,
simulated Sarvam failure -> breaker open -> /ready degraded -> text continues on the same question,
recovery, delete.
"""
import sys
import uuid

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
c = httpx.Client(base_url=BASE, timeout=60)
fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def H(tok):
    return {"X-Participant-Token": tok}


def answer(tok, text, answer_id=None, audio=False):
    data = {"answer_id": answer_id or uuid.uuid4().hex}
    files = None
    if audio:
        files = {"audio": ("a.webm", b"\x00fake", "audio/webm")}
    else:
        data["text"] = text
    return c.post("/answer", data=data, files=files, headers=H(tok))


def me(sess):
    return c.get(f"/sessions/{sess['session_id']}/me", headers=H(sess["token"])).json()


# 1. session + join
s = c.post("/sessions").json()
a = c.post("/sessions/join", json={"join_code": s["join_code"], "language": "te-IN"}).json()
check("create + join as A", a.get("role") == "A" and "token" in a)
ta, sid = a["token"], a["session_id"]
check("config served", c.get("/config").json()["voice_mode"] in (True, False))

# 2. interview: 3 topics. First answer is deliberately vague -> exactly one follow-up
q = me(a)["question"]
check("first question is topic 1 (1/3)", q["topic"] == "living_arrangement" and q["total"] == 3)
r1 = answer(ta, "not sure").json()
check("vague answer -> one follow-up", "followup" in r1)
r1b = answer(ta, "I would like our own home near my family, that is very important to me.").json()
check("follow-up answered -> position extracted (pending)",
      r1b["position"]["review_status"] == "pending" and r1b["position"]["stance_key"] == "independent_near_family",
      str(r1b)[:200])

# idempotency: same answer_id twice must not advance or duplicate
aid = uuid.uuid4().hex
t2 = "Parents come first for money, but we can share the household costs as well, I am open to it."
r2 = answer(ta, t2, aid).json()
r2dup = answer(ta, t2, aid).json()
st = me(a)
check("idempotent /answer (repeat = same result, one advance)",
      r2 == r2dup and st["question"]["topic"] == "career_relocation", str(st["question"]))

answer(ta, "I would be open to moving for opportunity, we should decide together, it is flexible.")
check("3 topics done", me(a)["done"])

# 3. review: edit then approve each position
pos = {p["topic"]: p for p in me(a)["positions"]}
check("3 positions pending", len(pos) == 3 and all(p["review_status"] == "pending" for p in pos.values()))
ed = c.patch(f"/positions/{pos['living_arrangement']['id']}", json={"importance": 5, "flexibility": 2}, headers=H(ta)).json()
check("edit keeps pending and applies", ed["importance"] == 5 and ed["review_status"] == "pending")

# 4. privacy: partner B joins, must not see A's pending data
bj = c.post("/sessions/join", json={"join_code": s["join_code"], "language": "hi-IN"}).json()
tb = bj["token"]
mb = c.get(f"/sessions/{sid}/map", headers=H(tb)).json()
check("B sees nothing of A while pending", all(t["theirs"] is None for t in mb["topics"]))
check("B cannot touch A's position by id",
      c.patch(f"/positions/{pos['living_arrangement']['id']}", json={}, headers=H(tb)).status_code == 404)
check("third participant is rejected",
      c.post("/sessions/join", json={"join_code": s["join_code"], "language": "en-IN"}).status_code == 409)
for p in pos.values():
    ok = c.post(f"/positions/{p['id']}/approve", headers=H(ta))
    check(f"approve {p['topic']}", ok.status_code == 200 and ok.json()["review_status"] == "approved", ok.text[:120])

# 5. demo: seeded synthetic partner in a fresh session
s2 = c.post("/sessions").json()
a2 = c.post("/sessions/join", json={"join_code": s2["join_code"], "language": "te-IN"}).json()
for t in ("I would like our own home near my family, very important.",
          "We could share between parents and household, I am flexible.",
          "I would be open to move for opportunity abroad, it is important to me, definitely."):
    answer(a2["token"], t)
for p in me(a2)["positions"]:
    c.post(f"/positions/{p['id']}/approve", headers=H(a2["token"]))
sd = c.post("/admin/seed-partner-b", params={"session_id": a2["session_id"]}, headers=H(a2["token"]))
check("seed synthetic partner B", sd.status_code == 200 and sd.json().get("synthetic"), sd.text[:120])
m = c.get(f"/sessions/{a2['session_id']}/map", headers=H(a2["token"])).json()
check("map flags partner as synthetic", m["partner_synthetic"] is True)
labels = {t["topic"]: t["comparison"]["label"] for t in m["topics"] if t["comparison"]}
print("     labels:", labels)
check("map has all 3 comparisons with explanations",
      len(labels) == 3 and all(t["comparison"]["explanation"] for t in m["topics"]))
check("map shows an Important difference", "Important difference" in labels.values())
check("no score/percent in map payload", "score" not in str(m).lower().replace("compatibility score", "") and "%" not in str(m))
check("every label is one of the three allowed",
      set(labels.values()) <= {"Aligned", "Needs discussion", "Important difference"})
check("suggested question present and is a question",
      all(t["suggested_question"].endswith("?") for t in m["topics"] if t["comparison"]))
check("partner's quote and transcript are not exposed",
      all("supporting_quote" not in (t["theirs"] or {}) and "transcript" not in (t["theirs"] or {}) for t in m["topics"]))

# 6. reliability: simulated Sarvam failure, breaker opens, text continues, same question
c.post("/admin/simulate-sarvam-failure", json={"on": False})
s3 = c.post("/sessions").json()
a3 = c.post("/sessions/join", json={"join_code": s3["join_code"], "language": "te-IN"}).json()
qb = me(a3)["question"]
rd = c.post("/admin/simulate-sarvam-failure", json={"on": True}).json()
check("breaker open + /ready degraded after simulated failures",
      rd["breaker_state"] == "open" and rd["error_counts"]["tts"] >= 3, str(rd))
rv = answer(a3["token"], None, audio=True)
check("voice answer during outage -> 503 with text fallback",
      rv.status_code == 503 and rv.json()["detail"]["fallback"] == "text", rv.text[:150])
qa = me(a3)["question"]
check("same question retained after voice failure", qa["topic"] == qb["topic"] and qa["index"] == qb["index"])
rt = answer(a3["token"], "I would like our own home near my family, very important.")
check("text answer works during outage", rt.status_code == 200 and "position" in rt.json(), rt.text[:150])
c.post("/admin/simulate-sarvam-failure", json={"on": False})
check("recovery closes breaker", c.get("/ready").json()["breaker_state"] == "closed")

# 7. delete wipes everything
for sess in (a, a2, a3):
    c.delete(f"/sessions/{sess['session_id']}", headers=H(sess["token"]))
check("deleted session's token no longer valid", c.get(f"/sessions/{sid}/me", headers=H(ta)).status_code == 401)

print("\nRESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
