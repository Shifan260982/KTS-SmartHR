import { Link, Navigate, Route, Routes } from 'react-router-dom'
import { Plus } from 'lucide-react'
import { useJobs } from './api/client'
import { Rail } from './components/Rail'
import { JobPage } from './pages/JobPage'
import { NewJobPage } from './pages/NewJobPage'
import { RubricPage } from './pages/RubricPage'

function Home() {
  const { data: jobs, isLoading, isError, error } = useJobs()
  if (isLoading) return <div className="skeleton" style={{ width: 320, height: 28 }} />
  if (isError) return <div className="error-box" role="alert">{error.message}</div>
  if (jobs?.length) return <Navigate to={`/jobs/${jobs[0].id}`} replace />
  return (
    <div className="sheet empty">
      <h2>No jobs yet</h2>
      <p>Create a job from its description, write the checklist HR wants every candidate measured against, then upload resumes.</p>
      <Link to="/jobs/new" className="btn btn-primary"><Plus size={17} aria-hidden="true" /> New job</Link>
    </div>
  )
}

export default function App() {
  return (
    <div className="app">
      <Rail />
      <main className="main">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/jobs/new" element={<NewJobPage />} />
          <Route path="/jobs/:jobId" element={<JobPage />} />
          <Route path="/jobs/:jobId/rubric" element={<RubricPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  )
}
