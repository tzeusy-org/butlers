// ---------------------------------------------------------------------------
// NavIcon — hairline 16px SVG glyphs for the sidebar rail.
// Paths ported verbatim from pr/overview/sidebar.jsx ICONS map so the rail
// matches the design intent: custom minimal strokes, distinct from lucide.
// ---------------------------------------------------------------------------

export type NavIconName =
  | 'overview'
  | 'butlers'
  | 'qa'
  | 'ingestion'
  | 'approvals'
  | 'memory'
  | 'entities'
  | 'secrets'
  | 'settings'
  | 'timeline'
  | 'notifications'
  | 'issues'
  | 'sessions'
  | 'audit'
  | 'system'

const PATHS: Record<NavIconName, React.ReactNode> = {
  overview: (
    <>
      <rect x="2" y="2" width="5" height="5" />
      <rect x="9" y="2" width="5" height="5" />
      <rect x="2" y="9" width="5" height="5" />
      <rect x="9" y="9" width="5" height="5" />
    </>
  ),
  butlers: (
    <>
      <circle cx="8" cy="6" r="2.5" />
      <path d="M3 14c0-2.5 2.2-4 5-4s5 1.5 5 4" />
    </>
  ),
  qa: (
    <>
      <circle cx="7" cy="7" r="4.5" />
      <path d="M10.5 10.5L14 14" />
    </>
  ),
  ingestion: (
    <>
      <path d="M8 2v8M4.5 6.5L8 10l3.5-3.5" />
      <path d="M2 12h12" />
    </>
  ),
  approvals: (
    <>
      <path d="M3 4.5L6.5 8 13 2.5" />
      <path d="M3 9.5L6.5 13 13 7.5" />
    </>
  ),
  memory: (
    <>
      <circle cx="8" cy="8" r="5" />
      <circle cx="8" cy="8" r="2" />
    </>
  ),
  entities: (
    <>
      <rect x="2.5" y="2.5" width="4" height="4" />
      <rect x="9.5" y="2.5" width="4" height="4" />
      <rect x="2.5" y="9.5" width="4" height="4" />
      <rect x="9.5" y="9.5" width="4" height="4" />
    </>
  ),
  secrets: (
    <>
      <rect x="3.5" y="7" width="9" height="7" rx="1" />
      <path d="M5 7V5a3 3 0 016 0v2" />
    </>
  ),
  settings: (
    <>
      <circle cx="8" cy="8" r="2" />
      <path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.5 3.5l1.4 1.4M11.1 11.1l1.4 1.4M3.5 12.5l1.4-1.4M11.1 4.9l1.4-1.4" />
    </>
  ),
  timeline: (
    <>
      <path d="M2 8h12" />
      <circle cx="4" cy="8" r="1.5" />
      <circle cx="11" cy="8" r="1.5" />
    </>
  ),
  notifications: (
    <>
      <path d="M4 11V7a4 4 0 018 0v4l1 2H3z" />
      <path d="M6.5 14a1.5 1.5 0 003 0" />
    </>
  ),
  issues: (
    <>
      <path d="M8 2L14 13H2z" />
      <path d="M8 7v3M8 11.5v.5" />
    </>
  ),
  sessions: (
    <>
      <rect x="2" y="3" width="12" height="9" rx="1" />
      <path d="M2 6h12" />
    </>
  ),
  audit: (
    <>
      <path d="M3.5 2h6L13 5.5V14H3.5z" />
      <path d="M9 2v4h4M5.5 9h5M5.5 11.5h5" />
    </>
  ),
  system: (
    <>
      <rect x="2.5" y="2.5" width="11" height="11" rx="1.5" />
      <circle cx="8" cy="8" r="2" />
    </>
  ),
}

export interface NavIconProps {
  name: NavIconName
  size?: number
  className?: string
}

export function NavIcon({ name, size = 16, className }: NavIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.25"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
      style={{ display: 'block' }}
    >
      {PATHS[name]}
    </svg>
  )
}
