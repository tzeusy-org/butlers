/**
 * DateEyebrow -- uppercase mono date/time row with a BriefingStatus slot.
 *
 * Format: "Overview · Wed, 7 May 2026 · 14:21"
 * Font: --font-mono, 10px, 0.14em letter-spacing, muted color.
 *
 * The slot is a ReactNode so the parent (DashboardPage) can pass
 * <BriefingStatus /> without DateEyebrow depending on briefing data.
 *
 * Topology: about/lay-and-land/frontend.md §Editorial archetype layout
 */

interface DateEyebrowProps {
  /** Slot for BriefingStatus pill. */
  statusSlot?: React.ReactNode;
}

function formatEyebrowDate(now: Date): string {
  const weekday = now.toLocaleDateString("en-GB", { weekday: "short" });
  const day = now.getDate();
  const month = now.toLocaleDateString("en-GB", { month: "long" });
  const year = now.getFullYear();
  const hh = String(now.getHours()).padStart(2, "0");
  const mm = String(now.getMinutes()).padStart(2, "0");
  return `Overview · ${weekday}, ${day} ${month} ${year} · ${hh}:${mm}`;
}

export function DateEyebrow({ statusSlot }: DateEyebrowProps) {
  const label = formatEyebrowDate(new Date());

  return (
    <div className="flex items-center gap-3">
      <p
        className="tnum uppercase"
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "10px",
          letterSpacing: "0.14em",
          lineHeight: 1,
          color: "var(--muted-foreground)",
        }}
      >
        {label}
      </p>
      {statusSlot}
    </div>
  );
}
