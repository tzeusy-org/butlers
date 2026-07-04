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
import { useEventStream } from '../hooks/use-event-stream'
import { FloatingChatWidget } from '../components/chat/FloatingChatWidget'

export default function RootLayout() {
  useKeyboardShortcuts()

  // Single app-wide fleet event-stream connection (bu-86c4c.8, §JARVIS audit
  // move 5). Mounted once here — not per-page — so every route shares one
  // socket, one reconnect back-off, and one declarative cache-patch registry
  // pass, rather than each page opening its own. `status` is threaded down
  // into PageHeader so the shell's Live indicator reflects actual socket
  // health.
  const { status: eventStreamStatus } = useEventStream()

  return (
    <BreadcrumbsControlProvider>
      <CommandRegistryProvider>
        <Shell header={<PageHeader liveStatus={eventStreamStatus} />}>
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
        {/* Floating chat widget (bu-p6ey8.3) — bottom-right button on every
            route, opening a compact popover chat panel routed through the
            Switchboard butler. Also registers the "Talk to Butlers" cmdk
            command. Mounted here (not inside Shell.tsx) since Shell has no
            floating layer. */}
        <FloatingChatWidget />
      </CommandRegistryProvider>
    </BreadcrumbsControlProvider>
  )
}
