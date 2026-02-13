import { lazy, Suspense, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import type { SessionParams, SessionSummary } from "@/api/types";
import ButlerConfigTab from "@/components/butler-detail/ButlerConfigTab";
import ButlerOverviewTab from "@/components/butler-detail/ButlerOverviewTab";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import { useButlerSessions } from "@/hooks/use-sessions";
import { useUpcomingDates } from "@/hooks/use-contacts";

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
const ButlerStateTab = lazy(
  () => import("@/components/butler-detail/ButlerStateTab.tsx"),
);

// General butler tabs (lazy)
const ButlerCollectionsTab = lazy(
  () => import("@/components/butler-detail/ButlerCollectionsTab.tsx"),
);
const ButlerEntitiesTab = lazy(
  () => import("@/components/butler-detail/ButlerEntitiesTab.tsx"),
);

// Switchboard butler tabs (lazy)
const ButlerMemoryTab = lazy(
  () => import("@/components/butler-detail/ButlerMemoryTab.tsx"),
);

const ButlerRoutingLogTab = lazy(
  () => import("@/components/butler-detail/ButlerRoutingLogTab.tsx"),
);
const ButlerRegistryTab = lazy(
  () => import("@/components/butler-detail/ButlerRegistryTab.tsx"),
);

// ---------------------------------------------------------------------------
// Tab configuration
// ---------------------------------------------------------------------------

const BASE_TABS = [
  "overview",
  "sessions",
  "config",
  "skills",
  "schedules",
  "state",
  "trigger",
  "crm",
  "memory",
] as const;

const HEALTH_TABS = ["health"] as const;
const GENERAL_TABS = ["collections", "entities"] as const;
const SWITCHBOARD_TABS = ["routing-log", "registry"] as const;

type TabValue =
  | (typeof BASE_TABS)[number]
  | (typeof HEALTH_TABS)[number]
  | (typeof GENERAL_TABS)[number]
  | (typeof SWITCHBOARD_TABS)[number];

function getAllTabs(butlerName: string): readonly string[] {
  const tabs: string[] = [...BASE_TABS];
  if (butlerName === "health") {
    tabs.push(...HEALTH_TABS);
  }
  if (butlerName === "general") {
    tabs.push(...GENERAL_TABS);
  }
  if (butlerName === "switchboard") {
    tabs.push(...SWITCHBOARD_TABS);
  }
  return tabs;
}

const PAGE_SIZE = 20;

function isValidTab(value: string | null, butlerName: string): value is TabValue {
  return getAllTabs(butlerName).includes(value as string);
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

// ---------------------------------------------------------------------------
// Health Tab sub-component
// ---------------------------------------------------------------------------

function ButlerHealthTab({ butlerName }: { butlerName: string }) {
  const isHealth = butlerName === "health";

  if (!isHealth) {
    return (
      <Card>
        <CardContent className="py-12">
          <p className="text-muted-foreground text-center text-sm">
            Health features are only available for the health butler.
          </p>
        </CardContent>
      </Card>
    );
  }

  const sections = [
    {
      title: "Measurements",
      description: "Track weight, blood pressure, heart rate, and more.",
      link: "/health/measurements",
    },
    {
      title: "Medications",
      description: "Manage medications and track dose adherence.",
      link: "/health/medications",
    },
    {
      title: "Conditions",
      description: "View and manage health conditions.",
      link: "/health/conditions",
    },
    {
      title: "Symptoms",
      description: "Track symptoms with severity ratings.",
      link: "/health/symptoms",
    },
    {
      title: "Meals",
      description: "Log meals and monitor nutrition.",
      link: "/health/meals",
    },
    {
      title: "Research",
      description: "Health research notes and references.",
      link: "/health/research",
    },
  ];

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {sections.map((section) => (
        <Card key={section.title}>
          <CardHeader>
            <CardTitle className="text-base">{section.title}</CardTitle>
            <CardDescription>{section.description}</CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" size="sm" asChild>
              <Link to={section.link}>View</Link>
            </Button>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ButlerDetailPage
// ---------------------------------------------------------------------------

export default function ButlerDetailPage() {
  const { name = "" } = useParams<{ name: string }>();
  const [searchParams, setSearchParams] = useSearchParams();

  const tabParam = searchParams.get("tab");
  const activeTab: TabValue = isValidTab(tabParam, name) ? tabParam : "overview";

  const isGeneral = name === "general";
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

  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: "Overview", href: "/" }, { label: "Butlers", href: "/butlers" }, { label: name }]} />
      <h1 className="text-2xl font-bold tracking-tight">{name}</h1>

      <Tabs value={activeTab} onValueChange={handleTabChange}>
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="sessions">Sessions</TabsTrigger>
          <TabsTrigger value="config">Config</TabsTrigger>
          <TabsTrigger value="skills">Skills</TabsTrigger>
          <TabsTrigger value="schedules">Schedules</TabsTrigger>
          <TabsTrigger value="trigger">Trigger</TabsTrigger>
          <TabsTrigger value="state">State</TabsTrigger>
          <TabsTrigger value="crm">CRM</TabsTrigger>
          <TabsTrigger value="memory">Memory</TabsTrigger>
          {showHealthTab && <TabsTrigger value="health">Health</TabsTrigger>}
          {isGeneral && (
            <>
              <TabsTrigger value="collections">Collections</TabsTrigger>
              <TabsTrigger value="entities">Entities</TabsTrigger>
            </>
          )}
          {isSwitchboard && (
            <>
              <TabsTrigger value="routing-log">Routing Log</TabsTrigger>
              <TabsTrigger value="registry">Registry</TabsTrigger>
            </>
          )}
        </TabsList>

        <TabsContent value="overview">
          <ButlerOverviewTab butlerName={name} />
        </TabsContent>

        <TabsContent value="sessions">
          <ButlerSessionsTab butlerName={name} />
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

        <TabsContent value="crm">
          <ButlerCrmTab butlerName={name} />
        </TabsContent>

        <TabsContent value="memory">
          <Suspense fallback={<TabFallback label="memory" />}>
            <ButlerMemoryTab butlerName={name} />
          </Suspense>
        </TabsContent>

        {showHealthTab && (
          <TabsContent value="health">
            <ButlerHealthTab butlerName={name} />
          </TabsContent>
        )}

        {isGeneral && (
          <>
            <TabsContent value="collections">
              <Suspense fallback={<TabFallback label="collections" />}>
                <ButlerCollectionsTab />
              </Suspense>
            </TabsContent>
            <TabsContent value="entities">
              <Suspense fallback={<TabFallback label="entities" />}>
                <ButlerEntitiesTab />
              </Suspense>
            </TabsContent>
          </>
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
      </Tabs>
    </div>
  );
}
