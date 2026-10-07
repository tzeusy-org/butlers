import { denseCountBuckets, orderedBuckets, type BucketWindow, type CountBucket } from "@/lib/bucket-series"
import { formatOwnerDateTime } from "@/components/ui/time"
import { useTimezone } from "@/components/ui/timezone-context"

interface BucketStripProps {
  buckets: readonly CountBucket[]
  /** Explicit source bounds. Null means present but invalid source metadata. */
  window?: BucketWindow | null
  className?: string
  height?: number
  maxValue?: number
  variant?: "stripe" | "histogram"
  noun?: string
  onBucketClick?: (bucket: CountBucket) => void
}

/** One count grammar: actual source keys, independent receiver evidence, no animation. */
export function BucketStrip({ buckets, window, className, height = 28, maxValue, onBucketClick, variant = "histogram", noun = "events" }: BucketStripProps) {
  const timezone = useTimezone()
  const ordered = window === null ? [] : window ? denseCountBuckets(buckets, window) : orderedBuckets(buckets)
  const peak = maxValue ?? Math.max(...ordered.map(bucket => bucket.count ?? 0), 1)
  const filteredPeak = Math.max(...ordered.map(bucket => bucket.filtered ?? 0), 1)
  const total = ordered.reduce((sum, bucket) => sum + (bucket.count ?? 0), 0)
  const incompleteCounts = ordered.some(bucket => bucket.count === null)
  const anyMeasuredCount = ordered.some(bucket => bucket.count !== null)
  const countSummary = incompleteCounts
    ? anyMeasuredCount ? `known total ${total} ${noun}, count incomplete` : "count unavailable"
    : `total ${total} ${noun}`
  const filteredTotal = ordered.reduce((sum, bucket) => sum + (bucket.filtered ?? 0), 0)
  const deafHours = ordered.reduce((sum, bucket) => sum + (bucket.listening === "deaf"
    ? (Date.parse(bucket.bucket_end) - Date.parse(bucket.bucket_start)) / 3_600_000 : 0), 0)
  const unknown = ordered.some(bucket => bucket.listening === "unknown")
  const peakBucket = ordered.find(bucket => bucket.count === peak)
  const formatInstant = (instant: string) => formatOwnerDateTime(instant, timezone, "minute", false)
  const label = `Count activity, ${countSummary}${filteredTotal ? ` (${filteredTotal} filtered)` : ""}` +
    (peakBucket ? `, peak ${peak} at ${formatInstant(peakBucket.bucket_start)}` : "") +
    (deafHours ? `, not listening ${deafHours}h` : "") + (unknown ? ", liveness unknown" : "") + (ordered.some(bucket => bucket.counts_partial) ? ", partial count window" : "")
  if (!ordered.length) return <div role="img" aria-label="Count window unavailable, liveness unknown"
    className={className}>Count window unavailable</div>
  return <><div role={onBucketClick ? "group" : "img"} aria-label={label}
    data-testid={!anyMeasuredCount ? "histogram-unavailable" : total === 0 && filteredTotal === 0 ? "histogram-empty" : "histogram-bars"}
    data-has-filtered={filteredTotal > 0 ? "true" : undefined}
    className={`flex gap-px ${onBucketClick ? "overflow-x-auto" : ""} ${className ?? ""}`}
    style={{ minHeight: onBucketClick ? Math.max(44, height) : height }}>
    {ordered.map(bucket => {
      const start = formatInstant(bucket.bucket_start), end = formatInstant(bucket.bucket_end)
      const state = bucket.listening === "deaf" ? "not listening" : bucket.listening === "unknown" ? "liveness unknown" : "listening (heartbeat observed)"
      const title = `${start} to ${end}: ${bucket.count === null ? "count unavailable" : `${bucket.count} ${noun}`}; ${state}${bucket.counts_partial ? "; partial count window" : ""}`
      const content = <>
        {bucket.count !== null && bucket.count > 0 && <span aria-hidden="true" className="absolute inset-x-0 bottom-0 bg-foreground/60"
          style={variant === "stripe" ? { height: "100%", backgroundColor: `color-mix(in oklch, var(--foreground) ${Math.round((.20 + bucket.count / Math.max(peak, 1) * .55) * 100)}%, transparent)` } :
            { height: Math.max(1, (bucket.count / Math.max(peak, 1)) * height) }} />}
        {(bucket.filtered ?? 0) > 0 && <span aria-hidden="true" className="absolute inset-x-0 top-0 bg-muted-foreground/25"
          style={{ height: Math.max(1, ((bucket.filtered ?? 0) / filteredPeak) * height * .3) }} />}
      </>
      const classes = `relative flex-1 min-w-1 border-0 p-0 bg-muted/40 ${bucket.listening === "deaf" ? "bucket-deaf" : bucket.listening === "unknown" ? "bucket-unknown" : "bg-muted/40"}`
      return onBucketClick ? <button key={bucket.bucket_start} type="button" aria-label={title}
        title={title} className={`${classes} min-h-11 min-w-11 shrink-0 cursor-pointer focus-visible:outline focus-visible:outline-1 focus-visible:outline-focus focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2`}
        onClick={event => { event.stopPropagation(); onBucketClick(bucket) }}
        onKeyDown={event => event.stopPropagation()}>{content}</button>
        : <div key={bucket.bucket_start} title={title} aria-label={title} className={classes}>{content}</div>
    })}
  </div>{(deafHours > 0 || unknown) && <p className="text-[9px] text-muted-foreground">{[deafHours ? `not listening ${deafHours}h · no accepted heartbeat in covered intervals` : null, unknown ? "liveness unknown" : null].filter(Boolean).join(" · ")}</p>}</>
}
