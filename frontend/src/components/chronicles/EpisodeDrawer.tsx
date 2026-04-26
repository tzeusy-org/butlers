// ---------------------------------------------------------------------------
// EpisodeDrawer — click-to-drilldown side drawer for Chronicler episodes
//
// Opens when the user clicks an episode bar on the Gantt swimlane.
// Fetches and displays:
//   - Full episode detail (GET /api/chronicler/episodes/{id})
//   - Linked point events (GET /api/chronicler/episodes/{id}/events)
//   - Correction history (GET /api/chronicler/episodes/{id}/corrections)
//
// The "Explain this episode" button is the SINGLE Tier-2 LLM path exposed
// on the chronicles page per RFC 0014 §D5. It:
//   - Triggers only on explicit user click (never auto)
//   - Is disabled while a rate-limit window is active (backend enforces 24h)
//   - Shows a countdown when rate-limited (retry_after_seconds from 429 body)
//
// Constraints:
//   - Does NOT auto-trigger on hover, scroll, or scrub
//   - Mounts/unmounts cleanly via Sheet (Radix Dialog primitive)
//   - Sensitive episodes: title is masked in the drawer header
// ---------------------------------------------------------------------------

import { useState } from "react"
import { Loader2, Sparkles } from "lucide-react"

import type { ChroniclerEpisode } from "@/api/types"
import { ApiError } from "@/api/client"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet"
import {
  useChroniclerEpisode,
  useChroniclerEpisodeCorrections,
  useChroniclerEpisodeEvents,
  useChroniclerExplain,
} from "@/hooks/use-chronicles"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—"
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  })
}

function formatDuration(startIso: string, endIso: string | null | undefined): string {
  if (!endIso) return "ongoing"
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime()
  if (ms < 0) return "—"
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`
  if (ms < 3_600_000) return `${Math.round(ms / 60_000)}m`
  const h = Math.floor(ms / 3_600_000)
  const m = Math.round((ms % 3_600_000) / 60_000)
  return m === 0 ? `${h}h` : `${h}h ${m}m`
}

function privacyBadgeVariant(
  privacy: string,
): "default" | "secondary" | "destructive" | "outline" {
  if (privacy === "restricted") return "destructive"
  if (privacy === "sensitive") return "secondary"
  return "outline"
}

// Extract date string (YYYY-MM-DD) for the day-close refresh endpoint.
function episodeDateStr(episode: ChroniclerEpisode): string {
  return episode.canonical_start_at.slice(0, 10)
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function FieldRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <span className="text-sm">{value}</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Explain button — Tier-2 LLM path (RFC 0014 §D5)
// ---------------------------------------------------------------------------

interface ExplainButtonProps {
  episode: ChroniclerEpisode
}

function ExplainButton({ episode }: ExplainButtonProps) {
  // retryAfterSeconds: populated when the backend returns 429 with
  // details.retry_after_seconds. Surfaces feedback without a live countdown.
  const [retryAfterSeconds, setRetryAfterSeconds] = useState<number | null>(null)

  const explain = useChroniclerExplain()

  const isRateLimited = retryAfterSeconds !== null || explain.error instanceof ApiError && (explain.error as ApiError).status === 429
  const isLoading = explain.isPending

  function handleClick() {
    if (isRateLimited || isLoading) return
    const date = episodeDateStr(episode)
    explain.mutate(
      { date },
      {
        onError: (err) => {
          if (err instanceof ApiError && err.status === 429) {
            // Extract retry_after_seconds from the error details if available.
            // The backend includes it in the ErrorDetail.details field.
            const details = (err as unknown as { details?: { retry_after_seconds?: number } })
              .details
            setRetryAfterSeconds(details?.retry_after_seconds ?? 1)
          }
        },
        onSuccess: () => {
          // Clear any stale rate-limit state on successful refresh.
          setRetryAfterSeconds(null)
        },
      },
    )
  }

  return (
    <div className="space-y-1">
      <Button
        variant="outline"
        size="sm"
        disabled={isRateLimited || isLoading}
        onClick={handleClick}
        data-testid="explain-button"
      >
        {isLoading ? (
          <>
            <Loader2 className="size-3.5 animate-spin" />
            Explaining…
          </>
        ) : (
          <>
            <Sparkles className="size-3.5" />
            Explain this episode
          </>
        )}
      </Button>
      {isRateLimited && retryAfterSeconds !== null && (
        <p className="text-xs text-muted-foreground" data-testid="rate-limit-notice">
          Rate limit active — retry in ~{Math.ceil(retryAfterSeconds / 3600)}h
        </p>
      )}
      {explain.isSuccess && (
        <p className="text-xs text-emerald-600" data-testid="explain-success">
          Day summary refreshed.
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Drawer content — exported for direct unit-testing (Sheet portals are opaque
// to renderToStaticMarkup; testing the content independently keeps coverage
// aligned with the project's renderToStaticMarkup convention).
// ---------------------------------------------------------------------------

export interface EpisodeDrawerContentProps {
  episodeId: string
}

export function EpisodeDrawerContent({ episodeId }: EpisodeDrawerContentProps) {
  const episode = useChroniclerEpisode(episodeId)
  const events = useChroniclerEpisodeEvents(episodeId)
  const corrections = useChroniclerEpisodeCorrections(episodeId)

  const ep = episode.data
  const isSensitive = ep?.canonical_privacy === "sensitive"

  // Loading state while the episode is first fetched.
  if (episode.isLoading) {
    return (
      <div className="space-y-4 px-4" data-testid="episode-drawer-loading">
        <Skeleton className="h-6 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
      </div>
    )
  }

  if (episode.error) {
    return (
      <div
        className="px-4 py-8 text-center text-sm text-destructive"
        data-testid="episode-drawer-error"
      >
        Failed to load episode.{" "}
        {episode.error instanceof Error ? episode.error.message : "Unknown error."}
      </div>
    )
  }

  if (!ep) return null

  const duration = formatDuration(ep.canonical_start_at, ep.canonical_end_at)

  return (
    <div className="flex flex-col gap-6 overflow-y-auto px-4 pb-6" data-testid="episode-drawer-content">

      {/* ── Episode detail ─────────────────────────────────────── */}
      <section aria-label="Episode detail">
        <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Episode
        </h3>
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={privacyBadgeVariant(ep.canonical_privacy)}>
              {ep.canonical_privacy}
            </Badge>
            <Badge variant="outline">{ep.precision}</Badge>
            {ep.canonical_privacy !== "restricted" && (
              <Badge variant="outline">{ep.episode_type}</Badge>
            )}
          </div>

          {!isSensitive && (
            <>
              <FieldRow label="Source" value={ep.source_name} />
              <FieldRow label="Start" value={formatDateTime(ep.canonical_start_at)} />
              <FieldRow
                label="End"
                value={ep.canonical_end_at ? formatDateTime(ep.canonical_end_at) : "ongoing"}
              />
              <FieldRow label="Duration" value={duration} />
              {ep.canonical_title && (
                <FieldRow label="Title" value={ep.canonical_title} />
              )}
              {ep.corrected_at && (
                <FieldRow
                  label="Corrected"
                  value={formatDateTime(ep.corrected_at)}
                />
              )}
              {ep.correction_note && (
                <FieldRow label="Correction note" value={ep.correction_note} />
              )}
            </>
          )}

          {isSensitive && (
            <>
              <p className="text-sm font-medium">Private activity</p>
              <p className="text-sm text-muted-foreground">
                Content hidden for sensitive episodes.
              </p>
            </>
          )}
        </div>
      </section>

      {/* ── Explain button (Tier-2 — explicit click only) ─────── */}
      {!isSensitive && (
        <section aria-label="Explain this episode">
          <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Analysis
          </h3>
          <ExplainButton episode={ep} />
          <p className="mt-1 text-xs text-muted-foreground">
            Triggers a one-time day-close summary via the Chronicler. Rate-limited to once per 24h.
          </p>
        </section>
      )}

      {/* ── Linked point events ────────────────────────────────── */}
      <section aria-label="Linked point events">
        <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Linked events
        </h3>
        {events.isLoading ? (
          <Skeleton className="h-10 w-full" />
        ) : events.error ? (
          <p className="text-xs text-destructive">Failed to load events.</p>
        ) : !events.data || events.data.length === 0 ? (
          <p className="text-xs text-muted-foreground" data-testid="no-events">
            No linked point events.
          </p>
        ) : (
          <ul className="space-y-2" data-testid="events-list">
            {events.data.map((ev) => {
              const evIsSensitive = ev.canonical_privacy === "sensitive"
              return (
                <li
                  key={ev.id}
                  className="rounded-md border p-2 text-xs space-y-0.5"
                  data-testid={`event-item-${ev.id}`}
                >
                  <div className="flex items-center gap-1.5">
                    <Badge variant="outline" className="text-[10px]">
                      {ev.event_type}
                    </Badge>
                    <Badge variant={privacyBadgeVariant(ev.canonical_privacy)} className="text-[10px]">
                      {ev.canonical_privacy}
                    </Badge>
                  </div>
                  {evIsSensitive ? (
                    <p className="text-muted-foreground">Private event</p>
                  ) : (
                    <>
                      <p>{ev.canonical_title ?? ev.source_name}</p>
                      <p className="text-muted-foreground">
                        {formatDateTime(ev.canonical_occurred_at)}
                      </p>
                    </>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </section>

      {/* ── Correction history ─────────────────────────────────── */}
      <section aria-label="Correction history">
        <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Correction history
        </h3>
        {corrections.isLoading ? (
          <Skeleton className="h-10 w-full" />
        ) : corrections.error ? (
          <p className="text-xs text-destructive">Failed to load corrections.</p>
        ) : !corrections.data || corrections.data.length === 0 ? (
          <p className="text-xs text-muted-foreground" data-testid="no-corrections">
            No corrections applied.
          </p>
        ) : (
          <ul className="space-y-2" data-testid="corrections-list">
            {corrections.data.map((c) => (
              <li
                key={c.id}
                className="rounded-md border p-2 text-xs space-y-0.5"
                data-testid={`correction-item-${c.id}`}
              >
                <p className="text-muted-foreground">
                  {formatDateTime(c.created_at)}
                  {c.submitted_by ? ` · ${c.submitted_by}` : ""}
                </p>
                {c.note && <p>{c.note}</p>}
                {c.corrected_title && (
                  <p>
                    <span className="text-muted-foreground">Title → </span>
                    {c.corrected_title}
                  </p>
                )}
                {c.corrected_start_at && (
                  <p>
                    <span className="text-muted-foreground">Start → </span>
                    {formatDateTime(c.corrected_start_at)}
                  </p>
                )}
                {c.corrected_end_at && (
                  <p>
                    <span className="text-muted-foreground">End → </span>
                    {formatDateTime(c.corrected_end_at)}
                  </p>
                )}
                {c.corrected_privacy && (
                  <p>
                    <span className="text-muted-foreground">Privacy → </span>
                    {c.corrected_privacy}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export interface EpisodeDrawerProps {
  /** Episode ID to display, or null/undefined when drawer is closed. */
  episodeId: string | null | undefined
  /** Whether the drawer is open. */
  open: boolean
  /** Called when the drawer requests closing (overlay click, Escape key). */
  onClose: () => void
}

/**
 * Side drawer that shows full episode detail for a clicked Gantt bar.
 *
 * Mounts and unmounts cleanly via the Sheet (Radix Dialog) primitive.
 * Content is lazy-loaded inside: a loading skeleton is shown while the
 * episode, events, and corrections requests are in-flight.
 *
 * The "Explain this episode" button (inside EpisodeDrawerContent) is the
 * SINGLE Tier-2 LLM call path on the chronicles page per RFC 0014 §D5.
 */
export function EpisodeDrawer({ episodeId, open, onClose }: EpisodeDrawerProps) {
  // Compute a display label for the Sheet title. We use the episode ID as
  // a short fallback since the full title is fetched inside the content.
  const titleLabel = episodeId ? `Episode ${episodeId.slice(0, 8)}…` : "Episode"

  return (
    <Sheet open={open} onOpenChange={(isOpen) => { if (!isOpen) onClose() }}>
      <SheetContent
        side="right"
        className="w-full sm:max-w-lg flex flex-col gap-0 p-0"
        data-testid="episode-drawer"
      >
        <SheetHeader className="border-b px-4 py-3">
          <SheetTitle data-testid="episode-drawer-title">{titleLabel}</SheetTitle>
          <SheetDescription className="sr-only">
            Episode detail, linked events, and correction history.
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto pt-4">
          {open && episodeId ? (
            <EpisodeDrawerContent episodeId={episodeId} />
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  )
}
