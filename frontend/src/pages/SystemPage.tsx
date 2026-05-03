/**
 * System Overview page (/system).
 *
 * Surfaces five ownership-fact domains: software version and uptime, database
 * state, backup state, data egress catalog (owner-only), and per-butler
 * heartbeats.
 *
 * Tile content is rendered as pass-through JSON stubs for now. Sibling beads
 * (e5/e6/e7) will replace each stub with purpose-built tile bodies.
 */

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
  useBackupFacts,
  useButlerHeartbeats,
  useDatabaseFacts,
  useEgressFacts,
  useInstanceFacts,
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
// Individual tile implementations (stub pass-through for bu-ngfzz.4)
// ---------------------------------------------------------------------------

function InstanceTile() {
  const { data, isLoading, error } = useInstanceFacts();

  if (isLoading) {
    return (
      <SystemTile title="Instance">
        <div className="h-16 animate-pulse rounded bg-muted" />
      </SystemTile>
    );
  }

  if (error) {
    return (
      <SystemTile title="Instance">
        <p className="text-sm text-destructive">Failed to load instance facts.</p>
      </SystemTile>
    );
  }

  return (
    <SystemTile title="Instance">
      <pre className="overflow-auto text-xs text-muted-foreground">
        {JSON.stringify(data?.data, null, 2)}
      </pre>
    </SystemTile>
  );
}

function DatabaseTile() {
  const { data, isLoading, error } = useDatabaseFacts();

  if (isLoading) {
    return (
      <SystemTile title="Database">
        <div className="h-16 animate-pulse rounded bg-muted" />
      </SystemTile>
    );
  }

  if (error) {
    return (
      <SystemTile title="Database">
        <p className="text-sm text-destructive">Failed to load database facts.</p>
      </SystemTile>
    );
  }

  return (
    <SystemTile title="Database">
      <pre className="overflow-auto text-xs text-muted-foreground">
        {JSON.stringify(data?.data, null, 2)}
      </pre>
    </SystemTile>
  );
}

function BackupTile() {
  const { data, isLoading, error } = useBackupFacts();

  if (isLoading) {
    return (
      <SystemTile title="Backups">
        <div className="h-16 animate-pulse rounded bg-muted" />
      </SystemTile>
    );
  }

  if (error) {
    return (
      <SystemTile title="Backups">
        <p className="text-sm text-destructive">Failed to load backup facts.</p>
      </SystemTile>
    );
  }

  if (data && !data.data.backup_source_reachable) {
    return (
      <SystemTile title="Backups">
        <p className="text-sm text-muted-foreground">Backup status unavailable.</p>
      </SystemTile>
    );
  }

  return (
    <SystemTile title="Backups">
      <pre className="overflow-auto text-xs text-muted-foreground">
        {JSON.stringify(data?.data, null, 2)}
      </pre>
    </SystemTile>
  );
}

function EgressTile() {
  const { data, isLoading, error, isForbidden } = useEgressFacts();

  if (isLoading) {
    return (
      <SystemTile title="Data Egress">
        <div className="h-16 animate-pulse rounded bg-muted" />
      </SystemTile>
    );
  }

  if (isForbidden) {
    return (
      <SystemTile title="Data Egress">
        <p className="text-sm text-muted-foreground">Owner only.</p>
      </SystemTile>
    );
  }

  if (error) {
    return (
      <SystemTile title="Data Egress">
        <p className="text-sm text-destructive">Failed to load egress catalog.</p>
      </SystemTile>
    );
  }

  return (
    <SystemTile title="Data Egress">
      <pre className="overflow-auto text-xs text-muted-foreground">
        {JSON.stringify(data?.data, null, 2)}
      </pre>
    </SystemTile>
  );
}

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
        <InstanceTile />
        <DatabaseTile />
        <BackupTile />
        <EgressTile />
        <HeartbeatTile />
      </div>

      {/* Ecosystem topology -- full-width section below ownership fact tiles */}
      <TopologyTile />
    </Page>
  );
}

export default SystemPage;
