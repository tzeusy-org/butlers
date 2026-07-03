// ---------------------------------------------------------------------------
// ButlerSystemSection — "System" top-level tab body (bu-86c4c.18)
//
// One butler console, one tab set: the low-frequency operational tabs
// (Config, Skills, Schedules, MCP, State, Models, Manage) are checked
// monthly, not hourly, so they fold into a single System section instead of
// each occupying a slot in the primary tab rail. This replaces the former
// operator-mode-only tab set.
// ---------------------------------------------------------------------------

import { Suspense, lazy } from "react"
import { useSearchParams } from "react-router"

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import ButlerConfigTab from "@/components/butler-detail/ButlerConfigTab"

const ButlerSkillsTab = lazy(() => import("@/components/butler-detail/ButlerSkillsTab.tsx"))
const ButlerSchedulesTab = lazy(() => import("@/components/butler-detail/ButlerSchedulesTab.tsx"))
const ButlerMcpTab = lazy(() => import("@/components/butler-detail/ButlerMcpTab.tsx"))
const ButlerStateTab = lazy(() => import("@/components/butler-detail/ButlerStateTab.tsx"))
const ButlerModelOverridesTab = lazy(
  () => import("@/components/butler-detail/ButlerModelOverridesTab.tsx"),
)
const ButlerManagementTab = lazy(() => import("@/components/butler-detail/ButlerManagementTab.tsx"))

const SYSTEM_SECTIONS = ["config", "skills", "schedules", "mcp", "state", "models", "manage"] as const
type SystemSection = (typeof SYSTEM_SECTIONS)[number]

function isSystemSection(value: string | null): value is SystemSection {
  return SYSTEM_SECTIONS.includes(value as SystemSection)
}

const sectionLabel: Record<SystemSection, string> = {
  config: "Config",
  skills: "Skills",
  schedules: "Schedules",
  mcp: "MCP",
  state: "State",
  models: "Models",
  manage: "Manage",
}

function SectionFallback({ label }: { label: string }) {
  return (
    <div className="text-muted-foreground flex items-center justify-center py-12 text-sm">
      Loading {label}...
    </div>
  )
}

export interface ButlerSystemSectionProps {
  butlerName: string
}

export default function ButlerSystemSection({ butlerName }: ButlerSystemSectionProps) {
  const [searchParams, setSearchParams] = useSearchParams()

  const sectionParam = searchParams.get("section")
  const activeSection: SystemSection = isSystemSection(sectionParam) ? sectionParam : "config"

  function handleSectionChange(value: string) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (value === "config") {
          next.delete("section")
        } else {
          next.set("section", value)
        }
        return next
      },
      { replace: true },
    )
  }

  return (
    <div data-testid="butler-system-section">
      <Tabs value={activeSection} onValueChange={handleSectionChange}>
        <TabsList
          variant="line"
          className="h-auto w-full justify-start overflow-x-auto rounded-none bg-transparent p-0"
        >
          {SYSTEM_SECTIONS.map((section) => (
            <TabsTrigger
              key={section}
              value={section}
              data-testid={`system-section-${section}`}
              className="h-auto flex-none rounded-none px-3 py-1.5 font-mono text-[10px] font-medium uppercase tracking-[0.10em]"
            >
              {sectionLabel[section]}
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="config">
          <ButlerConfigTab butlerName={butlerName} />
        </TabsContent>

        <TabsContent value="skills">
          <Suspense fallback={<SectionFallback label="skills" />}>
            <ButlerSkillsTab butlerName={butlerName} />
          </Suspense>
        </TabsContent>

        <TabsContent value="schedules">
          <Suspense fallback={<SectionFallback label="schedules" />}>
            <ButlerSchedulesTab butlerName={butlerName} />
          </Suspense>
        </TabsContent>

        <TabsContent value="mcp">
          <Suspense fallback={<SectionFallback label="mcp" />}>
            <ButlerMcpTab butlerName={butlerName} />
          </Suspense>
        </TabsContent>

        <TabsContent value="state">
          <Suspense fallback={<SectionFallback label="state" />}>
            <ButlerStateTab butlerName={butlerName} />
          </Suspense>
        </TabsContent>

        <TabsContent value="models">
          <Suspense fallback={<SectionFallback label="models" />}>
            <ButlerModelOverridesTab butlerName={butlerName} />
          </Suspense>
        </TabsContent>

        <TabsContent value="manage">
          <Suspense fallback={<SectionFallback label="manage" />}>
            <ButlerManagementTab butlerName={butlerName} />
          </Suspense>
        </TabsContent>
      </Tabs>
    </div>
  )
}
