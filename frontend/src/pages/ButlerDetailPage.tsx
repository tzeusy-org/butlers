import { lazy, Suspense, useCallback, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router";

import type { SessionParams, SessionSummary } from "@/api/types";
import ButlerConfigTab from "@/components/butler-detail/ButlerConfigTab";
import { ButlerDetailActions } from "@/components/butler-detail/ButlerDetailActions";
import ButlerOverviewTab from "@/components/butler-detail/ButlerOverviewTab";
import { ButlerHeartbeatTile } from "@/components/system/ButlerHeartbeatTile";
import { SessionDetailDrawer } from "@/components/sessions/SessionDetailDrawer";
import { SessionTable } from "@/components/sessions/SessionTable";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { DetailPage } from "@/components/layout/DetailPage";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useButler } from "@/hooks/use-butlers";
import { useButlerSessions } from "@/hooks/use-sessions";
import { useUpcomingDates } from "@/hooks/use-contacts";
import { titleize } from "@/lib/utils";
import {
  type DetailMode,
  type TabValue,
  getAllTabs,
  isValidTab,
} from "@/pages/butler-detail-tabs";

// ---------------------------------------------------------------------------
// Lazy-loaded tabs
// ---------------------------------------------------------------------------

const ButlerSchedulesTab = lazy(
  () => import("@/components/butler-detail/ButlerSchedulesTab.tsx"),
);
const ButlerSkillsTab = lazy(
  () => import("@/components/butler-detail/ButlerSkillsTab.tsx"),
);
const ButlerTriggerTab = lazy(
  () => import("@/components/butler-detail/ButlerTriggerTab.tsx"),
);
const ButlerMcpTab = lazy(
  () => import("@/components/butler-detail/ButlerMcpTab.tsx"),
);
const ButlerStateTab = lazy(
  () => import("@/components/butler-detail/ButlerStateTab.tsx"),
);

// Switchboard butler tabs (lazy)
const ButlerMemoryTab = lazy(
  () => import("@/components/butler-detail/ButlerMemoryTab.tsx"),
);

// Travel butler tabs (lazy)
const ButlerTravelTripsTab = lazy(
  () => import("@/components/butler-detail/ButlerTravelTripsTab.tsx"),
);

// Education butler tabs (lazy)
const ButlerEducationReviewsTab = lazy(
  () => import("@/components/butler-detail/ButlerEducationReviewsTab.tsx"),
);

// Finance butler tabs (lazy)
const ButlerFinanceFinancesTab = lazy(
  () => import("@/components/butler-detail/ButlerFinanceFinancesTab.tsx"),
);

const ButlerRoutingLogTab = lazy(
  () => import("@/components/butler-detail/ButlerRoutingLogTab.tsx"),
);
const ButlerRegistryTab = lazy(
  () => import("@/components/butler-detail/ButlerRegistryTab.tsx"),
);
const ButlerModelOverridesTab = lazy(
  () => import("@/components/butler-detail/ButlerModelOverridesTab.tsx"),
);

// Chronicler butler tabs (lazy)
const ButlerChroniclerTimelinesTab = lazy(
  () => import("@/components/butler-detail/ButlerChroniclerTimelinesTab.tsx"),
);

// Relationship butler tabs (lazy)
const ButlerRelationshipContactsTab = lazy(
  () => import("@/components/butler-detail/ButlerRelationshipContactsTab.tsx"),
);

// Home butler tabs (lazy)
const ButlerHomeDevicesTab = lazy(
  () => import("@/components/butler-detail/ButlerHomeDevicesTab.tsx"),
);

// Resident-mode tabs (lazy)
const ButlerApprovalsTab = lazy(
  () => import("@/components/butler-detail/ButlerApprovalsTab.tsx"),
);

// Health butler tabs (lazy)
const ButlerHealthMeasurementsTab = lazy(
  () => import("@/components/butler-detail/ButlerHealthMeasurementsTab.tsx"),
);

// QA butler tabs (lazy)
const ButlerQaInvestigationsTab = lazy(
  () => import("@/components/butler-detail/ButlerQaInvestigationsTab.tsx"),
);

// ---------------------------------------------------------------------------
// Page-local constants
// ---------------------------------------------------------------------------

/** localStorage key for persisting the detail page mode. */
const MODE_STORAGE_KEY = "butlers.detail.mode";

const PAGE_SIZE = 20;

/**
 * Reads the persisted mode from localStorage, defaulting to "resident".
 */
function readPersistedMode(): DetailMode {
  try {
    const stored = localStorage.getItem(MODE_STORAGE_KEY);
    if (stored === "operator" || stored === "resident") return stored;
  } catch {
    // localStorage not available (e.g. SSR or private browsing restrictions)
  }
  return "resident";
}

/**
 * Writes the mode to localStorage.
 */
function persistMode(mode: DetailMode): void {
  try {
    localStorage.setItem(MODE_STORAGE_KEY, mode);
  } catch {
    // Ignore write failures
  }
}

// ---------------------------------------------------------------------------
// Suspense fallback
// ---------------------------------------------------------------------------

function TabFallback({ label }: { label: string }) {
  return (
    <div className="text-muted-foreground flex items-center justify-center py-12 text-sm">
      Loading {label}...
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sessions Tab sub-component
// ---------------------------------------------------------------------------

function ButlerSessionsTab({ butlerName }: { butlerName: string }) {
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
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Sessions</CardTitle>
          <CardDescription>Session history for this butler</CardDescription>
        </CardHeader>
        <CardContent>
          <SessionTable
            sessions={sessions}
            isLoading={isLoading}
            onSessionClick={handleSessionClick}
            showButlerColumn={false}
          />
        </CardContent>
      </Card>

      {/* Pagination controls */}
      {total > 0 && (
        <div className="flex items-center justify-between">
          <p className="text-muted-foreground text-sm">
            Page {currentPage} of {totalPages}
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!hasMore}
              onClick={() => setPage((p) => p + 1)}
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

// ---------------------------------------------------------------------------
// CRM Tab sub-component
// ---------------------------------------------------------------------------

function ButlerCrmTab({ butlerName }: { butlerName: string }) {
  const isRelationship = butlerName === "relationship";
  const { data: upcomingDates, isLoading } = useUpcomingDates(
    isRelationship ? 30 : undefined,
  );

  if (!isRelationship) {
    return (
      <Card>
        <CardContent className="py-12">
          <p className="text-muted-foreground text-center text-sm">
            CRM features are only available for the relationship butler.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      {/* Upcoming dates widget */}
      <Card>
        <CardHeader>
          <CardTitle>Upcoming Dates</CardTitle>
          <CardDescription>
            Birthdays, anniversaries, and other important dates in the next 30 days
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 3 }, (_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : !upcomingDates || upcomingDates.length === 0 ? (
            <p className="text-muted-foreground py-6 text-center text-sm">
              No upcoming dates in the next 30 days.
            </p>
          ) : (
            <div className="space-y-2">
              {upcomingDates.map((item, idx) => (
                <div
                  key={`${item.contact_id}-${item.date_type}-${idx}`}
                  className="flex items-center justify-between rounded-md border px-3 py-2"
                >
                  <div className="flex items-center gap-3">
                    <Badge variant="outline" className="text-xs">
                      {item.date_type}
                    </Badge>
                    <Link
                      to={`/contacts/${item.contact_id}`}
                      className="text-sm font-medium hover:underline"
                    >
                      {item.contact_name}
                    </Link>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-muted-foreground text-sm">{item.date}</span>
                    <Badge
                      variant={item.days_until <= 3 ? "destructive" : "secondary"}
                      className="text-xs"
                    >
                      {item.days_until === 0
                        ? "Today"
                        : item.days_until === 1
                          ? "Tomorrow"
                          : `${item.days_until} days`}
                    </Badge>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Quick links */}
      <Card>
        <CardHeader>
          <CardTitle>Quick Links</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex gap-3">
            <Button variant="outline" asChild>
              <Link to="/contacts">Contacts</Link>
            </Button>
            <Button variant="outline" asChild>
              <Link to="/groups">Groups</Link>
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// (ButlerHealthTab removed — replaced by lazy ButlerHealthMeasurementsTab)

// ---------------------------------------------------------------------------
// ButlerDetailPage
// ---------------------------------------------------------------------------

export default function ButlerDetailPage() {
  const { name = "" } = useParams<{ name: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const { data: butlerResponse, isLoading: butlerLoading, error: butlerError } = useButler(name);
  const queryClient = useQueryClient();
  const handleRetry = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["butlers", name], exact: true });
  }, [queryClient, name]);

  const tabParam = searchParams.get("tab");

  // ---------------------------------------------------------------------------
  // Mode — operator vs resident
  // Initialised from localStorage; defaults to "resident".
  //
  // Deep-link auto-promotion (Spec Decision 6): if the URL tab param names an
  // operator-only tab, the initial mode is immediately promoted to "operator"
  // and persisted. This happens synchronously during state initialisation so
  // that the correct tab list is rendered on the first pass (works in both
  // CSR and SSR rendering contexts where effects don't run).
  // ---------------------------------------------------------------------------
  const [mode, setModeState] = useState<DetailMode>(() => {
    const stored = readPersistedMode();
    // Auto-promote bidirectionally: if the URL tab is exclusive to a different mode, switch now.
    // This runs synchronously in state initialisation so the correct tab list renders on the
    // first pass (works in CSR and SSR contexts where effects don't run).
    if (tabParam) {
      const validForOperator = getAllTabs(name, "operator").includes(tabParam);
      const validForResident = getAllTabs(name, "resident").includes(tabParam);
      if (validForOperator && !validForResident && stored !== "operator") {
        // Forward promotion: resident → operator
        persistMode("operator");
        return "operator";
      }
      if (validForResident && !validForOperator && stored !== "resident") {
        // Reverse promotion: operator → resident
        persistMode("resident");
        return "resident";
      }
    }
    return stored;
  });

  const setMode = useCallback(
    (next: DetailMode) => {
      setModeState(next);
      persistMode(next);
      // If the current tab is not valid in the target mode, reset to overview
      // so the URL doesn't carry a stale tab param that would auto-promote
      // mode back on the next page load.
      const currentTab = searchParams.get("tab");
      if (currentTab && !isValidTab(currentTab, name, next)) {
        setSearchParams({}, { replace: true });
      }
    },
    [name, searchParams, setSearchParams],
  );

  const activeTab: TabValue = isValidTab(tabParam, name, mode) ? tabParam : "overview";

  const isSwitchboard = name === "switchboard";

  function handleTabChange(value: string) {
    if (value === "overview") {
      // Remove tab param for the default tab to keep URLs clean
      setSearchParams({}, { replace: true });
    } else {
      setSearchParams({ tab: value }, { replace: true });
    }
  }

  const showHealthTab = name === "health";
  const showReviewsTab = name === "education";
  const showTimelinesTab = name === "chronicler";
  const showFinancesTab = name === "finance";
  const showDevicesTab = name === "home";
  const showInvestigationsTab = name === "qa";
  const showContactsTab = name === "relationship";
  const showTripsTab = name === "travel";

  // Extract description from butler response (ButlerSummary.description is optional)
  const description = butlerResponse?.data?.description ?? undefined;

  const breadcrumbs = useMemo(
    () => [
      { label: "Overview", href: "/" },
      { label: "Butlers", href: "/butlers" },
      { label: name },
    ],
    [name],
  );

  return (
    <DetailPage
      record={{ title: titleize(name), subtitle: description }}
      breadcrumbs={breadcrumbs}
      actions={<ButlerDetailActions butlerName={name} mode={mode} onModeChange={setMode} />}
      loading={butlerLoading}
      error={butlerError}
      onRetry={handleRetry}
      pulse={<ButlerHeartbeatTile />}
      primary={
        <Tabs value={activeTab} onValueChange={handleTabChange}>
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            {mode === "operator" && (
              <>
                <TabsTrigger value="sessions">Sessions</TabsTrigger>
              </>
            )}
            {mode === "resident" && (
              <>
                <TabsTrigger value="activity">Activity</TabsTrigger>
                <TabsTrigger value="logs">Logs</TabsTrigger>
                <TabsTrigger value="approvals">Approvals</TabsTrigger>
                <TabsTrigger value="spend">Spend</TabsTrigger>
              </>
            )}
            <TabsTrigger value="config">Config</TabsTrigger>
            {mode === "operator" && (
              <>
                <TabsTrigger value="skills">Skills</TabsTrigger>
                <TabsTrigger value="schedules">Schedules</TabsTrigger>
                <TabsTrigger value="trigger">Trigger</TabsTrigger>
                <TabsTrigger value="mcp">MCP</TabsTrigger>
                <TabsTrigger value="state">State</TabsTrigger>
                <TabsTrigger value="crm">CRM</TabsTrigger>
              </>
            )}
            <TabsTrigger value="memory">Memory</TabsTrigger>
            {mode === "operator" && (
              <TabsTrigger value="models">Models</TabsTrigger>
            )}
            {showHealthTab && <TabsTrigger value="health">Health</TabsTrigger>}
            {isSwitchboard && (
              <>
                <TabsTrigger value="routing-log">Routing Log</TabsTrigger>
                <TabsTrigger value="registry">Registry</TabsTrigger>
              </>
            )}
            {showReviewsTab && <TabsTrigger value="reviews">Reviews</TabsTrigger>}
            {showTimelinesTab && <TabsTrigger value="timelines">Timelines</TabsTrigger>}
            {showFinancesTab && <TabsTrigger value="finances">Finances</TabsTrigger>}
            {showDevicesTab && <TabsTrigger value="devices">Devices</TabsTrigger>}
            {showInvestigationsTab && (
              <TabsTrigger value="investigations">Investigations</TabsTrigger>
            )}
            {showContactsTab && <TabsTrigger value="contacts">Contacts</TabsTrigger>}
            {showTripsTab && <TabsTrigger value="trips">Trips</TabsTrigger>}
          </TabsList>

          <TabsContent value="overview">
            <ButlerOverviewTab butlerName={name} />
          </TabsContent>

          <TabsContent value="sessions">
            <ButlerSessionsTab butlerName={name} />
          </TabsContent>

          {/* Resident-mode tabs — vocabulary stubs, not yet implemented */}
          <TabsContent value="activity">
            <Card>
              <CardContent className="py-12">
                <p className="text-muted-foreground text-center text-sm">
                  Activity view coming soon.
                </p>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="logs">
            <Card>
              <CardContent className="py-12">
                <p className="text-muted-foreground text-center text-sm">
                  Logs view coming soon.
                </p>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="approvals">
            <Suspense fallback={<Skeleton className="h-[calc(100vh-18rem)] w-full" />}>
              <ButlerApprovalsTab butlerName={name} />
            </Suspense>
          </TabsContent>

          <TabsContent value="spend">
            <Card>
              <CardContent className="py-12">
                <p className="text-muted-foreground text-center text-sm">
                  Spend view coming soon.
                </p>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="config">
            <ButlerConfigTab butlerName={name} />
          </TabsContent>

          <TabsContent value="skills">
            <Suspense fallback={<TabFallback label="skills" />}>
              <ButlerSkillsTab butlerName={name} />
            </Suspense>
          </TabsContent>

          <TabsContent value="schedules">
            <Suspense fallback={<TabFallback label="schedules" />}>
              <ButlerSchedulesTab butlerName={name} />
            </Suspense>
          </TabsContent>

          <TabsContent value="trigger">
            <Suspense fallback={<TabFallback label="trigger" />}>
              <ButlerTriggerTab butlerName={name} />
            </Suspense>
          </TabsContent>

          <TabsContent value="state">
            <Suspense fallback={<TabFallback label="state" />}>
              <ButlerStateTab butlerName={name} />
            </Suspense>
          </TabsContent>

          <TabsContent value="mcp">
            <Suspense fallback={<TabFallback label="mcp" />}>
              <ButlerMcpTab butlerName={name} />
            </Suspense>
          </TabsContent>

          <TabsContent value="crm">
            <ButlerCrmTab butlerName={name} />
          </TabsContent>

          <TabsContent value="memory">
            <Suspense fallback={<TabFallback label="memory" />}>
              <ButlerMemoryTab butlerName={name} />
            </Suspense>
          </TabsContent>

          <TabsContent value="models">
            <Suspense fallback={<TabFallback label="models" />}>
              <ButlerModelOverridesTab butlerName={name} />
            </Suspense>
          </TabsContent>

          {showHealthTab && (
            <TabsContent value="health">
              <Suspense fallback={<Skeleton className="h-[1000px] w-full rounded-lg" />}>
                <ButlerHealthMeasurementsTab />
              </Suspense>
            </TabsContent>
          )}

          {isSwitchboard && (
            <>
              <TabsContent value="routing-log">
                <Suspense fallback={<TabFallback label="routing log" />}>
                  <ButlerRoutingLogTab />
                </Suspense>
              </TabsContent>
              <TabsContent value="registry">
                <Suspense fallback={<TabFallback label="registry" />}>
                  <ButlerRegistryTab />
                </Suspense>
              </TabsContent>
            </>
          )}

          {showReviewsTab && (
            <TabsContent value="reviews">
              <Suspense fallback={<TabFallback label="reviews" />}>
                <ButlerEducationReviewsTab />
              </Suspense>
            </TabsContent>
          )}

          {showTimelinesTab && (
            <TabsContent value="timelines">
              <Suspense fallback={<Skeleton className="h-64 w-full rounded-lg" />}>
                <ButlerChroniclerTimelinesTab />
              </Suspense>
            </TabsContent>
          )}

          {showFinancesTab && (
            <TabsContent value="finances">
              <Suspense fallback={<TabFallback label="finances" />}>
                <ButlerFinanceFinancesTab />
              </Suspense>
            </TabsContent>
          )}

          {showDevicesTab && (
            <TabsContent value="devices">
              <Suspense fallback={<Skeleton className="h-64 w-full rounded-lg" />}>
                <ButlerHomeDevicesTab />
              </Suspense>
            </TabsContent>
          )}

          {showInvestigationsTab && (
            <TabsContent value="investigations">
              <Suspense fallback={<TabFallback label="investigations" />}>
                <ButlerQaInvestigationsTab />
              </Suspense>
            </TabsContent>
          )}

          {showContactsTab && (
            <TabsContent value="contacts">
              <Suspense fallback={<Skeleton className="h-64 w-full rounded-lg" />}>
                <ButlerRelationshipContactsTab />
              </Suspense>
            </TabsContent>
          )}

          {showTripsTab && (
            <TabsContent value="trips">
              <Suspense fallback={<TabFallback label="trips" />}>
                <ButlerTravelTripsTab />
              </Suspense>
            </TabsContent>
          )}
        </Tabs>
      }
    />
  );
}
