import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { components } from './schema'

export type Job = components['schemas']['JobOut']
export type JobSettings = components['schemas']['JobSettings']
export type Requirement = components['schemas']['Requirement']
// settings has a server-side default, so the request may omit it.
export type JobCreate = Omit<components['schemas']['JobCreate'], 'settings'> & { settings?: JobSettings }
export type ApplicationSummary = components['schemas']['ApplicationSummary']
export type Application = components['schemas']['ApplicationOut']
export type Evidence = components['schemas']['Evidence']
export type Verdict = Evidence['verdict']

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/** FastAPI returns `detail` as a string, or as a list of validation errors for 422. */
function describe(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((d: { msg?: string; loc?: (string | number)[] }) => {
        const field = d.loc?.filter((p) => p !== 'body').join(' › ')
        return [field, d.msg?.replace(/^Value error, /, '')].filter(Boolean).join(': ')
      })
      .join('\n')
  }
  return 'Something went wrong. Please try again.'
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api/v1${path}`, init)
  } catch {
    throw new ApiError(0, 'Cannot reach the SmartHR server. Check that the API is running on port 8000.')
  }
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new ApiError(response.status, describe(body?.detail))
  return body as T
}

const json = (method: string, data?: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: data === undefined ? undefined : JSON.stringify(data),
})

const PAGE = 200

async function allApplications(jobId: number): Promise<ApplicationSummary[]> {
  const rows: ApplicationSummary[] = []
  for (let offset = 0; ; offset += PAGE) {
    const page = await request<ApplicationSummary[]>(`/jobs/${jobId}/applications?limit=${PAGE}&offset=${offset}`)
    rows.push(...page)
    if (page.length < PAGE) return rows
  }
}

export const useJobs = () => useQuery({ queryKey: ['jobs'], queryFn: () => request<Job[]>('/jobs') })

export const useJob = (jobId: number) =>
  useQuery({ queryKey: ['jobs', jobId], queryFn: () => request<Job>(`/jobs/${jobId}`) })

export const useApplications = (jobId: number) =>
  useQuery({ queryKey: ['applications', jobId], queryFn: () => allApplications(jobId) })

export const useApplication = (id: number | undefined) =>
  useQuery({
    queryKey: ['application', id],
    queryFn: () => request<Application>(`/applications/${id}`),
    enabled: id !== undefined,
  })

export const useApplicationText = (id: number | undefined) =>
  useQuery({
    queryKey: ['application-text', id],
    queryFn: () => request<{ text: string }>(`/applications/${id}/text`),
    enabled: id !== undefined,
    staleTime: Infinity, // a document's text never changes
  })

/** After anything that rescores, every cached list and detail for the job is stale. */
function useJobMutation<TVars>(jobId: number, fn: (vars: TVars) => Promise<Job>) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: (job) => {
      client.setQueryData(['jobs', jobId], job)
      client.invalidateQueries({ queryKey: ['jobs'], exact: true })
      client.invalidateQueries({ queryKey: ['applications', jobId] })
      client.invalidateQueries({ queryKey: ['application'] })
    },
  })
}

export const useCreateJob = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: JobCreate) => request<Job>('/jobs', json('POST', body)),
    onSuccess: () => client.invalidateQueries({ queryKey: ['jobs'], exact: true }),
  })
}

export const useSaveRubric = (jobId: number) =>
  useJobMutation(jobId, (requirements: Requirement[]) => request<Job>(`/jobs/${jobId}/rubric`, json('PUT', { requirements })))

export const useApproveRubric = (jobId: number) =>
  useJobMutation(jobId, () => request<Job>(`/jobs/${jobId}/rubric:approve`, json('POST')))

export const useSaveSettings = (jobId: number) =>
  useJobMutation(jobId, (settings: JobSettings) => request<Job>(`/jobs/${jobId}/settings`, json('PUT', settings)))

export type JobDraft = components['schemas']['JobDraft']

export function extractJobDescription(file: File): Promise<JobDraft> {
  const form = new FormData()
  form.append('file', file)
  return request<JobDraft>('/jds:extract', { method: 'POST', body: form })
}

export const saveRubric = (jobId: number, requirements: Requirement[]) =>
  request<Job>(`/jobs/${jobId}/rubric`, json('PUT', { requirements }))

export function uploadResume(jobId: number, file: File): Promise<Application> {
  const form = new FormData()
  form.append('file', file)
  return request<Application>(`/jobs/${jobId}/resumes`, { method: 'POST', body: form })
}
