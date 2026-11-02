/**
 * System Overview page (/system).
 *
 * Surfaces ownership-fact domains: software version and uptime, database
 * state, backup state, data egress catalog (owner-only), per-butler
 * heartbeats, migration drift, and (bu-hmdqz.1) the deployment ledger --
 * what git SHA is actually serving, and how far behind origin/main it is.
 */

import { useMemo } from "react";

import { BackgroundJobsSection } from "@/components/system/BackgroundJobsSection";
import { BackupTile } from "@/components/system/BackupTile";
import { ButlerHeartbeatTile } from "@/components/system/ButlerHeartbeatTile";
import { DbSizeTile } from "@/components/system/DbSizeTile";
import { DeploymentTile } from "@/components/system/DeploymentTile";
import { DriftTile } from "@/components/system/DriftTile";
import { EgressCatalogTile } from "@/components/system/EgressCatalogTile";
import { InsightDeliveryTile } from "@/components/system/InsightDeliveryTile";
import { SecurityPostureTile } from "@/components/system/SecurityPostureTile";
import { StandingConditionsTile } from "@/components/system/StandingConditionsTile";
import { StoredFunctionsTile } from "@/components/system/StoredFunctionsTile";
import { SystemVerdictBanner } from "@/components/system/SystemVerdictBanner";
import { UptimeTile } from "@/components/system/UptimeTile";
import { VersionTile } from "@/components/system/VersionTile";
import TopologyGraph from "@/components/topology/TopologyGraph";
import {
  Section,
  SectionContent,
  SectionHeader,
  SectionTitle,
} from "@/components/ui/Section";
import { Page } from "@/components/ui/page";
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board";
import { useConnectorSummaries } from "@/hooks/use-ingestion";
import { useButlerHeartbeats } from "@/hooks/use-system";
import { usePageActions, type PageAction } from "@/hooks/use-page-actions";

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
    <Section>
      <SectionHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <SectionTitle>{title}</SectionTitle>
        {action}
      </SectionHeader>
      <SectionContent>{children}</SectionContent>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// TopologyTile
// ---------------------------------------------------------------------------

function TopologyTile() {
  // Keep the topology's existing board activity/tone contract. The heartbeat
  // tile separately displays receiver observation and service-policy facts;
  // neither query snapshot is an atomic or row-wide availability verdict.
  const { rows, aggregates } = useButlerStatusBoard();
  const { refetch: refetchHeartbeats } = useButlerHeartbeats();
  const {
    data: connectorsResponse,
    isLoading: connectorsLoading,
    isError: connectorsError,
    refetch: refetchConnectors,
  } = useConnectorSummaries();

  // Hot-loop keyboard coverage (bu-ep4ks.12): /system had zero
  // useRegisterShortcut coverage. "r" refreshes the same butler-status and
  // connector data this tile already polls, plus the independent heartbeat facts.
  const pageActions = useMemo<PageAction[]>(
    () => [
      {
        id: "system-refresh",
        label: "Refresh system status",
        key: "r",
        display: ["r"],
        description: "Refresh system status",
        handler: () => {
          void aggregates.refetch();
          void refetchConnectors();
          void refetchHeartbeats();
        },
      },
    ],
    [aggregates, refetchConnectors, refetchHeartbeats],
  );
  usePageActions(pageActions);

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
    tone: row.cellTone,
  }));
  const connectors = (connectorsResponse?.data?.connectors ?? []).filter(
    (connector) => !connector.archived,
  );

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
      <BackgroundJobsSection />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        <VersionTile />
        <UptimeTile />
        <DbSizeTile />
        <SecurityPostureTile />
        <InsightDeliveryTile />
        <DriftTile />
        <StoredFunctionsTile />
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
        <div className="lg:col-span-3 h-full">
          <StandingConditionsTile />
        </div>
      </div>

      {/* Ecosystem topology -- full-width section below ownership fact tiles */}
      <TopologyTile />
    </Page>
  );
}

export default SystemPage;
