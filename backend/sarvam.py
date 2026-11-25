"""Sarvam AI REST wrappers (STT, chat, TTS, translate) with timeout, one retry, circuit breaker.

Endpoints/models checked against docs.sarvam.ai on 2026-11-25:
  STT  POST /speech-to-text   (multipart: file, model=saaras:v4, mode, language_code)  header api-subscription-key
  Chat POST /v1/chat/completions (model=sarvam-105b)
  TTS  POST /text-to-speech   (model=bulbul:v3, speaker, language_code) -> {"audios":[b64 wav]}
  Translate POST /translate   (model=sarvam-translate:v1, source/target_language_code)
"""
import base64
import os
import statistics
import threading
import time
from collections import deque

import httpx

BASE = os.getenv("SARVAM_BASE_URL", "https://api.sarvam.ai")
STT_MODEL = os.getenv("SARVAM_STT_MODEL", "saaras:v4")
# sarvam-105b spends its whole token budget on hidden reasoning and returns empty content for short
# JSON tasks; the conversations variant answers directly (no reasoning tokens), faster and cheaper.
CHAT_MODEL = os.getenv("SARVAM_CHAT_MODEL", "sarvam-105b-conversations")
TTS_MODEL = os.getenv("SARVAM_TTS_MODEL", "bulbul:v3")
TTS_SPEAKER = os.getenv("SARVAM_TTS_SPEAKER", "shubh")
TRANSLATE_MODEL = os.getenv("SARVAM_TRANSLATE_MODEL", "sarvam-translate:v1")

FAIL_THRESHOLD = int(os.getenv("BREAKER_FAILURES", "3"))
COOLDOWN_S = float(os.getenv("BREAKER_COOLDOWN_S", "30"))
TIMEOUTS = {"stt": 20.0, "chat": 25.0, "tts": 15.0, "translate": 10.0}


class SarvamError(Exception):
    pass


class BreakerOpen(SarvamError):
    pass


class Breaker:
    """closed -> (N consecutive failures) -> open -> (cooldown) -> half_open probe -> closed|open"""

    def __init__(self):
        self.lock = threading.Lock()
        self.failures = 0
        self.opened_at = 0.0
        self.state = "closed"
        self.on_open = None    # callbacks wired by main.py (NudgeBee signal)
        self.on_close = None

    def current(self):
        with self.lock:
            if self.state == "open" and time.time() - self.opened_at >= COOLDOWN_S:
                self.state = "half_open"
            return self.state

    def allow(self):
        if self.current() == "open":
            raise BreakerOpen("sarvam breaker open")

    def success(self):
        with self.lock:
            was = self.state
            self.failures, self.state = 0, "closed"
        if was != "closed" and self.on_close:
            self.on_close()

    def failure(self):
        fire = False
        with self.lock:
            self.failures += 1
            if self.state == "half_open" or self.failures >= FAIL_THRESHOLD:
                fire = self.state != "open"
                self.state, self.opened_at = "open", time.time()
        if fire and self.on_open:
            self.on_open()


breaker = Breaker()
error_counts = {"stt": 0, "chat": 0, "tts": 0, "translate": 0}
_latencies: deque = deque(maxlen=200)
# demo-only failure injection. scope "voice" fails stt+tts only; "all" fails every call.
simulate = {"on": False, "scope": "voice"}


usage = {"stt": 0, "chat": 0, "tts": 0, "translate": 0, "key_rotations": 0}   # successful calls (credit watch)
_key_idx = 0


def keys() -> list:
    """SARVAM_API_KEY may hold several comma-separated keys; we fail over when one runs out of credit."""
    return [k.strip() for k in os.getenv("SARVAM_API_KEY", "").split(",") if k.strip()]


def current_key() -> str:
    ks = keys()
    return ks[_key_idx % len(ks)] if ks else ""


def _rotate() -> bool:
    global _key_idx
    if len(keys()) > 1:
        _key_idx = (_key_idx + 1) % len(keys())
        usage["key_rotations"] += 1
        return True
    return False


def reset_keys():
    global _key_idx
    _key_idx = 0


def _is_key_error(e) -> bool:
    return isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (401, 402, 403, 429)


def configured() -> bool:
    return bool(keys())


def p95_latency_ms():
    if len(_latencies) < 5:
        return None
    return round(statistics.quantiles(_latencies, n=20)[18] * 1000)


def _headers():
    return {"api-subscription-key": current_key()}


def _call(op: str, fn):
    """Per-request timeout lives in fn; one retry with backoff; breaker wraps the whole attempt."""
    if not configured() and not simulate["on"]:
        raise SarvamError("SARVAM_API_KEY not set")
    breaker.allow()
    last = None
    tries = rotations = 0
    while tries < 2:
        t0 = time.time()
        try:
            if simulate["on"] and (simulate["scope"] == "all" or op in ("stt", "tts")):
                raise SarvamError("simulated failure")
            out = fn()
            _latencies.append(time.time() - t0)
            usage[op] += 1
            breaker.success()
            return out
        except Exception as e:
            last = e
            # out of credit / rate-limited key: switch to the next key without counting a failure
            if _is_key_error(e) and rotations < len(keys()) - 1 and _rotate():
                rotations += 1
                continue
            error_counts[op] += 1
            tries += 1
            if tries < 2:
                time.sleep(0.4)
    breaker.failure()
    raise SarvamError(f"{op} failed: {type(last).__name__}")


def transcribe(audio: bytes, filename: str, mime: str, language: str) -> str:
    """Audio bytes live only in memory for this call; they are never written to disk or DB."""
    def go():
        r = httpx.post(
            f"{BASE}/speech-to-text",
            headers=_headers(),
            files={"file": (filename or "audio.webm", audio, mime or "audio/webm")},
            data={"model": STT_MODEL, "mode": "transcribe", "language_code": language},
            timeout=TIMEOUTS["stt"],
        )
        r.raise_for_status()
        return (r.json().get("transcript") or "").strip()

    return _call("stt", go)


def chat(system: str, user: str, max_tokens: int = 700) -> str:
    def go():
        r = httpx.post(
            f"{BASE}/v1/chat/completions",
            headers={**_headers(), "Authorization": f"Bearer {current_key()}"},
            json={
                "model": CHAT_MODEL,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": 0.2,
                "max_tokens": max_tokens,
                **({"reasoning_effort": "low"} if CHAT_MODEL == "sarvam-105b" else {}),
            },
            timeout=TIMEOUTS["chat"],
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"] or ""

    return _call("chat", go)


_tts_cache: dict = {}   # same text+language is never paid for twice


def tts(text: str, language: str) -> bytes:
    if (text, language) in _tts_cache:
        return _tts_cache[(text, language)]
    audio = _tts_call(text, language)
    if text != "probe":
        _tts_cache[(text, language)] = audio
    return audio


def _tts_call(text: str, language: str) -> bytes:
    def go():
        r = httpx.post(
            f"{BASE}/text-to-speech",
            headers=_headers(),
            json={"text": text[:2400], "language_code": language, "speaker": TTS_SPEAKER, "model": TTS_MODEL},
            timeout=TIMEOUTS["tts"],
        )
        r.raise_for_status()
        return base64.b64decode(r.json()["audios"][0])

    return _call("tts", go)


def translate(text: str, source: str, target: str) -> str:
    if source == target or not text:
        return text

    def go():
        r = httpx.post(
            f"{BASE}/translate",
            headers=_headers(),
            json={
                "input": text[:1900],
                "source_language_code": source,
                "target_language_code": target,
                "model": TRANSLATE_MODEL,
            },
            timeout=TIMEOUTS["translate"],
        )
        r.raise_for_status()
        return r.json()["translated_text"]

    return _call("translate", go)
