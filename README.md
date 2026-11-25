# Before the Pheras

**The conversations worth having before saying yes.**

In an Indian wedding you marry a household. In arranged matches, couples often meet only a few times before families commit, and the hardest topics never come up: who lives with whom, money to parents, whose career moves, children, faith. Before the Pheras lets each partner answer a few of those questions **privately, by voice, in Telugu, Hindi or English**. Each person reviews and approves what the AI understood. Only approved answers are shown side by side, as a conversation map.

> A conversation starter, **not a compatibility score**. We never rate, predict or advise on whether to marry.

## The key point: surface the deal-breakers in week one, not month six

**The problem we solve is communication, and the time lost to it.**

- Today, couples and families spend **months** of meetings, calls and family visits before the real questions come out. Will we live with your parents? Does my career move or yours? How much goes to each family every month? When do we want children?
- Often these only surface **after the engagement or after the wedding**, when changing course costs everyone far more: emotionally, socially and financially.
- Before the Pheras puts those questions **at the start**. In about 15 minutes each, privately and in their own language, both people know where they **agree**, where they **need to talk**, and where there is an **important difference**: a potential deal-breaker.
- **This saves time for everyone.** The couple, both families and matchmakers spend the following months on the conversations that matter, instead of discovering them too late.
- We **don't** decide for anyone. An "Important difference" isn't a verdict. It's the conversation you'd otherwise have had six months later, now, while it's still easy to have.

Why we believe this works: premarital education research shows the clearest, most reliable gain is in **communication** ([Carroll & Doherty, 2003](https://scholarsarchive.byu.edu/facpub/4333); [Fawcett et al., 2010](https://scholarsarchive.byu.edu/facpub/4230)). That's precisely the skill this app exercises, and it does so at the moment it saves the most time.

**One-line pitch:** *"Families take months to find out what really matters to each other. Before the Pheras gets the deal-breakers on the table in week one, privately, in your own language, and leaves the decision to you."*

## What's new here

| Today | Before the Pheras |
|---|---|
| Kundli matching: compatibility from horoscopes | "Kundli for values": what each person actually wants |
| Matrimony filters: caste, salary, height | The household topics families avoid raising |
| Western premarital tools: English-only, run by a counsellor | Voice-first in Telugu, Hindi and English, self-serve |
| An AI "72% compatible" verdict | No score. Fixed, explainable rules: Aligned / Needs discussion / Important difference, plus one neutral question to start talking |

**Private until approved:** your partner never sees a transcript, only the summary you approved. You can revoke an approval or delete the session at any time.

## Research-backed topics

The couple picks 3 of 8 topics. Each topic shows a "why we ask" note and its source in the app (`backend/flags.py`, `TOPIC_NOTES`):

| Topic | Why we ask |
|---|---|
| Where to live (joint family or not) | In-laws and joint-family living are the most-cited source of arguments in surveys of Indian couples |
| Money towards parents | Supporting parents is common; household money is often controlled by one side |
| Career relocation | The bride traditionally moves to the husband's home; who relocates is assumed, not discussed |
| Housework and childcare | Unfair division of domestic work is named as a major conflict source |
| Children | A core premarital-inventory area; timing is often decided by families |
| Both careers after marriage | Whether one partner steps back is assumed in arranged matches |
| Faith, rituals, festivals | Shapes daily life and both families' expectations |
| Wedding spending and gifts | A frequent early strain; dowry is illegal (Dowry Prohibition Act, 1961) |

**Evidence for the approach:** a meta-analysis of premarital education programmes found about a 30% average improvement in outcomes ([Carroll & Doherty, 2003](https://scholarsarchive.byu.edu/facpub/4333)). A later review found the gains were mainly in **communication**, and effects on satisfaction were weaker once unpublished studies were included ([Fawcett et al., 2010](https://scholarsarchive.byu.edu/facpub/4230)). Communication is exactly what this app targets. The survey figures behind the topics come from convenience samples ([The Swaddle](https://www.theswaddle.com/dealing-with-in-laws-major-source-of-relationship-conflict-in-modern-indian-marriages), [IMRB / Shaadi.com](https://imrbint.com/downloads/Shaadi.com%20-%20IMRB%20International%20Report_FINAL_Latest.pdf)), so treat them as "commonly reported", not national statistics.

## How each integration is used

| Integration | Why | How |
|---|---|---|
| **Sarvam speech-to-text** (`saaras:v4`) | People speak in their own language instead of typing | `/answer` transcribes the audio in memory; audio is never stored |
| **Sarvam chat** (`sarvam-105b-conversations`) | Turns a free-form answer into a structured stance; asks one follow-up if the answer is vague; phrases one neutral opening question | `extract.py`: schema-validated, one retry, repairs malformed JSON, falls back to "needs clarification" instead of guessing |
| **Sarvam translate** | Each partner reads the other's approved summary in their own language | `_their_text()` in `main.py`, cached |
| **Sarvam text-to-speech** (`bulbul:v3`) | Questions can be listened to | `/tts`, cached so the same text is never paid for twice |
| **Sarvam reliability** | 100-credit budget, flaky networks | Timeout, retry, circuit breaker, comma-separated key failover, `/admin/reload-env` to swap keys live |
| **Flagsmith: local evaluation** | No network call per request; participants' traits never leave our server | Server-side key, environment refreshed every 5 s |
| **Flagsmith: kill switches** | Turn risky features off without a redeploy | `voice_mode`, `text_fallback`, `ai_followups` |
| **Flagsmith: remote config** | Change content live | `interview_topics` (question wording), `enabled_languages`, `max_followups` (0-2) |
| **Flagsmith: identities + segments** | Personalise per participant | Each participant is an identity; language is the only trait. The `indic_speakers` segment turns read-aloud on for Telugu and Hindi |
| **Flagsmith: multivariate A/B** | Test whether gentler wording gets more honest answers | `question_tone`: "gentle" adds a reassuring line in all 3 languages, "direct" asks plainly; each participant keeps one variant |
| **Flagsmith: Admin API** | Ops automation that judges can see | "Approve runbook" turns `voice_mode` off in Flagsmith (visible in the dashboard and audit log); "Recover" turns it back on |
| **NudgeBee** (local stand-in) | When Sarvam fails, an incident is raised and a runbook falls back to text | The circuit breaker opening sends a content-free incident to `NUDGEBEE_WEBHOOK_URL`. See the note below |

**Honest note on NudgeBee:** NudgeBee had no self-serve signup (AccessDenied), and its public repos document no inbound alert API for a non-Kubernetes app. So `NUDGEBEE_WEBHOOK_URL` points at a clearly labelled local stand-in (`/ops/nudgebee-standin`). It receives the same payload and mimics NudgeBee's approval-gated runbook. Going live is a one-line `.env` change.

## Demo script (about 3 minutes)

0. **Open with the key point (20 s):** "Families spend months finding out what really matters to each other, and often find the deal-breakers after the engagement. We put those conversations in week one."
1. **Landing:** pick a language, then **Start a new session**, then choose 3 topics. Point at the "why we ask" notes.
2. **Interview:** answer one question by voice and two by typing. Show the review screen: "here is what we understood", edit if needed, then **Approve & share**.
3. **Map:** click **Add synthetic demo partner**. Show Aligned / Needs discussion / Important difference, the explanation for each, and the suggested opening question. Stress: no score. Point at the **Important difference** on career relocation: "This is the deal-breaker conversation this couple would otherwise have had months later. They're having it on day one."
4. **Ops** (ⓘ → Ops demo): **Simulate Sarvam voice failure**. The breaker opens, the incident appears in the NudgeBee inbox, and **Approve runbook** flips `voice_mode` in Flagsmith, so the app switches to text mode. Then **Recover**. Show the Flagsmith panel: flags, segment per language, the A/B split.

## Business

- **Market:** about 4.6 million weddings in India's 2025 peak season alone ([CAIT via Kotak MF](https://www.kotakmf.com/Information/blogs/indias-fourth-largest-industry-wedding-blitz_)). Matrimony.com made about ₹456 crore in FY25 ([FilingReader](https://filingreader.com/news-wire/mumbai/2025-07-16/matrimonycom-reports-inr-4558m-revenue-in-fy25)), which shows people pay for the step before marriage.
- **Models:**
  - B2C, one-time ₹499 per couple. Illustration: 0.5% of 4.6M couples ≈ ₹1.1 crore a year.
  - B2B, as a premium add-on for matrimony platforms after shortlisting.
  - Per-seat SaaS for premarital counsellors and wedding planners.
- **What's not proven yet:** demand. Next step: ask engaged and recently married couples whether they'd use it and pay ₹499.

## Roadmap

- **Values-first introductions (with explicit opt-in):** matrimony platforms use a person's *approved* answers to surface people who think alike, alongside their usual filters. Sold B2B; no dating pool of our own.
- More languages (Tamil, Kannada, Marathi, Bengali) via Flagsmith `enabled_languages`.
- Counsellor mode: a guided session using the conversation map.

## Run it

```bash
cp .env.example .env    # add SARVAM_API_KEY, FLAGSMITH_ENVIRONMENT_KEY; ALLOW_DEMO_ADMIN=1 for the demo
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn main:app --port 8000
cd ../frontend && npm install && npm run dev    # http://localhost:5173
```

Optional `.env` values: `NUDGEBEE_WEBHOOK_URL`, plus `FLAGSMITH_ADMIN_API_KEY` and `FLAGSMITH_ENV_CLIENT_KEY` for the runbook to flip the real flag.

To test without spending Sarvam credits, run the backend with an empty `SARVAM_API_KEY` (offline keyword mode), then run `python scripts/smoke.py http://localhost:8000`. `scripts/first_stt.py <clip.wav>` makes one standalone speech-to-text call.
