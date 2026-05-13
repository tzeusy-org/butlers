// ---------------------------------------------------------------------------
// ButlerSessionsTab — bu-j7b5n (follow-up from epic bu-hdavr)
//
// Sessions tab body for the butler detail page. Uses the 4-column panel-grid
// frame from finish-butler-detail-body-panel-grid.
//
// Layout:
//   Row 1: sessions table (span=4, scroll, height="480px")
//   Below: pagination controls when total > 0
//
// Hooks:
//   useButlerSessions(butlerName, params) — paginated session history
//
// Doctrine gates:
//   - No <Card> / <CardHeader> / <CardContent> wrappers.
//   - No raw oklch/hex literals.
//   - No em-dashes in JSX text.
//   - No pid field anywhere.
//   - Token-only chrome.
//   - Timestamps via <Time> where timestamps are shown.
// ---------------------------------------------------------------------------

import { useState } from "react";

import type { SessionParams, SessionSummary } from "@/api/types";
import { SessionTable } from "@/components/sessions/SessionTable";
import { SessionDetailDrawer } from "@/components/sessions/SessionDetailDrawer";
import { Button } from "@/components/ui/button";
import { ButlerPanelGrid, Panel } from "@/components/butler-detail/atoms";
import { useButlerSessions } from "@/hooks/use-sessions";

// ---------------------------------------------------------------------------
// Page size constant
// ---------------------------------------------------------------------------

const PAGE_SIZE = 20;

// ---------------------------------------------------------------------------
// ButlerSessionsTab
// ---------------------------------------------------------------------------

interface ButlerSessionsTabProps {
  butlerName: string;
}

export default function ButlerSessionsTab({ butlerName }: ButlerSessionsTabProps) {
  const [page, setPage] = useState(0);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);

  const params: SessionParams = {
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };

  const { data: sessionsResponse, isLoading } = useButlerSessions(butlerName, params);
  const sessions = sessionsResponse?.data ?? [];
  const meta = sessionsResponse?.meta;
  const total = meta?.total ?? 0;
  const hasMore = meta?.has_more ?? false;

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = page + 1;

  function handleSessionClick(session: SessionSummary) {
    setSelectedSessionId(session.id);
  }

  return (
    <div data-testid="butler-sessions-tab">
      <ButlerPanelGrid>
        <Panel title="sessions" span={4} testId="panel-sessions">
          <SessionTable
            sessions={sessions}
            isLoading={isLoading}
            onSessionClick={handleSessionClick}
            showButlerColumn={false}
          />
        </Panel>
      </ButlerPanelGrid>

      {/* Pagination controls */}
      {total > 0 && (
        <div
          className="flex items-center justify-between border-x border-b border-border/60 px-4 py-3"
          data-testid="sessions-pagination"
        >
          <p className="text-muted-foreground text-sm">
            Page {currentPage} of {totalPages}
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              data-testid="sessions-prev"
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!hasMore}
              onClick={() => setPage((p) => p + 1)}
              data-testid="sessions-next"
            >
              Next
            </Button>
          </div>
        </div>
      )}

      {/* Session detail drawer */}
      <SessionDetailDrawer
        butler={butlerName}
        sessionId={selectedSessionId}
        onClose={() => setSelectedSessionId(null)}
      />
    </div>
  );
}
