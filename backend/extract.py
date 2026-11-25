"""Interview helper + position extraction: separate LLM operations, Pydantic-validated, one retry.

Two distinct Sarvam chat calls (per the brief):
  1. needs_followup()  - interviewer: is the answer vague? at most one follow-up per topic.
  2. extract()         - extractor: transcript -> structured position JSON.
When SARVAM_API_KEY is absent, a small keyword heuristic stands in (offline demo/smoke mode only).
"""
import json
import re

from pydantic import BaseModel, Field, ValidationError, field_validator

import sarvam

LANG_NAMES = {"te-IN": "Telugu", "hi-IN": "Hindi", "en-IN": "English"}


class Extraction(BaseModel):
    topic: str
    stance_key: str
    stance_scale: int
    importance: int = Field(ge=1, le=5)
    flexibility: int = Field(ge=1, le=5)
    conditions: list[str] = []
    summary_native: str
    summary_english: str
    supporting_quote: str
    confidence: float = Field(ge=0, le=1)
    needs_clarification: bool = False

    @field_validator("conditions", mode="before")
    @classmethod
    def _conds(cls, v):
        return [str(x) for x in v][:6] if isinstance(v, list) else []


def _repair(s: str) -> str:
    """The chat model sometimes leaves long string values (e.g. Telugu summaries) unquoted.
    Quote any value that is not already JSON, then fix up missing/trailing commas."""
    def q(m):
        return f'"{m.group(1)}": {json.dumps(m.group(2).strip().rstrip(","))},'
    s = re.sub(r'"(\w+)":[ \t]*(?![ \t"\[{\-\d]|true\b|false\b|null\b)([^\n]+)', q, s)
    s = re.sub(r'(["\d\]}]|true|false|null)[ \t]*\n(\s*")', r"\1,\n\2", s)
    return re.sub(r",(\s*[}\]])", r"\1", s)


def _json_from(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise ValueError("no json")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return json.loads(_repair(m.group(0)))


def _looks_unsure(t: str) -> bool:
    return bool(re.search(r"not sure|don'?t know|no idea|maybe|తెలియదు|తెలీదు|पता नहीं", t, re.I))


def needs_followup(topic: dict, transcript: str, lang: str) -> str | None:
    """Return a follow-up question or None. Caller enforces the at-most-one rule."""
    if len(transcript.split()) >= 12 and not _looks_unsure(transcript):
        return None  # substantive enough; skip the LLM call
    fu = topic.get("followup")
    if sarvam.configured():
        try:
            out = sarvam.chat(
                "You are a neutral interviewer. Decide if the answer is too vague to extract a position. "
                'Reply ONLY JSON: {"vague": true|false, "followup": "<one short neutral question in '
                f'{LANG_NAMES.get(lang, "English")}>"}}. Never suggest a right answer or imply traditional or modern is better.',
                f"Topic question: {topic['question']}\nAnswer: {transcript}",
                max_tokens=200,
            )
            d = _json_from(out)
            return (d.get("followup") or fu) if d.get("vague") else None
        except Exception:
            pass
    return fu if len(transcript.split()) < 8 or _looks_unsure(transcript) else None


def extract(topic: dict, transcript: str, lang: str) -> dict:
    """Returns a dict of position columns. Never raises: bad output -> needs_clarification."""
    opts = {o["key"]: o["scale"] for o in topic["stance_options"]}
    if not sarvam.configured():
        return _offline_extract(topic, transcript, opts)
    sys = (
        "You extract a person's stated position from their interview answer. Be faithful and neutral; "
        "never judge. Reply ONLY with JSON matching exactly these keys: topic, stance_key, stance_scale, importance(1-5), "
        "flexibility(1-5; higher = more open to alternatives), conditions(list of short snake_case strings), "
        f"summary_native (1-2 sentences in {LANG_NAMES.get(lang, 'English')}), summary_english, "
        "supporting_quote (a short verbatim quote from the answer), confidence(0-1; extraction quality only), "
        "needs_clarification(bool). stance_key MUST be one of: "
        + ", ".join(f"{k}={s}" for k, s in opts.items())
        + ". If the person says they are unsure, set needs_clarification=true and confidence<=0.4; do not force a stance."
        " Output strict JSON: every string value in double quotes, no markdown."
    )
    user = f"topic: {topic['id']}\nquestion: {topic['question']}\nanswer: {transcript}"
    for attempt in range(2):
        try:
            raw = sarvam.chat(sys, user if attempt == 0 else user + "\nReturn valid JSON only.", max_tokens=700)
            e = Extraction(**_json_from(raw))
            if e.stance_key not in opts:
                raise ValueError("bad stance_key")
            d = e.model_dump()
            d["stance_scale"] = opts[e.stance_key]    # scale is authoritative from topic config
            d["topic"] = topic["id"]
            return d
        except (ValidationError, ValueError, json.JSONDecodeError, KeyError):
            continue
        except sarvam.SarvamError:
            break
    return _needs_clarification(transcript)


def _needs_clarification(transcript):
    return {
        "stance_key": None, "stance_scale": None, "importance": None, "flexibility": None, "conditions": [],
        "summary_native": "", "summary_english": "", "supporting_quote": transcript[:160],
        "confidence": 0.0, "needs_clarification": True,
    }


KEYWORDS = {
    "living_arrangement": [
        ("abroad_or_open", r"abroad|foreign|overseas|anywhere|usa|uk\b"),
        ("independent_elsewhere", r"another city|different city|bangalore|bengaluru|mumbai|elsewhere"),
        ("independent_near_family", r"near|nearby|close to|same city|own (house|home|place)"),
        ("with_parents", r"with (my )?(parents|family|in-laws)|joint|together with|తల్లిదండ్రుల"),
    ],
    "money_and_family": [
        ("support_parents_first", r"parents? (first|come first)|fully support|main responsibility"),
        ("separate_finances", r"separate|own household only|independent finance"),
        ("household_first_some_support", r"household first|some support|fixed amount|small amount"),
        ("shared_with_family", r"shar|together|both families|equal"),
    ],
    "career_relocation": [
        ("move_for_opportunity", r"move for|open to mov|go for it|relocate happily|opportunity is important"),
        ("stay_put", r"stay|not (relocate|move)|wouldn'?t move|won'?t move|rather not"),
        ("move_only_if_necessary", r"only if|necessary|no other option"),
        ("decide_together_case_by_case", r"together|discuss|case by case|depends"),
    ],
}


def _offline_extract(topic, transcript, opts):
    """Keyword stand-in used ONLY when no Sarvam key is configured (offline demo / smoke test)."""
    # later lines (the follow-up answer) win over an earlier "not sure"
    last = transcript.strip().splitlines()[-1] if transcript.strip() else ""
    if _looks_unsure(last) and not any(re.search(p, last, re.I) for _, p in KEYWORDS.get(topic["id"], [])):
        d = _needs_clarification(transcript)
        d["summary_native"] = d["summary_english"] = "Not sure yet."
        d["confidence"] = 0.3
        return d
    for key, pat in KEYWORDS.get(topic["id"], []):
        if re.search(pat, transcript, re.I):
            importance = 5 if re.search(r"very|important|must|definitely|always|strongly", transcript, re.I) else 3
            flex = 1 if re.search(r"must|definitely|non.?negotiable|never|strongly", transcript, re.I) else (
                4 if re.search(r"open|flexible|depends|willing", transcript, re.I) else 3)
            quote = transcript.strip()[:160]
            return {
                "stance_key": key, "stance_scale": opts[key], "importance": importance, "flexibility": flex,
                "conditions": [], "summary_native": quote, "summary_english": quote,
                "supporting_quote": quote, "confidence": 0.7, "needs_clarification": False,
            }
    return _needs_clarification(transcript)


def suggest_question(topic: dict, a_summary: str, b_summary: str) -> str:
    """The LLM may only PHRASE one neutral opening question. Falls back to the topic default."""
    if not sarvam.configured():
        return topic["default_opening"]
    try:
        out = sarvam.chat(
            "Write ONE neutral, open-ended question in English that two partners could use to start a conversation "
            "about this topic, based on both short summaries. No advice, no judgement, no scores, no 'should'. "
            "Reply with the question only.",
            f"Topic: {topic['question']}\nPartner 1: {a_summary}\nPartner 2: {b_summary}",
            max_tokens=120,
        )
        q = re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip().strip('"')
        return q if 10 < len(q) < 300 and q.endswith("?") else topic["default_opening"]
    except Exception:
        return topic["default_opening"]
