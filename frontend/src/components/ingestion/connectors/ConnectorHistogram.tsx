import { BucketStrip } from "@/components/ui/BucketStrip"
import type { BucketWindow, CountBucket } from "@/lib/bucket-series"

interface ConnectorHistogramProps {
  buckets?: readonly CountBucket[]
  window?: BucketWindow | null
  data?: number[]
  secondaryData?: number[]
  height?: number
  className?: string
}
export function ConnectorHistogram({ buckets = [], window, height = 96, className }: ConnectorHistogramProps) {
  return <BucketStrip buckets={buckets} window={window} height={height} className={className} />
}
