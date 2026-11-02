import type { ApplicationSummary, Verdict } from '../api/client'

export type Band = 'strong_fit' | 'possible_fit' | 'low_match' | 'must_have_missing' | 'attention'

export const BAND_LABEL: Record<Band, string> = {
  strong_fit: 'Strong fit',
  possible_fit: 'Possible fit',
  low_match: 'Low match',
  must_have_missing: 'Must-have missing',
  attention: 'Needs attention',
}

export const VERDICT_LABEL: Record<Verdict, string> = { met: 'Met', partial: 'Partial', not_found: 'Not found' }

export function bandOf(app: ApplicationSummary): Band {
  return app.status === 'NEEDS_ATTENTION' ? 'attention' : (app.band as Band)
}

export const displayName = (filename: string) => filename.replace(/\.(pdf|docx|txt)$/i, '')
