import { useCallback, useEffect, useRef, useState } from 'react'

/* ---------- tiny API client (all calls go to OUR backend; no keys in the browser) ---------- */
const store = {
  get: () => { try { return JSON.parse(localStorage.getItem('pheras') || 'null') } catch { return null } },
  set: (v) => { try { v ? localStorage.setItem('pheras', JSON.stringify(v)) : localStorage.removeItem('pheras') } catch {} },
}
async function api(path, { method = 'GET', json, form, token, raw } = {}) {
  const headers = {}
  if (token) headers['X-Participant-Token'] = token
  let body
  if (json) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(json) }
  if (form) body = form
  const r = await fetch(path, { method, headers, body })
  if (!r.ok) {
    let d = null
    try { d = (await r.json()).detail } catch {}
    throw Object.assign(new Error(typeof d === 'string' ? d : d?.reason || `HTTP ${r.status}`), { status: r.status, detail: d })
  }
  return raw ? r : r.json()
}

const uid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2))
const STATUS_STYLE = {
  aligned: 'bg-sage-soft text-sage border-sage/30',
  needs_discussion: 'bg-gold-soft text-[#8a6a2c] border-gold/40',
  important_difference: 'bg-burgundy-soft text-burgundy border-burgundy/30',
}

/* ---------- shared bits ---------- */
function Chip({ voice }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold ${voice ? 'border-sage/40 bg-sage-soft text-sage' : 'border-gold/40 bg-gold-soft text-[#8a6a2c]'}`}>
      <span className={`h-2 w-2 rounded-full ${voice ? 'bg-sage' : 'bg-gold'}`} />
      {voice ? 'Voice on' : 'Text mode'}
    </span>
  )
}

function Shell({ children, cfg, onAbout, right }) {
  return (
    <div className="mx-auto min-h-screen max-w-2xl px-4 pb-16 pt-5">
      <header className="mb-6 flex items-center justify-between">
        <div className="font-serif text-xl font-semibold text-burgundy">Before the Pheras</div>
        <div className="flex items-center gap-2">
          {cfg && <Chip voice={cfg.voice_mode} />}
          {right}
          <button className="btn btn-ghost !px-3 !py-1.5 text-sm" onClick={onAbout} aria-label="About and privacy">ⓘ</button>
        </div>
      </header>
      {children}
    </div>
  )
}

function About({ onClose, onDelete, cfg, onOps }) {
  return (
    <div className="fixed inset-0 z-10 flex items-end justify-center bg-black/30 p-4 sm:items-center" onClick={onClose}>
      <div className="card max-h-[85vh] w-full max-w-lg overflow-auto" onClick={(e) => e.stopPropagation()}>
        <h2 className="mb-2 font-serif text-xl text-burgundy">About &amp; privacy</h2>
        <p className="mb-3 text-sm">A conversation starter, not a compatibility score. We never rate, predict or advise on whether to marry.</p>
        <ul className="mb-4 list-disc space-y-1.5 pl-5 text-sm text-muted">
          <li>Your partner can only see answers you have <b>approved</b>. Pending and rejected ones stay private.</li>
          <li>Audio is processed only to transcribe it and is not stored by this app after transcription.</li>
          <li>Transcripts and positions <b>are stored</b> in this app&rsquo;s database until the session is deleted.</li>
          <li>Sarvam AI (speech and language models) processes your audio/text to provide the service; we cannot delete data from their systems.</li>
          <li>You can revoke an approval or delete the whole session at any time.</li>
          <li>Demo partner answers are <b>synthetic</b>.</li>
        </ul>
        {cfg && <p className="mb-4 text-xs text-muted">Config source: {cfg.source}{cfg.override_active ? ' · local fallback override active' : ''} · Sarvam: {cfg.sarvam}</p>}
        <div className="flex justify-between gap-2">
          {onDelete ? <button className="btn btn-ghost text-burgundy" onClick={onDelete}>Delete session &amp; all data</button> : <span />}
          <div className="flex gap-2">
            <button className="btn btn-ghost text-sm" onClick={onOps}>Ops demo</button>
            <button className="btn btn-primary" onClick={onClose}>Close</button>
          </div>
        </div>
      </div>
    </div>
  )
}

/* ---------- ops: Sarvam health -> breaker -> NudgeBee (local stand-in) -> runbook ---------- */
function Ops({ onBack }) {
  const [ready, setReady] = useState(null)
  const [inbox, setInbox] = useState(null)
  const [busy, setBusy] = useState(false)
  const load = useCallback(async () => {
    try { setReady(await api('/ready')) } catch {}
    try { setInbox(await api('/ops/nudgebee-standin')) } catch { setInbox({ incidents: [] }) }
  }, [])
  useEffect(() => { load(); const t = setInterval(load, 2000); return () => clearInterval(t) }, [load])
  const act = async (path, json) => { setBusy(true); try { await api(path, { method: 'POST', json }) } catch {} setBusy(false); load() }
  const ok = ready?.breaker_state === 'closed'
  const fmt = (t) => new Date(t * 1000).toLocaleTimeString()
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="font-serif text-2xl text-burgundy">Ops &amp; incident demo</h1>
        <button className="btn btn-ghost text-sm" onClick={onBack}>← Back</button>
      </div>
      <div className="card space-y-2 text-sm">
        <div className="flex flex-wrap gap-2">
          <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${ok ? 'border-sage/40 bg-sage-soft text-sage' : 'border-burgundy/30 bg-burgundy-soft text-burgundy'}`}>Sarvam: {ready?.sarvam ?? '…'}</span>
          <span className="rounded-full border px-3 py-1 text-xs">Breaker: {ready?.breaker_state ?? '…'}</span>
          <span className="rounded-full border px-3 py-1 text-xs">Flagsmith: {ready?.flagsmith ?? '…'}</span>
          <span className="rounded-full border px-3 py-1 text-xs">Incident signal: {ready?.incident?.nudgebee_signal ?? '—'}</span>
          {ready?.incident?.self_heal && <span className="rounded-full border border-gold/40 bg-gold-soft px-3 py-1 text-xs text-[#8a6a2c]">Runbook applied: text mode</span>}
        </div>
        {ready?.simulated_failure && <p className="text-xs text-muted">Failure is <b>simulated</b> for this demo; no real outage.</p>}
        <div className="flex gap-2 pt-1">
          <button className="btn btn-primary text-sm" disabled={busy} onClick={() => act('/admin/simulate-sarvam-failure', { on: true, scope: 'voice' })}>Simulate Sarvam voice failure</button>
          <button className="btn btn-ghost text-sm" disabled={busy} onClick={() => act('/admin/simulate-sarvam-failure', { on: false })}>Recover</button>
        </div>
      </div>
      <div className="card text-sm">
        <h2 className="mb-1 font-serif text-lg text-burgundy">NudgeBee inbox <span className="font-sans text-xs text-muted">(local stand-in)</span></h2>
        <p className="mb-3 text-xs text-muted">NudgeBee signup was unavailable, so incident signals go to a local receiver with the same content-free payload. Point NUDGEBEE_WEBHOOK_URL at a real NudgeBee endpoint to go live.</p>
        {!inbox?.incidents?.length && <p className="text-muted">No incidents received.</p>}
        {inbox?.incidents?.map((i, n) => (
          <div key={n} className="mb-2 rounded-xl border p-3">
            <div className="flex justify-between gap-2"><b>{i.summary}</b><span className="text-xs text-muted">{fmt(i.received_at)}</span></div>
            <div className="text-xs text-muted">{i.event} · {i.severity} · {i.labels?.service} → {i.labels?.dependency} · errors {JSON.stringify(i.detail?.error_counts)}</div>
            <div className="mt-2 flex items-center justify-between gap-2">
              <span className="text-xs">Suggested runbook: {i.suggested_runbook}</span>
              {n === 0 && (i.runbook_approved
                ? <span className="text-xs text-sage">✓ approved</span>
                : <button className="btn btn-primary !px-3 !py-1 text-xs" disabled={busy || ok} onClick={() => act('/ops/nudgebee-standin/approve')}>Approve runbook</button>)}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ---------- screen 1: landing ---------- */
function Landing({ cfg, onJoined }) {
  const [lang, setLang] = useState('te-IN')
  const [code, setCode] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const langs = cfg?.enabled_languages || []
  useEffect(() => { if (langs.length && !langs.find((l) => l.code === lang)) setLang(langs[0].code) }, [langs, lang])

  const join = async (joinCode) => {
    setBusy(true); setErr('')
    try {
      const r = await api('/sessions/join', { method: 'POST', json: { join_code: joinCode, language: lang } })
      onJoined({ ...r, join_code: joinCode })
    } catch (e) {
      setErr(e.status === 404 ? 'No session with that code.' : e.status === 409 ? 'That session already has two people.' : 'Something went wrong. Try again.')
    } finally { setBusy(false) }
  }
  const create = async () => {
    setBusy(true); setErr('')
    try { const s = await api('/sessions', { method: 'POST' }); await join(s.join_code) }
    catch { setErr('Could not create a session.'); setBusy(false) }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="font-serif text-4xl leading-tight text-burgundy">The conversations worth having before saying yes.</h1>
        <p className="mt-3 text-muted">In a wedding you marry a household. Each of you answers three short questions privately, in your own language. You review what was understood, approve it, and only then see each other&rsquo;s approved answers side by side.</p>
      </div>
      <div className="card space-y-4">
        <div>
          <div className="lbl mb-1.5">Your language</div>
          <div className="flex flex-wrap gap-2">
            {langs.map((l) => (
              <button key={l.code} onClick={() => setLang(l.code)} className={`btn !py-2 text-sm ${lang === l.code ? 'btn-primary' : 'btn-ghost'}`}>{l.name}</button>
            ))}
          </div>
        </div>
        <button className="btn btn-primary w-full" disabled={busy || !langs.length} onClick={create}>Start a new session</button>
        <div className="flex items-center gap-2 text-xs text-muted"><div className="h-px flex-1 bg-line" />or join your partner<div className="h-px flex-1 bg-line" /></div>
        <div className="flex gap-2">
          <input className="min-w-0 flex-1 rounded-full border border-line bg-white px-4 py-2.5 text-center font-semibold uppercase tracking-[.3em]" placeholder="CODE" maxLength={5} value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} />
          <button className="btn btn-ghost" disabled={busy || code.length < 5} onClick={() => join(code)}>Join</button>
        </div>
        {err && <p className="text-sm text-burgundy">{err}</p>}
      </div>
      <p className="text-xs text-muted">Privacy, in one line: your partner only ever sees what you approve; transcripts are stored until you delete the session (see ⓘ).</p>
    </div>
  )
}

/* ---------- screen 2: interview + inline review ---------- */
function ReviewCard({ pos, options, token, onChange, onReanswer }) {
  const [draft, setDraft] = useState({
    summary_native: pos.summary_native || '', stance_key: pos.stance_key || '',
    importance: pos.importance || 3, flexibility: pos.flexibility || 3,
  })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const needsPick = pos.needs_clarification || !pos.stance_key

  const run = async (fn) => { setBusy(true); setErr(''); try { await fn(); await onChange() } catch (e) { setErr(e.message) } finally { setBusy(false) } }
  const save = () => api(`/positions/${pos.id}`, { method: 'PATCH', token, json: { ...draft, stance_key: draft.stance_key || null } })
  const approve = () => run(async () => {
    const dirty = draft.summary_native !== (pos.summary_native || '') || draft.stance_key !== (pos.stance_key || '') || draft.importance !== pos.importance || draft.flexibility !== pos.flexibility
    if ((dirty || needsPick) && draft.stance_key) await save()
    await api(`/positions/${pos.id}/approve`, { method: 'POST', token })
  })
  const reject = () => run(async () => { await api(`/positions/${pos.id}/reject`, { method: 'POST', token }); onReanswer(pos.topic) })

  return (
    <div className="card space-y-4 border-gold/50">
      <div className="lbl">Here is what we understood. Please check it.</div>
      {needsPick && <p className="rounded-xl bg-gold-soft p-3 text-sm text-[#8a6a2c]">We weren&rsquo;t sure what you meant. Choose the closest option below, or answer again. We won&rsquo;t guess for you.</p>}
      <div>
        <div className="lbl mb-1">Your position</div>
        <div className="grid gap-2">
          {options.map((o) => (
            <label key={o.key} className={`flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-2 text-sm ${draft.stance_key === o.key ? 'border-burgundy bg-burgundy-soft' : 'border-line bg-white'}`}>
              <input type="radio" name={`s-${pos.id}`} checked={draft.stance_key === o.key} onChange={() => setDraft({ ...draft, stance_key: o.key })} />{o.label}
            </label>
          ))}
        </div>
      </div>
      <div>
        <div className="lbl mb-1">In your words</div>
        <textarea className="w-full rounded-xl border border-line bg-white p-3 text-sm" rows={3} value={draft.summary_native} onChange={(e) => setDraft({ ...draft, summary_native: e.target.value })} />
        {pos.supporting_quote && <p className="mt-1 text-xs italic text-muted">From your answer: &ldquo;{pos.supporting_quote}&rdquo;</p>}
      </div>
      <div className="grid grid-cols-2 gap-4 text-sm">
        {[['importance', 'How much this matters'], ['flexibility', 'How open to alternatives']].map(([k, t]) => (
          <label key={k}><div className="lbl mb-1">{t}: {draft[k]}/5</div>
            <input type="range" min={1} max={5} className="w-full accent-[#6b1f2e]" value={draft[k]} onChange={(e) => setDraft({ ...draft, [k]: +e.target.value })} /></label>
        ))}
      </div>
      {err && <p className="text-sm text-burgundy">{err}</p>}
      <div className="flex flex-wrap gap-2">
        <button className="btn btn-primary" disabled={busy || !draft.stance_key} onClick={approve}>Approve &amp; share</button>
        <button className="btn btn-ghost" disabled={busy} onClick={() => onReanswer(pos.topic)}>Answer again</button>
        <button className="btn btn-ghost text-burgundy" disabled={busy} onClick={reject}>Delete this</button>
      </div>
      <p className="text-xs text-muted">Nothing is shared with your partner until you approve.</p>
    </div>
  )
}

function Interview({ session, cfg, me, refresh, goMap, speak }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [lastTranscript, setLastTranscript] = useState('')
  const [rec, setRec] = useState(null)       // MediaRecorder while recording
  const [redo, setRedo] = useState(null)
  const chunks = useRef([])
  const q = me.question
  const voiceOn = cfg?.voice_mode
  const voiceOk = cfg?.voice_available
  const textAllowed = cfg ? (cfg.text_fallback || !cfg.voice_mode) : true

  const reviewTarget = me.positions.find((p) => p.review_status === 'pending' && (p.stance_key || p.needs_clarification))
  const topicMeta = (id) => (cfg?.topics || []).find((t) => t.id === id)

  const send = async ({ audio }) => {
    setBusy(true); setNote('')
    const form = new FormData()
    form.append('answer_id', uid())
    if (redo) form.append('redo_topic', redo)
    if (audio) form.append('audio', audio, 'answer.webm'); else form.append('text', text)
    try {
      const r = await api('/answer', { method: 'POST', form, token: session.token })
      setLastTranscript(r.transcript || '')
      setText(''); setRedo(null)
      await refresh()
    } catch (e) {
      if (e.status === 503 || e.detail?.fallback === 'text') setNote('Voice is unavailable right now. Please type your answer below. Your progress is saved and this is the same question.')
      else if (e.detail?.reason === 'empty_transcript') setNote('We could not hear anything. Please try again or type.')
      else setNote(e.message || 'Something went wrong. Please try again.')
      await refresh()
    } finally { setBusy(false) }
  }

  const startRec = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream)
      chunks.current = []
      mr.ondataavailable = (e) => chunks.current.push(e.data)
      mr.onstop = () => { stream.getTracks().forEach((t) => t.stop()); send({ audio: new Blob(chunks.current, { type: mr.mimeType || 'audio/webm' }) }) }
      mr.start(); setRec(mr)
    } catch { setNote('Microphone unavailable. Please type your answer.') }
  }
  const stopRec = () => { rec?.stop(); setRec(null) }

  useEffect(() => { setNote('') }, [q?.topic])

  if (!q && !reviewTarget) {
    return (
      <div className="card space-y-3 text-center">
        <h2 className="font-serif text-2xl text-burgundy">All three topics done</h2>
        <p className="text-muted">Approved answers go to your Conversation Map. You can still edit or revoke any of them there.</p>
        <button className="btn btn-primary" onClick={goMap}>Open Conversation Map</button>
      </div>
    )
  }

  const progress = q ? q.index + 1 : me.positions.length
  const total = q?.total || 3
  const reviewOptions = reviewTarget ? me.stance_options?.[reviewTarget.topic] || [] : []

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between text-sm text-muted">
        <span>Question {Math.min(progress, total)} of {total}</span>
        <div className="flex gap-1">{Array.from({ length: total }).map((_, i) => <span key={i} className={`h-1.5 w-8 rounded-full ${i < progress ? 'bg-burgundy' : 'bg-line'}`} />)}</div>
      </div>

      {reviewTarget && !redo && (
        <ReviewCard key={reviewTarget.id + reviewTarget.review_status} pos={reviewTarget} options={reviewOptions} token={session.token} onChange={refresh}
          onReanswer={(topic) => { setRedo(topic); refresh() }} />
      )}

      {redo && <p className="rounded-xl bg-gold-soft p-3 text-sm text-[#8a6a2c]">Answering again: {topicMeta(redo)?.question}</p>}

      {(!reviewTarget || redo) && (q || redo) && (
        <div className="card space-y-4">
          <div className="lbl">{q?.kind === 'followup' && !redo ? 'One more thing' : 'Your question'}</div>
          <p className="font-serif text-2xl leading-snug">{redo ? topicMeta(redo)?.question : q.text}</p>
          {cfg?.tts_replies && cfg.sarvam === 'ok' && q && <button className="btn btn-ghost !py-1.5 text-sm" onClick={() => speak(q.text, session.language)}>▶ Listen</button>}
          {lastTranscript && <p className="rounded-xl bg-ivory p-3 text-sm"><span className="lbl block">What we heard</span>{lastTranscript}</p>}

          {voiceOn && (
            <div className="flex items-center gap-3">
              {!rec
                ? <button className="btn btn-primary" disabled={busy || !voiceOk} onClick={startRec}>● Record answer</button>
                : <button className="btn btn-primary animate-pulse" onClick={stopRec}>■ Stop &amp; send</button>}
              {rec && <span className="text-sm text-burgundy">Recording…</span>}
              {!voiceOk && !rec && <span className="text-xs text-muted">Voice unavailable. Please type instead.</span>}
            </div>
          )}
          {textAllowed && (
            <div className="space-y-2">
              <textarea className="w-full rounded-xl border border-line bg-white p-3" rows={3} placeholder={voiceOn ? 'Or type your answer…' : 'Type your answer…'} value={text} onChange={(e) => setText(e.target.value)} />
              <button className="btn btn-primary" disabled={busy || !text.trim()} onClick={() => send({})}>{busy ? 'Working…' : 'Send answer'}</button>
            </div>
          )}
          {note && <p className="rounded-xl bg-gold-soft p-3 text-sm text-[#8a6a2c]">{note}</p>}
        </div>
      )}
    </div>
  )
}

/* ---------- screen 3: conversation map ---------- */
function MapView({ session, cfg, speak, goInterview }) {
  const [map, setMap] = useState(null)
  const [me, setMe] = useState(null)
  const [err, setErr] = useState('')
  const load = useCallback(async () => {
    try {
      setMap(await api(`/sessions/${session.session_id}/map`, { token: session.token }))
      setMe(await api(`/sessions/${session.session_id}/me`, { token: session.token }))
    } catch (e) { setErr(e.message) }
  }, [session])
  useEffect(() => { load(); const t = setInterval(load, 3000); return () => clearInterval(t) }, [load])

  const seed = async () => { try { await api(`/admin/seed-partner-b?session_id=${session.session_id}`, { method: 'POST', token: session.token }); load() } catch { setErr('Demo partner is not enabled on this server.') } }
  const revoke = async (pos) => { await api(`/positions/${pos.id}/revoke`, { method: 'POST', token: session.token }); load() }
  const discussed = async (topic) => { await api(`/sessions/${session.session_id}/topics/${topic}/discussed`, { method: 'POST', token: session.token }); load() }

  if (!map || !me) return <p className="text-muted">Loading…</p>
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-serif text-3xl text-burgundy">Conversation Map</h2>
        <span className="rounded-full border border-gold/40 bg-gold-soft px-3 py-1 text-xs font-semibold text-[#8a6a2c]">{map.badge}</span>
      </div>
      <p className="text-sm text-muted">{map.note} {map.partner_synthetic && <b>Your partner here is a synthetic demo persona.</b>}</p>
      {!me.partner_joined && (
        <div className="card flex flex-wrap items-center justify-between gap-3 text-sm">
          <span>Waiting for your partner. Share code <b className="tracking-[.25em]">{me.join_code}</b>.</span>
          <button className="btn btn-ghost !py-1.5" onClick={seed}>Add synthetic demo partner</button>
        </div>
      )}
      {err && <p className="text-sm text-burgundy">{err}</p>}
      {map.topics.map((t) => (
        <div key={t.topic} className="card space-y-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <p className="font-serif text-lg leading-snug">{t.question}</p>
            {t.comparison && <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${STATUS_STYLE[t.comparison.status]}`}>{t.comparison.label}</span>}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-xl bg-ivory p-3 text-sm">
              <div className="lbl mb-1">You</div>
              {t.mine ? <>{t.mine.summary_native || t.mine.summary_english}
                <div><button className="mt-2 text-xs text-burgundy underline" onClick={() => revoke(t.mine)}>Hide / revoke approval</button></div></>
                : <span className="text-muted">Not approved yet. <button className="text-burgundy underline" onClick={goInterview}>Go to interview</button></span>}
            </div>
            <div className="rounded-xl bg-ivory p-3 text-sm">
              <div className="lbl mb-1">{map.partner_synthetic ? 'Partner (synthetic)' : 'Partner'}</div>
              {t.theirs ? <>{t.theirs.text}{t.theirs.translated && <div className="mt-1 text-xs text-muted">Translated for you</div>}{t.theirs.note && <div className="mt-1 text-xs text-muted">{t.theirs.note}</div>}</>
                : <span className="text-muted">{me.partner_joined ? 'Hasn’t shared an approved answer yet.' : 'Waiting for partner.'}</span>}
            </div>
          </div>
          {t.comparison && <p className="text-sm text-muted">{t.comparison.explanation}</p>}
          {t.suggested_question && (
            <div className="rounded-xl border border-gold/40 bg-gold-soft p-3">
              <div className="lbl mb-1">A way to start talking</div>
              <p className="font-serif text-lg">{t.suggested_question}</p>
              <div className="mt-2 flex gap-2">
                {cfg?.tts_replies && cfg.sarvam === 'ok' && <button className="btn btn-ghost !py-1.5 text-sm" onClick={() => speak(t.suggested_question, 'en-IN')}>▶ Listen</button>}
                <button className="btn btn-ghost !py-1.5 text-sm" onClick={() => discussed(t.topic)}>{t.discussed ? '✓ Discussed' : 'Mark as discussed'}</button>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

/* ---------- app ---------- */
export default function App() {
  const [session, setSession] = useState(store.get())
  const [cfg, setCfg] = useState(null)
  const [me, setMe] = useState(null)
  const [screen, setScreen] = useState('interview')
  const [about, setAbout] = useState(false)
  const audioRef = useRef(null)

  // flags are polled every 3 s so a Flagsmith dashboard change shows up without reload
  useEffect(() => {
    let alive = true
    const tick = async () => { try { const c = await api('/config', { token: session?.token }); if (alive) setCfg(c) } catch {} }
    tick(); const t = setInterval(tick, 3000)
    return () => { alive = false; clearInterval(t) }
  }, [session?.token])

  const refresh = useCallback(async () => {
    if (!session) return
    try { setMe(await api(`/sessions/${session.session_id}/me`, { token: session.token })) }
    catch (e) { if (e.status === 401) { store.set(null); setSession(null); setMe(null) } }
  }, [session])
  useEffect(() => { refresh() }, [refresh])
  useEffect(() => {
    if (session && me?.done && screen === 'interview' && !me.positions.some((p) => p.review_status === 'pending' && (p.stance_key || p.needs_clarification))) setScreen('map')
  }, [me, session])  // eslint-disable-line

  const speak = async (text, language) => {
    try {
      const r = await api('/tts', { method: 'POST', json: { text, language }, token: session.token, raw: true })
      const url = URL.createObjectURL(await r.blob())
      if (audioRef.current) audioRef.current.pause()
      audioRef.current = new Audio(url); audioRef.current.play()
    } catch { /* TTS is optional; stay silent */ }
  }

  const joined = (s) => { store.set(s); setSession(s); setScreen('interview') }
  const del = async () => {
    if (!window.confirm('Delete this session and everything stored for it?')) return
    try { await api(`/sessions/${session.session_id}`, { method: 'DELETE', token: session.token }) } catch {}
    store.set(null); setSession(null); setMe(null); setAbout(false)
  }

  const nav = session && (
    <div className="flex gap-1">
      {['interview', 'map'].map((s) => (
        <button key={s} onClick={() => setScreen(s)} className={`btn !px-3 !py-1.5 text-sm ${screen === s ? 'btn-primary' : 'btn-ghost'}`}>{s === 'map' ? 'Map' : 'Interview'}</button>
      ))}
    </div>
  )

  return (
    <Shell cfg={cfg} onAbout={() => setAbout(true)} right={nav}>
      {cfg && cfg.sarvam === 'degraded' && cfg.voice_mode && (
        <p className="mb-4 rounded-xl bg-gold-soft p-3 text-sm text-[#8a6a2c]">Voice is having trouble. You can keep going by typing. Nothing you have answered is lost.</p>
      )}
      {screen === 'ops' && <Ops onBack={() => setScreen(session ? 'interview' : 'landing')} />}
      {screen !== 'ops' && !session && <Landing cfg={cfg} onJoined={joined} />}
      {screen !== 'ops' && session && screen === 'interview' && me && <Interview session={session} cfg={cfg} me={me} refresh={refresh} goMap={() => setScreen('map')} speak={speak} />}
      {screen !== 'ops' && session && screen === 'map' && <MapView session={session} cfg={cfg} speak={speak} goInterview={() => setScreen('interview')} />}
      {about && <About cfg={cfg} onClose={() => setAbout(false)} onDelete={session ? del : null} onOps={() => { setAbout(false); setScreen('ops') }} />}
    </Shell>
  )
}
