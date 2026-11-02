import type { Verdict } from '../api/client'

/** Examiner's marks, drawn: tick for met, a struck tick for partial, a cross for not found. */
export function MarkGlyph({ verdict, size = 16 }: { verdict: Verdict; size?: number }) {
  return (
    <svg className="mark-glyph" width={size} height={size} viewBox="0 0 16 16" aria-hidden="true">
      {verdict === 'met' && <path d="M2.5 8.5l3.5 3.5 7.5-8.5" />}
      {verdict === 'partial' && <path d="M2.5 8.5l3.5 3.5 7.5-8.5M3 5.5h7" />}
      {verdict === 'not_found' && <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" />}
    </svg>
  )
}
