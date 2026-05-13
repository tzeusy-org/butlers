import { lazy, Suspense, useCallback, useState, type ComponentProps } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useParams, useSearchParams } from "react-router";

import ButlerConfigTab from "@/components/butler-detail/ButlerConfigTab";
import { ButlerDetailActions } from "@/components/butler-detail/ButlerDetailActions";
import { ButlerDetailHeader } from "@/components/butler-detail/ButlerDetailHeader";
import ButlerOverviewTab from "@/components/butler-detail/ButlerOverviewTab";
import ButlerSessionsTab from "@/components/butler-detail/ButlerSessionsTab";
import ButlerCrmTab from "@/components/butler-detail/ButlerCrmTab";
import { Page } from "@/components/ui/page";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useButler } from "@/hooks/use-butlers";
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

// Lifestyle butler tabs (lazy)
const ButlerLifestyleTasteTab = lazy(
  () => import("@/components/butler-detail/ButlerLifestyleTasteTab.tsx"),
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

// Messenger butler tabs (lazy)
const ButlerMessengerConversationsTab = lazy(
  () => import("@/components/butler-detail/ButlerMessengerConversationsTab.tsx"),
);

// General butler tabs (lazy)
const ButlerGeneralCollectionsTab = lazy(
  () => import("@/components/butler-detail/ButlerGeneralCollectionsTab.tsx"),
);

// Resident-mode core tabs (lazy)
const ButlerLogsTab = lazy(
  () => import("@/components/butler-detail/ButlerLogsTab.tsx"),
);

// Activity tab — replaces stub (bu-iuol4.16)
const ButlerActivityTab = lazy(
  () => import("@/components/butler-detail/ButlerActivityTab.tsx"),
);

// Spend tab (lazy) — bu-iuol4.19
const ButlerSpendTab = lazy(
  () => import("@/components/butler-detail/ButlerSpendTab.tsx"),
);

const detailTabTriggerClassName =
  "h-auto flex-none rounded-none px-3 py-2 font-mono text-[11px] font-medium uppercase tracking-[0.10em] " +
  "data-[state=active]:border-transparent data-[state=active]:bg-transparent " +
  "dark:data-[state=active]:border-transparent dark:data-[state=active]:bg-transparent";

function DetailTabTrigger({
  className,
  ...props
}: ComponentProps<typeof TabsTrigger>) {
  return (
    <TabsTrigger
      className={[detailTabTriggerClassName, className].filter(Boolean).join(" ")}
      {...props}
    />
  );
}

// ---------------------------------------------------------------------------
// Page-local constants
// ---------------------------------------------------------------------------

/** localStorage key for persisting the detail page mode. */
const MODE_STORAGE_KEY = "butlers.detail.mode";

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

  const showCollectionsTab = name === "general";
  const showHealthTab = name === "health";
  const showReviewsTab = name === "education";
  const showTimelinesTab = name === "chronicler";
  const showFinancesTab = name === "finance";
  const showDevicesTab = name === "home";
  const showTasteTab = name === "lifestyle";
  const showConversationsTab = name === "messenger";
  const showInvestigationsTab = name === "qa";
  const showContactsTab = name === "relationship";
  const showTripsTab = name === "travel";

  // Extract description from butler response (ButlerSummary.description is optional)
  const description = butlerResponse?.data?.description ?? undefined;

  return (
    <Page
      archetype="status-board"
      title={titleize(name)}
      description={description}
      loading={butlerLoading}
      error={butlerError}
      onRetry={handleRetry}
      header={
        <ButlerDetailHeader
          butler={name}
          actions={<ButlerDetailActions butlerName={name} mode={mode} onModeChange={setMode} />}
        />
      }
    >
        <Tabs value={activeTab} onValueChange={handleTabChange}>
          <TabsList
            variant="line"
            className="h-auto w-full justify-start rounded-none border-b border-border bg-transparent p-0"
          >
            <DetailTabTrigger value="overview">Overview</DetailTabTrigger>
            {mode === "operator" && (
              <>
                <DetailTabTrigger value="sessions">Sessions</DetailTabTrigger>
              </>
            )}
            {mode === "resident" && (
              <>
                <DetailTabTrigger value="activity">Activity</DetailTabTrigger>
                <DetailTabTrigger value="logs">Logs</DetailTabTrigger>
                <DetailTabTrigger value="approvals">Approvals</DetailTabTrigger>
                <DetailTabTrigger value="spend">Spend</DetailTabTrigger>
              </>
            )}
            <DetailTabTrigger value="config">Config</DetailTabTrigger>
            {mode === "operator" && (
              <>
                <DetailTabTrigger value="skills">Skills</DetailTabTrigger>
                <DetailTabTrigger value="schedules">Schedules</DetailTabTrigger>
                <DetailTabTrigger value="trigger">Trigger</DetailTabTrigger>
                <DetailTabTrigger value="mcp">MCP</DetailTabTrigger>
                <DetailTabTrigger value="state">State</DetailTabTrigger>
                <DetailTabTrigger value="crm">CRM</DetailTabTrigger>
              </>
            )}
            <DetailTabTrigger value="memory">Memory</DetailTabTrigger>
            {mode === "operator" && (
              <DetailTabTrigger value="models">Models</DetailTabTrigger>
            )}
            {showCollectionsTab && (
              <DetailTabTrigger value="collections">Collections</DetailTabTrigger>
            )}
            {showHealthTab && <DetailTabTrigger value="health">Health</DetailTabTrigger>}
            {isSwitchboard && (
              <>
                <DetailTabTrigger value="routing-log">Routing Log</DetailTabTrigger>
                <DetailTabTrigger value="registry">Registry</DetailTabTrigger>
              </>
            )}
            {showReviewsTab && <DetailTabTrigger value="reviews">Reviews</DetailTabTrigger>}
            {showTimelinesTab && <DetailTabTrigger value="timelines">Timelines</DetailTabTrigger>}
            {showFinancesTab && <DetailTabTrigger value="finances">Finances</DetailTabTrigger>}
            {showDevicesTab && <DetailTabTrigger value="devices">Devices</DetailTabTrigger>}
            {showTasteTab && <DetailTabTrigger value="taste">Taste</DetailTabTrigger>}
            {showConversationsTab && (
              <DetailTabTrigger value="conversations">Conversations</DetailTabTrigger>
            )}
            {showInvestigationsTab && (
              <DetailTabTrigger value="investigations">Investigations</DetailTabTrigger>
            )}
            {showContactsTab && <DetailTabTrigger value="contacts">Contacts</DetailTabTrigger>}
            {showTripsTab && <DetailTabTrigger value="trips">Trips</DetailTabTrigger>}
          </TabsList>

          <TabsContent value="overview">
            <ButlerOverviewTab butlerName={name} />
          </TabsContent>

          <TabsContent value="sessions">
            <ButlerSessionsTab butlerName={name} />
          </TabsContent>

          {/* Resident-mode tabs */}
          <TabsContent value="activity">
            <Suspense fallback={<TabFallback label="activity" />}>
              <ButlerActivityTab butlerName={name} />
            </Suspense>
          </TabsContent>

          <TabsContent value="logs">
            <Suspense fallback={<TabFallback label="logs" />}>
              <ButlerLogsTab butlerName={name} />
            </Suspense>
          </TabsContent>

          <TabsContent value="approvals">
            <Suspense fallback={<Skeleton className="h-[calc(100vh-18rem)] w-full" />}>
              <ButlerApprovalsTab butlerName={name} />
            </Suspense>
          </TabsContent>

          <TabsContent value="spend">
            <Suspense fallback={<TabFallback label="spend" />}>
              <ButlerSpendTab butlerName={name} />
            </Suspense>
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

          {showCollectionsTab && (
            <TabsContent value="collections">
              <Suspense fallback={<TabFallback label="collections" />}>
                <ButlerGeneralCollectionsTab />
              </Suspense>
            </TabsContent>
          )}

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

          {showTasteTab && (
            <TabsContent value="taste">
              <Suspense fallback={<Skeleton className="h-64 w-full rounded-lg" />}>
                <ButlerLifestyleTasteTab />
              </Suspense>
            </TabsContent>
          )}

          {showConversationsTab && (
            <TabsContent value="conversations">
              <Suspense fallback={<Skeleton className="h-64 w-full rounded-lg" />}>
                <ButlerMessengerConversationsTab />
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
    </Page>
  );
}
