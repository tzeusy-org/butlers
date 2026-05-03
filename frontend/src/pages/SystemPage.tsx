/**
 * System Overview page (/system).
 *
 * Surfaces five ownership-fact domains: software version and uptime, database
 * state, backup state, data egress catalog (owner-only), and per-butler
 * heartbeats.
 */

import { BackupTile } from "@/components/system/BackupTile";
import { DbSizeTile } from "@/components/system/DbSizeTile";
import { EgressCatalogTile } from "@/components/system/EgressCatalogTile";
import { UptimeTile } from "@/components/system/UptimeTile";
import { VersionTile } from "@/components/system/VersionTile";
import TopologyGraph from "@/components/topology/TopologyGraph";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Page } from "@/components/ui/page";
import { useButlers } from "@/hooks/use-butlers";
import { useConnectorSummaries } from "@/hooks/use-ingestion";
import {
  useButlerHeartbeats,
} from "@/hooks/use-system";

// ---------------------------------------------------------------------------
// SystemTile
// ---------------------------------------------------------------------------

interface SystemTileProps {
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}

function SystemTile({ title, action, children }: SystemTileProps) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        {action}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// HeartbeatTile stub (bu-ngfzz.7)
// ---------------------------------------------------------------------------

function HeartbeatTile() {
  const { data, isLoading, error } = useButlerHeartbeats();

  if (isLoading) {
    return (
      <SystemTile title="Butler Heartbeats">
        <div className="h-16 animate-pulse rounded bg-muted" />
      </SystemTile>
    );
  }

  if (error) {
    return (
      <SystemTile title="Butler Heartbeats">
        <p className="text-sm text-destructive">Failed to load heartbeat data.</p>
      </SystemTile>
    );
  }

  return (
    <SystemTile title="Butler Heartbeats">
      <pre className="overflow-auto text-xs text-muted-foreground">
        {JSON.stringify(data?.data, null, 2)}
      </pre>
    </SystemTile>
  );
}

// ---------------------------------------------------------------------------
// TopologyTile
// ---------------------------------------------------------------------------

function TopologyTile() {
  const { data: butlersResponse, isLoading: butlersLoading, error: butlersError } = useButlers();
  const { data: connectorsResponse, isLoading: connectorsLoading } = useConnectorSummaries();

  if (butlersError) {
    return (
      <SystemTile title="Ecosystem Topology">
        <p className="text-sm text-destructive">Failed to load topology data.</p>
      </SystemTile>
    );
  }

  const butlers = butlersResponse?.data ?? [];
  const connectors = connectorsResponse?.data ?? [];

  return (
    <TopologyGraph
      butlers={butlers}
      connectors={connectors}
      isLoading={butlersLoading || connectorsLoading}
    />
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function SystemPage() {
  return (
    <Page
      archetype="overview"
      title="System"
      description="Your instance, your data, your butlers."
    >
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        <VersionTile />
        <UptimeTile />
        <DbSizeTile />
        <BackupTile />
        <EgressCatalogTile />
        <HeartbeatTile />
      </div>

      {/* Ecosystem topology -- full-width section below ownership fact tiles */}
      <TopologyTile />
    </Page>
  );
}

export default SystemPage;
