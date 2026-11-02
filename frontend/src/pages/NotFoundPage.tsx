/**
 * Not-found page for unmatched dashboard routes (bu-h40h2b.17).
 *
 * Rendered by the router's catch-all `*` route inside the root layout, so the shell, header and
 * sidebar stay. Shows the requested path and a door back to the dashboard home.
 */

import { Link, useLocation } from 'react-router'

import { Page } from '@/components/ui/page.tsx'

export default function NotFoundPage() {
  const { pathname } = useLocation()
  return (
    <Page title="Page not found" archetype="detail">
      <div className="space-y-3" data-testid="not-found-page">
        <p className="text-sm text-muted-foreground">
          Nothing is registered at <code className="font-mono">{pathname}</code>.
        </p>
        <p className="text-sm text-muted-foreground">
          Press <kbd className="font-mono">/</kbd> to search, or go back to the dashboard.
        </p>
        <Link to="/" className="text-sm underline">
          Back to the dashboard
        </Link>
      </div>
    </Page>
  )
}
