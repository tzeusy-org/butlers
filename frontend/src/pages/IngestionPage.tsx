import { useSearchParams } from 'react-router'

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { BackfillHistoryTab } from '@/components/switchboard/BackfillHistoryTab'
import { FiltersTab } from '@/components/switchboard/FiltersTab'
import { OverviewTab } from '@/components/ingestion/OverviewTab'
import { ConnectorsTab } from '@/components/ingestion/ConnectorsTab'

// ---------------------------------------------------------------------------
// Tab value constants
// ---------------------------------------------------------------------------

const INGESTION_TABS = ['overview', 'connectors', 'filters', 'history'] as const
type IngestionTab = (typeof INGESTION_TABS)[number]

function isValidTab(value: string | null): value is IngestionTab {
  return INGESTION_TABS.includes(value as IngestionTab)
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
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        next.set('tab', value)
        // Clear period when switching tabs so each tab uses its own fresh default
        next.delete('period')
        return next
      }, { replace: true })
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
          <OverviewTab isActive={activeTab === 'overview'} />
        </TabsContent>

        <TabsContent value="connectors">
          <ConnectorsTab isActive={activeTab === 'connectors'} />
        </TabsContent>

        <TabsContent value="filters">
          <FiltersTab />
        </TabsContent>

        <TabsContent value="history">
          <BackfillHistoryTab />
        </TabsContent>
      </Tabs>
    </div>
  )
}
