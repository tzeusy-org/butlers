// ---------------------------------------------------------------------------
// ButlerCrmTab — bu-j7b5n (follow-up from epic bu-hdavr)
//
// CRM tab body for the butler detail page. Uses the 4-column panel-grid frame
// from finish-butler-detail-body-panel-grid.
//
// Layout (relationship butler only):
//   Row 1: upcoming dates (span=3) | quick links (span=1)
//
// For non-relationship butlers, a single full-width panel shows an empty state.
//
// Hooks:
//   useUpcomingDates(days) — upcoming birthdays, anniversaries (relationship only)
//
// Doctrine gates:
//   - No <Card> / <CardHeader> / <CardContent> wrappers.
//   - No raw oklch/hex literals.
//   - No em-dashes in JSX text.
//   - No pid field anywhere.
//   - Token-only chrome.
// ---------------------------------------------------------------------------

import { Link } from "react-router";

import type { UpcomingDate } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ButlerPanelGrid, Panel, EmptyLine } from "@/components/butler-detail/atoms";
import { useUpcomingDates } from "@/hooks/use-contacts";

// ---------------------------------------------------------------------------
// UpcomingDateRow
// ---------------------------------------------------------------------------

function UpcomingDateRow({ item }: { item: UpcomingDate }) {
  return (
    <div
      className="flex items-center justify-between border-b border-border/40 last:border-b-0 py-2 min-w-0 gap-3"
      data-testid="upcoming-date-row"
    >
      <div className="flex items-center gap-3 min-w-0">
        <Badge variant="outline" className="text-xs shrink-0">
          {item.date_type}
        </Badge>
        <Link
          to={`/contacts/${item.contact_id}`}
          className="text-sm font-medium hover:underline truncate"
        >
          {item.contact_name}
        </Link>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <span className="text-muted-foreground text-sm font-mono tnum">{item.date}</span>
        <Badge
          variant={item.days_until <= 3 ? "destructive" : "secondary"}
          className="text-xs"
        >
          {item.days_until === 0
            ? "Today"
            : item.days_until === 1
              ? "Tomorrow"
              : `${item.days_until}d`}
        </Badge>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// UpcomingDatesPanelBody
// ---------------------------------------------------------------------------

interface UpcomingDatesPanelBodyProps {
  upcomingDates: UpcomingDate[] | undefined;
  isLoading: boolean;
}

function UpcomingDatesPanelBody({ upcomingDates, isLoading }: UpcomingDatesPanelBodyProps) {
  if (isLoading) {
    return (
      <div className="space-y-2" data-testid="upcoming-dates-loading">
        {Array.from({ length: 3 }, (_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  if (!upcomingDates || upcomingDates.length === 0) {
    return (
      <EmptyLine>No upcoming dates in the next 30 days.</EmptyLine>
    );
  }

  return (
    <div data-testid="upcoming-dates-list">
      {upcomingDates.map((item, idx) => (
        <UpcomingDateRow key={`${item.contact_id}-${item.date_type}-${idx}`} item={item} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ButlerCrmTab
// ---------------------------------------------------------------------------

interface ButlerCrmTabProps {
  butlerName: string;
}

export default function ButlerCrmTab({ butlerName }: ButlerCrmTabProps) {
  const isRelationship = butlerName === "relationship";
  const { data: upcomingDates, isLoading } = useUpcomingDates(
    isRelationship ? 30 : undefined,
  );

  if (!isRelationship) {
    return (
      <ButlerPanelGrid data-testid="butler-crm-tab">
        <Panel span={4} testId="panel-crm-unavailable">
          <EmptyLine>CRM features are only available for the relationship butler.</EmptyLine>
        </Panel>
      </ButlerPanelGrid>
    );
  }

  return (
    <ButlerPanelGrid data-testid="butler-crm-tab">
      {/* Upcoming dates (span=3) */}
      <Panel title="upcoming dates" span={3} testId="panel-upcoming-dates">
        <UpcomingDatesPanelBody upcomingDates={upcomingDates} isLoading={isLoading} />
      </Panel>

      {/* Quick links (span=1) */}
      <Panel title="quick links" span={1} testId="panel-quick-links">
        <div className="flex flex-col gap-2">
          <Button variant="outline" size="sm" asChild>
            <Link to="/contacts">Contacts</Link>
          </Button>
          <Button variant="outline" size="sm" asChild>
            <Link to="/groups">Groups</Link>
          </Button>
        </div>
      </Panel>
    </ButlerPanelGrid>
  );
}
