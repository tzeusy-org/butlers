import { useLocation, Link } from 'react-router'
import { Search } from 'lucide-react'
import { SiblingButlerNav } from '@/components/butler-detail/SiblingButlerNav'
import { Button } from '../ui/button'
import { useBreadcrumbsControl } from '../ui/breadcrumbs-control'
import { useDarkMode } from '../../hooks/useDarkMode'
import { dispatchOpenCommandPalette } from '../../lib/command-palette'

interface Breadcrumb {
  label: string
  path?: string
}

interface PageHeaderProps {
  breadcrumbs?: Breadcrumb[]
  hideBreadcrumbs?: boolean
}

// Known acronyms that should render fully uppercased rather than title-cased.
// Keep this list tight — only true acronyms used as URL segments.
const BREADCRUMB_ACRONYMS: Record<string, string> = {
  qa: 'QA',
  api: 'API',
  ui: 'UI',
}

function formatSegment(segment: string): string {
  const lower = segment.toLowerCase()
  if (BREADCRUMB_ACRONYMS[lower]) return BREADCRUMB_ACRONYMS[lower]
  return segment.charAt(0).toUpperCase() + segment.slice(1)
}

function buildBreadcrumbs(pathname: string): Breadcrumb[] {
  const segments = pathname.split('/').filter(Boolean)
  const crumbs: Breadcrumb[] = [{ label: 'Home', path: '/' }]

  let currentPath = ''
  for (const segment of segments) {
    currentPath += `/${segment}`
    crumbs.push({
      label: formatSegment(segment),
      path: currentPath,
    })
  }

  // Last breadcrumb has no link (current page)
  if (crumbs.length > 1) {
    delete crumbs[crumbs.length - 1].path
  }

  return crumbs
}

function getButlerDetailName(pathname: string): string | null {
  const match = pathname.match(/^\/butlers\/([^/]+)\/?$/)
  if (!match) return null
  return decodeURIComponent(match[1])
}

function titleizeSegment(value: string): string {
  return value
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

export default function PageHeader({ breadcrumbs, hideBreadcrumbs = false }: PageHeaderProps) {
  const location = useLocation()
  const { theme, setTheme, resolvedTheme } = useDarkMode()
  const { isSupplyingBreadcrumbs } = useBreadcrumbsControl()

  const crumbs = breadcrumbs ?? buildBreadcrumbs(location.pathname)
  // Suppress shell auto-builder only when the active <Page> supplies breadcrumbs
  // AND this header has no explicit breadcrumbs prop of its own. If the header
  // is given its own breadcrumbs they should always render.
  const shouldHideBreadcrumbs = hideBreadcrumbs || (isSupplyingBreadcrumbs && breadcrumbs == null)
  const activeButlerName = breadcrumbs == null
    ? getButlerDetailName(location.pathname)
    : null

  const toggleTheme = () => {
    if (theme === 'system') {
      setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')
    } else {
      setTheme(theme === 'dark' ? 'light' : 'dark')
    }
  }

  return (
    <div className="flex w-full min-w-0 items-center justify-between gap-3">
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        {activeButlerName && (
          <div className="flex min-w-0 items-center gap-4">
            <div className="flex shrink-0 items-center gap-2 font-mono text-[11px] tracking-[0.06em] text-muted-foreground">
              <Link to="/butlers" className="transition-colors hover:text-foreground">
                &larr;<span className="md:hidden"> Butlers</span>
                <span className="hidden md:inline"> /butlers</span>
              </Link>
              <span className="hidden md:inline">/</span>
              <span className="hidden md:inline text-foreground">{titleizeSegment(activeButlerName)}</span>
            </div>
            <div className="min-w-0 flex-1">
              <SiblingButlerNav activeButlerName={activeButlerName} />
            </div>
          </div>
        )}

        {/* Breadcrumbs */}
        {!activeButlerName && !shouldHideBreadcrumbs && (
          <nav className="flex items-center gap-1.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground tabular-nums">
            {crumbs.map((crumb, i) => (
              <span key={i} className="flex items-center gap-1.5">
                {i > 0 && <span aria-hidden="true">/</span>}
                {crumb.path ? (
                  <Link to={crumb.path} className="transition-colors hover:text-foreground">
                    {crumb.label}
                  </Link>
                ) : (
                  <span className="text-foreground">{crumb.label}</span>
                )}
              </span>
            ))}
          </nav>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-1">
        <Button
          variant="ghost"
          size="sm"
          onClick={dispatchOpenCommandPalette}
          aria-label="Open command palette"
          title="Cmd/Ctrl+K"
          className="h-8 w-8 p-0"
        >
          <Search className="h-4 w-4" />
        </Button>

        {/* Dark mode toggle */}
        <Button
          variant="ghost"
          size="sm"
          onClick={toggleTheme}
          aria-label="Toggle dark mode"
          className="h-8 w-8 p-0"
        >
          {resolvedTheme === 'dark' ? (
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>
          ) : (
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
          )}
        </Button>
      </div>
    </div>
  )
}
