import { BucketStrip } from "@/components/ui/BucketStrip"
import type { CountBucket } from "@/lib/bucket-series"

interface ConnectorHistogramProps {
  buckets?: readonly CountBucket[]
  data?: number[]
  secondaryData?: number[]
  height?: number
  className?: string
}
export function ConnectorHistogram({ buckets = [], height = 96, className }: ConnectorHistogramProps) {
  return <BucketStrip buckets={buckets} height={height} className={className} />
}
