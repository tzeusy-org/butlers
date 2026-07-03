// ---------------------------------------------------------------------------
// ButlerActivitySection — "Activity" top-level tab body (bu-86c4c.18)
//
// One butler console, one tab set: this composite folds the former
// standalone Sessions and Logs top-level tabs (previously operator/resident
// mode exclusives) into sub-sections of a single Activity tab, alongside the
// existing analytics body (KPIs, hourly/daily chart, kind breakdown).
//
// Sub-nav state is carried in the `section` URL search param so the Overview
// "doors" (bu-86c4c.18) can deep-link straight to e.g.
// ?tab=activity&section=sessions&since=...&until=... from an activity-stripe
// bar click.
// ---------------------------------------------------------------------------

import { Suspense, lazy } from "react"
import { useSearchParams } from "react-router"

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import ButlerActivityTab from "@/components/butler-detail/ButlerActivityTab"

const ButlerSessionsTab = lazy(() => import("@/components/butler-detail/ButlerSessionsTab.tsx"))
const ButlerLogsTab = lazy(() => import("@/components/butler-detail/ButlerLogsTab.tsx"))

const ACTIVITY_SECTIONS = ["analytics", "sessions", "logs"] as const
type ActivitySection = (typeof ACTIVITY_SECTIONS)[number]

function isActivitySection(value: string | null): value is ActivitySection {
  return ACTIVITY_SECTIONS.includes(value as ActivitySection)
}

const sectionLabel: Record<ActivitySection, string> = {
  analytics: "Analytics",
  sessions: "Sessions",
  logs: "Logs",
}

function SectionFallback({ label }: { label: string }) {
  return (
    <div className="text-muted-foreground flex items-center justify-center py-12 text-sm">
      Loading {label}...
    </div>
  )
}

export interface ButlerActivitySectionProps {
  butlerName: string
}

export default function ButlerActivitySection({ butlerName }: ButlerActivitySectionProps) {
  const [searchParams, setSearchParams] = useSearchParams()

  const sectionParam = searchParams.get("section")
  const activeSection: ActivitySection = isActivitySection(sectionParam) ? sectionParam : "analytics"

  const since = searchParams.get("since") ?? undefined
  const until = searchParams.get("until") ?? undefined

  function handleSectionChange(value: string) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (value === "analytics") {
          next.delete("section")
          next.delete("since")
          next.delete("until")
        } else {
          next.set("section", value)
        }
        return next
      },
      { replace: true },
    )
  }

  function handleClearTimeFilter() {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        next.delete("since")
        next.delete("until")
        return next
      },
      { replace: true },
    )
  }

  return (
    <div data-testid="butler-activity-section">
      <Tabs value={activeSection} onValueChange={handleSectionChange}>
        <TabsList
          variant="line"
          className="h-auto w-full justify-start rounded-none bg-transparent p-0"
        >
          {ACTIVITY_SECTIONS.map((section) => (
            <TabsTrigger
              key={section}
              value={section}
              data-testid={`activity-section-${section}`}
              className="h-auto flex-none rounded-none px-3 py-1.5 font-mono text-[10px] font-medium uppercase tracking-[0.10em]"
            >
              {sectionLabel[section]}
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="analytics">
          <ButlerActivityTab butlerName={butlerName} />
        </TabsContent>

        <TabsContent value="sessions">
          <Suspense fallback={<SectionFallback label="sessions" />}>
            <ButlerSessionsTab
              butlerName={butlerName}
              since={since}
              until={until}
              onClearFilter={handleClearTimeFilter}
            />
          </Suspense>
        </TabsContent>

        <TabsContent value="logs">
          <Suspense fallback={<SectionFallback label="logs" />}>
            <ButlerLogsTab butlerName={butlerName} />
          </Suspense>
        </TabsContent>
      </Tabs>
    </div>
  )
}
