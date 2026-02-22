import { useLocation, Link } from 'react-router'
import { Search } from 'lucide-react'
import { Button } from '../ui/button'
import { useDarkMode } from '../../hooks/useDarkMode'
import { dispatchOpenCommandPalette } from '../../lib/command-palette'

interface Breadcrumb {
  label: string
  path?: string
}

interface PageHeaderProps {
  title?: string
  breadcrumbs?: Breadcrumb[]
}

function buildBreadcrumbs(pathname: string): Breadcrumb[] {
  const segments = pathname.split('/').filter(Boolean)
  const crumbs: Breadcrumb[] = [{ label: 'Home', path: '/' }]

  let currentPath = ''
  for (const segment of segments) {
    currentPath += `/${segment}`
    crumbs.push({
      label: segment.charAt(0).toUpperCase() + segment.slice(1),
      path: currentPath,
    })
  }

  // Last breadcrumb has no link (current page)
  if (crumbs.length > 1) {
    delete crumbs[crumbs.length - 1].path
  }

  return crumbs
}

export default function PageHeader({ title, breadcrumbs }: PageHeaderProps) {
  const location = useLocation()
  const { theme, setTheme, resolvedTheme } = useDarkMode()

  const crumbs = breadcrumbs ?? buildBreadcrumbs(location.pathname)

  const toggleTheme = () => {
    if (theme === 'system') {
      setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')
    } else {
      setTheme(theme === 'dark' ? 'light' : 'dark')
    }
  }

  return (
    <div className="flex items-center justify-between w-full">
      <div className="flex flex-col gap-0.5">
        {/* Breadcrumbs */}
        <nav className="flex items-center gap-1.5 text-xs text-muted-foreground">
          {crumbs.map((crumb, i) => (
            <span key={i} className="flex items-center gap-1.5">
              {i > 0 && <span>/</span>}
              {crumb.path ? (
                <Link to={crumb.path} className="hover:text-foreground transition-colors">
                  {crumb.label}
                </Link>
              ) : (
                <span className="text-foreground">{crumb.label}</span>
              )}
            </span>
          ))}
        </nav>

        {/* Title */}
        {title && <h1 className="text-lg font-semibold">{title}</h1>}
      </div>

      <div className="flex items-center gap-1">
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
