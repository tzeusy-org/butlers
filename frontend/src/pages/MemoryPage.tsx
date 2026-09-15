import { useEffect } from "react";

import MemoryOverture from "@/components/memory/MemoryOverture";
import MemoryBrowser from "@/components/memory/MemoryBrowser";
import AttentionRail from "@/components/memory/AttentionRail";
import HousekeepingBand from "@/components/memory/HousekeepingBand";
import { usePageSubject } from "@/lib/page-context.tsx";

// ---------------------------------------------------------------------------
// MemoryPage
// ---------------------------------------------------------------------------

export default function MemoryPage() {
  // Page-context enrichment (bu-0ynlk.4): a typed marker so a chat message
  // sent from here (e.g. "that fact is wrong") is grounded to Memory even
  // before a specific fact/rule/episode detail page is opened.
  const setPageSubject = usePageSubject().set;
  useEffect(() => {
    setPageSubject({ visible_resource: { kind: "memory" }, visible_summary: "Memory" });
  }, [setPageSubject]);

  return (
    <div className="space-y-6">
      {/* Overture (Bands 1 & 2): headline, Voice, KPI strip, pipeline band.
          Answers "is remembering working" before any scrolling. */}
      <MemoryOverture />

      {/* Band 3 — Registers + rail. grid 1.4fr/1fr, gap 56px:
          left = the one search affordance + focused register (or results);
          right = the attention rail (the page's state color) + recent activity. */}
      <div className="grid gap-x-14 gap-y-10 lg:grid-cols-[1.4fr_1fr]">
        <MemoryBrowser />
        <AttentionRail />
      </div>

      {/* Band 4 — Housekeeping. Retention, compaction, and re-embed in one
          quiet band; carries the #housekeeping anchor the rail deep-links to. */}
      <HousekeepingBand />
    </div>
  );
}
