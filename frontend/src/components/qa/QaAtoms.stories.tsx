import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useState } from "react";

import type { QaActiveBreakdown, QaCaseSummary, QaKpiBlock } from "@/api/types";

import { CaseDossierHeader, CaseList, QaKpiStrip, StateTrack } from "./index";

const kpis: QaKpiBlock = {
  prs_landed_24h: 4,
  mttr_24h_seconds: 4380,
  self_resolved_7d_pct: 73,
  active_cases_now: 6,
  prs_landed_prior_24h: 2,
  mttr_prior_24h_seconds: 5200,
  self_resolved_prior_7d_pct: 68,
};

const activeBreakdown: QaActiveBreakdown = {
  awaiting_ci: 3,
  escalated_open_cases: 1,
};

const cases: QaCaseSummary[] = [
  {
    id: "case-1",
    short_id: "mfg",
    sev: "high",
    butler: "qa",
    headline: "Runtime args dropped before adapter launch",
    detected: "2026-05-14T09:12:00Z",
    age_seconds: 5400,
    state: "pr",
    pr_state: "open",
    pr_url: "https://github.com/Tzeusy/butlers/pull/1",
  },
  {
    id: "case-2",
    short_id: "h7",
    sev: "medium",
    butler: "health",
    headline: "Measurement sync stalled",
    detected: "2026-05-14T10:00:00Z",
    age_seconds: 340,
    state: "diagnose",
    pr_state: "drafted",
    pr_url: null,
  },
];

function StoryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: false, staleTime: Infinity } },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

export function QaAtoms() {
  return (
    <StoryProvider>
      <div className="max-w-5xl space-y-8 p-8">
        <QaKpiStrip kpis={kpis} active={activeBreakdown} />
        <div className="grid gap-8 md:grid-cols-[320px_1fr]">
          <CaseList cases={cases} selectedId="case-1" onSelect={() => undefined} />
          <div className="space-y-4 border-t border-border/60 pt-4">
            <CaseDossierHeader case={cases[0]} stage="pr" fingerprint={null} dismissal={null} />
            <StateTrack stage="escalated" />
          </div>
        </div>
      </div>
    </StoryProvider>
  );
}
