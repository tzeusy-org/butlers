import { Outlet } from 'react-router'
import Shell from '../components/layout/Shell'
import PageHeader from '../components/layout/PageHeader'
import EntityFinder from '../components/layout/EntityFinder'
import { GlobalActionsRegistrar } from '../components/layout/GlobalActionsRegistrar'
import { ErrorBoundary } from '../components/ErrorBoundary'
import { Toaster } from '../components/ui/sonner'
import { BreadcrumbsControlProvider } from '../components/ui/breadcrumbs-control'
import { CommandRegistryProvider } from '../lib/command-registry'
import { useKeyboardShortcuts } from '../hooks/use-keyboard-shortcuts'
import { ShortcutHints } from '../components/ui/shortcut-hints'

export default function RootLayout() {
  useKeyboardShortcuts()

  return (
    <BreadcrumbsControlProvider>
      <CommandRegistryProvider>
        <Shell header={<PageHeader />}>
          <ErrorBoundary>
            <Outlet />
          </ErrorBoundary>
        </Shell>
        {/* EntityFinder: the one command menu (bu-86c4c.7) — opened
            identically by Cmd+K, '/', and the header button. Absorbs the
            legacy CommandPalette's page/butler/session/state search. */}
        <EntityFinder />
        {/* Registers the always-available "Trigger <butler>" Actions. */}
        <GlobalActionsRegistrar />
        <ShortcutHints />
        <Toaster />
      </CommandRegistryProvider>
    </BreadcrumbsControlProvider>
  )
}
