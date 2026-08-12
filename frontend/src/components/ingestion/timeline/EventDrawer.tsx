/**
 * EventDrawer — slide-in panel for a focused ingestion event.
 *
 * Backed by the `?event=<id>` URL parameter. Opening the drawer sets that
 * param; closing it clears it. Loading the page with `?event=<id>` already
 * set opens the drawer immediately.
 *
 * Tabs:
 *   sessions   — flamegraph + per-session step blocks + session index
 *   raw        — pretty-printed raw payload (audit-gated; 403 → explicit unavailable state)
 *   replays    — replay attempt history (gated the same way)
 *
 * Right rail (visible on all tabs):
 *   - request KV block (id, received, channel, status, sender, tier, cost)
 *   - sessions index (anchor-scroll links to session blocks on the left)
 *   - footer actions: replay / copy id
 *
 * Design: hairline-divided, no card chrome, mono numerals, serif voice for
 * empty/error states, state colors as foreground/border only.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Timeline URL opens an event drawer"
 * Reference: docs/redesigns/ingestion-handoff.md §"The drawer"
 */

import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'
import { Check, Copy, Download, Loader2, RotateCw, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Time } from '@/components/ui/time'
import { categoricalHueVar } from '@/lib/visual-token-roles'
import {
  useIngestionEventLineage,
  useIngestionEventReplays,
  useIngestionEventPayload,
  useIngestionEventDetail,
} from '@/hooks/use-ingestion-events'
import { replayIngestionEvent } from '@/api/index.ts'
import { ApiError } from '@/api/index.ts'
import { Tip } from '@/components/ui/tip'
import { StatusBadge } from '../StatusBadge'
import { isReplaySafe } from '../bulkEligibility'
import type { IngestionEventSession, IngestionEventSummary, IngestionEventStatus } from '@/api/index.ts'
import { formatDurationTicks } from '@/lib/format-duration'
import { formatCostUsdPrecise } from '@/lib/format-cost'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function truncateId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) + '…' : id
}

/**
 * Explain *why* an event has no butler sessions. Not every ingested event spawns
 * an LLM session: high-frequency sensor data (e.g. home_assistant wellness
 * readings) is routed straight to a butler via a policy-bypass rule and ingested
 * deterministically — no model is invoked. Skip/filter/error events never
 * dispatch at all. This turns a bare "no sessions" into an honest reason.
 */
function emptySessionsReason(
  event: IngestionEventSummary,
  decompositionOutput: Record<string, unknown> | null,
): string {
  const policyBypass = decompositionOutput?.policy_bypass === true
  const routed = decompositionOutput?.routed
  const routedTarget = Array.isArray(routed) && typeof routed[0] === 'string' ? routed[0] : null
  const target = event.triage_target ?? routedTarget

  if (policyBypass || event.triage_decision === 'route_to') {
    return target
      ? `Routed directly to ${target} via a policy-bypass rule and ingested deterministically. No LLM session was spawned (and no model cost).`
      : 'Routed via a policy-bypass rule and ingested deterministically. No LLM session was spawned (and no model cost).'
  }
  if (event.status === 'skipped' || event.triage_decision === 'skip') {
    return 'This event matched a skip rule, so it was stored but never dispatched to a butler.'
  }
  if (event.status === 'filtered') {
    return event.filter_reason
      ? `Filtered before dispatch (${event.filter_reason}).`
      : 'Filtered before dispatch, so no butler ran.'
  }
  if (event.status === 'error') {
    return event.error_detail
      ? `Dispatch failed before a session could start (${event.error_detail}).`
      : 'Dispatch failed before a session could start.'
  }
  if (event.status === 'failed') {
    return event.error_detail
      ? `Routing failed after this event was ingested, so no butler session ran (${event.error_detail}).`
      : 'Routing failed after this event was ingested, so no butler session ran.'
  }
  return 'This event was ingested but no butler session has been recorded for it.'
}

function formatSessionSpan(startedAt: string | null, completedAt: string | null): string {
  if (!startedAt || !completedAt) return '—'
  try {
    const ms = new Date(completedAt).getTime() - new Date(startedAt).getTime()
    if (ms < 0) return '—'
    return formatDurationTicks(ms)
  } catch {
    return '—'
  }
}

function fmtNum(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  return n.toLocaleString()
}

function sessionCostLabel(session: IngestionEventSession): string {
  if (session.cost_evidence === 'no_usage') return 'no token usage'
  if (session.cost_evidence === 'unpriced') return 'cost unavailable'
  return `cost ${formatCostUsdPrecise(session.cost_usd)}`
}

// ---------------------------------------------------------------------------
// CopyButton
// ---------------------------------------------------------------------------

function CopyButton({ value, label }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current)
    }
  }, [])

  function handleCopy(e: React.MouseEvent) {
    e.stopPropagation()
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true)
      if (timerRef.current !== null) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => setCopied(false), 900)
    })
  }

  return (
    <Tip content={copied ? 'Copied' : 'Copy to clipboard'}>
      <button
        type="button"
        onClick={handleCopy}
        className="inline-flex items-center gap-1 rounded px-1 py-0.5 font-mono text-[11px] tracking-[0.01em] text-muted-foreground hover:bg-muted transition-colors"
        data-testid="copy-button"
      >
        <span className="truncate max-w-[160px]">{label ?? value}</span>
        {copied ? (
          <Check className="size-3 text-[var(--green,theme(colors.emerald.500))] shrink-0" />
        ) : (
          <Copy className="size-3 shrink-0" />
        )}
      </button>
    </Tip>
  )
}

// ---------------------------------------------------------------------------
// KV row
// ---------------------------------------------------------------------------

function KVRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex gap-2 text-[13px] leading-[1.5]">
      <span className="font-mono text-[10px] tracking-[0.14em] uppercase text-muted-foreground shrink-0 w-20 pt-0.5">
        {label}
      </span>
      <span className="text-foreground break-all">{value}</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sessions tab (flamegraph + per-session blocks)
// ---------------------------------------------------------------------------

function DrawerSessionsTab({
  requestId,
  contentRef,
  event,
  decompositionOutput,
}: {
  requestId: string
  contentRef: React.RefObject<HTMLDivElement>
  event: IngestionEventSummary
  decompositionOutput: Record<string, unknown> | null
}) {
  const { sessions } = useIngestionEventLineage(requestId, { enabled: true })
  const sessionList = sessions.data?.data ?? []

  if (sessions.isLoading) {
    return (
      <div className="space-y-3 p-4" data-testid="sessions-tab-loading">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>
    )
  }

  if (sessions.isError) {
    return (
      <p className="p-4 font-serif text-[15px] leading-[1.55] text-muted-foreground italic" data-testid="sessions-tab-error">
        Session lineage unavailable.
      </p>
    )
  }

  if (sessionList.length === 0) {
    return (
      <div className="p-4 space-y-1.5" data-testid="sessions-tab-empty">
        <p className="font-serif text-[15px] leading-[1.55] text-muted-foreground italic">
          No sessions were triggered by this event.
        </p>
        <p className="font-serif text-[13px] leading-[1.55] text-muted-foreground italic">
          {emptySessionsReason(event, decompositionOutput)}
        </p>
      </div>
    )
  }

  // Flamegraph
  const withTimes = sessionList.filter((s) => s.started_at)
  // Use completed_at if available; fall back to started_at + 1ms as a sentinel
  // for in-progress sessions. maxTime is the visible window right edge — clamp
  // in-progress spans to it so they never overflow 100% width.
  // (avoids calling Date.now() during render — ESLint rule: react-hooks/purity)
  const starts = withTimes.map((s) => new Date(s.started_at!).getTime())
  const ends = withTimes.map((s) =>
    s.completed_at
      ? new Date(s.completed_at).getTime()
      : new Date(s.started_at!).getTime() + 1,
  )
  const minTime = starts.length ? Math.min(...starts) : 0
  const maxTime = ends.length ? Math.max(...ends) : 0
  const span = maxTime - minTime || 1
  const butlers = [...new Set(sessionList.map((s) => s.butler_name))]

  return (
    <div ref={contentRef} className="p-4 space-y-6" data-testid="sessions-tab-content">
      {/* Flamegraph */}
      {withTimes.length > 0 && (
        <div className="space-y-1.5">
          <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground font-mono">
            {butlers.map((b) => (
              <span key={b} className="flex items-center gap-1">
                <span className="inline-block size-2.5 rounded-sm" style={{ backgroundColor: categoricalHueVar(b) }} />
                {b}
              </span>
            ))}
          </div>
          <div className="relative rounded-md border bg-muted/10 overflow-hidden">
            {butlers.map((butler) => {
              const laneSessions = withTimes.filter((s) => s.butler_name === butler)
              const laneColor = categoricalHueVar(butler)
              return (
                <div key={butler} className="relative h-7 border-b last:border-0">
                  {laneSessions.map((s) => {
                    const sStart = new Date(s.started_at!).getTime()
                    // Clamp in-progress spans to maxTime (the visible window right
                    // edge) so they don't overflow the flamegraph container.
                    const sEnd = s.completed_at
                      ? new Date(s.completed_at).getTime()
                      : maxTime
                    const left = ((sStart - minTime) / span) * 100
                    const width = Math.max(((sEnd - sStart) / span) * 100, 1)
                    const dur = s.completed_at ? formatSessionSpan(s.started_at, s.completed_at) : '--'
                    return (
                      <Tip key={s.id} content={`${s.butler_name}: ${dur}`}>
                        <Link
                          to={`/sessions/${s.id}?butler=${encodeURIComponent(s.butler_name)}`}
                          className="absolute top-0.5 bottom-0.5 rounded-sm opacity-80 hover:opacity-100 transition-opacity cursor-pointer"
                          style={{ left: `${left}%`, width: `${width}%`, backgroundColor: laneColor }}
                        >
                          <span className="px-1 text-[10px] font-medium text-white truncate block leading-6">
                            {dur}
                          </span>
                        </Link>
                      </Tip>
                    )
                  })}
                </div>
              )
            })}
          </div>
          <p className="text-[10px] text-muted-foreground">
            Bars are proportional to session duration, not actual token cost.
          </p>
        </div>
      )}

      {/* Per-session step blocks */}
      {sessionList.map((s) => (
        <div key={s.id} id={`session-${s.id}`} className="border-t pt-4 space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className="inline-block size-3 rounded-sm shrink-0"
              style={{ backgroundColor: categoricalHueVar(s.butler_name) }}
            />
            <span className="font-medium text-[14px]">{s.butler_name}</span>
            <span className="text-muted-foreground text-[12px]">
              {s.success === true ? '● ok' : s.success === false ? '■ error' : '○ unknown'}
            </span>
            <CopyButton value={s.id} label={truncateId(s.id)} />
            {s.model && (
              <span className="font-mono text-[11px] text-muted-foreground">{s.model}</span>
            )}
            <span className="font-mono text-[11px] text-muted-foreground">
              {formatSessionSpan(s.started_at, s.completed_at)}
            </span>
            <Link
              to={`/sessions/${s.id}?butler=${encodeURIComponent(s.butler_name)}`}
              className="ml-auto font-mono text-[11px] text-muted-foreground hover:text-foreground underline-offset-4 hover:underline transition-colors"
            >
              open →
            </Link>
          </div>
          {/*
            The API does not report per-step token/cost/duration data today
            (only session-level aggregates), so this renders one honestly
            labeled totals row rather than a step-by-step table with headers
            promising rows that don't exist. Future extension: once the
            backend records per-step accounting, restore a per-step
            breakdown here.
          */}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px]">
            <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
              session total
            </span>
            <span className="tabular-nums text-foreground">in {fmtNum(s.input_tokens)}</span>
            <span className="tabular-nums text-foreground">out {fmtNum(s.output_tokens)}</span>
            <span className="tabular-nums text-foreground">{sessionCostLabel(s)}</span>
            <span className="tabular-nums text-foreground">dur {formatSessionSpan(s.started_at, s.completed_at)}</span>
          </div>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Raw payload tab
// ---------------------------------------------------------------------------

function DrawerRawTab({
  requestId,
  enabled,
  onLoad,
}: {
  requestId: string
  enabled: boolean
  onLoad: () => void
}) {
  const { data, isLoading, isError, error } = useIngestionEventPayload(requestId, { enabled })

  // Not yet enabled for THIS event — this is the state when the tab
  // selection was restored from a remembered preference rather than an
  // explicit click during this drawer's lifetime (see handleTabChange).
  // Requires one explicit, audited action per event; never auto-fetches
  // from a preference set on a previously viewed event.
  if (!enabled) {
    return (
      <div className="p-4 space-y-3">
        <p className="font-serif text-[15px] leading-[1.55] text-muted-foreground italic">
          Raw payload not loaded.
        </p>
        <button
          type="button"
          onClick={onLoad}
          className="rounded border px-3 py-1.5 font-mono text-[11px] tracking-[0.01em] hover:bg-muted transition-colors"
          data-testid="raw-tab-load-button"
        >
          Load payload (audited)
        </button>
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="p-4 space-y-2" data-testid="raw-tab-loading">
        <Skeleton className="h-4 w-1/3" />
        <Skeleton className="h-32 w-full" />
      </div>
    )
  }

  // 403 → gated state (audit-access required)
  const is403 = isError && error instanceof ApiError && error.status === 403
  if (is403) {
    return (
      <div className="p-4 space-y-2" data-testid="raw-tab-gated">
        <p className="font-serif text-[15px] leading-[1.55] text-muted-foreground italic">
          Payload access is disabled for this session.
        </p>
        <p className="font-mono text-[11px] text-muted-foreground">
          Reads are recorded in the audit log.
        </p>
      </div>
    )
  }

  if (isError) {
    return (
      <p className="p-4 font-serif text-[15px] leading-[1.55] text-muted-foreground italic" data-testid="raw-tab-error">
        Raw payload unavailable.
      </p>
    )
  }

  const payload = data?.data
  if (!payload) {
    return (
      <p className="p-4 font-serif text-[15px] leading-[1.55] text-muted-foreground italic" data-testid="raw-tab-empty">
        No payload recorded for this event.
      </p>
    )
  }

  return (
    <div className="p-4 space-y-3" data-testid="raw-tab-content">
      <div className="flex items-center gap-3 font-mono text-[11px] text-muted-foreground">
        {payload.channel && <span>{payload.channel}</span>}
        <span>{payload.bytes.toLocaleString()} bytes</span>
        {payload.truncated && <span className="text-[var(--amber,theme(colors.amber.500))]">truncated</span>}
        <button
          type="button"
          className="ml-auto hover:text-foreground transition-colors flex items-center gap-1"
          onClick={() => {
            const blob = new Blob([payload.content], { type: 'application/json' })
            const url = URL.createObjectURL(blob)
            const a = document.createElement('a')
            a.href = url
            a.download = `payload-${requestId.slice(0, 8)}.json`
            a.click()
            URL.revokeObjectURL(url)
          }}
          title="Download raw payload"
        >
          <Download className="size-3" />
          download
        </button>
      </div>
      <pre className="border rounded font-mono text-[11px] leading-[1.6] p-3 max-h-80 overflow-auto whitespace-pre-wrap break-all bg-muted/20">
        {payload.content}
      </pre>
      {payload.truncated && (
        <p className="font-mono text-[10px] text-muted-foreground">
          truncated · full payload larger than displayed
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Replay history tab
// ---------------------------------------------------------------------------

function DrawerReplaysTab({ requestId, enabled }: { requestId: string; enabled: boolean }) {
  const { data, isLoading, isError, error } = useIngestionEventReplays(requestId)

  if (!enabled) {
    return (
      <p className="p-4 font-serif text-[15px] leading-[1.55] text-muted-foreground italic">
        Replay history not loaded.
      </p>
    )
  }

  if (isLoading) {
    return (
      <div className="p-4 space-y-2" data-testid="replays-tab-loading">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-5 w-full" />
        ))}
      </div>
    )
  }

  // 403 gating (future: replay history may also require elevated access)
  const is403 = isError && error instanceof ApiError && error.status === 403
  if (is403) {
    return (
      <div className="p-4" data-testid="replays-tab-gated">
        <p className="font-serif text-[15px] leading-[1.55] text-muted-foreground italic">
          Replay history access requires elevated permission.
        </p>
      </div>
    )
  }

  if (isError) {
    return (
      <p className="p-4 font-serif text-[15px] leading-[1.55] text-muted-foreground italic" data-testid="replays-tab-error">
        Replay history unavailable.
      </p>
    )
  }

  const entries = data?.data ?? []

  if (entries.length === 0) {
    return (
      <p className="p-4 font-serif text-[15px] leading-[1.55] text-muted-foreground italic" data-testid="replays-tab-empty">
        No replay attempts recorded.
      </p>
    )
  }

  return (
    <div className="p-4 space-y-2" data-testid="replays-tab-content">
      <div className="grid grid-cols-[auto_1fr_auto_auto] gap-x-3 text-[11px] font-mono">
        <div className="text-muted-foreground tracking-[0.14em] uppercase text-[10px] pb-1">at</div>
        <div className="text-muted-foreground tracking-[0.14em] uppercase text-[10px] pb-1">by</div>
        <div className="text-muted-foreground tracking-[0.14em] uppercase text-[10px] pb-1 text-right">result</div>
        <div className="text-muted-foreground tracking-[0.14em] uppercase text-[10px] pb-1 text-right">cost</div>
        {entries.map((e, i) => (
          <div key={i} className="contents">
            <div className="tabular-nums text-muted-foreground">
              <Time value={e.ts} mode="absolute" precision="second" />
            </div>
            <div className="truncate">{e.actor}</div>
            <div className="text-right">{e.result ?? '—'}</div>
            <div className="text-right tabular-nums">{formatCostUsdPrecise(e.cost)}</div>
          </div>
        ))}
      </div>
      {/* Grounded in code (bu-lkzsf.3), replacing a fabricated "3 attempts with exponential
          backoff" claim: dispatch is single-attempt (src/butlers/modules/pipeline.py:1982,
          2603, 2950 hardcode "attempt": 1, no retry loop). Recovery is the manual replay
          request above (src/butlers/core/ingestion_events.py `ingestion_event_replay_request`). */}
      <p className="font-serif text-[13px] leading-[1.55] text-muted-foreground italic pt-2">
        No automatic retries. Each dispatch is a single attempt. A failed event stays failed
        until replay is requested from this drawer.
      </p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// EventDrawer
// ---------------------------------------------------------------------------

type DrawerTab = 'sessions' | 'raw' | 'replays'

export interface EventDrawerProps {
  event: IngestionEventSummary
  onClose: () => void
  onOptimisticUpdate: (id: string, newStatus: IngestionEventStatus) => void
}

/**
 * Slide-in drawer panel for a focused ingestion event.
 *
 * Renders inline below the ledger (not a floating overlay) to preserve the
 * ledger rhythm. Backed by `?event=<id>` URL state — closing clears the param.
 */
export function EventDrawer({ event, onClose, onOptimisticUpdate }: EventDrawerProps) {
  const contentRef = useRef<HTMLDivElement>(null!)
  // bu-86c4c.16: real disclosure choreography (JARVIS audit move 11) — focus
  // moves into the drawer on open (this heading is the drawer's landing
  // point), Escape closes and returns focus to the triggering row, and a
  // polite live region announces the drawer opened once session data
  // resolves ("event detail open — N sessions").
  const headingRef = useRef<HTMLHeadingElement>(null)

  useEffect(() => {
    headingRef.current?.focus()
    // Only on mount — this component remounts fresh whenever the drawer
    // switches to a different event (TimelineTab renders it conditionally
    // per-row), so a mount-only effect is exactly "focus moves in on open".
  }, [])

  function handleDrawerKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
    }
  }

  // Preserve tab selection across row switches
  const [activeTab, setActiveTab] = useState<DrawerTab>(() => {
    try {
      const saved = sessionStorage.getItem('ingestion-drawer-tab')
      if (saved === 'sessions' || saved === 'raw' || saved === 'replays') return saved
    } catch {
      // sessionStorage unavailable
    }
    return 'sessions'
  })

  // raw tab is only fetched when the user opens it
  const [rawEnabled, setRawEnabled] = useState(false)
  const [isReplaying, setIsReplaying] = useState(false)

  // Track the session ID that should be scrolled to once the sessions tab is
  // active and its content has rendered. Replaces the fragile setTimeout approach.
  const [pendingScrollId, setPendingScrollId] = useState<string | null>(null)

  // Explicitly clicking the raw tab is a deliberate, in-the-moment request
  // for THIS event's payload, so it's safe to auto-fetch (the fetch itself
  // IS the audited read: writes ingestion.event.payload_read to
  // public.audit_log). A tab selection merely *restored* from a remembered
  // preference (sessionStorage, set by a previously viewed event) is not
  // such a request — auto-fetching there would silently audit-log reads
  // for events the user never chose to inspect. See DrawerRawTab's explicit
  // "Load payload (audited)" affordance for that remembered-tab case.
  function handleTabChange(tab: DrawerTab) {
    setActiveTab(tab)
    if (tab === 'raw') setRawEnabled(true)
    try {
      sessionStorage.setItem('ingestion-drawer-tab', tab)
    } catch {
      // sessionStorage unavailable
    }
  }

  const { data: detailData } = useIngestionEventDetail(event.id, { enabled: true })
  const detail = detailData?.data ?? null

  const { sessions } = useIngestionEventLineage(event.id, { enabled: true })
  const sessionList = sessions.data?.data ?? []
  // Stable primitive: true once session data has loaded (avoids a new [] reference on each render
  // during the loading state, which would cause the scroll effect to fire on every render).
  const hasSessions = !!sessions.data

  // Scroll to the pending session once the sessions tab is rendered and the
  // target element is in the DOM. Clears the pending ID after scrolling.
  useEffect(() => {
    if (!pendingScrollId || activeTab !== 'sessions') return
    const el = contentRef.current?.querySelector(`#session-${CSS.escape(pendingScrollId)}`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
      setPendingScrollId(null)
    }
  }, [pendingScrollId, activeTab, hasSessions])

  async function handleReplay() {
    setIsReplaying(true)
    try {
      await replayIngestionEvent(event.id)
      onOptimisticUpdate(event.id, 'replay_pending')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Replay request failed')
    } finally {
      setIsReplaying(false)
    }
  }

  const TABS: { id: DrawerTab; label: string }[] = [
    { id: 'sessions', label: 'sessions' },
    { id: 'raw', label: 'raw payload' },
    { id: 'replays', label: 'replay history' },
  ]

  const replayableStatus =
    event.status === 'filtered' ||
    event.status === 'error' ||
    event.status === 'failed' ||
    event.status === 'replay_failed'
  const canReplay = replayableStatus && isReplaySafe(event)
  const replayBlockedReason =
    replayableStatus && !isReplaySafe(event)
      ? (event.replay_block_reason ?? 'Replay safety has not been confirmed for this event')
      : null

  const announceText = hasSessions
    ? `Event detail open: ${sessionList.length} ${sessionList.length === 1 ? 'session' : 'sessions'}`
    : 'Event detail open'

  return (
    // Keep the focused sr-only heading inside the app shell, not the document.
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- role="complementary" is a landmark, not a widget, but Escape-to-close on the panel root is the standard dialog/disclosure keyboard pattern (matches Radix Dialog's own internal behavior) — not a click/hover "interaction" the rule is guarding against.
    <div
      className="relative border-t border-border bg-background"
      data-testid="event-drawer"
      id={`event-drawer-${event.id}`}
      role="complementary"
      aria-label="Event detail drawer"
      onKeyDown={handleDrawerKeyDown}
    >
      {/* Focus lands here on open (tabIndex=-1: programmatically focusable,
          not a Tab stop); visually hidden since StatusBadge + timestamp
          already carry the same information for sighted users. */}
      <h2 ref={headingRef} tabIndex={-1} className="sr-only focus:outline-none">
        Event detail: {event.source_channel ?? 'unknown channel'}, {event.status}
      </h2>
      {/* Polite live-region announcement — screen-reader users hear the
          drawer arrived without needing to discover the heading above. */}
      <span role="status" aria-live="polite" className="sr-only">
        {announceText}
      </span>

      {/* Drawer header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
        <StatusBadge status={event.status} filterReason={event.filter_reason} errorDetail={event.error_detail} />
        <span className="font-mono text-[11px] text-muted-foreground">
          {event.source_channel ?? '—'}
        </span>
        <span className="font-mono text-[11px] text-muted-foreground">
          {event.received_at ? <Time value={event.received_at} mode="absolute" precision="second" /> : '—'}
        </span>
        <button
          type="button"
          onClick={onClose}
          className="ml-auto rounded p-1 hover:bg-muted transition-colors"
          aria-label="Close drawer"
          data-testid="drawer-close-button"
        >
          <X className="size-4" />
        </button>
      </div>

      {/* Two-column body */}
      <div className="flex gap-0 min-h-0">
        {/* Left: tabs + content */}
        <div className="flex-1 min-w-0 flex flex-col">
          {/* Tab bar */}
          <div className="flex border-b border-border">
            {TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => handleTabChange(tab.id)}
                className={[
                  'px-4 py-2 font-mono text-[11px] tracking-[0.01em] border-b-2 transition-colors',
                  activeTab === tab.id
                    ? 'border-foreground text-foreground'
                    : 'border-transparent text-muted-foreground hover:text-foreground',
                ].join(' ')}
                data-testid={`drawer-tab-${tab.id}`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {/* Tab content — no independent scroll region; the page's main
              scroll area is the single scroll context for the whole drawer
              (avoids nested scrollbars alongside the right rail). */}
          <div>
            {activeTab === 'sessions' && (
              <DrawerSessionsTab
                requestId={event.id}
                contentRef={contentRef}
                event={event}
                decompositionOutput={detail?.decomposition_output ?? null}
              />
            )}
            {activeTab === 'raw' && (
              <DrawerRawTab
                requestId={event.id}
                enabled={rawEnabled}
                onLoad={() => setRawEnabled(true)}
              />
            )}
            {activeTab === 'replays' && (
              <DrawerReplaysTab requestId={event.id} enabled={true} />
            )}
          </div>
        </div>

        {/* Right rail: request KV + session index */}
        <div className="w-52 shrink-0 border-l border-border p-4 space-y-4">
          {/* Request KV block */}
          <div className="space-y-1.5">
            <p className="font-mono text-[10px] tracking-[0.14em] uppercase text-muted-foreground mb-2">
              request
            </p>
            <KVRow label="id" value={<CopyButton value={event.id} label={truncateId(event.id)} />} />
            <KVRow
              label="received"
              value={event.received_at ? <Time value={event.received_at} mode="absolute" precision="second" /> : '—'}
            />
            <KVRow label="channel" value={event.source_channel ?? '—'} />
            <KVRow label="tier" value={event.policy_tier ?? event.ingestion_tier ?? '—'} />
            <KVRow label="sender" value={event.source_sender_identity ?? '—'} />
            {event.filter_reason && (
              <KVRow label="filtered" value={<span className="text-[var(--amber,theme(colors.amber.600))]">{event.filter_reason}</span>} />
            )}
            {event.error_detail && (
              <KVRow label="error" value={<span className="text-[var(--red,theme(colors.red.600))]">{event.error_detail}</span>} />
            )}
            {detail?.lifecycle_state && (
              <KVRow label="lifecycle" value={detail.lifecycle_state} />
            )}
          </div>

          {/* Session index */}
          {sessionList.length > 0 && (
            <div className="space-y-1" data-testid="drawer-session-index">
              <p className="font-mono text-[10px] tracking-[0.14em] uppercase text-muted-foreground mb-1.5">
                sessions ({sessionList.length})
              </p>
              <nav className="space-y-1">
                {sessionList.map((s, i) => (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => {
                      if (activeTab !== 'sessions') handleTabChange('sessions')
                      setPendingScrollId(s.id)
                    }}
                    className="flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                    data-testid={`drawer-session-index-item-${s.id}`}
                  >
                    <span
                      className="inline-block size-2 rounded-sm shrink-0"
                      style={{ backgroundColor: categoricalHueVar(s.butler_name) }}
                    />
                    <span className="truncate">#{i + 1} {s.butler_name}</span>
                  </button>
                ))}
              </nav>
            </div>
          )}

          {/* Footer actions */}
          <div className="flex flex-col gap-1.5 pt-2 border-t border-border">
            {replayBlockedReason && (
              <p
                className="font-serif text-[12px] leading-[1.45] text-muted-foreground"
                data-testid="drawer-replay-unavailable"
              >
                Replay unavailable: {replayBlockedReason}
              </p>
            )}
            {canReplay && (
              <Button
                variant="outline"
                size="sm"
                onClick={handleReplay}
                disabled={isReplaying}
                className="w-full font-mono text-[11px]"
                data-testid="drawer-replay-button"
              >
                {isReplaying ? (
                  <Loader2 className="size-3 animate-spin mr-1" />
                ) : (
                  <RotateCw className="size-3 mr-1" />
                )}
                replay event
              </Button>
            )}
            <CopyButton value={event.id} label="copy id" />
          </div>
        </div>
      </div>
    </div>
  )
}
