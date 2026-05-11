// ---------------------------------------------------------------------------
// ButlerHomeDevicesTab — bu-iuol4.32
//
// Devices bespoke tab for the Home butler detail page.
//
// Layout (4-col panel grid, 3 rows):
//   Row 1: KPI strip (4 cells, full width)
//     — total devices | offline | overdue maintenance | snapshot freshness
//   Row 2: Active devices (span 2) | Maintenance queue (span 1) | Command log (span 1)
//   Row 3: Energy · 7d chart (span 2) | Top consumers (span 2)
//
// Data hooks:
//   useHomeSnapshotStatus, useHomeDevices, useHomeMaintenance,
//   useHomeEnergy, useHomeEnergyTopConsumers, useHomeCommandLog
//
// No backend changes — all data comes from existing hooks.
// ---------------------------------------------------------------------------

import { useMemo } from "react";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
} from "recharts";

import { Badge } from "@/components/ui/badge";
import { Time } from "@/components/ui/time";
import { Panel, KpiCell } from "@/components/butler-detail/atoms";
import {
  useHomeSnapshotStatus,
  useHomeDevices,
  useHomeMaintenance,
  useHomeEnergy,
  useHomeEnergyTopConsumers,
  useHomeCommandLog,
} from "@/hooks/use-home";
import type {
  HomeDeviceEntry,
  HomeMaintenanceItem,
  HomeEnergyDataPoint,
  HomeTopConsumer,
  HomeCommandLogEntry,
} from "@/api/types";

// ---------------------------------------------------------------------------
// Shared UI primitives
// ---------------------------------------------------------------------------

function EmptyLine({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-sm text-muted-foreground italic" data-testid="empty-state-line">
      {children}
    </p>
  );
}

function ErrorLine({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-sm text-destructive" data-testid="error-state-line">
      {children}
    </p>
  );
}

function LoadingLine() {
  return (
    <p className="text-sm text-muted-foreground" data-testid="loading-line">
      Loading...
    </p>
  );
}

// ---------------------------------------------------------------------------
// Row 1: KPI strip
// ---------------------------------------------------------------------------

interface KpiStripProps {
  totalDevices: number | undefined;
  offlineCount: number | undefined;
  overdueCount: number | undefined;
  newestCapturedAt: string | null | undefined;
  isLoading: boolean;
}

function KpiStrip({
  totalDevices,
  offlineCount,
  overdueCount,
  newestCapturedAt,
  isLoading,
}: KpiStripProps) {
  const kpiValue = (v: number | undefined) =>
    isLoading ? "..." : v != null ? String(v) : "—";

  return (
    <div
      className="col-span-1 lg:col-span-4 grid grid-cols-2 sm:grid-cols-4"
      data-testid="kpi-strip"
    >
      <Panel testId="kpi-item">
        <KpiCell
          label="Total devices"
          value={kpiValue(totalDevices)}
        />
      </Panel>
      <Panel testId="kpi-item">
        <KpiCell
          label="Offline"
          value={kpiValue(offlineCount)}
          tone={offlineCount != null && offlineCount > 0 ? "red" : "fg"}
        />
      </Panel>
      <Panel testId="kpi-item">
        <KpiCell
          label="Overdue maintenance"
          value={kpiValue(overdueCount)}
          tone={overdueCount != null && overdueCount > 0 ? "amber" : "fg"}
        />
      </Panel>
      <Panel testId="kpi-item">
        <KpiCell
          label="Last snapshot"
          value={isLoading ? "..." : "—"}
          sub={
            newestCapturedAt ? (
              <Time value={newestCapturedAt} mode="relative-compact" />
            ) : undefined
          }
        />
      </Panel>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row 2a: Active device inventory table
// ---------------------------------------------------------------------------

function HealthBadge({ status }: { status: "healthy" | "offline" }) {
  return (
    <Badge
      variant={status === "offline" ? "destructive" : "secondary"}
      className="text-xs font-mono shrink-0"
    >
      {status}
    </Badge>
  );
}

interface DeviceInventoryProps {
  devices: HomeDeviceEntry[];
  isLoading: boolean;
  isError?: boolean;
}

function DeviceInventory({ devices, isLoading, isError }: DeviceInventoryProps) {
  if (isLoading && devices.length === 0) {
    return <LoadingLine />;
  }

  if (isError) {
    return <ErrorLine>Failed to load device inventory.</ErrorLine>;
  }

  if (devices.length === 0) {
    return <EmptyLine>No devices in snapshot cache.</EmptyLine>;
  }

  return (
    <div className="overflow-auto" data-testid="device-inventory-table">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-xs text-muted-foreground">
            <th className="pb-2 text-left font-medium">Device</th>
            <th className="pb-2 text-left font-medium hidden sm:table-cell">Domain</th>
            <th className="pb-2 text-left font-medium hidden md:table-cell">Area</th>
            <th className="pb-2 text-left font-medium">State</th>
            <th className="pb-2 text-right font-medium">Health</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {devices.map((device) => (
            <tr
              key={device.entity_id}
              className="py-1"
              data-testid="device-inventory-row"
            >
              <td className="py-2 pr-2">
                <p className="font-medium truncate max-w-[10rem]">
                  {device.friendly_name ?? device.entity_id}
                </p>
                <p className="text-xs text-muted-foreground truncate max-w-[10rem]">
                  {device.entity_id}
                </p>
              </td>
              <td className="py-2 pr-2 hidden sm:table-cell text-muted-foreground">
                {device.domain}
              </td>
              <td className="py-2 pr-2 hidden md:table-cell text-muted-foreground">
                {device.area_name ?? "—"}
              </td>
              <td className="py-2 pr-2 font-mono text-xs tnum">
                {device.state}
              </td>
              <td className="py-2 text-right">
                <HealthBadge status={device.health_status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row 2b: Maintenance queue
// ---------------------------------------------------------------------------

function MaintenanceStatusBadge({ status }: { status: HomeMaintenanceItem["status"] }) {
  const variant =
    status === "overdue"
      ? "destructive"
      : status === "due"
        ? "default"
        : "secondary";

  return (
    <Badge variant={variant} className="text-xs shrink-0 capitalize">
      {status}
    </Badge>
  );
}

interface MaintenanceQueueProps {
  items: HomeMaintenanceItem[];
  isLoading: boolean;
  isError?: boolean;
}

function MaintenanceQueue({ items, isLoading, isError }: MaintenanceQueueProps) {
  if (isLoading && items.length === 0) {
    return <LoadingLine />;
  }

  if (isError) {
    return <ErrorLine>Failed to load maintenance queue.</ErrorLine>;
  }

  if (items.length === 0) {
    return <EmptyLine>No maintenance items.</EmptyLine>;
  }

  return (
    <ul className="space-y-3" aria-label="Maintenance queue" data-testid="maintenance-queue">
      {items.map((item) => (
        <li
          key={item.id}
          className="flex items-start justify-between gap-2"
          data-testid="maintenance-item"
        >
          <div className="min-w-0">
            <p className="text-sm font-medium truncate">{item.name}</p>
            <p className="text-xs text-muted-foreground truncate">{item.category}</p>
            {item.next_due_at && (
              <p className="text-xs text-muted-foreground">
                Due{" "}
                <Time value={item.next_due_at} mode="absolute" precision="day" compact />
              </p>
            )}
          </div>
          <MaintenanceStatusBadge status={item.status} />
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Row 2c: HA command log
// ---------------------------------------------------------------------------

function CommandResultBadge({ result }: { result: Record<string, unknown> | null }) {
  const isError =
    result != null &&
    (result["error"] != null ||
      result["success"] === false ||
      result["result"] === "error");

  return (
    <Badge variant={isError ? "destructive" : "secondary"} className="text-xs shrink-0">
      {isError ? "error" : "ok"}
    </Badge>
  );
}

interface CommandLogProps {
  entries: HomeCommandLogEntry[];
  isLoading: boolean;
  isError?: boolean;
}

function CommandLog({ entries, isLoading, isError }: CommandLogProps) {
  if (isLoading && entries.length === 0) {
    return <LoadingLine />;
  }

  if (isError) {
    return <ErrorLine>Failed to load command log.</ErrorLine>;
  }

  if (entries.length === 0) {
    return <EmptyLine>No commands logged.</EmptyLine>;
  }

  return (
    <ul
      className="space-y-2 divide-y divide-border"
      aria-label="HA command log"
      data-testid="command-log"
    >
      {entries.map((entry) => (
        <li
          key={entry.id}
          className="flex items-start justify-between gap-2 pt-2 first:pt-0"
          data-testid="command-log-entry"
        >
          <div className="min-w-0">
            <p className="text-sm font-mono font-medium truncate">
              {entry.domain}.{entry.service}
            </p>
            {entry.target && Object.keys(entry.target).length > 0 && (
              <p className="text-xs text-muted-foreground truncate">
                {JSON.stringify(entry.target)}
              </p>
            )}
            <p className="text-xs text-muted-foreground">
              <Time value={entry.issued_at} mode="relative-compact" />
            </p>
          </div>
          <CommandResultBadge result={entry.result} />
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Row 3a: Energy 7d area chart
// ---------------------------------------------------------------------------

/**
 * Custom tooltip styled with design tokens.
 * Uses popover/border token classes — no raw oklch/hex.
 */
function EnergyTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ value: number; payload: { date: string; total_kwh: number } }>;
}) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div
      className="rounded border border-border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-sm"
      data-testid="energy-tooltip"
    >
      <p className="text-muted-foreground">{point.date}</p>
      <p className="font-mono tnum font-medium">
        {point.total_kwh.toFixed(2)}
        <span className="ml-1 text-muted-foreground">kWh</span>
      </p>
    </div>
  );
}

interface EnergyChartProps {
  dataPoints: HomeEnergyDataPoint[];
  isLoading: boolean;
  isError?: boolean;
}

function EnergyAreaChart({ dataPoints, isLoading, isError }: EnergyChartProps) {
  const chartData = useMemo(
    () =>
      dataPoints
        .slice()
        .sort(
          (a, b) =>
            new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
        )
        .map((d) => ({
          date: d.timestamp.slice(0, 10),
          total_kwh: d.total_kwh,
        })),
    [dataPoints],
  );

  if (isLoading) {
    return <LoadingLine />;
  }

  if (isError) {
    return <ErrorLine>Failed to load energy data.</ErrorLine>;
  }

  if (chartData.length === 0) {
    return <EmptyLine>No energy data available.</EmptyLine>;
  }

  return (
    <div data-testid="energy-chart">
      <div data-testid="energy-area-chart">
        <ResponsiveContainer width="100%" height={120}>
          <AreaChart data={chartData} margin={{ top: 4, right: 4, bottom: 4, left: 4 }}>
            <defs>
              <linearGradient id="energyGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.3} />
                <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0.0} />
              </linearGradient>
            </defs>
            <XAxis dataKey="date" hide />
            <YAxis hide domain={["auto", "auto"]} />
            <Tooltip
              content={<EnergyTooltip />}
              isAnimationActive={false}
            />
            <Area
              dataKey="total_kwh"
              type="monotone"
              stroke="hsl(var(--primary))"
              strokeWidth={1.5}
              fill="url(#energyGradient)"
              dot={false}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <p className="sr-only">{`Energy usage · ${chartData.length} day trend`}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row 3b: Top consumers list
// ---------------------------------------------------------------------------

interface TopConsumersProps {
  consumers: HomeTopConsumer[];
  isLoading: boolean;
  isError?: boolean;
}

function TopConsumersList({ consumers, isLoading, isError }: TopConsumersProps) {
  if (isLoading) {
    return <LoadingLine />;
  }

  if (isError) {
    return <ErrorLine>Failed to load top consumers.</ErrorLine>;
  }

  if (consumers.length === 0) {
    return <EmptyLine>No consumer data available.</EmptyLine>;
  }

  const top5 = consumers.slice(0, 5);

  return (
    <ul className="space-y-2" data-testid="top-consumers">
      {top5.map((c) => (
        <li
          key={c.entity_id}
          className="flex items-center justify-between gap-2"
          data-testid="top-consumer-item"
        >
          <span className="text-sm truncate text-muted-foreground">
            {c.friendly_name ?? c.entity_id}
          </span>
          <span className="font-mono tnum text-xs shrink-0">
            {c.total_kwh.toFixed(1)} kWh
            <span className="text-muted-foreground ml-1">({c.percentage.toFixed(0)}%)</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// ButlerHomeDevicesTab — composed entry point
// ---------------------------------------------------------------------------

export default function ButlerHomeDevicesTab() {
  // Row 1: KPI strip — snapshot status
  const { data: snapshotStatus, isLoading: snapshotLoading } = useHomeSnapshotStatus();

  // Row 1 KPI: offline device count
  const { data: offlineDevices, isLoading: offlineLoading } = useHomeDevices({
    health: "offline",
    page: 1,
    page_size: 1,
  });

  // Row 1 KPI: overdue maintenance count
  const { data: overdueItems, isLoading: overdueLoading } = useHomeMaintenance({
    status: "overdue",
  });

  // Row 2: Full device inventory (first 50)
  const {
    data: deviceInventory,
    isLoading: devicesLoading,
    isError: devicesError,
  } = useHomeDevices({ page: 1, page_size: 50 });

  // Row 2: All maintenance items (sorted by urgency server-side)
  const {
    data: maintenanceItems,
    isLoading: maintenanceLoading,
    isError: maintenanceError,
  } = useHomeMaintenance();

  // Row 2: HA command log (last 20)
  const {
    data: commandLogResp,
    isLoading: commandLogLoading,
    isError: commandLogError,
  } = useHomeCommandLog({ limit: 20 });

  // Row 3: Energy time-series (7d, day granularity)
  const {
    data: energyData,
    isLoading: energyLoading,
    isError: energyError,
  } = useHomeEnergy({ period: "day" });

  // Row 3: Top consumers (7d)
  const {
    data: topConsumers,
    isLoading: consumersLoading,
    isError: consumersError,
  } = useHomeEnergyTopConsumers();

  const kpiLoading = snapshotLoading || offlineLoading || overdueLoading;

  const offlineCount = offlineDevices?.meta.total_count;
  const overdueCount = overdueItems?.length;
  const devices = deviceInventory?.data ?? [];
  const maintenance = maintenanceItems ?? [];
  const commandEntries = commandLogResp?.data ?? [];
  const energy = energyData ?? [];
  const consumers = topConsumers ?? [];

  return (
    <div
      className="grid grid-cols-1 lg:grid-cols-4 border-t border-l border-border/60"
      data-testid="home-devices-tab"
    >
      {/* Row 1: KPI strip — 4 cells across the full grid width */}
      <KpiStrip
        totalDevices={snapshotStatus?.total_entities}
        offlineCount={offlineCount}
        overdueCount={overdueCount}
        newestCapturedAt={snapshotStatus?.newest_captured_at}
        isLoading={kpiLoading}
      />

      {/* Row 2: Active devices (span 2) | Maintenance (span 1) | Commands (span 1) */}
      <Panel
        title="Active devices"
        span={2}
        scroll
        height="400px"
        testId="device-inventory-card"
      >
        <DeviceInventory
          devices={devices}
          isLoading={devicesLoading}
          isError={devicesError}
        />
      </Panel>

      <Panel title="Maintenance queue" span={1} scroll height="400px" testId="maintenance-queue-card">
        <MaintenanceQueue
          items={maintenance}
          isLoading={maintenanceLoading}
          isError={maintenanceError}
        />
      </Panel>

      <Panel title="Recent commands" span={1} scroll height="400px" testId="command-log-card">
        <CommandLog
          entries={commandEntries}
          isLoading={commandLogLoading}
          isError={commandLogError}
        />
      </Panel>

      {/* Row 3: Energy · 7d chart (span 2) | Top consumers (span 2) */}
      <Panel title="Energy" sub="7d" span={2} testId="energy-chart-card">
        <EnergyAreaChart
          dataPoints={energy}
          isLoading={energyLoading}
          isError={energyError}
        />
      </Panel>

      <Panel title="Top consumers" sub="7d" span={2} testId="top-consumers-card">
        <TopConsumersList
          consumers={consumers}
          isLoading={consumersLoading}
          isError={consumersError}
        />
      </Panel>
    </div>
  );
}
