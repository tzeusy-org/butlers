// ---------------------------------------------------------------------------
// ButlerRelationshipContactsTab — bu-ax5bi
//
// Contacts bespoke tab for the Relationship butler detail page.
//
// Five sections (4-col grid):
//   1. KPI strip (full-width)         — contacts count + upcoming dates + unlinked count
//   2. Dunbar map (2col)              — GET /api/relationship/dunbar/ranking
//   3. Upcoming dates (1col)          — GET /api/relationship/upcoming-dates
//   4. Contact roster (3col)          — GET /api/relationship/contacts
//   5. Group summary (1col)           — GET /api/relationship/groups
//
// All data comes from existing hooks. No new HTTP routes.
// ---------------------------------------------------------------------------

import type {
  ContactListResponse,
  ContactSummary,
  DunbarEntry,
  DunbarRankingResponse,
  Group,
  UpcomingDate,
  UnlinkedContactsResponse,
} from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useContacts, useGroups, useUnlinkedContacts, useUpcomingDates } from "@/hooks/use-contacts";
import { useDunbarRanking } from "@/hooks/use-memory";

// ---------------------------------------------------------------------------
// Section 1: KPI Strip
// ---------------------------------------------------------------------------

interface KpiItemProps {
  label: string;
  value: string;
  subLabel?: string;
}

function KpiItem({ label, value, subLabel }: KpiItemProps) {
  return (
    <div className="flex flex-col gap-0.5" data-testid="kpi-item">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-2xl font-semibold tabular-nums" data-testid="kpi-value">
        {value}
      </span>
      {subLabel && (
        <span className="text-xs text-muted-foreground truncate">{subLabel}</span>
      )}
    </div>
  );
}

interface KpiStripProps {
  contacts: ContactListResponse | undefined;
  upcomingDates: UpcomingDate[] | undefined;
  unlinked: UnlinkedContactsResponse | undefined;
  isLoading: boolean;
}

function KpiStrip({ contacts, upcomingDates, unlinked, isLoading }: KpiStripProps) {
  if (isLoading && !contacts && !upcomingDates && !unlinked) {
    return (
      <Card data-testid="kpi-strip">
        <CardHeader>
          <CardTitle className="text-sm font-medium">Relationship overview</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-6">
            {Array.from({ length: 3 }, (_, i) => (
              <div key={i} className="space-y-1" data-testid="loading-line">
                <Skeleton className="h-3 w-20 rounded" />
                <Skeleton className="h-7 w-12 rounded" />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  const contactsCount = contacts?.total ?? 0;
  const upcomingCount = upcomingDates?.length ?? 0;
  const nearestDate = upcomingDates?.[0];
  const unlinkedCount = unlinked?.total ?? 0;

  return (
    <Card data-testid="kpi-strip">
      <CardHeader>
        <CardTitle className="text-sm font-medium">Relationship overview</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-6">
          <KpiItem label="Active contacts" value={String(contactsCount)} />
          <KpiItem
            label="Upcoming dates (30d)"
            value={String(upcomingCount)}
            subLabel={nearestDate ? `${nearestDate.contact_name} in ${nearestDate.days_until}d` : undefined}
          />
          <KpiItem label="Unlinked contacts" value={String(unlinkedCount)} />
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section 2: Dunbar Map
// ---------------------------------------------------------------------------

/** Dunbar tier ring sizes as the canonical Dunbar numbers */
const DUNBAR_TIERS: { tier: number; label: string; colour: string }[] = [
  { tier: 1, label: "Tier 1 (5)", colour: "bg-violet-500" },
  { tier: 2, label: "Tier 2 (15)", colour: "bg-blue-500" },
  { tier: 3, label: "Tier 3 (50)", colour: "bg-green-500" },
  { tier: 4, label: "Tier 4 (150)", colour: "bg-yellow-500" },
  { tier: 5, label: "Tier 5 (500)", colour: "bg-orange-400" },
];

interface DunbarMapProps {
  ranking: DunbarRankingResponse | undefined;
  isLoading: boolean;
}

function DunbarMap({ ranking, isLoading }: DunbarMapProps) {
  if (isLoading && !ranking) {
    return (
      <div className="space-y-3" data-testid="dunbar-map-loading">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="space-y-1" data-testid="loading-line">
            <Skeleton className="h-3 w-20 rounded" />
            <div className="flex flex-wrap gap-1">
              {Array.from({ length: 3 }, (_, j) => (
                <Skeleton key={j} className="h-5 w-16 rounded-full" />
              ))}
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (!ranking || ranking.entries.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="empty-state-line">
        No Dunbar ranking data available.
      </p>
    );
  }

  // Group entries by tier
  const byTier = new Map<number, DunbarEntry[]>();
  for (const entry of ranking.entries) {
    const tier = entry.dunbar_tier;
    if (!byTier.has(tier)) byTier.set(tier, []);
    byTier.get(tier)!.push(entry);
  }

  return (
    <ul
      className="space-y-3"
      aria-label="Dunbar tier ranking"
      data-testid="dunbar-map"
    >
      {DUNBAR_TIERS.map(({ tier, label, colour }) => {
        const members = byTier.get(tier) ?? [];
        if (members.length === 0) return null;
        return (
          <li key={tier} data-testid="dunbar-tier-row">
            <p className="text-xs font-medium text-muted-foreground mb-1">{label}</p>
            <div className="flex flex-wrap gap-1">
              {members.map((entry) => (
                <Badge
                  key={entry.contact_id}
                  variant="outline"
                  className={`text-xs ${entry.dunbar_tier_override ? "border-dashed" : ""}`}
                >
                  <span className={`mr-1.5 inline-block h-2 w-2 rounded-full ${colour}`} aria-hidden />
                  {entry.canonical_name}
                  {entry.dunbar_tier_override && (
                    <span className="ml-1 text-muted-foreground" title="Manually pinned">★</span>
                  )}
                </Badge>
              ))}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Section 3: Upcoming Dates Panel
// ---------------------------------------------------------------------------

interface UpcomingDatesPanelProps {
  dates: UpcomingDate[] | undefined;
  isLoading: boolean;
}

function UpcomingDatesPanel({ dates, isLoading }: UpcomingDatesPanelProps) {
  if (isLoading && !dates) {
    return (
      <div className="space-y-2" data-testid="upcoming-dates-loading">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="flex items-center justify-between" data-testid="loading-line">
            <Skeleton className="h-3 w-28 rounded" />
            <Skeleton className="h-5 w-12 rounded-full" />
          </div>
        ))}
      </div>
    );
  }

  if (!dates || dates.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="empty-state-line">
        No upcoming dates in the next 30 days.
      </p>
    );
  }

  return (
    <ul
      className="space-y-2"
      aria-label="Upcoming dates"
      data-testid="upcoming-dates-list"
    >
      {dates.map((item, idx) => (
        <li
          key={`${item.contact_id}-${item.date_type}-${idx}`}
          className="flex items-start justify-between gap-2 text-sm"
          data-testid="upcoming-date-row"
        >
          <div className="min-w-0">
            <p className="font-medium truncate">{item.contact_name}</p>
            <p className="text-xs text-muted-foreground capitalize">{item.date_type}</p>
          </div>
          <Badge
            variant={item.days_until <= 7 ? "default" : "secondary"}
            className="shrink-0 tabular-nums"
          >
            {item.days_until === 0 ? "today" : `${item.days_until}d`}
          </Badge>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Section 4: Contact Roster
// ---------------------------------------------------------------------------

interface ContactRosterProps {
  contacts: ContactSummary[] | undefined;
  isLoading: boolean;
}

function ContactRoster({ contacts, isLoading }: ContactRosterProps) {
  if (isLoading && (!contacts || contacts.length === 0)) {
    return (
      <div className="space-y-3" data-testid="contact-roster-loading">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="flex items-center gap-3" data-testid="loading-line">
            <Skeleton className="h-8 w-8 rounded-full shrink-0" />
            <div className="flex-1 space-y-1">
              <Skeleton className="h-3 w-32 rounded" />
              <Skeleton className="h-3 w-20 rounded" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (!contacts || contacts.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="empty-state-line">
        No contacts found.
      </p>
    );
  }

  return (
    <ul
      className="space-y-2 max-h-[480px] overflow-y-auto"
      aria-label="Contact roster"
      data-testid="contact-roster"
    >
      {contacts.map((contact) => (
        <li
          key={contact.id}
          className="flex items-center gap-3 text-sm py-1"
          data-testid="contact-roster-row"
        >
          {/* Avatar initials */}
          <div
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium uppercase"
            aria-hidden
          >
            {contact.first_name?.[0] ?? contact.full_name[0]}
          </div>
          <div className="min-w-0 flex-1">
            <p className="font-medium truncate">{contact.full_name}</p>
            {contact.last_interaction_at && (
              <p className="text-xs text-muted-foreground">
                Last: {new Date(contact.last_interaction_at).toLocaleDateString()}
              </p>
            )}
          </div>
          {contact.labels.length > 0 && (
            <div className="flex shrink-0 gap-1">
              {contact.labels.slice(0, 2).map((label) => (
                <Badge key={label.id} variant="outline" className="text-xs">
                  {label.name}
                </Badge>
              ))}
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Section 5: Group Summary
// ---------------------------------------------------------------------------

interface GroupSummaryProps {
  groups: Group[] | undefined;
  isLoading: boolean;
}

function GroupSummary({ groups, isLoading }: GroupSummaryProps) {
  if (isLoading && (!groups || groups.length === 0)) {
    return (
      <div className="space-y-2" data-testid="group-summary-loading">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="flex items-center justify-between" data-testid="loading-line">
            <Skeleton className="h-3 w-24 rounded" />
            <Skeleton className="h-5 w-8 rounded-full" />
          </div>
        ))}
      </div>
    );
  }

  if (!groups || groups.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="empty-state-line">
        No groups configured.
      </p>
    );
  }

  return (
    <ul
      className="space-y-2"
      aria-label="Groups"
      data-testid="group-summary-list"
    >
      {groups.map((group) => (
        <li
          key={group.id}
          className="flex items-center justify-between gap-2 text-sm"
          data-testid="group-summary-row"
        >
          <p className="font-medium truncate">{group.name}</p>
          <Badge variant="secondary" className="shrink-0 tabular-nums">
            {group.member_count}
          </Badge>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Main tab component
// ---------------------------------------------------------------------------

export default function ButlerRelationshipContactsTab() {
  // --- Section 1: KPI strip (contacts count, upcoming dates, unlinked count)
  const { data: contactsData, isLoading: contactsLoading } = useContacts({ limit: 50 });
  const { data: upcomingDatesData, isLoading: upcomingLoading } = useUpcomingDates(30);
  const { data: unlinkedData, isLoading: unlinkedLoading } = useUnlinkedContacts({ limit: 1 });

  // --- Section 2: Dunbar map
  const { data: dunbarData, isLoading: dunbarLoading } = useDunbarRanking(true);

  // --- Section 3: Upcoming dates (60-day window for the panel)
  const { data: upcomingDates60, isLoading: upcomingLoading60 } = useUpcomingDates(60);

  // --- Section 4: Contact roster (first page, 50 contacts)
  // Reuse contactsData from above (already fetched for KPI)

  // --- Section 5: Group summary
  const { data: groupsData, isLoading: groupsLoading } = useGroups();

  const kpiLoading = contactsLoading || upcomingLoading || unlinkedLoading;

  return (
    <div className="space-y-6" data-testid="relationship-contacts-tab">
      {/* Section 1: KPI strip — full width */}
      <KpiStrip
        contacts={contactsData}
        upcomingDates={upcomingDatesData}
        unlinked={unlinkedData}
        isLoading={kpiLoading}
      />

      {/* Sections 2–3: Dunbar map (2col) + Upcoming dates (1col) */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card className="lg:col-span-2" data-testid="dunbar-map-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Dunbar map</CardTitle>
          </CardHeader>
          <CardContent>
            <DunbarMap ranking={dunbarData} isLoading={dunbarLoading} />
          </CardContent>
        </Card>

        <Card className="lg:col-span-1" data-testid="upcoming-dates-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Upcoming dates · 30d</CardTitle>
          </CardHeader>
          <CardContent>
            <UpcomingDatesPanel dates={upcomingDates60} isLoading={upcomingLoading60} />
          </CardContent>
        </Card>
      </div>

      {/* Sections 4–5: Contact roster (3col) + Group summary (1col) */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        <Card className="lg:col-span-3" data-testid="contact-roster-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Contact roster</CardTitle>
          </CardHeader>
          <CardContent>
            <ContactRoster contacts={contactsData?.contacts} isLoading={contactsLoading} />
          </CardContent>
        </Card>

        <Card className="lg:col-span-1" data-testid="group-summary-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Groups</CardTitle>
          </CardHeader>
          <CardContent>
            <GroupSummary groups={groupsData?.groups} isLoading={groupsLoading} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
