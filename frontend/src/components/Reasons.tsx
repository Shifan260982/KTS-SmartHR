import { useApplication, type Job } from '../api/client'
import { VERDICT_LABEL } from '../lib/bands'
import { MarkGlyph } from './Marks'

/** Why a candidate scored as they did: each requirement's verdict and the exact resume line behind it. */
export function Reasons({ applicationId, job }: { applicationId: number; job: Job }) {
  const { data: app, isLoading, isError, error } = useApplication(applicationId)

  if (isLoading) return <div className="reasons" aria-busy="true"><div className="skeleton" style={{ width: '70%' }} /></div>
  if (isError || !app) return <div className="reasons"><p className="error-box" role="alert">{error?.message ?? 'Reasons could not be loaded.'}</p></div>
  if (app.status === 'NEEDS_ATTENTION') {
    return (
      <div className="reasons">
        <p className="reasons-note">This resume could not be read ({app.quality.replace(/_/g, ' ')}), so it has no score. Scanned PDFs need OCR, which is not available yet. Check the original file by hand.</p>
      </div>
    )
  }

  const mustIds = new Set(job.rubric.filter((r) => r.type === 'must').map((r) => r.id))
  const evidence = app.evidence ?? []
  const found = evidence.filter((e) => e.verdict !== 'not_found')
  const missing = evidence.filter((e) => e.verdict === 'not_found')
  const mustMet = evidence.filter((e) => mustIds.has(e.requirement_id) && e.verdict === 'met').length

  return (
    <div className="reasons">
      <ul className="reason-list">
        {found.map((e) => (
          <li key={e.requirement_id}>
            <span className="reason-mark"><MarkGlyph verdict={e.verdict} /></span>
            <div>
              <p className="reason-req">
                {e.requirement}
                <span className="reason-tag">{VERDICT_LABEL[e.verdict]}{mustIds.has(e.requirement_id) ? ' · must-have' : ''}</span>
              </p>
              <blockquote className="reason-quote">{e.quote?.replace(/\s+/g, ' ').trim().replace(/^[-–•*]\s+/, '')}</blockquote>
            </div>
          </li>
        ))}
      </ul>
      {missing.length > 0 && (
        <p className="reasons-note">
          <span className="reason-mark muted"><MarkGlyph verdict="not_found" size={14} /></span>
          Not found in the resume: {missing.map((e) => e.requirement + (mustIds.has(e.requirement_id) ? ' (must-have)' : '')).join('; ')}.
        </p>
      )}
      <p className="reasons-foot">
        Must-haves {mustMet}/{mustIds.size} · {found.filter((e) => e.verdict === 'met').length} of {evidence.length} met · checklist v{app.rubric_version}
      </p>
    </div>
  )
}
