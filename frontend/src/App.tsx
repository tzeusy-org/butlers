import { QueryClientProvider } from '@tanstack/react-query'
import { ReactQueryDevtools } from '@tanstack/react-query-devtools'
import { RouterProvider } from 'react-router'
import { queryClient } from './lib/query-client'
import { router } from './router.tsx'
import { AppTimezoneProvider, DEFAULT_TZ } from '@/components/ui/timezone-context'
import { useGeneralSettings } from '@/hooks/use-general-settings'

// ---------------------------------------------------------------------------
// Inner component — reads owner timezone after QueryClientProvider is mounted
// ---------------------------------------------------------------------------

function AppWithTimezone() {
  const { data: generalSettings } = useGeneralSettings()
  const ownerTz = generalSettings?.data?.timezone ?? DEFAULT_TZ

  return (
    <AppTimezoneProvider timezone={ownerTz}>
      <RouterProvider router={router} />
      <ReactQueryDevtools initialIsOpen={false} />
    </AppTimezoneProvider>
  )
}

// ---------------------------------------------------------------------------
// App root — QueryClientProvider must wrap the hook consumer
// ---------------------------------------------------------------------------

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppWithTimezone />
    </QueryClientProvider>
  )
}

export default App
