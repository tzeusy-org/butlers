import { Skeleton } from "@/components/ui/skeleton";
import { useEligibilityHistory } from "@/hooks/use-general";
import type { EligibilitySegment } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const STATE_COLORS: Record<string, string> = {
  active: "bg-emerald-600",
  stale: "bg-amber-500",
  quarantined: "bg-red-600",
};

function segmentColor(state: string): string {
  return STATE_COLORS[state] ?? "bg-gray-400";
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatRange(seg: EligibilitySegment): string {
  return `${seg.state}: ${formatTime(seg.start_at)} \u2013 ${formatTime(seg.end_at)}`;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface EligibilityTimelineProps {
  butlerName: string;
  hours?: number;
}

export default function EligibilityTimeline({
  butlerName,
  hours = 24,
}: EligibilityTimelineProps) {
  const { data, isLoading } = useEligibilityHistory(butlerName, hours);

  if (isLoading) {
    return <Skeleton className="h-5 w-full rounded" />;
  }

  const history = data?.data;
  if (!history || history.segments.length === 0) {
    return <span className="text-xs text-muted-foreground">No data</span>;
  }

  const windowStart = new Date(history.window_start).getTime();
  const windowEnd = new Date(history.window_end).getTime();
  const totalMs = windowEnd - windowStart;

  return (
    <div>
      <div className="flex h-5 w-full overflow-hidden rounded">
        {history.segments.map((seg, i) => {
          const startMs = new Date(seg.start_at).getTime();
          const endMs = new Date(seg.end_at).getTime();
          const pct = totalMs > 0 ? ((endMs - startMs) / totalMs) * 100 : 0;
          if (pct <= 0) return null;
          return (
            <div
              key={i}
              className={`${segmentColor(seg.state)} min-w-[2px]`}
              style={{ width: `${pct}%` }}
              title={formatRange(seg)}
            />
          );
        })}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-muted-foreground">
        <span>{formatTime(history.window_start)}</span>
        <span>{formatTime(history.window_end)}</span>
      </div>
    </div>
  );
}
