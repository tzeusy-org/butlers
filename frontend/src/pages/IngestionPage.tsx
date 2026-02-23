import { useSearchParams } from 'react-router'

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { BackfillHistoryTab } from '@/components/switchboard/BackfillHistoryTab'

// ---------------------------------------------------------------------------
// Tab value constants
// ---------------------------------------------------------------------------

const INGESTION_TABS = ['overview', 'connectors', 'filters', 'history'] as const
type IngestionTab = (typeof INGESTION_TABS)[number]

function isValidTab(value: string | null): value is IngestionTab {
  return INGESTION_TABS.includes(value as IngestionTab)
}

// ---------------------------------------------------------------------------
// Placeholder tab content components
// These will be replaced by dedicated implementations in child issues:
//   butlers-dsa4.4.3 - Filters tab
//   butlers-dsa4.4.5 - Overview and Connectors tab analytics
// ---------------------------------------------------------------------------

function OverviewTabContent() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Overview</CardTitle>
        <CardDescription>
          Aggregate ingestion telemetry and high-level routing economics.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">
          Overview analytics will be implemented in a follow-up task.
        </p>
      </CardContent>
    </Card>
  )
}

function ConnectorsTabContent() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Connectors</CardTitle>
        <CardDescription>
          Source connector health, volume, and fanout distribution.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">
          Connectors analytics will be implemented in a follow-up task.
        </p>
      </CardContent>
    </Card>
  )
}

function FiltersTabContent() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Filters</CardTitle>
        <CardDescription>
          Deterministic ingestion policy — triage rules, thread affinity, and label filters.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">
          Filter controls will be implemented in a follow-up task.
        </p>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// IngestionPage
// ---------------------------------------------------------------------------

export default function IngestionPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  const tabParam = searchParams.get('tab')
  const activeTab: IngestionTab = isValidTab(tabParam) ? tabParam : 'overview'

  function handleTabChange(value: string) {
    if (value === 'overview') {
      // Omit `tab` param for the default tab to keep URLs clean
      setSearchParams({}, { replace: true })
    } else {
      setSearchParams({ tab: value }, { replace: true })
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Ingestion</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Unified ingestion control surface — source visibility, routing policy, and historical replay.
        </p>
      </div>

      <Tabs value={activeTab} onValueChange={handleTabChange}>
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="connectors">Connectors</TabsTrigger>
          <TabsTrigger value="filters">Filters</TabsTrigger>
          <TabsTrigger value="history">History</TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <OverviewTabContent />
        </TabsContent>

        <TabsContent value="connectors">
          <ConnectorsTabContent />
        </TabsContent>

        <TabsContent value="filters">
          <FiltersTabContent />
        </TabsContent>

        <TabsContent value="history">
          <BackfillHistoryTab />
        </TabsContent>
      </Tabs>
    </div>
  )
}
