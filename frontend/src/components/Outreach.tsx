import { useState } from 'react'
import { CalendarClock, Check, Mail } from 'lucide-react'
import { getFreeSlots, useOutreachSettings, useScheduleInterview, useSendShortlisted, type Application, type Job, type Slot } from '../api/client'
import { displayName } from '../lib/bands'

const when = new Intl.DateTimeFormat(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
const fmt = (iso: string) => when.format(new Date(iso))
const SIGNER = 'The Recruitment Team'
const INTERVIEWER_KEY = 'smarthr.interviewer'
const isEmail = (value: string) => /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(value)

/** The interviewer is remembered per browser so HR types it once; storage can be blocked, so it is optional. */
function savedInterviewer(): string {
  try { return localStorage.getItem(INTERVIEWER_KEY) ?? '' } catch { return '' }
}
function saveInterviewer(value: string) {
  try { localStorage.setItem(INTERVIEWER_KEY, value) } catch { /* not remembered; still works */ }
}

const message = (error: unknown) => (error instanceof Error ? error.message : 'Something went wrong. Please try again.')

type Draft = { subject: string; body: string }

function shortlistedDraft(name: string, job: Job, signer: string): Draft {
  return {
    subject: `Your application for ${job.title}`,
    body: `Dear ${name},\n\nThank you for applying for the ${job.title} role. We are pleased to let you know that you have been shortlisted.\n\nWe will contact you shortly to arrange a first-round interview.\n\nKind regards,\n${signer}`,
  }
}

function interviewDraft(name: string, job: Job, signer: string): Draft {
  return {
    subject: `First-round interview: ${job.title}`,
    body: `Dear ${name},\n\nWe would like to invite you to a first-round interview for the ${job.title} role. The interview will be held on Microsoft Teams; the joining link is in this invitation.\n\nIf this time does not suit you, please propose a new time or reply to this email.\n\nKind regards,\n${signer}`,
  }
}

function DraftFields({ id, draft, onChange }: { id: string; draft: Draft; onChange: (d: Draft) => void }) {
  return (
    <>
      <div className="field">
        <label htmlFor={`${id}-subject`}>Subject</label>
        <input id={`${id}-subject`} className="input" value={draft.subject} maxLength={200} onChange={(e) => onChange({ ...draft, subject: e.target.value })} />
      </div>
      <div className="field">
        <label htmlFor={`${id}-body`}>Message</label>
        <textarea id={`${id}-body`} className="textarea" rows={9} value={draft.body} maxLength={10000} onChange={(e) => onChange({ ...draft, body: e.target.value })} />
      </div>
    </>
  )
}

/** Shortlisted email and first-round Teams interview, sent from SmartHR's mailbox after HR reviews each one. */
export function Outreach({ app, job }: { app: Application; job: Job }) {
  const settings = useOutreachSettings().data
  const redirectTo = settings?.redirect_to ?? null
  const [interviewer, setInterviewer] = useState(savedInterviewer)
  const [to, setTo] = useState(app.candidate_email ?? '')
  const [mail, setMail] = useState<Draft | null>(null)
  const [invite, setInvite] = useState<Draft | null>(null)
  const [minutes, setMinutes] = useState(30)
  const [slots, setSlots] = useState<Slot[] | null>(null)
  const [choice, setChoice] = useState<string>('') // a slot's start, or 'custom'
  const [custom, setCustom] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const shortlist = useSendShortlisted(app.id)
  const schedule = useScheduleInterview(app.id)
  // a test-mode send does not count as done: the real one can still go out
  const shortlisted = app.outreach.shortlisted?.test ? null : app.outreach.shortlisted
  const interview = app.outreach.interview?.test ? null : app.outreach.interview
  const tests = [app.outreach.shortlisted, app.outreach.interview].filter((s) => s?.test)
  const name = app.candidate_name ?? displayName(app.filename)
  const validTo = isEmail(to)
  const validInterviewer = isEmail(interviewer)

  if (!settings) return null
  if (!settings.mailbox) {
    return <p className="reasons-note outreach">The SmartHR mailbox is not set up, so emails and interviews cannot be sent from here. See docs/SmartHR_Email_Setup.pdf.</p>
  }

  const run = async (step: () => Promise<unknown>) => {
    setError(null)
    setBusy(true)
    try {
      await step()
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(false)
    }
  }

  const loadSlots = (length: number) =>
    run(async () => {
      saveInterviewer(interviewer)
      const found = await getFreeSlots(interviewer, length)
      setSlots(found)
      setChoice(found[0]?.start ?? 'custom')
    })

  const chosen = (): { start: string; end: string } | null => {
    if (choice !== 'custom') return slots?.find((s) => s.start === choice) ?? null
    if (!custom) return null
    const start = new Date(custom) // datetime-local is the HR user's local time
    return { start: start.toISOString(), end: new Date(start.getTime() + minutes * 60_000).toISOString() }
  }
  const slot = chosen()

  return (
    <section className="outreach" aria-label="Contact candidate">
      <div className="outreach-head">
        <h3>Contact candidate</h3>
        <span className="outreach-account">Sends from {settings.mailbox}</span>
      </div>

      {redirectTo && (
        <p className="outreach-test" role="status">
          <strong>Test mode is on.</strong> Emails and invites go to {redirectTo}, not the candidate (set by SMARTHR_EMAIL_REDIRECT).
        </p>
      )}
      {tests.map((t) => (
        <p key={t!.sent_at} className="hint">Test {t === app.outreach.interview ? 'invite' : 'email'} sent to {t!.delivered_to} on {fmt(t!.sent_at)}; the candidate has not been contacted.</p>
      ))}

      <div className="field outreach-to">
        <label htmlFor={`to-${app.id}`}>Candidate email</label>
        <input id={`to-${app.id}`} className="input" type="email" value={to} onChange={(e) => setTo(e.target.value)}
          placeholder="No email found in the resume" aria-invalid={!!to && !validTo} />
        {!app.candidate_email && <span className="hint">No email address was found in the resume. Type it in.</span>}
      </div>

      <div className="outreach-step">
        <h4><Mail size={16} aria-hidden="true" /> Shortlisted email</h4>
        {shortlisted ? (
          <p className="outreach-done"><Check size={16} aria-hidden="true" /> Sent to {shortlisted.to} on {fmt(shortlisted.sent_at)}</p>
        ) : !mail ? (
          <button type="button" className="btn" onClick={() => setMail(shortlistedDraft(name, job, SIGNER))}>Write shortlisted email</button>
        ) : (
          <div className="outreach-form">
            <DraftFields id={`mail-${app.id}`} draft={mail} onChange={setMail} />
            <div className="actions">
              <button type="button" className="btn btn-primary" disabled={busy || !validTo || !mail.subject.trim() || !mail.body.trim()}
                onClick={() => run(() => shortlist.mutateAsync({ to, ...mail }))}>
                {busy ? 'Sending…' : redirectTo ? `Send test to ${redirectTo}` : `Send to ${validTo ? to : 'candidate'}`}
              </button>
              <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => setMail(null)}>Cancel</button>
            </div>
          </div>
        )}
      </div>

      <div className="outreach-step">
        <h4><CalendarClock size={16} aria-hidden="true" /> First-round interview</h4>
        {interview ? (
          <p className="outreach-done">
            <Check size={16} aria-hidden="true" /> Teams interview on {fmt(interview.start)} with {interview.to}{interview.interviewer && ` and ${interview.interviewer}`}; invites sent.
            {interview.join_url && <> <a href={interview.join_url} target="_blank" rel="noreferrer">Join link</a></>}
          </p>
        ) : !invite ? (
          <div className="outreach-form">
            <div className="field outreach-to">
              <label htmlFor={`interviewer-${app.id}`}>Interviewer email</label>
              <input id={`interviewer-${app.id}`} className="input" type="email" value={interviewer} placeholder="you@kiaratech.ai"
                onChange={(e) => setInterviewer(e.target.value)} aria-invalid={!!interviewer && !validInterviewer} />
              <span className="hint">Their free time is read from Outlook, and they are invited too.</span>
            </div>
            <div className="actions">
              <button type="button" className="btn" disabled={busy || !validInterviewer} onClick={() => {
                setInvite(interviewDraft(name, job, SIGNER))
                loadSlots(minutes)
              }}>{busy ? 'Checking the calendar…' : 'Find interview times'}</button>
            </div>
          </div>
        ) : (
          <div className="outreach-form">
            <div className="field">
              <label htmlFor={`len-${app.id}`}>Length</label>
              <select id={`len-${app.id}`} className="select outreach-len" value={minutes} disabled={busy}
                onChange={(e) => { const m = Number(e.target.value); setMinutes(m); loadSlots(m) }}>
                {[30, 45, 60].map((m) => <option key={m} value={m}>{m} minutes</option>)}
              </select>
            </div>
            <fieldset className="outreach-slots" disabled={busy}>
              <legend>Time {busy && slots === null ? `(checking ${interviewer}'s calendar…)` : `(free in ${interviewer}'s calendar)`}</legend>
              {slots?.length === 0 && <p className="hint">No free time in working hours in the next 7 days. Choose another time.</p>}
              {slots?.map((s) => (
                <label key={s.start} className="outreach-slot">
                  <input type="radio" name={`slot-${app.id}`} checked={choice === s.start} onChange={() => setChoice(s.start)} />
                  {fmt(s.start)}
                </label>
              ))}
              <label className="outreach-slot">
                <input type="radio" name={`slot-${app.id}`} checked={choice === 'custom'} onChange={() => setChoice('custom')} />
                Another time
                <input type="datetime-local" className="input outreach-custom" value={custom} aria-label="Another interview time"
                  onChange={(e) => { setCustom(e.target.value); setChoice('custom') }} />
              </label>
            </fieldset>
            <DraftFields id={`invite-${app.id}`} draft={invite} onChange={setInvite} />
            <div className="actions">
              <button type="button" className="btn btn-primary" disabled={busy || !validTo || !slot || !invite.subject.trim() || !invite.body.trim()}
                onClick={() => slot && run(() => schedule.mutateAsync({ to, interviewer, ...invite, ...slot }))}>
                {busy && slots !== null ? 'Booking…' : redirectTo ? `Create Teams meeting, invite ${redirectTo} (test)` : 'Create Teams meeting and invite'}
              </button>
              <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => { setInvite(null); setSlots(null) }}>Cancel</button>
            </div>
          </div>
        )}
      </div>

      {error && <p className="error-box" role="alert">{error}</p>}
    </section>
  )
}
