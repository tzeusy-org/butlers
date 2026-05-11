// ---------------------------------------------------------------------------
// ButlerRelationshipContactsTab — bu-iuol4.21
//
// Contacts bespoke tab for the Relationship butler detail page.
//
// Six panels (4-col grid):
//   1. KPI strip (span 4)          — tracked count, T1 warmth avg, cadence ok, overdue count
//   2. Tier distribution (span 2)  — T1–T4 rows: count + warmth bar + warmth score
//   3. Overdue (span 2)            — names ranked by owed_days desc
//   4. Watchlist T1+T2 (span 4)    — scrollable table: warmth, last contact, tier
//   5. Selected thread (span 3)    — last 4 messages with selected contact
//   6. Known facts (span 1)        — bullet facts for selected contact
//
// Hooks consumed:
//   useDunbarRanking       — tier distribution + watchlist
//   useUpcomingDates       — upcoming dates (KPI)
//   useContacts            — contacts list (facts panel, total count)
//   useGroups              — group summary
//   useContactInteractions — NEW: contact interaction thread
//   useOverdueContacts     — NEW: overdue contacts by owed_days
// ---------------------------------------------------------------------------

import { useState } from "react";
import type { ReactNode } from "react";

import { AlertTriangle } from "lucide-react";

import type {
  ContactDetail,
  DunbarEntry,
  DunbarRankingResponse,
  ContactInteraction,
  OverdueContact,
} from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { KpiCell } from "./atoms";
import { useContacts, useContact, useContactInteractions, useOverdueContacts } from "@/hooks/use-contacts";
import { useDunbarRanking } from "@/hooks/use-memory";

// ---------------------------------------------------------------------------
// Tier constants
// ---------------------------------------------------------------------------

/** Dunbar tier display labels and target cadence in days. */
const TIER_META: Record<number, { label: string; cadence_days: number }> = {
  5:    { label: "T1 · Support 5",       cadence_days: 7  },
  15:   { label: "T2 · Sympathy 15",     cadence_days: 14 },
  50:   { label: "T3 · Friends 50",      cadence_days: 30 },
  150:  { label: "T4 · Network 150",     cadence_days: 90 },
  500:  { label: "T5 · Acquaintances",   cadence_days: 180 },
  1500: { label: "T6 · Familiar",        cadence_days: 365 },
};

const TRACKED_TIERS = [5, 15, 50, 150];

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

/** Empty-state placeholder: serif italic. */
function EmptyStateLine({ children }: { children: ReactNode }) {
  return (
    <p
      className="text-sm text-muted-foreground italic font-[family-name:var(--font-serif,serif)]"
      data-testid="empty-state-line"
    >
      {children}
    </p>
  );
}

/** Error state: icon + destructive-tone text. */
function ErrorLine({ children }: { children: ReactNode }) {
  return (
    <p className="flex items-center gap-1.5 text-sm text-destructive min-w-0" data-testid="error-state-line">
      <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
      <span className="truncate">{children}</span>
    </p>
  );
}

/** Loading placeholder row. */
function LoadingRows({ count = 4 }: { count?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="flex items-center gap-2" data-testid="loading-line">
          <Skeleton className="h-3 w-28 rounded" />
          <Skeleton className="h-3 flex-1 rounded" />
        </div>
      ))}
    </div>
  );
}

/** Format a date string as short relative text ("3d ago" or ISO date). */
function relativeDate(isoStr: string | null | undefined): string {
  if (!isoStr) return "—";
  const d = new Date(isoStr);
  if (isNaN(d.getTime())) return "—";
  const diffMs = Date.now() - d.getTime();
  const diffDays = Math.floor(diffMs / 86_400_000);
  if (diffDays < 0) return `in ${-diffDays}d`;
  if (diffDays === 0) return "today";
  if (diffDays === 1) return "1d ago";
  if (diffDays < 365) return `${diffDays}d ago`;
  return d.toLocaleDateString();
}

/** Clamp a number to [0, 1] and format as a percentage bar width. */
function warmthBarWidth(warmth: number | null | undefined): string {
  if (warmth == null) return "0%";
  return `${Math.round(Math.max(0, Math.min(1, warmth)) * 100)}%`;
}

/** Format warmth as a display string (0.00–1.00). */
function fmtWarmth(warmth: number | null | undefined): string {
  if (warmth == null) return "—";
  return warmth.toFixed(2);
}

// ---------------------------------------------------------------------------
// Panel 1: KPI Strip (span 4)
// ---------------------------------------------------------------------------

interface KpiStripProps {
  ranking: DunbarRankingResponse | undefined;
  overdueCount: number;
  totalContacts: number;
  isLoading: boolean;
  isError: boolean;
}

function RelationshipKpiStrip({ ranking, overdueCount, totalContacts, isLoading, isError }: KpiStripProps) {
  const kpiSkeleton = (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 px-4 py-3">
      {Array.from({ length: 4 }, (_, i) => (
        <div key={i} className="space-y-1" data-testid="loading-line">
          <Skeleton className="h-2.5 w-20 rounded" />
          <Skeleton className="h-7 w-12 rounded" />
        </div>
      ))}
    </div>
  );

  if (isLoading && !ranking) {
    return (
      <Card data-testid="kpi-strip">
        <CardHeader>
          <CardTitle className="text-sm font-medium">Relationship overview</CardTitle>
        </CardHeader>
        <CardContent className="p-0 pb-4">{kpiSkeleton}</CardContent>
      </Card>
    );
  }

  if (isError) {
    return (
      <Card data-testid="kpi-strip">
        <CardHeader>
          <CardTitle className="text-sm font-medium">Relationship overview</CardTitle>
        </CardHeader>
        <CardContent>
          <ErrorLine>Could not load relationship overview.</ErrorLine>
        </CardContent>
      </Card>
    );
  }

  // Compute T1 warmth average over entries that actually have a warmth score.
  const t1Entries = ranking?.entries.filter((e) => e.dunbar_tier === 5) ?? [];
  const t1ScoredEntries = t1Entries.filter((e) => e.warmth != null);
  const t1WarmthAvg =
    t1ScoredEntries.length > 0
      ? t1ScoredEntries.reduce((sum, e) => sum + e.warmth!, 0) / t1ScoredEntries.length
      : null;

  // Cadence ok count: tracked contacts not overdue (warmth >= 0.5 proxy).
  const trackedEntries = ranking?.entries.filter((e) => TRACKED_TIERS.includes(e.dunbar_tier)) ?? [];
  const cadenceOkCount = trackedEntries.filter((e) => (e.warmth ?? 0) >= 0.5).length;

  return (
    <Card data-testid="kpi-strip">
      <CardHeader>
        <CardTitle className="text-sm font-medium">Relationship overview</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-6">
          <div data-testid="kpi-item">
            <KpiCell
              label="Tracked contacts"
              value={String(totalContacts)}
            />
          </div>
          <div data-testid="kpi-item">
            <KpiCell
              label="T1 warmth avg"
              value={fmtWarmth(t1WarmthAvg)}
              tone={t1WarmthAvg != null && t1WarmthAvg >= 0.6 ? "green" : t1WarmthAvg != null && t1WarmthAvg < 0.4 ? "red" : "fg"}
            />
          </div>
          <div data-testid="kpi-item">
            <KpiCell
              label="Warm / tracked"
              value={`${cadenceOkCount} / ${trackedEntries.length}`}
              tone={cadenceOkCount === trackedEntries.length ? "green" : "fg"}
            />
          </div>
          <div data-testid="kpi-item">
            <KpiCell
              label="Overdue"
              value={String(overdueCount)}
              tone={overdueCount > 0 ? "amber" : "fg"}
            />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Panel 2: Tier Distribution (span 2)
// ---------------------------------------------------------------------------

interface TierDistributionProps {
  ranking: DunbarRankingResponse | undefined;
  isLoading: boolean;
  isError: boolean;
}

function TierDistributionPanel({ ranking, isLoading, isError }: TierDistributionProps) {
  if (isLoading && !ranking) {
    return <LoadingRows count={4} />;
  }

  if (isError) {
    return <ErrorLine>Could not load tier distribution.</ErrorLine>;
  }

  if (!ranking || ranking.entries.length === 0) {
    return <EmptyStateLine>No tier data available.</EmptyStateLine>;
  }

  const byTier = new Map<number, DunbarEntry[]>();
  for (const e of ranking.entries) {
    if (!byTier.has(e.dunbar_tier)) byTier.set(e.dunbar_tier, []);
    byTier.get(e.dunbar_tier)!.push(e);
  }

  return (
    <ul className="space-y-3" data-testid="tier-distribution-list">
      {TRACKED_TIERS.map((tier) => {
        const members = byTier.get(tier) ?? [];
        if (members.length === 0) return null;
        const meta = TIER_META[tier];
        const scoredMembers = members.filter((m) => m.warmth != null);
        const avgWarmth =
          scoredMembers.length > 0
            ? scoredMembers.reduce((s, m) => s + m.warmth!, 0) / scoredMembers.length
            : null;

        return (
          <li key={tier} className="space-y-1" data-testid="tier-distribution-row">
            <div className="flex items-center justify-between text-xs">
              <span className="font-medium text-foreground">{meta.label}</span>
              <span className="tnum text-muted-foreground">
                {members.length} · {fmtWarmth(avgWarmth)}
              </span>
            </div>
            {/* Warmth bar */}
            <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{ width: warmthBarWidth(avgWarmth) }}
                aria-label={`Warmth: ${fmtWarmth(avgWarmth)}`}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Panel 3: Overdue (span 2)
// ---------------------------------------------------------------------------

interface OverduePanelProps {
  contacts: OverdueContact[];
  isLoading: boolean;
  isError: boolean;
}

function OverduePanel({ contacts, isLoading, isError }: OverduePanelProps) {
  if (isLoading && contacts.length === 0) {
    return <LoadingRows count={3} />;
  }

  if (isError) {
    return <ErrorLine>Could not load overdue contacts.</ErrorLine>;
  }

  if (contacts.length === 0) {
    return <EmptyStateLine>No overdue contacts. Cadence all clear.</EmptyStateLine>;
  }

  // Sort descending by owed_days (most overdue first)
  const sorted = [...contacts].sort((a, b) => b.owed_days - a.owed_days);

  return (
    <ul className="space-y-2" data-testid="overdue-list">
      {sorted.map((c) => (
        <li
          key={c.contact_id}
          className="flex items-center justify-between gap-2 text-sm"
          data-testid="overdue-row"
        >
          <span className="truncate font-medium">{c.name}</span>
          <Badge
            variant={c.owed_days > 30 ? "destructive" : "outline"}
            className="shrink-0 tnum text-xs"
          >
            {c.owed_days}d overdue
          </Badge>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Panel 4: Watchlist T1+T2 (span 4, scrollable)
// ---------------------------------------------------------------------------

interface WatchlistPanelProps {
  ranking: DunbarRankingResponse | undefined;
  isLoading: boolean;
  isError: boolean;
  selectedContactId: string | null;
  onSelectContact: (id: string, name: string) => void;
}

function WatchlistPanel({ ranking, isLoading, isError, selectedContactId, onSelectContact }: WatchlistPanelProps) {
  if (isLoading && !ranking) {
    return (
      <div className="space-y-2" data-testid="watchlist-loading">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="flex items-center gap-3" data-testid="loading-line">
            <Skeleton className="h-3 w-32 rounded" />
            <Skeleton className="h-3 w-16 rounded" />
            <Skeleton className="h-3 w-20 rounded" />
          </div>
        ))}
      </div>
    );
  }

  if (isError) {
    return <ErrorLine>Could not load watchlist.</ErrorLine>;
  }

  const entries = (ranking?.entries ?? []).filter((e) =>
    e.dunbar_tier === 5 || e.dunbar_tier === 15,
  );

  if (entries.length === 0) {
    return <EmptyStateLine>No T1 or T2 contacts yet.</EmptyStateLine>;
  }

  // Sort by warmth desc (null last)
  const sorted = [...entries].sort((a, b) => {
    const wa = a.warmth ?? -1;
    const wb = b.warmth ?? -1;
    return wb - wa;
  });

  return (
    <div className="overflow-x-auto max-h-[320px] overflow-y-auto" data-testid="watchlist">
      <table className="w-full text-sm" data-testid="watchlist-table">
        <thead className="sticky top-0 bg-card">
          <tr className="border-b text-xs text-muted-foreground">
            <th className="py-1.5 pr-4 text-left font-medium">Name</th>
            <th className="py-1.5 pr-4 text-left font-medium">Tier</th>
            <th className="py-1.5 pr-4 text-left font-medium">Last contact</th>
            <th className="py-1.5 text-right font-medium tnum">Warmth</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {sorted.map((entry) => {
            const isSelected = entry.contact_id === selectedContactId;
            return (
              <tr
                key={entry.contact_id}
                data-testid="watchlist-row"
                role="button"
                tabIndex={0}
                className={`cursor-pointer hover:bg-muted/50 transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring ${isSelected ? "bg-muted" : ""}`}
                onClick={() => onSelectContact(entry.contact_id, entry.canonical_name)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelectContact(entry.contact_id, entry.canonical_name);
                  }
                }}
              >
                <td className="py-2 pr-4 font-medium truncate max-w-[180px]">
                  {entry.canonical_name}
                  {entry.dunbar_tier_override && (
                    <span className="ml-1 text-muted-foreground" title="Manually pinned">★</span>
                  )}
                </td>
                <td className="py-2 pr-4 text-xs text-muted-foreground">
                  {TIER_META[entry.dunbar_tier]?.label ?? `T${entry.dunbar_tier}`}
                </td>
                <td
                  className="py-2 pr-4 text-xs text-muted-foreground tnum"
                  data-testid="watchlist-last-contact"
                >
                  {relativeDate(entry.last_interaction_at)}
                </td>
                <td className="py-2 text-right tnum font-mono text-xs">
                  {fmtWarmth(entry.warmth)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel 5: Selected thread (span 3)
// ---------------------------------------------------------------------------

interface ThreadPanelProps {
  contactId: string | null;
  contactName: string | null;
  isLoading: boolean;
  isError: boolean;
  interactions: ContactInteraction[];
}

const DIRECTION_META: Record<ContactInteraction["direction"], { label: string; tone: string }> = {
  in:      { label: "In",    tone: "text-primary"          },
  out:     { label: "Out",   tone: "text-emerald-500"      },
  drafted: { label: "Draft", tone: "text-amber-500"        },
};

function ThreadPanel({ contactId, contactName, isLoading, isError, interactions }: ThreadPanelProps) {
  if (!contactId) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="thread-empty-prompt">
        Select a contact from the watchlist above to see their recent messages.
      </p>
    );
  }

  if (isLoading) {
    return <LoadingRows count={4} />;
  }

  if (isError) {
    return <ErrorLine>Could not load thread.</ErrorLine>;
  }

  if (interactions.length === 0) {
    return <EmptyStateLine>No recorded interactions with {contactName ?? contactId}.</EmptyStateLine>;
  }

  return (
    <ol className="space-y-3" data-testid="thread-list" aria-label={`Interactions with ${contactName}`}>
      {interactions.map((ix) => {
        const meta = DIRECTION_META[ix.direction] ?? { label: ix.direction, tone: "text-muted-foreground" };
        return (
          <li key={`${ix.ts}:${ix.direction}`} className="flex gap-2 text-sm" data-testid="thread-item">
            <span className={`shrink-0 font-mono text-xs tnum ${meta.tone}`}>
              {meta.label}
            </span>
            <div className="min-w-0">
              <p className="text-xs text-muted-foreground tnum">
                {new Date(ix.ts).toLocaleDateString()}
              </p>
              <p className="truncate text-sm leading-snug">{ix.text}</p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

// ---------------------------------------------------------------------------
// Panel 6: Known facts (span 1)
// ---------------------------------------------------------------------------

interface KnownFactsPanelProps {
  contact: ContactDetail | undefined;
  contactName: string | null;
}

function KnownFactsPanel({ contact, contactName }: KnownFactsPanelProps) {
  if (!contactName) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="facts-empty-prompt">
        Select a contact to see facts.
      </p>
    );
  }

  const facts: string[] = [];
  if (contact?.email) facts.push(`Email: ${contact.email}`);
  if (contact?.phone) facts.push(`Phone: ${contact.phone}`);
  if (contact?.labels?.length) {
    facts.push(`Labels: ${contact.labels.map((l) => l.name).join(", ")}`);
  }
  if (contact?.last_interaction_at) {
    facts.push(`Last seen: ${relativeDate(contact.last_interaction_at)}`);
  }

  if (facts.length === 0) {
    return <EmptyStateLine>No facts recorded yet.</EmptyStateLine>;
  }

  return (
    <ul className="space-y-1.5" data-testid="known-facts-list">
      {facts.map((fact, i) => (
        <li key={i} className="text-xs text-foreground" data-testid="known-fact-item">
          {fact}
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Main tab component
// ---------------------------------------------------------------------------

export default function ButlerRelationshipContactsTab() {
  const [selectedContactId, setSelectedContactId] = useState<string | null>(null);
  const [selectedContactName, setSelectedContactName] = useState<string | null>(null);

  // --- Panel 1 + 2 + 4: Dunbar ranking (tier distribution, watchlist, KPI warmth)
  const { data: dunbarData, isLoading: dunbarLoading, isError: dunbarError } = useDunbarRanking(true);

  // --- Panel 1: KPI — total contacts count
  const { data: contactsData, isLoading: contactsLoading, isError: contactsError } = useContacts({ limit: 1 });

  // --- Panel 3: Overdue contacts
  const { data: overdueData, isLoading: overdueLoading, isError: overdueError } = useOverdueContacts(14);

  // --- Panel 5: Interaction thread for selected contact
  const { data: interactionsData, isLoading: interactionsLoading, isError: interactionsError } = useContactInteractions(
    selectedContactId ?? undefined,
    4,
  );

  // --- Panel 6: Facts for selected contact (fetch single record; enabled only when selected)
  const { data: selectedContact } = useContact(selectedContactId ?? undefined);

  const overdueContacts = overdueData?.contacts ?? [];
  const interactions = interactionsData?.interactions ?? [];

  function handleSelectContact(id: string, name: string) {
    setSelectedContactId((prev) => (prev === id ? null : id));
    setSelectedContactName((prev) => (prev === name ? null : name));
  }

  const totalContacts = contactsData?.total ?? 0;
  const kpiLoading = dunbarLoading || contactsLoading || overdueLoading;
  const kpiError = dunbarError || contactsError || overdueError;

  return (
    <div className="space-y-6" data-testid="relationship-contacts-tab">
      {/* Panel 1: KPI strip — full width */}
      <RelationshipKpiStrip
        ranking={dunbarData}
        overdueCount={overdueContacts.length}
        totalContacts={totalContacts}
        isLoading={kpiLoading}
        isError={kpiError}
      />

      {/* Panels 2–3: Tier distribution (2col) + Overdue (2col) */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        <Card className="lg:col-span-2" data-testid="tier-distribution-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Tier distribution</CardTitle>
          </CardHeader>
          <CardContent>
            <TierDistributionPanel ranking={dunbarData} isLoading={dunbarLoading} isError={dunbarError} />
          </CardContent>
        </Card>

        <Card className="lg:col-span-2" data-testid="overdue-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Overdue · 14d threshold</CardTitle>
          </CardHeader>
          <CardContent>
            <OverduePanel contacts={overdueContacts} isLoading={overdueLoading} isError={overdueError} />
          </CardContent>
        </Card>
      </div>

      {/* Panel 4: Watchlist T1+T2 — full width, scrollable */}
      <Card data-testid="watchlist-card">
        <CardHeader>
          <CardTitle className="text-sm font-medium">Watchlist · T1 + T2</CardTitle>
        </CardHeader>
        <CardContent>
          <WatchlistPanel
            ranking={dunbarData}
            isLoading={dunbarLoading}
            isError={dunbarError}
            selectedContactId={selectedContactId}
            onSelectContact={handleSelectContact}
          />
        </CardContent>
      </Card>

      {/* Panels 5–6: Selected thread (3col) + Known facts (1col) */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        <Card className="lg:col-span-3" data-testid="thread-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">
              {selectedContactName
                ? `Thread · ${selectedContactName}`
                : "Thread · select a contact"}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ThreadPanel
              contactId={selectedContactId}
              contactName={selectedContactName}
              isLoading={interactionsLoading}
              isError={interactionsError}
              interactions={interactions}
            />
          </CardContent>
        </Card>

        <Card className="lg:col-span-1" data-testid="known-facts-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Known facts</CardTitle>
          </CardHeader>
          <CardContent>
            <KnownFactsPanel contact={selectedContact} contactName={selectedContactName} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
