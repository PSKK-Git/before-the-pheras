"""First Sarvam call: transcribe one audio clip with saaras:v4 (speech to text).

Usage: backend/.venv/bin/python scripts/first_stt.py path/to/clip.wav
Reads SARVAM_API_KEY from the environment or the project-root .env (first key if comma-separated).
"""
import os
import sys
from pathlib import Path

from sarvamai import SarvamAI

env = Path(__file__).resolve().parent.parent / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

key = os.environ.get("SARVAM_API_KEY", "").split(",")[0].strip()
if not key:
    sys.exit("SARVAM_API_KEY is not set")

client = SarvamAI(api_subscription_key=key)
with open(sys.argv[1], "rb") as f:
    res = client.speech_to_text.transcribe(
        file=f, model="saaras:v4", mode="transcribe", language_code="unknown"
    )

print("transcript:", res.transcript)
print("language:  ", res.language_code)
print("request_id:", res.request_id)
