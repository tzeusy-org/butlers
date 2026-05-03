import { useState } from 'react'

import CostBreakdownTable from '@/components/costs/CostBreakdownTable'
import CostChart from '@/components/costs/CostChart'
import type { Period } from '@/components/costs/CostChart'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Page } from '@/components/ui/page'
import { useCostSummary, useDailyCosts } from '@/hooks/use-costs'

function formatCost(amount: number): string {
  if (amount < 0.01) return '$0.00'
  return `$${amount.toFixed(2)}`
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

function StatsCard({ title, value }: { title: string; value: string }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
      </CardContent>
    </Card>
  )
}

export default function CostsPage() {
  const [period, setPeriod] = useState<Period>('7d')
  const { data: summaryResponse, isLoading: summaryLoading } = useCostSummary(period)
  const { data: dailyResponse, isLoading: dailyLoading } = useDailyCosts()

  const summary = summaryResponse?.data
  const dailyData = dailyResponse?.data ?? []

  return (
    <Page
      archetype="overview"
      title="Costs & Usage"
      loading={summaryLoading || dailyLoading}
    >
      {/* Summary Stats */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {summaryLoading ? (
          Array.from({ length: 4 }).map((_, i) => (
            <Card key={i}>
              <CardHeader className="pb-2">
                <div className="h-4 w-24 animate-pulse rounded bg-muted" />
              </CardHeader>
              <CardContent>
                <div className="h-8 w-16 animate-pulse rounded bg-muted" />
              </CardContent>
            </Card>
          ))
        ) : (
          <>
            <StatsCard title="Total Cost" value={formatCost(summary?.total_cost_usd ?? 0)} />
            <StatsCard title="Total Sessions" value={String(summary?.total_sessions ?? 0)} />
            <StatsCard
              title="Input Tokens"
              value={formatTokens(summary?.total_input_tokens ?? 0)}
            />
            <StatsCard
              title="Output Tokens"
              value={formatTokens(summary?.total_output_tokens ?? 0)}
            />
          </>
        )}
      </div>

      {/* Chart + Breakdown Grid */}
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <CostChart
            data={dailyData}
            isLoading={dailyLoading}
            period={period}
            onPeriodChange={setPeriod}
          />
        </div>
        <div>
          <CostBreakdownTable
            byButler={summary?.by_butler ?? {}}
            totalCost={summary?.total_cost_usd ?? 0}
            isLoading={summaryLoading}
          />
        </div>
      </div>
    </Page>
  )
}
