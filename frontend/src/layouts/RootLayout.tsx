import { Outlet } from 'react-router'
import Shell from '../components/layout/Shell'
import PageHeader from '../components/layout/PageHeader'
import CommandPalette from '../components/layout/CommandPalette'
import { ErrorBoundary } from '../components/ErrorBoundary'
import { Toaster } from '../components/ui/sonner'
import { BreadcrumbsControlProvider } from '../components/ui/breadcrumbs-control'
import { useKeyboardShortcuts } from '../hooks/use-keyboard-shortcuts'
import { ShortcutHints } from '../components/ui/shortcut-hints'

export default function RootLayout() {
  useKeyboardShortcuts()

  return (
    <BreadcrumbsControlProvider>
      <Shell header={<PageHeader />}>
        <ErrorBoundary>
          <Outlet />
        </ErrorBoundary>
      </Shell>
      <CommandPalette />
      <ShortcutHints />
      <Toaster />
    </BreadcrumbsControlProvider>
  )
}
