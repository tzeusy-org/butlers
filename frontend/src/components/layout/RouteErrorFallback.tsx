/**
 * Root layout errorElement (bu-h40h2b.17): handles RootLayout render failures and route
 * loader/action errors with the shared fallback instead of React Router's default error screen.
 * Page render failures are caught by the in-shell ErrorBoundary.
 */

import { useRouteError } from 'react-router'

import { ErrorFallback } from '@/components/ErrorBoundary.tsx'

export function RouteErrorFallback() {
  const error = useRouteError()
  return (
    <ErrorFallback
      error={error instanceof Error ? error : null}
      onRetry={() => window.location.reload()}
    />
  )
}
