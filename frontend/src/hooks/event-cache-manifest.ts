/**
 * Registry↔hook coverage manifest (bu-qvnce.14 slice 5).
 *
 * event-cache-registry.ts's patches are hand-maintained per event type;
 * nothing previously verified that a hook's query key actually appeared in
 * the patch it claims to be covered by. The proven gap: notificationPatch
 * invalidated only the messenger health keys and the timeline, never
 * ["notifications"] / ["butler-notifications"] / ["notification-stats"]
 * (use-notifications.ts) -- the notifications list/stats pages silently
 * relied on their own polling, never on the "notification" bus event, for as
 * long as that patch existed. Fixed alongside this manifest.
 *
 * This is the single declarative list of "hook query key -> event type that
 * must invalidate it." event-cache-registry.coverage.test.ts asserts every
 * entry here actually fires -- add a row whenever a hook's doc comment
 * claims bus coverage (or whenever you demote a refetchInterval onto
 * POLL_BUS_RECONCILE_MS on the strength of a bus event), and the test
 * catches drift immediately instead of the claim silently rotting the next
 * time event-cache-registry.ts is refactored.
 */

/** One "this query key must be invalidated by this event type" assertion. */
export interface CoverageEntry {
  /** One dashboard-relevant fleet event type (see EVENT_TYPES in
   *  src/butlers/api/routers/events.py). */
  eventType: string;
  /** The exact query key the corresponding hook uses (or a realistic
   *  parameterized example). invalidateQueries prefix-matches, so a key with
   *  extra trailing segments (e.g. ["timeline", params]) still counts as
   *  covered by an invalidation of its prefix (["timeline"]). */
  queryKey: unknown[];
  /** Which hook/file this key belongs to, for a readable failure message. */
  source: string;
}

export const EVENT_CACHE_COVERAGE_MANIFEST: CoverageEntry[] = [
  // approval
  { eventType: "approval", queryKey: ["approvals", "flat"], source: "use-approvals.ts / ApprovalsPage.tsx (pending + flat list)" },
  { eventType: "approval", queryKey: ["approvals", "history"], source: "ApprovalsPage.tsx (history)" },
  { eventType: "approval", queryKey: ["approvals", "metrics"], source: "use-approvals.ts (useApprovalMetrics)" },
  { eventType: "approval", queryKey: ["approvals", "detail", "abc-1"], source: "use-approvals.ts (useApprovalDetail)" },

  // spend
  { eventType: "spend", queryKey: ["cost-summary"], source: "use-spend.ts (useSpendSummary)" },
  { eventType: "spend", queryKey: ["daily-costs"], source: "use-spend.ts (useDailySpend)" },
  { eventType: "spend", queryKey: ["top-sessions"], source: "use-spend.ts (useTopSessions)" },
  { eventType: "spend", queryKey: ["costs-by-schedule"], source: "use-spend.ts (useCostsBySchedule)" },
  // bu-01r64.4: SpendPage's own derived keys — see event-cache-registry.ts's
  // spendPatch header comment for why these lag no differently than the four
  // hook-owned keys above despite living in the page component itself.
  { eventType: "spend", queryKey: ["spend-breakdown"], source: "SpendPage.tsx (BreakdownSection)" },
  { eventType: "spend", queryKey: ["spend-rules"], source: "SpendPage.tsx (SpendRulesSection)" },
  { eventType: "spend", queryKey: ["spend-forecast"], source: "SpendPage.tsx (posture forecast query)" },

  // session
  { eventType: "session", queryKey: ["sessions"], source: "use-sessions.ts (useSessions)" },
  { eventType: "session", queryKey: ["session-aggregate"], source: "use-sessions.ts (useSessionAggregate)" },
  { eventType: "session", queryKey: ["butler-sessions"], source: "use-sessions.ts (useButlerSessions)" },
  { eventType: "session", queryKey: ["butlers", "board"], source: "use-butlers.ts (useButlersBoard)" },
  { eventType: "session", queryKey: ["timeline"], source: "use-timeline.ts (useTimeline)" },
  { eventType: "session", queryKey: ["session-detail-global", "sess-1"], source: "use-sessions.ts (useGlobalSessionDetail / SessionDetailPage)" },
  // bu-01r64.4: the session-stripe chart above SessionsPage's list lagged the
  // list itself by up to 60s because sessionPatch never touched its key.
  { eventType: "session", queryKey: ["session-stripe"], source: "session-stripe-utils.ts (useSessionStripeData) / SessionStripeChart.tsx" },

  // notification (the proven-gap surfaces, fixed alongside this manifest)
  { eventType: "notification", queryKey: ["notifications"], source: "use-notifications.ts (useNotifications)" },
  { eventType: "notification", queryKey: ["notification-stats"], source: "use-notifications.ts (useNotificationStats)" },
  { eventType: "notification", queryKey: ["butler-notifications"], source: "use-notifications.ts (useButlerNotifications)" },
  { eventType: "notification", queryKey: ["timeline"], source: "use-timeline.ts (useTimeline, notification is a timeline source)" },

  // issue
  { eventType: "issue", queryKey: ["issues"], source: "use-issues.ts (useIssues, both active and dismissed views)" },

  // ingestion
  { eventType: "ingestion", queryKey: ["ingestion", "events"], source: "use-ingestion-events.ts (ingestionEventKeys.list)" },
  { eventType: "ingestion", queryKey: ["ingestion", "window-rollup"], source: "use-ingestion-events.ts (window rollup)" },
  { eventType: "ingestion", queryKey: ["ingestion", "events-histogram"], source: "use-ingestion-events.ts (histogram)" },

  // calendar — provider and internal scheduler projections
  { eventType: "calendar", queryKey: ["calendar-workspace"], source: "use-calendar-workspace.ts (useCalendarWorkspace)" },
  { eventType: "calendar", queryKey: ["calendar-overlays"], source: "use-calendar-workspace.ts (useCalendarOverlays)" },
  { eventType: "calendar", queryKey: ["calendar-day-briefing"], source: "use-calendar-workspace.ts (useCalendarDayBriefing)" },
  { eventType: "calendar", queryKey: ["calendar-proposals"], source: "use-calendar-workspace.ts (useCalendarProposals)" },
  { eventType: "calendar", queryKey: ["calendar-duplicates"], source: "use-calendar-workspace.ts (useCalendarDuplicates)" },
  { eventType: "calendar", queryKey: ["calendar-conflicts"], source: "use-calendar-workspace.ts (useCalendarConflicts)" },
  { eventType: "calendar", queryKey: ["calendar-workspace-entry"], source: "use-calendar-workspace.ts (useCalendarWorkspaceEntry)" },
  { eventType: "calendar", queryKey: ["calendar-workspace-search"], source: "use-calendar-workspace.ts (useCalendarWorkspaceSearch)" },
  { eventType: "calendar", queryKey: ["calendar-workspace-meta"], source: "use-calendar-workspace.ts (useCalendarWorkspaceMeta)" },
  { eventType: "calendar", queryKey: ["calendar-workspace-audit"], source: "use-calendar-workspace.ts (useCalendarWorkspaceAudit)" },

  // chronicles — deterministic scheduled projections
  { eventType: "chronicles", queryKey: ["chronicles"], source: "use-chronicles.ts (projection-backed queries)" },
];

/** Every event type in EVENT_CACHE_COVERAGE_MANIFEST -- used by the coverage
 *  test to also assert the manifest itself does not go stale as new
 *  cache-affecting event types are added to the registry. */
export const CACHE_AFFECTING_EVENT_TYPES = [
  "approval",
  "spend",
  "session",
  "notification",
  "issue",
  "ingestion",
  "calendar",
  "chronicles",
] as const;
