import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { FileText, Loader2, Upload } from 'lucide-react'
import { extractJobDescription, saveRubric, useCreateJob, type Requirement } from '../api/client'

type Mode = 'upload' | 'manual'

export function NewJobPage() {
  const navigate = useNavigate()
  const client = useQueryClient()
  const create = useCreateJob()
  const [mode, setMode] = useState<Mode>('upload')
  const [code, setCode] = useState('')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [drafted, setDrafted] = useState<Requirement[]>([])
  const [jdName, setJdName] = useState('')
  const [over, setOver] = useState(false)

  const extract = useMutation({
    mutationFn: extractJobDescription,
    onSuccess: (draft, file) => {
      setJdName(file.name)
      setTitle(draft.title)
      setDescription(draft.description)
      setDrafted(draft.requirements)
    },
  })

  const submit = useMutation({
    mutationFn: async () => {
      const job = await create.mutateAsync({ code: code.trim(), title: title.trim(), description: description.trim() })
      // The drafted checklist is saved as a draft only: HR reviews and approves it before any scoring.
      if (mode === 'upload' && drafted.length) await saveRubric(job.id, drafted)
      return job
    },
    onSuccess: (job) => {
      client.invalidateQueries({ queryKey: ['jobs'] })
      navigate(`/jobs/${job.id}/rubric`)
    },
  })

  const pick = (files: FileList | null) => {
    const file = files?.[0]
    if (file) extract.mutate(file)
  }
  const showForm = mode === 'manual' || extract.isSuccess
  const mustCount = drafted.filter((r) => r.type === 'must').length

  return (
    <>
      <header className="page-head">
        <div>
          <h1>New job</h1>
          <div className="page-meta"><span>Start from the JD file, or type the job in yourself. You review the checklist before anything is scored.</span></div>
        </div>
      </header>

      <div className="form">
        <div className="segmented" role="radiogroup" aria-label="How to start">
          <button type="button" role="radio" aria-checked={mode === 'upload'} onClick={() => setMode('upload')}><Upload size={16} aria-hidden="true" /> Upload JD file</button>
          <button type="button" role="radio" aria-checked={mode === 'manual'} onClick={() => setMode('manual')}><FileText size={16} aria-hidden="true" /> Enter manually</button>
        </div>

        {mode === 'upload' && (
          <section className="sheet" aria-labelledby="jd-upload-title">
            <div className="section-head"><h2 id="jd-upload-title">Job description file</h2>{jdName && <span className="status-line">{jdName}</span>}</div>
            <div className="section-body">
              <label
                className="drop"
                data-over={over}
                onDragOver={(e) => { e.preventDefault(); setOver(true) }}
                onDragLeave={() => setOver(false)}
                onDrop={(e) => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files) }}
              >
                {extract.isPending ? <Loader2 size={26} className="spin" aria-hidden="true" /> : <Upload size={26} aria-hidden="true" />}
                <strong>{extract.isPending ? 'Reading the JD…' : jdName ? 'Drop another JD to replace it' : 'Drop the JD here, or choose a file'}</strong>
                <span>PDF or DOCX, up to 10 MB. The title, description and a draft checklist are filled in for you to check.</span>
                <input type="file" accept=".pdf,.docx,.txt" className="visually-hidden" onChange={(e) => { pick(e.target.files); e.target.value = '' }} />
              </label>
              {extract.error && <div className="error-box" role="alert">{extract.error.message}</div>}
              {extract.isSuccess && (
                <div role="status">
                  <h3 className="draft-title">
                    {drafted.length ? `Draft checklist: ${drafted.length} items, ${mustCount} must-have` : 'No checklist items found'}
                  </h3>
                  {drafted.length ? (
                    <ul className="draft-list">
                      {drafted.map((r) => (
                        <li key={r.id}><span className={`req-id${r.type === 'must' ? ' must' : ''}`}>{r.id}</span>{r.text}<span className="draft-type">{r.type === 'must' ? 'Must-have' : 'Nice-to-have'}</span></li>
                      ))}
                    </ul>
                  ) : (
                    <p className="status-line">The file has no recognisable requirements section. You can write the checklist on the next page.</p>
                  )}
                  <p className="status-line">Drafted by simple rules from the JD's requirements section. You can edit, reword and mark must-haves on the next page before approving.</p>
                </div>
              )}
            </div>
          </section>
        )}

        {showForm && (
          <form className="form" onSubmit={(e) => { e.preventDefault(); submit.mutate() }}>
            <div className="field">
              <label htmlFor="code">Job code</label>
              <input id="code" className="input" required pattern="[A-Za-z0-9-]{2,30}" value={code} onChange={(e) => setCode(e.target.value)} placeholder="KTS-SE2" aria-describedby="code-hint" style={{ maxWidth: 260 }} />
              <span className="hint" id="code-hint">2–30 letters, numbers or hyphens. Use the code from the job ad.</span>
            </div>
            <div className="field">
              <label htmlFor="title">Job title</label>
              <input id="title" className="input" required minLength={2} maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Senior Backend Engineer" />
            </div>
            <div className="field">
              <label htmlFor="jd">Job description</label>
              <textarea id="jd" className="textarea" required maxLength={20000} rows={mode === 'upload' ? 8 : 14} value={description} onChange={(e) => setDescription(e.target.value)} aria-describedby="jd-hint" />
              <span className="hint" id="jd-hint">{mode === 'upload' ? 'Text read from the file. Fix anything the PDF garbled.' : 'Paste the full JD. It stays beside the checklist while you write it.'}</span>
            </div>
            {submit.error && <div className="error-box" role="alert">{submit.error.message}</div>}
            <div className="actions">
              <button type="submit" className="btn btn-primary" disabled={submit.isPending}>
                {submit.isPending && <Loader2 size={16} className="spin" aria-hidden="true" />}
                {mode === 'upload' && drafted.length ? 'Create job and review checklist' : 'Create job and write checklist'}
              </button>
            </div>
          </form>
        )}
      </div>
    </>
  )
}
