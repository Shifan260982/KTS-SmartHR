import type { ApplicationSummary, Job, Verdict } from '../api/client'

export type BandFilter = 'all' | 'strong_fit' | 'possible_fit' | 'low_match' | 'must_have_missing' | 'attention'

export const BAND_LABEL: Record<Exclude<BandFilter, 'all'>, string> = {
  strong_fit: 'Strong fit',
  possible_fit: 'Possible fit',
  low_match: 'Low match',
  must_have_missing: 'Must-have missing',
  attention: 'Needs attention',
}

export const VERDICT_LABEL: Record<Verdict, string> = { met: 'Met', partial: 'Partial', not_found: 'Not found' }

export function bandOf(app: ApplicationSummary): Exclude<BandFilter, 'all'> {
  return app.status === 'NEEDS_ATTENTION' ? 'attention' : (app.band as Exclude<BandFilter, 'all' | 'attention'>)
}

/** "−6 to Strong fit" style distance, or which must-haves are missing. */
export function nextBand(app: ApplicationSummary, job: Job): string {
  if (app.status === 'NEEDS_ATTENTION' || app.fit_score == null) return 'Not scored'
  if (app.band === 'must_have_missing') {
    const must = new Set(job.rubric.filter((r) => r.type === 'must').map((r) => r.id))
    const missing = (app.marks ?? []).filter((m) => must.has(m.requirement_id) && m.verdict !== 'met').map((m) => m.requirement_id)
    return `Missing ${missing.join(', ')}`
  }
  const { strong_fit, floor } = job.settings
  if (app.fit_score >= strong_fit) return 'Top band'
  const [target, label] = app.fit_score >= floor ? [strong_fit, 'Strong fit'] : [floor, 'Possible fit']
  return `−${Math.ceil(target - app.fit_score)} to ${label}`
}

export const displayName = (filename: string) => filename.replace(/\.(pdf|docx|txt)$/i, '')
