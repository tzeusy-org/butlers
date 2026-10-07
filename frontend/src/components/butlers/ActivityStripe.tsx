import { BucketStrip } from "@/components/ui/BucketStrip"
import { orderedBuckets, type BucketWindow, type CountBucket } from "@/lib/bucket-series"

interface ActivityStripeProps {
  buckets?: readonly CountBucket[]
  window?: BucketWindow | null
  /** Legacy unkeyed responses remain unavailable; they cannot supply clock labels. */
  counts?: number[]
  windowEnd?: Date
  className?: string
  onBarClick?: (index: number) => void
  onBucketClick?: (bucket: CountBucket) => void
}

export function ActivityStripe({ buckets = [], window, className, onBarClick, onBucketClick }: ActivityStripeProps) {
  return <BucketStrip variant="stripe" noun="sessions" buckets={buckets} window={window} className={className}
    onBucketClick={onBucketClick ?? (onBarClick ? bucket => onBarClick(window
      ? (Date.parse(bucket.bucket_start) - Date.parse(window.window_start)) / (window.bucket_width_s * 1000)
      : orderedBuckets(buckets).indexOf(bucket)) : undefined)} />
}
