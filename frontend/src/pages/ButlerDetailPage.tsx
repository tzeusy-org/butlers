import { lazy, Suspense, useCallback, useMemo, type ComponentProps } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useParams, useSearchParams } from "react-router";

import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry";
import { ButlerDetailActions } from "@/components/butler-detail/ButlerDetailActions";
import { ButlerDetailHeader } from "@/components/butler-detail/ButlerDetailHeader";
import ButlerOverviewTab from "@/components/butler-detail/ButlerOverviewTab";
import ButlerActivitySection from "@/components/butler-detail/ButlerActivitySection";
import ButlerSystemSection from "@/components/butler-detail/ButlerSystemSection";
import { Page } from "@/components/ui/page";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useButler } from "@/hooks/use-butlers";
import { useRegisterShortcut, type ShortcutBinding } from "@/hooks/use-register-shortcut";
import { titleize } from "@/lib/utils";
import { getAllTabs, type TabValue, isValidTab } from "@/pages/butler-detail-tabs";

// ---------------------------------------------------------------------------
// Lazy-loaded tabs
// ---------------------------------------------------------------------------

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

// Approvals tab (lazy)
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
const ButlerGeneralEntitiesTab = lazy(
  () => import("@/components/butler-detail/ButlerGeneralEntitiesTab.tsx"),
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
// ButlerDetailPage
//
// One butler console, one tab set (bu-86c4c.18 -- JARVIS audit move 13). The
// former resident/operator mode toggle, its localStorage persistence, and
// the deep-link auto-promotion machinery have all been deleted: every butler
// now shows exactly the same tab vocabulary --
//   Overview, Activity, Approvals, Spend, Memory, <domain tab>, System
// -- with Sessions/Logs folded into Activity and Config/Skills/Schedules/
// MCP/State/Models/Manage folded into System (see ButlerActivitySection and
// ButlerSystemSection). The CRM base tab (a dead panel on every butler
// except relationship, which already has a bespoke Contacts tab) and the
// Trigger tab (unified into the ButlerDetailActions command bar) are gone.
// ---------------------------------------------------------------------------

export default function ButlerDetailPage() {
  const { name = "" } = useParams<{ name: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const { data: butlerResponse, isLoading: butlerLoading, error: butlerError } = useButler(name);
  const queryClient = useQueryClient();
  const handleRetry = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["butlers", name], exact: true });
  }, [queryClient, name]);

  // Palette verb (bu-t64p2 -- reachability sweep, bu-qvnce.11 slice 5). Reuses
  // the Page shell's own onRetry handler -- not gated behind an error state,
  // since forcing a refetch is harmless at any time.
  const butlerDetailCommands = useMemo<PaletteCommand[]>(
    () => [
      {
        id: "butler-detail-reload",
        label: `Reload ${name || "butler"}`,
        keywords: ["refresh", "reload", "butler"],
        perform: handleRetry,
      },
    ],
    [name, handleRetry],
  );
  useRegisterCommands(butlerDetailCommands);

  const tabParam = searchParams.get("tab");
  const activeTab: TabValue = isValidTab(tabParam, name) ? tabParam : "overview";

  const isSwitchboard = name === "switchboard";

  const handleTabChange = useCallback((value: string) => {
    if (value === "overview") {
      // Remove tab param for the default tab to keep URLs clean
      setSearchParams({}, { replace: true });
    } else {
      setSearchParams({ tab: value }, { replace: true });
    }
  }, [setSearchParams]);

  const showCollectionsTab = name === "general";
  const showEntitiesTab = name === "general";
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

  // Keep shortcut order in lockstep with the visual rail: base tabs first,
  // then this butler's bespoke tabs, with System remaining the terminal tab.
  const visibleTabs = useMemo<TabValue[]>(() => {
    const configuredTabs = getAllTabs(name) as TabValue[];
    return [...configuredTabs.filter((tab) => tab !== "system"), "system"];
  }, [name]);

  const tabShortcuts = useMemo<ShortcutBinding[]>(() => {
    const currentIndex = Math.max(0, visibleTabs.indexOf(activeTab));
    const previousTab = visibleTabs[(currentIndex - 1 + visibleTabs.length) % visibleTabs.length];
    const nextTab = visibleTabs[(currentIndex + 1) % visibleTabs.length];

    return [
      ...visibleTabs.slice(0, 9).map((tab, index) => ({
        key: String(index + 1),
        display: [String(index + 1)],
        description: `Switch to ${titleize(tab)}`,
        handler: () => handleTabChange(tab),
      })),
      {
        key: "[",
        display: ["["],
        description: "Previous tab",
        handler: () => handleTabChange(previousTab),
      },
      {
        key: "]",
        display: ["]"],
        description: "Next tab",
        handler: () => handleTabChange(nextTab),
      },
    ];
  }, [activeTab, handleTabChange, visibleTabs]);
  useRegisterShortcut(tabShortcuts);

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
          actions={<ButlerDetailActions butlerName={name} />}
        />
      }
    >
        <Tabs value={activeTab} onValueChange={handleTabChange}>
          <TabsList
            variant="line"
            className="h-auto w-full justify-start rounded-none border-b border-border bg-transparent p-0"
          >
            <DetailTabTrigger value="overview">Overview</DetailTabTrigger>
            <DetailTabTrigger value="activity">Activity</DetailTabTrigger>
            <DetailTabTrigger value="approvals">Approvals</DetailTabTrigger>
            <DetailTabTrigger value="spend">Spend</DetailTabTrigger>
            <DetailTabTrigger value="memory">Memory</DetailTabTrigger>
            {showCollectionsTab && (
              <DetailTabTrigger value="collections">Collections</DetailTabTrigger>
            )}
            {showEntitiesTab && (
              <DetailTabTrigger value="entities">Entities</DetailTabTrigger>
            )}
            {showHealthTab && <DetailTabTrigger value="health">Measurements</DetailTabTrigger>}
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
            <DetailTabTrigger value="system">System</DetailTabTrigger>
          </TabsList>

          <TabsContent value="overview">
            <ButlerOverviewTab butlerName={name} />
          </TabsContent>

          <TabsContent value="activity">
            <ButlerActivitySection butlerName={name} />
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

          <TabsContent value="memory">
            <Suspense fallback={<TabFallback label="memory" />}>
              <ButlerMemoryTab butlerName={name} />
            </Suspense>
          </TabsContent>

          {showCollectionsTab && (
            <TabsContent value="collections">
              <Suspense fallback={<TabFallback label="collections" />}>
                <ButlerGeneralCollectionsTab />
              </Suspense>
            </TabsContent>
          )}

          {showEntitiesTab && (
            <TabsContent value="entities">
              <Suspense fallback={<TabFallback label="entities" />}>
                <ButlerGeneralEntitiesTab />
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

          <TabsContent value="system">
            <ButlerSystemSection butlerName={name} />
          </TabsContent>
        </Tabs>
    </Page>
  );
}
