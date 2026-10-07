import { BucketStrip } from "@/components/ui/BucketStrip"
import type { CountBucket } from "@/lib/bucket-series"

interface SparklineProps {
  buckets?: readonly CountBucket[]
  /** Compatibility only: unkeyed legacy counts do not establish a time window. */
  data?: number[]
  secondaryData?: number[]
  maxValue?: number
  height?: number
  className?: string
}
export function Sparkline({ buckets = [], maxValue, height = 28, className }: SparklineProps) {
  return <BucketStrip buckets={buckets} maxValue={maxValue} height={height} className={className} />
}
