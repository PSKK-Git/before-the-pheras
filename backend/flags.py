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
    {
        "id": "household_roles",
        "question": "How do you see household work and childcare being shared between the two of you?",
        "question_i18n": {
            "te-IN": "ఇంటి పనులు మరియు పిల్లల సంరక్షణను మీ ఇద్దరి మధ్య ఎలా పంచుకోవాలని మీరు అనుకుంటున్నారు?",
            "hi-IN": "आप घर के काम और बच्चों की देखभाल को आप दोनों के बीच कैसे बाँटना चाहेंगे?",
        },
        "followup": "What would a normal weekday look like at home for each of you?",
        "stance_options": [
            {"key": "one_partner_mainly", "scale": 1, "label": "Mostly one partner, traditional roles"},
            {"key": "one_leads_other_helps", "scale": 2, "label": "One leads, the other helps"},
            {"key": "roughly_equal", "scale": 3, "label": "Roughly equal"},
            {"key": "fully_shared_or_outsourced", "scale": 4, "label": "Fully shared / flexible, or outsourced"},
        ],
        "default_opening": "What would a fair split of the work at home look like to each of you?",
    },
    {
        "id": "children",
        "question": "Would you like to have children, and if so, when?",
        "question_i18n": {
            "te-IN": "మీరు పిల్లలను కనాలనుకుంటున్నారా, అయితే ఎప్పుడు?",
            "hi-IN": "क्या आप बच्चे चाहते हैं, और अगर हाँ, तो कब?",
        },
        "followup": "What would need to be in place before you felt ready?",
        "stance_options": [
            {"key": "soon_after_marriage", "scale": 1, "label": "Soon after marriage (1-2 years)"},
            {"key": "in_a_few_years", "scale": 2, "label": "In a few years (2-4 years)"},
            {"key": "when_settled_later", "scale": 3, "label": "Later, once settled (5+ years)"},
            {"key": "unsure_or_no", "scale": 4, "label": "Unsure, or may not want children"},
        ],
        "default_opening": "What does each of you picture for children, and what would make you feel ready?",
    },
    {
        "id": "career_after_marriage",
        "question": "How do you see both of your careers after marriage, including after children?",
        "question_i18n": {
            "te-IN": "పెళ్లి తర్వాత, పిల్లలు పుట్టిన తర్వాత కూడా, మీ ఇద్దరి కెరీర్‌లను మీరు ఎలా చూస్తున్నారు?",
            "hi-IN": "शादी के बाद, और बच्चों के बाद भी, आप दोनों के करियर को आप कैसे देखते हैं?",
        },
        "followup": "If one of you had to slow down for a while, how would you decide who?",
        "stance_options": [
            {"key": "one_steps_back", "scale": 1, "label": "One partner steps back from work"},
            {"key": "one_primary", "scale": 2, "label": "One career is primary, the other flexible"},
            {"key": "both_with_adjustments", "scale": 3, "label": "Both continue, with adjustments"},
            {"key": "both_equal_priority", "scale": 4, "label": "Both careers are equal priority"},
        ],
        "default_opening": "How would each of you like your work life to look five years after the wedding?",
    },
    {
        "id": "faith_and_rituals",
        "question": "What role will religion, rituals and festivals play in your home?",
        "question_i18n": {
            "te-IN": "మీ ఇంట్లో మతం, ఆచారాలు మరియు పండుగలకు ఎలాంటి ప్రాధాన్యం ఉంటుంది?",
            "hi-IN": "आपके घर में धर्म, रीति-रिवाज़ और त्योहारों की क्या भूमिका होगी?",
        },
        "followup": "Which traditions matter most to you to keep, and which are you relaxed about?",
        "stance_options": [
            {"key": "central_daily", "scale": 1, "label": "Central, practised daily"},
            {"key": "regular_observance", "scale": 2, "label": "Regular: major rituals and festivals"},
            {"key": "cultural_occasional", "scale": 3, "label": "Occasional, mostly cultural"},
            {"key": "personal_choice", "scale": 4, "label": "Mostly secular / each person's choice"},
        ],
        "default_opening": "Which traditions would each of you most want to carry into your home?",
    },
    {
        "id": "wedding_and_gifts",
        "question": "What are your views on wedding spending and on gifts or expectations between the two families?",
        "question_i18n": {
            "te-IN": "పెళ్లి ఖర్చు గురించి, రెండు కుటుంబాల మధ్య బహుమతులు లేదా అంచనాల గురించి మీ అభిప్రాయం ఏమిటి?",
            "hi-IN": "शादी के खर्च और दोनों परिवारों के बीच तोहफ़ों या अपेक्षाओं के बारे में आपकी क्या राय है?",
        },
        "followup": "Who should have the final say on these decisions?",
        "stance_options": [
            {"key": "grand_traditional", "scale": 1, "label": "Large wedding, families' traditions decide"},
            {"key": "traditional_within_limits", "scale": 2, "label": "Traditional, within clear limits"},
            {"key": "simple_minimal_exchange", "scale": 3, "label": "Simple wedding, minimal exchanges"},
            {"key": "very_simple_none", "scale": 4, "label": "Very simple or registered, no exchanges"},
        ],
        "default_opening": "What kind of wedding feels right to each of you, and who should decide?",
    },
]

# "Why we ask": the evidence behind each topic, shown to the couple when they pick topics.
TOPIC_NOTES = {
    "living_arrangement": ("Family", "In-laws and joint-family living are the most-cited source of arguments in surveys of Indian couples, in joint and nuclear homes alike.", "The Swaddle reader survey on Indian marriages"),
    "money_and_family": ("Money", "Supporting parents is common, and household money is often controlled by one side; couples rarely agree the split before marriage.", "IMRB / Shaadi.com marriage survey"),
    "career_relocation": ("Career", "Traditionally the bride moves to the husband's family home; who relocates for whose career is usually assumed, not discussed.", "Allendorf, arranged-marriage research"),
    "household_roles": ("Home", "An unfair division of housework and childcare is named as a major source of conflict by Indian couples.", "The Swaddle reader survey on Indian marriages"),
    "children": ("Family", "Children and parenting is a core area in standard premarital inventories, and timing is often decided by families rather than the couple.", "Premarital education research (Carroll & Doherty, 2003)"),
    "career_after_marriage": ("Career", "Whether one partner (usually the wife) steps back from work after marriage or children is often assumed in arranged matches rather than discussed.", "Premarital education research; arranged-marriage studies"),
    "faith_and_rituals": ("Values", "Religious practice and festivals shape daily life and both families' expectations; differences surface after the wedding, not before.", "Premarital inventories: spiritual beliefs domain"),
    "wedding_and_gifts": ("Money", "Wedding costs and gift expectations between families are a frequent early strain. Dowry is illegal under the Dowry Prohibition Act, 1961.", "Dowry Prohibition Act, 1961"),
}
for _t in TOPICS_DEFAULT:
    _cat, _why, _src = TOPIC_NOTES[_t["id"]]
    _t.update(category=_cat, why=_why, source=_src)
DEFAULT_TOPIC_IDS = ["living_arrangement", "money_and_family", "career_relocation"]

DEFAULTS = {
    "voice_mode": True,
    "text_fallback": True,
    "tts_replies": True,
    "ai_followups": True,          # kill switch for the LLM follow-up question
    "enabled_languages": ["te-IN", "hi-IN", "en-IN"],
}
BOOL_FLAGS = ("voice_mode", "text_fallback", "tts_replies", "ai_followups")
# remote-config values (not on/off): max follow-ups per topic, and the multivariate A/B tone
VALUE_DEFAULTS = {"max_followups": 1, "question_tone": "gentle"}
TONES = ("gentle", "direct")
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
        # Local evaluation (server-side key only): the environment document is polled every few
        # seconds and identities/segments/multivariate splits are evaluated in-process. No per-request
        # network hop, and participant traits never leave our server.
        local = key.startswith("ser.") and os.getenv("FLAGSMITH_LOCAL_EVAL", "1") == "1"
        if local:
            kw.update(enable_local_evaluation=True, environment_refresh_interval_seconds=5)
        _client = Flagsmith(**kw)
        status["mode"] = "local_evaluation" if local else "remote_evaluation"
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
    # The whole research-backed bank always stays available; Flagsmith edits topics by id.
    out = copy.deepcopy(TOPICS_DEFAULT)
    by_id = {t["id"]: t for t in out}
    for t in remote:
        base = by_id.get(t.get("id")) if isinstance(t, dict) else None
        if not base:
            continue
        if isinstance(t.get("question"), str) and t["question"].strip() != base["question"]:
            base["question_i18n"] = {}   # edited remotely -> stale translations dropped, re-translated at runtime
            base["question"] = t["question"].strip()
        for k in ("followup", "default_opening", "stance_options", "why"):
            if t.get(k):
                base[k] = t[k]
    return out


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


def _values(fl) -> dict:
    """Remote-config values; anything missing or malformed keeps its local default."""
    out = {}
    try:
        out["max_followups"] = max(0, min(2, int(str(_value(fl, "max_followups")).strip())))
    except Exception:
        pass
    tone = _value(fl, "question_tone")
    if isinstance(tone, str) and tone.strip() in TONES:
        out["question_tone"] = tone.strip()
    return out


def get_all(force: bool = False) -> dict:
    now = time.time()
    if not force and _cache["val"] and now - _cache["t"] < CACHE_TTL:
        return _apply_override(_cache["val"])
    val = {**copy.deepcopy(DEFAULTS), **VALUE_DEFAULTS, "interview_topics": copy.deepcopy(TOPICS_DEFAULT),
           "source": "local_default"}
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
            val.update(_values(fl))
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
        for name in BOOL_FLAGS:
            try:
                per[name] = bool(fl.is_feature_enabled(name))
            except Exception:
                pass
        per.update(_values(fl))   # question_tone here is this identity's multivariate (A/B) variant
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


# --- Flagsmith Admin API: the approved runbook flips the REAL flag (shows in dashboard + audit log) ---

ADMIN_BASE = os.getenv("FLAGSMITH_ADMIN_API_URL", "https://api.flagsmith.com/api/v1").rstrip("/")
_feature_ids: dict = {}


def admin_configured() -> bool:
    return bool(os.getenv("FLAGSMITH_ADMIN_API_KEY") and os.getenv("FLAGSMITH_ENV_CLIENT_KEY"))


def admin_set_flag(name: str, enabled: bool) -> str:
    """Set the environment-default state of a flag. Returns 'ok' | 'unconfigured' | 'failed'."""
    if not admin_configured():
        return "unconfigured"
    env = os.environ["FLAGSMITH_ENV_CLIENT_KEY"].strip()
    h = {"Authorization": f"Api-Key {os.environ['FLAGSMITH_ADMIN_API_KEY'].strip()}"}
    try:
        with httpx.Client(base_url=ADMIN_BASE, headers=h, timeout=20) as s:   # admin API calls take ~1-5 s
            info = s.get(f"/environments/{env}/").raise_for_status().json()
            if name not in _feature_ids:
                feats = s.get(f"/projects/{info['project']}/features/", params={"search": name}).raise_for_status().json()
                _feature_ids[name] = next(f["id"] for f in feats["results"] if f["name"] == name)
            fid = _feature_ids[name]
            if info.get("use_v2_feature_versioning"):
                # Versioned environments reject direct edits: publish a new feature version instead
                # (it shows in the feature's History tab and the audit log).
                s.post(f"/environments/{info['id']}/features/{fid}/versions/", json={
                    "publish_immediately": True,
                    "feature_states_to_update": [{"feature_segment": None, "enabled": enabled,
                                                  "feature_state_value": {"type": "unicode", "string_value": ""}}],
                }).raise_for_status()
            else:
                states = s.get(f"/environments/{env}/featurestates/", params={"feature": fid}).raise_for_status().json()["results"]
                fs = next(x for x in states if not x.get("feature_segment") and not x.get("identity"))
                s.patch(f"/environments/{env}/featurestates/{fs['id']}/", json={"enabled": enabled}).raise_for_status()
    except Exception as e:
        status["admin_error"] = getattr(getattr(e, "response", None), "status_code", None) or type(e).__name__
        log.warning("flagsmith admin set %s failed: %s", name, status["admin_error"])
        return "failed"
    status["admin_error"] = None
    status["last_admin_change"] = {"flag": name, "enabled": enabled, "at": time.time()}
    _cache["t"] = 0
    _id_cache.clear()
    return "ok"


# --- what the Ops screen shows: every flag, the segment effect per language, the live A/B split ---

_snap = {"t": 0.0, "val": None}


def _enabled(fl, name, default):
    try:
        return bool(fl.is_feature_enabled(name))
    except Exception:   # flag not created in this environment yet
        return default


def ops_snapshot() -> dict:
    now = time.time()
    if _snap["val"] and now - _snap["t"] < 3:
        return _snap["val"]
    base = get_all()
    out = {"source": base["source"], "mode": status.get("mode", "off"), "flagsmith": status["flagsmith"],
           "admin_api": admin_configured(), "admin_error": status.get("admin_error"),
           "last_admin_change": status.get("last_admin_change"), "override_active": base["override_active"],
           "flags": [], "by_language": {}, "ab_split": None}
    c = _get_client()
    if c and base["source"] == "flagsmith":
        try:
            for f in c.get_environment_flags().all_flags():
                v = f.value
                out["flags"].append({"name": f.feature_name, "enabled": f.enabled,
                                     "value": None if v is None else str(v)[:48]})
            for lang in LANGS:   # synthetic preview identities: shows what a segment does per language
                fl = c.get_identity_flags(identifier=f"ops-preview-{lang}", traits={"language": lang})
                out["by_language"][lang] = {n: _enabled(fl, n, base[n]) for n in BOOL_FLAGS}
            has_tone = any(f["name"] == "question_tone" for f in out["flags"])
            if has_tone and out["mode"] == "local_evaluation":   # in-process, so 40 evaluations cost nothing
                tones = [_values(c.get_identity_flags(identifier=f"ops-ab-{i}", traits={"language": "en-IN"}))
                         .get("question_tone", base["question_tone"]) for i in range(40)]
                out["ab_split"] = {t: tones.count(t) for t in TONES}
        except Exception as e:
            out["error"] = type(e).__name__
    _snap.update(t=now, val=out)
    return out


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
