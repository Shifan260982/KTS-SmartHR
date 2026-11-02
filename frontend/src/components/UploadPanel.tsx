import { useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, CircleAlert, Clock, Loader2, Upload, X } from 'lucide-react'
import { uploadResume, type Job } from '../api/client'
import { BAND_LABEL, bandOf } from '../lib/bands'

const ACCEPT = '.pdf,.docx,.txt'
const MAX_BYTES = 10 * 1024 * 1024

type Item = { id: string; file: File; state: 'waiting' | 'uploading' | 'done' | 'error'; note?: string }

export function UploadPanel({ job, onClose }: { job: Job; onClose: () => void }) {
  const client = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const [items, setItems] = useState<Item[]>([])
  const [over, setOver] = useState(false)
  const busy = items.some((i) => i.state === 'waiting' || i.state === 'uploading')

  const update = (id: string, patch: Partial<Item>) => setItems((all) => all.map((i) => (i.id === id ? { ...i, ...patch } : i)))

  async function enqueue(files: FileList | null) {
    if (!files?.length) return
    const added: Item[] = [...files].map((file) => {
      const ok = /\.(pdf|docx|txt)$/i.test(file.name)
      const tooBig = file.size > MAX_BYTES
      return {
        id: crypto.randomUUID(),
        file,
        state: ok && !tooBig ? 'waiting' : 'error',
        note: !ok ? 'Only PDF, DOCX or TXT files can be read.' : tooBig ? 'File is larger than 10 MB.' : undefined,
      }
    })
    setItems((all) => [...added, ...all])
    // One at a time: the server scores each file as it arrives.
    for (const item of added.filter((i) => i.state === 'waiting')) {
      update(item.id, { state: 'uploading' })
      try {
        const app = await uploadResume(job.id, item.file)
        const band = BAND_LABEL[bandOf(app)]
        update(item.id, { state: 'done', note: app.fit_score != null ? `${Math.round(app.fit_score)} · ${band}` : band })
      } catch (e) {
        update(item.id, { state: 'error', note: (e as Error).message })
      }
      client.invalidateQueries({ queryKey: ['applications', job.id] })
      client.invalidateQueries({ queryKey: ['jobs'] })
    }
  }

  const done = items.filter((i) => i.state === 'done').length

  return (
    <aside className="sheet upload" aria-label="Upload resumes">
      <div className="section-head">
        <h2>Upload resumes</h2>
        <button type="button" className="btn btn-quiet" onClick={onClose} aria-label="Close upload panel"><X size={18} /></button>
      </div>
      <div className="section-body">
        {!job.rubric_approved ? (
          <div className="notice" role="status">Approve this job's checklist before uploading. Resumes are scored against the approved version.</div>
        ) : (
          <>
            <label
              className="drop"
              data-over={over}
              onDragOver={(e) => { e.preventDefault(); setOver(true) }}
              onDragLeave={() => setOver(false)}
              onDrop={(e) => { e.preventDefault(); setOver(false); enqueue(e.dataTransfer.files) }}
            >
              <Upload size={26} aria-hidden="true" />
              <strong>Drop resumes here, or choose files</strong>
              <span>PDF, DOCX or TXT, up to 10 MB each. Each one is scored against checklist v{job.rubric_version}.</span>
              <input ref={input} type="file" accept={ACCEPT} multiple className="visually-hidden" onChange={(e) => { enqueue(e.target.files); e.target.value = '' }} />
            </label>
            {items.length > 0 && (
              <p className="status-line" aria-live="polite">
                {busy ? `Scoring ${items.filter((i) => i.state === 'uploading' || i.state === 'waiting').length} remaining…` : `${done} of ${items.length} scored.`}
              </p>
            )}
            <ul className="uploads">
              {items.map((i) => (
                <li key={i.id}>
                  {i.state === 'waiting' && <Clock size={16} aria-label="Waiting" color="var(--ink-3)" />}
                  {i.state === 'uploading' && <Loader2 size={16} className="spin" aria-label="Scoring" color="var(--cover)" />}
                  {i.state === 'done' && <CheckCircle2 size={16} aria-label="Scored" color="var(--cover)" />}
                  {i.state === 'error' && <CircleAlert size={16} aria-label="Failed" color="#8c1f1a" />}
                  <span className="name" title={i.file.name}>{i.file.name}</span>
                  {i.state !== 'error' && <span className="state">{i.state === 'done' ? i.note : i.state === 'uploading' ? 'Scoring…' : 'Waiting'}</span>}
                  {i.state === 'error' && <span className="state err">{i.note}</span>}
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </aside>
  )
}
