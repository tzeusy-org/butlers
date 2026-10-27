import type { ExpectedSignalsResponse } from "@/api/types";

/** Acquisition state supplied by the routed page's existing expected-signal query. */
export interface MeasurementSignalRead {
  data?: ExpectedSignalsResponse;
  isPending: boolean;
  isError: boolean;
  refetch: () => unknown;
}

export function isMeasurementUnreachable(
  read: MeasurementSignalRead | undefined,
  type: string,
): boolean {
  if (!type || !read || read.isPending || read.isError || !read.data?.available) return false;
  return read.data.signals?.some(
    signal => signal.signal_key === `health:measurement-gap:${type}` &&
      signal.measurability === "unmeasurable",
  ) ?? false;
}
