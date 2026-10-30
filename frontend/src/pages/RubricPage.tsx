import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Loader2, Plus, Trash2 } from 'lucide-react'
import { useApproveRubric, useJob, useSaveRubric, useSaveSettings, type Job, type JobSettings, type Requirement } from '../api/client'

function nextId(rows: Requirement[]): string {
  const prefix = rows[0]?.id.match(/^[A-Za-z]+/)?.[0] ?? 'R'
  const max = Math.max(0, ...rows.map((r) => Number(r.id.replace(/^[A-Za-z]+/, '')) || 0))
  return `${prefix}${max + 1}`
}

const blank = (rows: Requirement[]): Requirement => ({ id: nextId(rows), text: '', type: 'nice', weight: 2 })

export function RubricPage() {
  const jobId = Number(useParams().jobId)
  const { data: job } = useJob(jobId)
  if (!job) return <div className="skeleton" style={{ width: 320, height: 28 }} />
  return <RubricEditor key={`${job.id}:${job.rubric_version}`} job={job} />
}

function RubricEditor({ job }: { job: Job }) {
  const [rows, setRows] = useState<Requirement[]>(job.rubric.length ? job.rubric : [blank([])])
  const save = useSaveRubric(job.id)
  const approve = useApproveRubric(job.id)
  const dirty = JSON.stringify(rows) !== JSON.stringify(job.rubric)
  const working = save.isPending || approve.isPending
  const error = save.error ?? approve.error

  const edit = (i: number, patch: Partial<Requirement>) => setRows((all) => all.map((r, j) => (j === i ? { ...r, ...patch } : r)))

  async function approveAndScore() {
    if (dirty) await save.mutateAsync(rows)
    await approve.mutateAsync()
  }

  return (
    <>
      <header className="page-head">
        <div>
          <Link to={`/jobs/${job.id}`} className="btn btn-quiet" style={{ marginLeft: -10, marginBottom: 6 }}><ArrowLeft size={16} aria-hidden="true" /> Back to shortlist</Link>
          <h1>Checklist for {job.title}</h1>
          <div className="page-meta">
            <span>{job.code}</span>
            <span>{job.rubric_version === 0 ? 'Not saved yet' : `v${job.rubric_version} ${job.rubric_approved ? 'approved' : 'draft'}`}</span>
            {dirty && <span>Unsaved changes</span>}
          </div>
        </div>
      </header>

      <div className="two-col">
        <div style={{ display: 'grid', gap: 22 }}>
          <section className="sheet" aria-labelledby="req-title">
            <div className="section-head">
              <h2 id="req-title">Requirements</h2>
              <span className="status-line">Must-haves that are not met put a candidate in "Must-have missing".</span>
            </div>
            <table className="rubric-table">
              <thead>
                <tr><th scope="col">ID</th><th scope="col">Requirement, as it would read in a resume</th><th scope="col">Type</th><th scope="col">Weight</th><th scope="col"><span className="visually-hidden">Remove</span></th></tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={r.id}>
                    <td className={`id${r.type === 'must' ? ' must' : ''}`}>{r.id}</td>
                    <td className="text-cell">
                      <textarea className="input grow" rows={1} value={r.text} onChange={(e) => edit(i, { text: e.target.value.replace(/\n/g, ' ') })} aria-label={`${r.id} requirement`} placeholder="e.g. Builds REST APIs in ASP.NET Core" maxLength={300} />
                    </td>
                    <td className="t">
                      <select className="select" value={r.type} onChange={(e) => edit(i, { type: e.target.value as Requirement['type'] })} aria-label={`${r.id} type`}>
                        <option value="must">Must-have</option>
                        <option value="nice">Nice-to-have</option>
                      </select>
                    </td>
                    <td className="w">
                      <input className="input" type="number" min={1} max={10} value={r.weight} onChange={(e) => edit(i, { weight: Number(e.target.value) })} aria-label={`${r.id} weight, 1 to 10`} />
                    </td>
                    <td className="del">
                      <button type="button" className="btn btn-quiet" onClick={() => setRows((all) => all.filter((_, j) => j !== i))} disabled={rows.length === 1} aria-label={`Remove ${r.id}`}><Trash2 size={16} /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="section-foot">
              <button type="button" className="btn" onClick={() => setRows((all) => [...all, blank(all)])} disabled={rows.length >= 30}><Plus size={16} aria-hidden="true" /> Add requirement</button>
              <div className="actions" style={{ alignItems: 'center' }}>
                {!dirty && !working && (
                  <span className="status-line" role="status">
                    {job.rubric_approved ? `No changes since v${job.rubric_version} was approved.` : job.rubric_version > 0 ? `v${job.rubric_version} is saved as a draft.` : ''}
                  </span>
                )}
                <button type="button" className="btn" onClick={() => save.mutate(rows)} disabled={!dirty || working}>Save draft</button>
                <button type="button" className="btn btn-primary" onClick={approveAndScore} disabled={working || (!dirty && job.rubric_approved)}>
                  {approve.isPending && <Loader2 size={16} className="spin" aria-hidden="true" />}
                  {approve.isPending ? `Scoring ${job.application_count} resumes…` : 'Approve and score'}
                </button>
              </div>
            </div>
            {(error || approve.isPending) && (
              <div className="section-body" style={{ paddingTop: 0 }}>
                {error && <div className="error-box" role="alert">{error.message}</div>}
                {approve.isPending && <p className="status-line" role="status">Every resume for this job is being rescored against the new checklist. With many resumes this takes a few minutes; keep this tab open.</p>}
              </div>
            )}
          </section>

          <ThresholdForm job={job} />
        </div>

        <aside className="sheet" aria-labelledby="jd-title">
          <div className="section-head"><h2 id="jd-title">Job description</h2></div>
          <div className="jd">{job.description}</div>
        </aside>
      </div>
    </>
  )
}

const FIELDS: { key: keyof JobSettings; label: string; hint: string; step: number; max: number }[] = [
  { key: 'met_threshold', label: 'Met threshold', hint: 'Reranker score (0–1) at or above which a requirement is met.', step: 0.05, max: 1 },
  { key: 'partial_similarity', label: 'Partial threshold', hint: 'Meaning similarity (0–1) at or above which an unconfirmed match counts as partial.', step: 0.025, max: 1 },
  { key: 'evidence_weight', label: 'Evidence weight', hint: 'Share of the fit score from coverage; the rest comes from match strength.', step: 0.05, max: 1 },
  { key: 'strong_fit', label: 'Strong fit from', hint: 'Fit score (0–100) for the Strong fit band.', step: 1, max: 100 },
  { key: 'floor', label: 'Low match below', hint: 'Fit score (0–100) under which a candidate is a Low match.', step: 1, max: 100 },
]

function ThresholdForm({ job }: { job: Job }) {
  const [values, setValues] = useState<JobSettings>(job.settings)
  const save = useSaveSettings(job.id)
  useEffect(() => setValues(job.settings), [job.settings])
  const dirty = JSON.stringify(values) !== JSON.stringify(job.settings)

  return (
    <form className="sheet" aria-labelledby="th-title" onSubmit={(e) => { e.preventDefault(); save.mutate(values) }}>
      <div className="section-head">
        <h2 id="th-title">Thresholds</h2>
        <span className="status-line">Tuned on a few samples only. Saving rescores every resume.</span>
      </div>
      <div className="section-body">
        <div className="settings-grid">
          {FIELDS.map((f) => (
            <div className="field" key={f.key}>
              <label htmlFor={f.key}>{f.label}</label>
              <input id={f.key} className="input" type="number" min={0} max={f.max} step={f.step} value={values[f.key]} onChange={(e) => setValues({ ...values, [f.key]: Number(e.target.value) })} aria-describedby={`${f.key}-hint`} />
              <span className="hint" id={`${f.key}-hint`}>{f.hint}</span>
            </div>
          ))}
        </div>
        {save.error && <div className="error-box" role="alert">{save.error.message}</div>}
      </div>
      <div className="section-foot">
        <span className="status-line" role="status">
          {save.isPending ? `Rescoring ${job.application_count} resumes…` : save.isSuccess && !dirty ? 'Saved and rescored.' : !dirty ? 'No changes to save. Edit a value to rescore.' : ''}
        </span>
        <button type="submit" className="btn btn-primary" disabled={!dirty || save.isPending}>
          {save.isPending && <Loader2 size={16} className="spin" aria-hidden="true" />}Save and rescore
        </button>
      </div>
    </form>
  )
}
