"""Flagsmith client (server-side only) + local defaults + NudgeBee incident signal."""
import copy
import json
import logging
import os
import time

import httpx

log = logging.getLogger("pheras.flags")

LANGS = {"te-IN": "తెలుగు", "hi-IN": "हिन्दी", "en-IN": "English"}

# Topic config: served from Flagsmith `interview_topics` (JSON); this is the local default.
# stance_options define the ordered scale per topic (deterministic comparison depends on it).
TOPICS_DEFAULT = [
    {
        "id": "living_arrangement",
        "question": "Where would you ideally like to live after marriage?",
        "question_i18n": {
            "te-IN": "పెళ్లి తర్వాత మీరు ఆదర్శంగా ఎక్కడ నివసించాలనుకుంటున్నారు?",
            "hi-IN": "शादी के बाद आप आदर्श रूप से कहाँ रहना चाहेंगे?",
        },
        "followup": "Could you say a bit more about what matters most in that choice?",
        "stance_options": [
            {"key": "with_parents", "scale": 1, "label": "With parents / joint household"},
            {"key": "independent_near_family", "scale": 2, "label": "Own home, near family"},
            {"key": "independent_elsewhere", "scale": 3, "label": "Own home, another city in India"},
            {"key": "abroad_or_open", "scale": 4, "label": "Abroad, or open to anywhere"},
        ],
        "default_opening": "Where would each of you picture home in the first few years, and what would make that feel right?",
    },
    {
        "id": "money_and_family",
        "question": "How do you imagine managing financial responsibilities towards your parents and your household?",
        "question_i18n": {
            "te-IN": "మీ తల్లిదండ్రుల పట్ల మరియు మీ ఇంటి పట్ల ఆర్థిక బాధ్యతలను ఎలా నిర్వహించాలని మీరు భావిస్తున్నారు?",
            "hi-IN": "आप अपने माता-पिता और अपने घर के प्रति आर्थिक ज़िम्मेदारियों को कैसे संभालने की कल्पना करते हैं?",
        },
        "followup": "Could you give an example of how that would work month to month?",
        "stance_options": [
            {"key": "support_parents_first", "scale": 1, "label": "Parents' needs come first"},
            {"key": "shared_with_family", "scale": 2, "label": "Shared between parents and household"},
            {"key": "household_first_some_support", "scale": 3, "label": "Household first, some support to parents"},
            {"key": "separate_finances", "scale": 4, "label": "Mostly separate / own household only"},
        ],
        "default_opening": "How would you like to decide together how much goes to each family and to your own household?",
    },
    {
        "id": "career_relocation",
        "question": "How would you approach a situation where one partner gets a career opportunity in another city or country?",
        "question_i18n": {
            "te-IN": "ఒక భాగస్వామికి మరో నగరంలో లేదా దేశంలో కెరీర్ అవకాశం వస్తే మీరు దాన్ని ఎలా సమీపిస్తారు?",
            "hi-IN": "अगर किसी एक साथी को दूसरे शहर या देश में करियर का मौका मिले, तो आप इसे कैसे संभालेंगे?",
        },
        "followup": "What would you need to feel good about such a move, or about staying?",
        "stance_options": [
            {"key": "stay_put", "scale": 1, "label": "Prefer to stay where family is"},
            {"key": "move_only_if_necessary", "scale": 2, "label": "Move only if clearly necessary"},
            {"key": "decide_together_case_by_case", "scale": 3, "label": "Decide together, case by case"},
            {"key": "move_for_opportunity", "scale": 4, "label": "Open to moving for opportunity"},
        ],
        "default_opening": "If a big opportunity came up somewhere else, how would you want to make that decision together?",
    },
]

DEFAULTS = {
    "voice_mode": True,
    "text_fallback": True,
    "tts_replies": True,
    "enabled_languages": ["te-IN", "hi-IN", "en-IN"],
}
CACHE_TTL = 3.0

_cache = {"t": 0.0, "val": None}
_override: dict = {}      # last-resort local self-heal (see ops_override)
_client = None
_client_tried = False
status = {"flagsmith": "unconfigured", "last_error": None}


def _get_client():
    global _client, _client_tried
    if _client_tried:
        return _client
    _client_tried = True
    key = os.getenv("FLAGSMITH_ENVIRONMENT_KEY")
    if not key:
        return None
    try:
        from flagsmith import Flagsmith

        kw = {"environment_key": key, "request_timeout_seconds": 2, "retries": 0, "enable_analytics": True}
        if os.getenv("FLAGSMITH_API_URL"):
            kw["api_url"] = os.getenv("FLAGSMITH_API_URL")
        _client = Flagsmith(**kw)
    except Exception as e:  # SDK init failure must never take the app down
        log.warning("flagsmith init failed: %s", type(e).__name__)
        status["last_error"] = type(e).__name__
    return _client


def reset_client():
    """Re-create the Flagsmith client (e.g. after the key in .env changed)."""
    global _client, _client_tried
    _client, _client_tried = None, False
    _cache["t"] = 0


def _merge_topics(remote):
    """Remote may change question text/follow-ups; scale definitions stay local unless supplied."""
    local = {t["id"]: t for t in TOPICS_DEFAULT}
    out = []
    for t in remote:
        base = copy.deepcopy(local.get(t.get("id")))
        if not base or not isinstance(t.get("question"), str):
            continue
        if t["question"].strip() != base["question"]:
            base["question_i18n"] = {}   # edited remotely -> stale translations dropped, re-translated at runtime
            base["question"] = t["question"].strip()
        for k in ("followup", "default_opening", "stance_options"):
            if t.get(k):
                base[k] = t[k]
        out.append(base)
    return out or copy.deepcopy(TOPICS_DEFAULT)


def _parse_json(raw):
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _value(fl, name):
    try:
        return fl.get_feature_value(name)
    except Exception:
        return None


def get_all(force: bool = False) -> dict:
    now = time.time()
    if not force and _cache["val"] and now - _cache["t"] < CACHE_TTL:
        return _apply_override(_cache["val"])
    val = {**copy.deepcopy(DEFAULTS), "interview_topics": copy.deepcopy(TOPICS_DEFAULT), "source": "local_default"}
    c = _get_client()
    if c:
        try:
            fl = c.get_environment_flags()
            for name in DEFAULTS:
                if name == "enabled_languages":
                    langs = _parse_json(_value(fl, name))
                    if isinstance(langs, list) and langs:
                        val[name] = [l for l in langs if l in LANGS] or DEFAULTS[name]
                else:
                    try:
                        val[name] = bool(fl.is_feature_enabled(name))
                    except Exception:
                        pass  # flag missing in this environment -> keep default
            topics = _parse_json(_value(fl, "interview_topics"))
            if isinstance(topics, list):
                val["interview_topics"] = _merge_topics(topics)
            val["source"] = "flagsmith"
            status.update(flagsmith="ok", last_error=None)
        except Exception as e:
            status.update(flagsmith="degraded", last_error=type(e).__name__)
    _cache.update(t=now, val=val)
    return _apply_override(val)


_id_cache: dict = {}


def get_for(participant_id: str, language: str) -> dict:
    """Per-participant flags via Flagsmith identities + segments (e.g. a "language = te-IN" segment).
    Identifier is the random participant id; the ONLY trait is language. No names, answers or contact data.
    Falls back to environment-level flags if identity evaluation is unavailable."""
    base = get_all()
    c = _get_client()
    if not c or base.get("source") != "flagsmith":
        return base
    hit = _id_cache.get(participant_id)
    now = time.time()
    if hit and now - hit[0] < CACHE_TTL:
        return _apply_override({**base, **hit[1]})
    per = {}
    try:
        fl = c.get_identity_flags(identifier=participant_id, traits={"language": language})
        for name in ("voice_mode", "text_fallback", "tts_replies"):
            try:
                per[name] = bool(fl.is_feature_enabled(name))
            except Exception:
                pass
        per["segment_evaluated"] = True
    except Exception:
        per = {}
    if len(_id_cache) > 500:
        _id_cache.clear()
    _id_cache[participant_id] = (now, per)
    return _apply_override({**base, **per})


def _apply_override(val):
    if _override:
        return {**val, **_override, "override_active": True}
    return {**val, "override_active": False}


def topics() -> list:
    return get_all()["interview_topics"]


# --- NudgeBee incident signal + last-resort local self-heal ---------------------------------

def notify_nudgebee(event: str, detail: dict) -> str:
    """POST a content-free incident signal (no transcripts, names or quotes) to NudgeBee.
    Returns 'sent' | 'unconfigured' | 'failed'. The payload shape is OUR choice: NudgeBee's
    inbound-webhook schema was not verifiable from public docs (see README)."""
    url = os.getenv("NUDGEBEE_WEBHOOK_URL")
    if not url:
        return "unconfigured"
    payload = {
        "source": "before-the-pheras",
        "event": event,
        "severity": "warning",
        "summary": "Sarvam voice dependency degraded: circuit breaker open",
        "labels": {"service": "pheras-backend", "dependency": "sarvam", "env": os.getenv("APP_ENV", "demo")},
        "detail": detail,   # counts / states only
        "suggested_runbook": "set Flagsmith voice_mode=false, text_fallback=true",
        "ts": time.time(),
    }
    try:
        r = httpx.post(url, json=payload, timeout=3)
        return "sent" if r.status_code < 300 else "failed"
    except Exception:
        return "failed"


def ops_override(on: bool):
    """LADDER STEP 4 (last resort, labelled in README): the app itself flips voice off / text on
    when SELF_HEAL_FALLBACK=1. Cleared when the breaker closes."""
    _override.clear()
    if on:
        _override.update(voice_mode=False, text_fallback=True)
    _cache["t"] = 0
