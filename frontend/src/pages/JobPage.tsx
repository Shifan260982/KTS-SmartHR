import { useEffect, useMemo, useRef } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { AlertTriangle, ListChecks, Upload } from 'lucide-react'
import { useApplications, useJob, type ApplicationSummary } from '../api/client'
import { BAND_LABEL, bandOf, displayName, nextBand, type BandFilter } from '../lib/bands'
import { MarksGrid, MarksHead, MarksLegend } from '../components/Marks'
import { CandidatePaper } from '../components/CandidatePaper'
import { UploadPanel } from '../components/UploadPanel'

const FILTERS: BandFilter[] = ['all', 'strong_fit', 'possible_fit', 'low_match', 'must_have_missing', 'attention']

export function JobPage() {
  const jobId = Number(useParams().jobId)
  const [params, setParams] = useSearchParams()
  const { data: job, isError: jobError, error } = useJob(jobId)
  const { data: apps, isLoading } = useApplications(jobId)
  const tableRef = useRef<HTMLTableSectionElement>(null)

  const filter = (params.get('band') as BandFilter) ?? 'all'
  const panel = params.get('panel')
  const selected = params.get('c') ? Number(params.get('c')) : undefined

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: apps?.length ?? 0 }
    for (const a of apps ?? []) c[bandOf(a)] = (c[bandOf(a)] ?? 0) + 1
    return c
  }, [apps])
  const rows = useMemo(() => (apps ?? []).filter((a) => filter === 'all' || bandOf(a) === filter), [apps, filter])

  const set = (patch: Record<string, string | undefined>, replace = false) => {
    const next = new URLSearchParams(params)
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined) next.delete(k)
      else next.set(k, v)
    }
    setParams(next, { replace })
  }

  // Open the top-ranked candidate so the paper is never blank.
  useEffect(() => {
    if (!panel && rows.length && (selected === undefined || !rows.some((r) => r.id === selected))) set({ c: String(rows[0].id) }, true)
  }, [rows, selected, panel]) // eslint-disable-line react-hooks/exhaustive-deps

  // J / K walk the queue; the current candidate stays highlighted as you go.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      if (e.ctrlKey || e.metaKey || e.altKey || target.closest('input, textarea, select')) return
      if (e.key !== 'j' && e.key !== 'k') return
      const index = rows.findIndex((r) => r.id === selected)
      const next = rows[Math.min(rows.length - 1, Math.max(0, index + (e.key === 'j' ? 1 : -1)))]
      if (!next) return
      set({ c: String(next.id), panel: undefined }, true)
      tableRef.current?.querySelector<HTMLElement>(`[data-id="${next.id}"]`)?.scrollIntoView({ block: 'nearest' })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [rows, selected]) // eslint-disable-line react-hooks/exhaustive-deps

  if (jobError) return <div className="error-box" role="alert">{error.message}</div>
  if (!job) return <div className="skeleton" style={{ width: 320, height: 28 }} />

  const scored = apps?.filter((a) => a.status === 'SCORED').length ?? 0
  const select = (app: ApplicationSummary) => set({ c: String(app.id), panel: undefined })

  return (
    <>
      <header className="page-head">
        <div>
          <h1>{job.title}</h1>
          <div className="page-meta">
            <span>{job.code}</span>
            <span>Checklist v{job.rubric_version} {job.rubric_approved ? 'approved' : 'draft'}</span>
            <span>{scored} scored</span>
            {job.attention_count > 0 && <span>{job.attention_count} need attention</span>}
          </div>
        </div>
        <div className="actions">
          <Link to={`/jobs/${job.id}/rubric`} className="btn"><ListChecks size={17} aria-hidden="true" /> Checklist and thresholds</Link>
          <button type="button" className="btn btn-primary" onClick={() => set({ panel: 'upload' })}><Upload size={17} aria-hidden="true" /> Upload resumes</button>
        </div>
      </header>

      {!job.rubric_approved && (
        <div className="notice" role="status">
          <AlertTriangle size={16} aria-hidden="true" />
          <span>
            {job.rubric.length === 0 ? 'This job has no checklist yet. ' : `Checklist v${job.rubric_version} is a draft. `}
            <Link to={`/jobs/${job.id}/rubric`}>{job.rubric.length === 0 ? 'Write the checklist' : 'Review and approve it'}</Link> to score resumes.
          </span>
        </div>
      )}

      <div className="filters" role="group" aria-label="Filter by band">
        {FILTERS.map((f) => (
          <button key={f} type="button" className="filter" aria-pressed={filter === f} onClick={() => set({ band: f === 'all' ? undefined : f, c: undefined })}>
            {f === 'all' ? 'All' : BAND_LABEL[f]}<span>{counts[f] ?? 0}</span>
          </button>
        ))}
        <span className="keys" aria-hidden="true"><kbd>J</kbd> <kbd>K</kbd> next / previous candidate</span>
      </div>

      <div className="workspace">
        <section className="sheet" aria-label="Ranked candidates">
          {isLoading ? (
            <div className="section-body">{[90, 75, 82, 68].map((w) => <div key={w} className="skeleton" style={{ width: `${w}%` }} />)}</div>
          ) : rows.length === 0 ? (
            <div className="empty">
              <h2>{apps?.length ? 'No candidates in this band' : 'No resumes yet'}</h2>
              <p>{apps?.length ? 'Try another band filter.' : job.rubric_approved ? 'Upload resumes and each one is scored against the approved checklist as it arrives.' : 'Approve the checklist first, then upload resumes.'}</p>
              {!apps?.length && job.rubric_approved && (
                <button type="button" className="btn btn-primary" onClick={() => set({ panel: 'upload' })}><Upload size={17} aria-hidden="true" /> Upload resumes</button>
              )}
            </div>
          ) : (
            <>
              <table className="ranked">
                <thead>
                  <tr>
                    <th scope="col">#</th>
                    <th scope="col">Candidate</th>
                    <th scope="col">Fit</th>
                    <th scope="col" className="col-marks"><span className="visually-hidden">Marks per requirement</span><MarksHead rubric={job.rubric} /></th>
                  </tr>
                </thead>
                <tbody ref={tableRef}>
                  {rows.map((app, i) => {
                    const band = bandOf(app)
                    return (
                      <tr key={app.id} data-id={app.id} data-selected={app.id === selected && !panel} onClick={() => select(app)}>
                        <td className="rank">{i + 1}</td>
                        <td className="cand">
                          <button type="button" title={app.filename} aria-current={app.id === selected && !panel ? 'true' : undefined} onClick={(e) => { e.stopPropagation(); select(app) }}>{displayName(app.filename)}</button>
                          <span className="next"><span className={`band band-${band}`}>{BAND_LABEL[band]}</span> {nextBand(app, job)}</span>
                        </td>
                        <td>{app.fit_score != null ? <span className="fit-box">{Math.round(app.fit_score)}</span> : <span className="fit-none">No score</span>}</td>
                        <td className="col-marks"><MarksGrid rubric={job.rubric} marks={app.marks} /></td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
              <MarksLegend />
            </>
          )}
        </section>

        {panel === 'upload' ? (
          <UploadPanel job={job} onClose={() => set({ panel: undefined })} />
        ) : selected !== undefined && rows.some((r) => r.id === selected) ? (
          <CandidatePaper applicationId={selected} job={job} />
        ) : null}
      </div>
    </>
  )
}
