import { useEffect, useRef } from 'react'
import { Outlet } from 'react-router'
import Shell from '../components/layout/Shell'
import PageHeader from '../components/layout/PageHeader'
import EntityFinder from '../components/layout/EntityFinder'
import { GlobalActionsRegistrar } from '../components/layout/GlobalActionsRegistrar'
import { ErrorBoundary } from '../components/ErrorBoundary'
import { Toaster } from '../components/ui/sonner'
import { BreadcrumbsControlProvider } from '../components/ui/breadcrumbs-control'
import { CommandRegistryProvider } from '../lib/command-registry'
import { ShortcutRegistryProvider } from '../hooks/use-register-shortcut'
import { PageContextProvider } from '../lib/page-context'
import { useKeyboardShortcuts } from '../hooks/use-keyboard-shortcuts'
import { ShortcutHints } from '../components/ui/shortcut-hints'
import { type EventBusHealth } from '../hooks/use-event-stream'
import { EventBusProvider, useEventBus } from '../lib/event-bus'
import { FloatingChatWidget } from '../components/chat/FloatingChatWidget'
import { announce, useShellAnnouncement } from '../lib/shell-announcer'

// Same connected/reconnecting/down grouping LiveIndicator renders, so the
// shell's sr-only announcement always says the same thing sighted users see.
const STREAM_EDGE_LABEL: Record<'connected' | 'reconnecting' | 'down', string> = {
  connected: 'Fleet event stream connected',
  reconnecting: 'Fleet event stream reconnecting',
  down: 'Fleet event stream offline',
}

function toStreamEdge(health: EventBusHealth): 'connected' | 'reconnecting' | 'down' {
  if (health === 'healthy') return 'connected'
  if (health === 'late') return 'reconnecting'
  return 'down'
}

/**
 * Shell-level sr-only aria-live region (bu-qvnce.10). Mounted once so every
 * route shares one announcer instead of each page rolling its own. Fed by:
 *   - useEventStream status edges (below)
 *   - the Page primitive's document.title effect (components/ui/page.tsx)
 *   - the ingestion ledger's NewEventsPill live-tail count
 */
function ShellAnnouncerRegion() {
  const message = useShellAnnouncement()
  return (
    <span role="status" aria-live="polite" className="sr-only" data-testid="shell-announcer">
      {message}
    </span>
  )
}

export default function RootLayout() {
  // EventBusProvider (bu-qvnce.14 slice 1) owns the single app-wide fleet
  // event-stream connection (bu-86c4c.8, §JARVIS audit move 5) and re-exposes
  // it as a subscribe(type, cb) API via useBusEvent -- so a page that needs
  // the RAW events (not just the cache invalidation every subscriber gets
  // for free) no longer has to open its own second WebSocket to get them.
  return (
    <EventBusProvider>
      <RootLayoutInner />
    </EventBusProvider>
  )
}

function RootLayoutInner() {
  useKeyboardShortcuts()

  // `status` is threaded down into PageHeader so the shell's Live indicator
  // reflects actual socket health.
  const { health: eventBusHealth } = useEventBus()

  // Announce stream-state edges only after the first valid envelope has made
  // the stream healthy. Before then, down -> late is the ordinary cold-start
  // handshake, not a reconnecting fleet problem to announce.
  const prevEdgeRef = useRef<'connected' | 'reconnecting' | 'down' | null>(null)
  const hasEstablishedStreamRef = useRef(false)
  useEffect(() => {
    const edge = toStreamEdge(eventBusHealth)
    if (
      hasEstablishedStreamRef.current &&
      prevEdgeRef.current !== null &&
      prevEdgeRef.current !== edge
    ) {
      announce(STREAM_EDGE_LABEL[edge])
    }
    if (edge === 'connected') hasEstablishedStreamRef.current = true
    prevEdgeRef.current = edge
  }, [eventBusHealth])

  return (
    <BreadcrumbsControlProvider>
      <CommandRegistryProvider>
        {/* ShortcutRegistryProvider (bu-qvnce.11): backs every page-scoped
            useRegisterShortcut() call app-wide, so the '?' help sheet's "On
            this page" section always reflects whichever page is currently
            routed underneath — same one-registry-many-scopes shape as
            CommandRegistryProvider above. */}
        <ShortcutRegistryProvider>
          {/* PageContextProvider (bu-p6ey8.4, typed by bu-0ynlk.4): wraps both
              the routed page content (which may enrich via
              usePageSubject().set(...)) and the chat surfaces (which snapshot
              route/query/visible_resource at send time via
              usePageContextCapture()). */}
          <PageContextProvider>
            <a
              href="#main-content"
              className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:flex focus:min-h-11 focus:items-center focus:border focus:border-border-strong focus:bg-background focus:px-3 focus:text-sm focus:text-foreground"
            >
              Skip to main content
            </a>
            <Shell header={<PageHeader liveStatus={eventBusHealth} />}>
              <ErrorBoundary>
                <Outlet />
              </ErrorBoundary>
            </Shell>
            {/* EntityFinder: the one command menu (bu-86c4c.7) — opened
                identically by Cmd+K, '/', and the header button. Absorbs the
                legacy CommandPalette's page/butler/session/state search. */}
            <EntityFinder />
            {/* Registers the always-available "Run <butler>" actions. */}
            <GlobalActionsRegistrar />
            <ShortcutHints />
            <Toaster />
            {/* Floating chat widget (bu-p6ey8.3) — bottom-right button on every
                route, opening a compact popover chat panel routed through the
                Switchboard butler. Also registers the "Talk to Butlers" cmdk
                command. Mounted here (not inside Shell.tsx) since Shell has no
                floating layer. */}
            <FloatingChatWidget />
            {/* Shell-level sr-only announcer (bu-qvnce.10) — one aria-live
                region for stream-state edges, page-title changes, and the
                ingestion ledger's new-event counts. */}
            <ShellAnnouncerRegion />
          </PageContextProvider>
        </ShortcutRegistryProvider>
      </CommandRegistryProvider>
    </BreadcrumbsControlProvider>
  )
}
