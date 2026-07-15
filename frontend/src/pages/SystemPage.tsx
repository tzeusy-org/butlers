/**
 * System Overview page (/system).
 *
 * Surfaces ownership-fact domains: software version and uptime, database
 * state, backup state, data egress catalog (owner-only), per-butler
 * heartbeats, migration drift, and (bu-hmdqz.1) the deployment ledger --
 * what git SHA is actually serving, and how far behind origin/main it is.
 */

import { BackupTile } from "@/components/system/BackupTile";
import { ButlerHeartbeatTile } from "@/components/system/ButlerHeartbeatTile";
import { DbSizeTile } from "@/components/system/DbSizeTile";
import { DeploymentTile } from "@/components/system/DeploymentTile";
import { DriftTile } from "@/components/system/DriftTile";
import { EgressCatalogTile } from "@/components/system/EgressCatalogTile";
import { InsightDeliveryTile } from "@/components/system/InsightDeliveryTile";
import { SecurityPostureTile } from "@/components/system/SecurityPostureTile";
import { SystemVerdictBanner } from "@/components/system/SystemVerdictBanner";
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
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board";
import { useConnectorSummaries } from "@/hooks/use-ingestion";

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
// TopologyTile
// ---------------------------------------------------------------------------

function TopologyTile() {
  // Canonical liveness source (bu-86c4c.17): the topology graph now colors
  // its nodes from the SAME activity/tone verdict as the roster board and
  // the heartbeat tile (useButlerStatusBoard / GET /api/butlers/board),
  // rather than a separate useButlers() status probe. This closes the bug
  // where a butler could render green here while amber-stale in a list.
  const { rows, aggregates } = useButlerStatusBoard();
  const { data: connectorsResponse, isLoading: connectorsLoading, isError: connectorsError } =
    useConnectorSummaries();

  if (aggregates.isError) {
    return (
      <SystemTile title="Ecosystem Topology">
        <p className="text-sm text-destructive">Failed to load topology data.</p>
      </SystemTile>
    );
  }

  const butlers = rows.map((row) => ({
    name: row.name,
    status: row.status,
    type: row.type,
    tone: row.cellTone,
  }));
  const connectors = connectorsResponse?.data ?? [];

  return (
    <TopologyGraph
      butlers={butlers}
      connectors={connectors}
      isLoading={aggregates.isLoading || connectorsLoading}
      connectorsError={connectorsError}
    />
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function SystemPage() {
  return (
    <Page
      archetype="overview"
      title="System"
      description="Your instance, your data, your butlers."
      breadcrumbs={[
        { label: "Home", href: "/" },
        { label: "System" },
      ]}
    >
      {/* Judgment layer: opens with a computed verdict -- "all clear" or a
          ranked problem list -- instead of leading with version/uptime
          trivia. The tiles below are elaboration, not the message. */}
      <SystemVerdictBanner />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        <VersionTile />
        <UptimeTile />
        <DbSizeTile />
        <SecurityPostureTile />
        <InsightDeliveryTile />
        <DriftTile />
        <DeploymentTile />
        <div className="lg:col-span-2 h-full">
          <BackupTile />
        </div>
        <div className="lg:col-span-3 h-full">
          <EgressCatalogTile />
        </div>
        <div className="lg:col-span-2 h-full">
          <ButlerHeartbeatTile />
        </div>
      </div>

      {/* Ecosystem topology -- full-width section below ownership fact tiles */}
      <TopologyTile />
    </Page>
  );
}

export default SystemPage;
