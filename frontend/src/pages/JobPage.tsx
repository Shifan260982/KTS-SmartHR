import { Link, useParams, useSearchParams } from 'react-router-dom'
import { AlertTriangle, ChevronDown, ListChecks, Upload } from 'lucide-react'
import { useApplications, useJob } from '../api/client'
import { BAND_LABEL, bandOf, displayName } from '../lib/bands'
import { Reasons } from '../components/Reasons'
import { UploadPanel } from '../components/UploadPanel'

export function JobPage() {
  const jobId = Number(useParams().jobId)
  const [params, setParams] = useSearchParams()
  const { data: job, isError: jobError, error } = useJob(jobId)
  const { data: apps, isLoading } = useApplications(jobId)

  const open = params.get('c') ? Number(params.get('c')) : undefined
  const uploading = params.get('panel') === 'upload'

  const set = (key: string, value: string | undefined) => {
    const next = new URLSearchParams(params)
    if (value === undefined) next.delete(key)
    else next.set(key, value)
    setParams(next, { replace: true })
  }

  if (jobError) return <div className="error-box" role="alert">{error.message}</div>
  if (!job) return <div className="skeleton" style={{ width: 320, height: 28 }} />

  return (
    <div className="shortlist">
      <header className="page-head">
        <div>
          <h1>{job.title}</h1>
          <div className="page-meta">
            <span>{job.code}</span>
            <span>{apps?.length ?? 0} candidates, best first</span>
          </div>
        </div>
        <div className="actions">
          <Link to={`/jobs/${job.id}/rubric`} className="btn"><ListChecks size={17} aria-hidden="true" /> Checklist</Link>
          <button type="button" className="btn btn-primary" onClick={() => set('panel', uploading ? undefined : 'upload')} aria-expanded={uploading}>
            <Upload size={17} aria-hidden="true" /> Upload resumes
          </button>
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

      {uploading && <UploadPanel job={job} onClose={() => set('panel', undefined)} />}

      {isLoading ? (
        <div className="section-body">{[90, 75, 82].map((w) => <div key={w} className="skeleton" style={{ width: `${w}%` }} />)}</div>
      ) : !apps?.length ? (
        <div className="sheet empty">
          <h2>No candidates yet</h2>
          <p>{job.rubric_approved ? 'Upload resumes and each one is scored against the checklist as it arrives.' : 'Approve the checklist first, then upload resumes.'}</p>
        </div>
      ) : (
        <ol className="candidates">
          {apps.map((app) => {
            const expanded = app.id === open
            const band = bandOf(app)
            const panelId = `reasons-${app.id}`
            return (
              <li key={app.id} className="candidate" data-open={expanded}>
                <button type="button" className="candidate-row" aria-expanded={expanded} aria-controls={panelId} onClick={() => set('c', expanded ? undefined : String(app.id))}>
                  <span className="candidate-name" title={app.filename}>{displayName(app.filename)}</span>
                  <span className={`candidate-band band-${band}`}>{BAND_LABEL[band]}</span>
                  {app.fit_score != null
                    ? <span className="fit-box" aria-label={`Score ${Math.round(app.fit_score)} out of 100`}>{Math.round(app.fit_score)}</span>
                    : <span className="fit-none">No score</span>}
                  <ChevronDown size={18} className="candidate-chevron" aria-hidden="true" />
                </button>
                {expanded && <div id={panelId}><Reasons applicationId={app.id} job={job} /></div>}
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}
