import { BucketStrip } from "@/components/ui/BucketStrip"
import type { BucketWindow, CountBucket } from "@/lib/bucket-series"

interface SparklineProps {
  buckets?: readonly CountBucket[]
  window?: BucketWindow | null
  /** Compatibility only: unkeyed legacy counts do not establish a time window. */
  data?: number[]
  secondaryData?: number[]
  maxValue?: number
  height?: number
  className?: string
}
export function Sparkline({ buckets = [], window, maxValue, height = 28, className }: SparklineProps) {
  return <BucketStrip buckets={buckets} window={window} maxValue={maxValue} height={height} className={className} />
}
