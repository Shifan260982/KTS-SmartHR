import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { useApplication, useApplicationText, type Evidence, type Job } from '../api/client'
import { BAND_LABEL, VERDICT_LABEL, bandOf, displayName } from '../lib/bands'
import { MarkGlyph } from './Marks'

type Block = { start: number; end: number; marks: Evidence[] }

/** Split the resume into plain and marked blocks. Evidence ranges are whole chunks, so they are equal or disjoint. */
function toBlocks(text: string, evidence: Evidence[]): Block[] {
  const ranges = new Map<string, Block>()
  for (const e of evidence) {
    if (e.start == null || e.end == null) continue
    const key = `${e.start}:${e.end}`
    const block = ranges.get(key) ?? { start: e.start, end: e.end, marks: [] }
    block.marks.push(e)
    ranges.set(key, block)
  }
  const blocks: Block[] = []
  let cursor = 0
  for (const block of [...ranges.values()].sort((a, b) => a.start - b.start)) {
    if (block.start < cursor) continue // overlapping range: keep the first, never render text twice
    if (block.start > cursor) blocks.push({ start: cursor, end: block.start, marks: [] })
    blocks.push(block)
    cursor = block.end
  }
  if (cursor < text.length) blocks.push({ start: cursor, end: text.length, marks: [] })
  return blocks
}

export function CandidatePaper({ applicationId, job }: { applicationId: number; job: Job }) {
  const { data: app, isLoading, isError, error } = useApplication(applicationId)
  const { data: doc } = useApplicationText(applicationId)
  const [flash, setFlash] = useState<string | null>(null)
  const blockRefs = useRef(new Map<string, HTMLDivElement>())
  const mustIds = useMemo(() => new Set(job.rubric.filter((r) => r.type === 'must').map((r) => r.id)), [job.rubric])

  useEffect(() => setFlash(null), [applicationId])

  if (isLoading) {
    return (
      <aside className="sheet paper" aria-busy="true" aria-label="Candidate">
        <div className="paper-head"><div className="skeleton" style={{ width: '60%' }} /></div>
        <div className="paper-section" style={{ display: 'grid', gap: 10 }}>
          {[80, 92, 70, 86].map((w) => <div key={w} className="skeleton" style={{ width: `${w}%` }} />)}
        </div>
      </aside>
    )
  }
  if (isError || !app) {
    return <aside className="sheet paper"><div className="paper-section error-box" role="alert">{error?.message ?? 'This candidate could not be loaded.'}</div></aside>
  }

  const evidence = app.evidence ?? []
  const met = evidence.filter((e) => e.verdict === 'met')
  const mustMet = evidence.filter((e) => mustIds.has(e.requirement_id) && e.verdict === 'met').length
  const unfound = evidence.filter((e) => e.verdict === 'not_found')
  const blocks = doc ? toBlocks(doc.text, evidence) : []
  const band = bandOf(app)
  const stale = app.rubric_version != null && app.rubric_version !== job.rubric_version

  const jumpTo = (e: Evidence) => {
    if (e.start == null) return
    const key = `${e.start}:${e.end}`
    blockRefs.current.get(key)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    setFlash(key)
  }

  return (
    <aside className="sheet paper" aria-label={`Candidate ${displayName(app.filename)}`} data-fresh="true" key={applicationId}>
      <div className="paper-head">
        {app.fit_score != null ? (
          <div className="score-box" aria-label={`Fit score ${Math.round(app.fit_score)} out of 100`}>{Math.round(app.fit_score)}</div>
        ) : (
          <div className="score-box" style={{ color: 'var(--ink-3)', borderColor: 'var(--rule)' }} aria-label="Not scored">–</div>
        )}
        <div>
          <h2>{displayName(app.filename)}</h2>
          <p>
            <span className={`band band-${band}`}>{BAND_LABEL[band]}</span>
          </p>
          {app.status === 'SCORED' && (
            <p>
              Must-haves {mustMet}/{mustIds.size} · {met.length} of {evidence.length} met · checklist v{app.rubric_version}
            </p>
          )}
        </div>
      </div>

      {stale && (
        <div className="paper-section">
          <div className="notice" style={{ margin: 0 }}>
            <AlertTriangle size={16} aria-hidden="true" />
            <span>Scored with checklist v{app.rubric_version}. The current checklist is v{job.rubric_version}; approve it to rescore.</span>
          </div>
        </div>
      )}

      {app.status === 'NEEDS_ATTENTION' ? (
        <div className="paper-section">
          <div className="notice" style={{ margin: 0 }}>
            <AlertTriangle size={16} aria-hidden="true" />
            <span>
              This resume could not be read ({app.quality.replace(/_/g, ' ')}), so it has no score. Scanned PDFs need OCR, which is not available yet. Check the original file by hand.
            </span>
          </div>
        </div>
      ) : (
        <>
          <section className="paper-section" aria-labelledby="marks-title">
            <h3 id="marks-title" className="visually-hidden">Marks</h3>
            <ul className="chips">
              {evidence.map((e) => (
                <li key={e.requirement_id}>
                  <button
                    type="button"
                    className={`chip chip-${e.verdict}${mustIds.has(e.requirement_id) ? ' must' : ''}`}
                    onClick={() => jumpTo(e)}
                    disabled={e.verdict === 'not_found'}
                    title={`${e.requirement_id} ${e.requirement}: ${VERDICT_LABEL[e.verdict]}`}
                    aria-label={`${e.requirement_id} ${e.requirement}: ${VERDICT_LABEL[e.verdict]}${e.verdict !== 'not_found' ? '. Show the line' : ''}`}
                  >
                    <MarkGlyph verdict={e.verdict} size={14} />{e.requirement_id}
                  </button>
                </li>
              ))}
            </ul>
          </section>

          <section className="script" aria-labelledby="script-title">
            <h3 id="script-title">Resume, with the lines that earned each mark</h3>
            {!doc && <div className="skeleton" style={{ width: '90%' }} />}
            {blocks.map((block) => {
              const key = `${block.start}:${block.end}`
              const marked = block.marks.length > 0
              return (
                <div
                  key={key}
                  ref={(el) => { if (el) blockRefs.current.set(key, el); else blockRefs.current.delete(key) }}
                  className={`script-row${marked ? ' marked' : ''}${flash === key ? ' flash' : ''}`}
                >
                  <div className="script-text">{doc!.text.slice(block.start, block.end).trim()}</div>
                  <div className={marked ? 'margin' : 'margin margin-empty'} aria-label={marked ? `Marks: ${block.marks.map((m) => `${m.requirement_id} ${VERDICT_LABEL[m.verdict]}`).join(', ')}` : undefined}>
                    {block.marks.map((m) => (
                      <span key={m.requirement_id}><MarkGlyph verdict={m.verdict} size={14} />{m.requirement_id}</span>
                    ))}
                  </div>
                </div>
              )
            })}
            {unfound.length > 0 && (
              <p className="unfound">No supporting line found for {unfound.map((e) => `${e.requirement_id} (${e.requirement})`).join('; ')}.</p>
            )}
          </section>

          <details className="paper-section detail">
            <summary>Scoring detail · coverage {app.coverage?.toFixed(2)} · semantic {app.semantic?.toFixed(2)}</summary>
            <ul className="req-list">
              {evidence.map((e) => (
                <li key={e.requirement_id}>
                  <button type="button" className="req" onClick={() => jumpTo(e)} disabled={e.verdict === 'not_found'} aria-label={`${e.requirement_id} ${e.requirement}: ${VERDICT_LABEL[e.verdict]}${e.verdict !== 'not_found' ? '. Show the line' : ''}`}>
                    <span style={{ color: e.verdict === 'not_found' ? 'var(--ink-3)' : 'var(--examiner)' }}><MarkGlyph verdict={e.verdict} /></span>
                    <span className={`req-id${mustIds.has(e.requirement_id) ? ' must' : ''}`}>{e.requirement_id}</span>
                    <span className="req-text">
                      {e.requirement}
                      <small>Weight {e.weight}{mustIds.has(e.requirement_id) ? ' · must-have' : ''} · reranker {e.match_score.toFixed(2)} · similarity {e.similarity.toFixed(2)}</small>
                    </span>
                    <span className="req-verdict">{VERDICT_LABEL[e.verdict]}</span>
                  </button>
                </li>
              ))}
            </ul>
          </details>
        </>
      )}
    </aside>
  )
}
