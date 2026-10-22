/**
 * Route-level errorElement (bu-h40h2b.17): a throwing route render or loader shows the shell's
 * existing "Something went wrong" fallback instead of React Router's default error screen.
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
