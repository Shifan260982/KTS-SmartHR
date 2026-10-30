import { Plus } from 'lucide-react'
import { NavLink, Link } from 'react-router-dom'
import { useJobs } from '../api/client'

export function Rail() {
  const { data: jobs, isError } = useJobs()
  const most = Math.max(1, ...(jobs ?? []).map((j) => j.application_count))

  return (
    <nav className="rail" aria-label="Jobs">
      <Link to="/" className="rail-brand">
        <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true">
          <rect width="32" height="32" rx="6" fill="#fbfaf5" />
          <path d="M8 17l5 5 11-12" fill="none" stroke="#1f6f6b" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        SmartHR
      </Link>
      <h2>Jobs</h2>
      {isError && <p className="rail-foot">Jobs could not be loaded.</p>}
      <div className="rail-jobs">
      {jobs?.map((job) => (
        <NavLink key={job.id} to={`/jobs/${job.id}`} className="rail-job" end={false}>
          <strong>{job.title}</strong>
          <small>
            {job.code} · {job.application_count} {job.application_count === 1 ? 'resume' : 'resumes'}
          </small>
          <div className="rail-bar" aria-hidden="true">
            <i style={{ width: `${(job.application_count / most) * 100}%` }} />
          </div>
        </NavLink>
      ))}
      <Link to="/jobs/new" className="rail-new">
        <Plus size={16} aria-hidden="true" /> New job
      </Link>
      </div>
      {!!jobs?.length && (
        <>
          <h2>Needs attention</h2>
          <div className="rail-attn">
            {jobs.map((job) => (
              <Link key={job.id} to={`/jobs/${job.id}?band=attention`} className="rail-attn-item">
                <span>{job.code}</span>
                <b aria-label={`${job.attention_count} unreadable resumes`}>{job.attention_count}</b>
              </Link>
            ))}
          </div>
        </>
      )}
      <p className="rail-foot">Scores rank resumes against each job's approved checklist. HR makes every decision.</p>
    </nav>
  )
}
