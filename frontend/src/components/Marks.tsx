import type { Requirement, Verdict } from '../api/client'
import { VERDICT_LABEL } from '../lib/bands'

/** Examiner's marks, drawn: tick for met, a struck tick for partial, a cross for not found. */
export function MarkGlyph({ verdict, size = 16 }: { verdict: Verdict; size?: number }) {
  return (
    <svg className="mark-glyph" width={size} height={size} viewBox="0 0 16 16" aria-hidden="true">
      {verdict === 'met' && <path d="M2.5 8.5l3.5 3.5 7.5-8.5" />}
      {verdict === 'partial' && <path d="M2.5 8.5l3.5 3.5 7.5-8.5M3 5.5h7" />}
      {verdict === 'not_found' && <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" />}
    </svg>
  )
}

type Mark = { requirement_id: string; verdict: Verdict }

export function MarksHead({ rubric }: { rubric: Requirement[] }) {
  return (
    <div className="marks-head" aria-hidden="true">
      {rubric.map((r) => (
        <span key={r.id} className={r.type === 'must' ? 'must' : undefined} title={r.text}>
          {r.id.replace(/^[A-Za-z]+/, '')}
        </span>
      ))}
    </div>
  )
}

export function MarksGrid({ rubric, marks }: { rubric: Requirement[]; marks: Mark[] | null | undefined }) {
  if (!marks) return <span className="fit-none">—</span>
  const byId = new Map(marks.map((m) => [m.requirement_id, m.verdict]))
  const summary = rubric.map((r) => `${r.id} ${VERDICT_LABEL[byId.get(r.id) ?? 'not_found'].toLowerCase()}`).join(', ')
  return (
    <div className="marks" role="img" aria-label={summary}>
      {rubric.map((r) => {
        const verdict = byId.get(r.id) ?? 'not_found'
        return (
          <span
            key={r.id}
            className={`cell cell-${verdict}${r.type === 'must' ? ' must' : ''}`}
            title={`${r.id} ${r.text}: ${VERDICT_LABEL[verdict]}`}
          />
        )
      })}
    </div>
  )
}

export function MarksLegend() {
  return (
    <div className="legend">
      <span><i className="cell cell-met" /> Met</span>
      <span><i className="cell cell-partial" /> Partial</span>
      <span><i className="cell" /> Not found</span>
      <span><i className="cell must" /> Must-have</span>
    </div>
  )
}
