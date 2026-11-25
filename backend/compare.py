"""Deterministic per-topic comparison of two APPROVED positions. No LLM, no score.

Labels: aligned | needs_discussion | important_difference. A numeric gap alone never means
incompatibility; every result carries the rule that fired so it can be explained to the user.
"""

LABELS = {
    "aligned": "Aligned",
    "needs_discussion": "Needs discussion",
    "important_difference": "Important difference",
}

DEFAULT_RULES = {
    "min_confidence": 0.5,          # below this on either side -> needs discussion
    "important_min_diff": 2,        # scale gap needed for "important difference"
    "important_min_importance": 4,  # BOTH partners must rate importance >= this
    "important_max_flex": 2,        # BOTH partners must be flexibility <= this
    "flexible_at": 4,               # either partner this flexible softens a 1-step gap
}

# Small per-topic overrides (everything else uses DEFAULT_RULES).
TOPIC_RULES = {
    "living_arrangement": {},
    "money_and_family": {},
    "career_relocation": {},
}

EXPLAIN = {
    "uncertain_side": "At least one answer is uncertain or still needs clarification, so this is a conversation, not a conclusion.",
    "same_stance": "You described the same approach.",
    "important_gap": "You described different approaches, and you both said this matters a lot and you have less room to flex.",
    "one_step_flexible": "Your approaches are close, and at least one of you is open to alternatives.",
    "differs_default": "Your approaches differ. Worth talking through how each of you sees it.",
}


def compare_topic(topic: str, a: dict, b: dict) -> dict:
    r = {**DEFAULT_RULES, **TOPIC_RULES.get(topic, {})}
    if (
        a.get("needs_clarification") or b.get("needs_clarification")
        or (a.get("confidence") or 0) < r["min_confidence"] or (b.get("confidence") or 0) < r["min_confidence"]
        or a.get("stance_scale") is None or b.get("stance_scale") is None
    ):
        return _res("needs_discussion", "uncertain_side", None)
    diff = abs(a["stance_scale"] - b["stance_scale"])
    if diff == 0:
        return _res("aligned", "same_stance", 0)
    if (
        diff >= r["important_min_diff"]
        and min(a["importance"], b["importance"]) >= r["important_min_importance"]
        and max(a["flexibility"], b["flexibility"]) <= r["important_max_flex"]
    ):
        return _res("important_difference", "important_gap", diff)
    if diff == 1 and max(a["flexibility"], b["flexibility"]) >= r["flexible_at"]:
        return _res("needs_discussion", "one_step_flexible", diff)
    return _res("needs_discussion", "differs_default", diff)


def _res(status, rule, diff):
    return {"status": status, "label": LABELS[status], "rule": rule, "explanation": EXPLAIN[rule], "scale_difference": diff}
