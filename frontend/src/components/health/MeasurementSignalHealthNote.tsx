import { SourceDegradedNote } from "@/components/ui/query-boundary";
import type { MeasurementSignalRead } from "@/lib/measurement-acquisition";

export function MeasurementSignalHealthNote({ read }: { read?: MeasurementSignalRead }) {
  const unavailable = read?.isError ||
    (read?.data && (!read.data.available || read.data.signals === null));
  if (!read || read.isPending || !unavailable) return null;

  return (
    <SourceDegradedNote
      label="Measurement signal health"
      detail="signal health unavailable; absence nudges are paused"
      onRetry={() => void read.refetch()}
      testId="expected-signals-degraded"
    />
  );
}
