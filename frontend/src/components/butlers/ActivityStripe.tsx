import { BucketStrip } from "@/components/ui/BucketStrip"
import type { CountBucket } from "@/lib/bucket-series"

interface ActivityStripeProps {
  buckets?: readonly CountBucket[]
  /** Legacy unkeyed responses remain unavailable; they cannot supply clock labels. */
  counts?: number[]
  windowEnd?: Date
  className?: string
  onBarClick?: (index: number) => void
  onBucketClick?: (bucket: CountBucket) => void
}

export function ActivityStripe({ buckets = [], className, onBarClick, onBucketClick }: ActivityStripeProps) {
  return <BucketStrip variant="stripe" noun="sessions" buckets={buckets} className={className}
    onBucketClick={onBucketClick ?? (onBarClick ? bucket => onBarClick(buckets.indexOf(bucket)) : undefined)} />
}
