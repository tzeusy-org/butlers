// ---------------------------------------------------------------------------
// ButlerHomeDevicesTab — bu-11mug
//
// Devices bespoke tab for the Home butler detail page.
//
// Five sections (4-col grid):
//   1. KPI strip (full-width)              — GET /api/home/snapshot-status +
//                                            GET /api/home/devices?health=offline +
//                                            GET /api/home/maintenance?status=overdue
//   2. Device inventory table (3col)       — GET /api/home/devices
//   3. Maintenance queue (1col)            — GET /api/home/maintenance
//   4. Energy · 7d chart (2col)            — GET /api/home/energy + top-consumers
//   5. HA command log (2col)               — GET /api/home/command-log
//
// All data comes from hooks in use-home.ts. No new HTTP routes are added.
// ---------------------------------------------------------------------------

import type { ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useHomeSnapshotStatus, useHomeDevices, useHomeMaintenance, useHomeEnergy, useHomeEnergyTopConsumers, useHomeCommandLog } from "@/hooks/use-home";
import type { HomeDeviceEntry, HomeMaintenanceItem, HomeEnergyDataPoint, HomeTopConsumer, HomeCommandLogEntry } from "@/api/types";

// ---------------------------------------------------------------------------
// Shared primitives
// ---------------------------------------------------------------------------

function EmptyStateLine({ children }: { children: ReactNode }) {
  return (
    <p
      className="text-sm text-muted-foreground italic"
      data-testid="empty-state-line"
    >
      {children}
    </p>
  );
}

function LoadingSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-2" data-testid="loading-line">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-4 w-full rounded" />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section 1: KPI Strip
// ---------------------------------------------------------------------------

interface KpiItem {
  label: string;
  value: string | number;
  variant?: "default" | "destructive" | "warning";
}

function KpiCard({ label, value, variant = "default" }: KpiItem) {
  const valueClass =
    variant === "destructive"
      ? "text-destructive"
      : variant === "warning"
        ? "text-amber-600 dark:text-amber-400"
        : "";

  return (
    <div className="flex flex-col gap-0.5" data-testid="kpi-item">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span
        className={`text-2xl font-semibold tabular-nums ${valueClass}`}
        data-testid="kpi-value"
      >
        {value}
      </span>
    </div>
  );
}

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
  const freshnessLabel = newestCapturedAt
    ? formatRelativeTime(newestCapturedAt)
    : "—";

  if (isLoading && totalDevices == null) {
    return (
      <Card data-testid="kpi-strip">
        <CardHeader>
          <CardTitle className="text-sm font-medium">Devices at a glance</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-6">
            {Array.from({ length: 4 }, (_, i) => (
              <div key={i} className="space-y-1" data-testid="loading-line">
                <Skeleton className="h-3 w-20 rounded" />
                <Skeleton className="h-7 w-12 rounded" />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card data-testid="kpi-strip">
      <CardHeader>
        <CardTitle className="text-sm font-medium">Devices at a glance</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-6">
          <KpiCard label="Total devices" value={totalDevices ?? "—"} />
          <KpiCard
            label="Offline"
            value={offlineCount ?? "—"}
            variant={offlineCount != null && offlineCount > 0 ? "destructive" : "default"}
          />
          <KpiCard
            label="Overdue maintenance"
            value={overdueCount ?? "—"}
            variant={overdueCount != null && overdueCount > 0 ? "warning" : "default"}
          />
          <KpiCard label="Last snapshot" value={freshnessLabel} />
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section 2: Device Inventory Table
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
}

function DeviceInventory({ devices, isLoading }: DeviceInventoryProps) {
  if (isLoading && devices.length === 0) {
    return <LoadingSkeleton rows={5} />;
  }

  if (devices.length === 0) {
    return <EmptyStateLine>No devices in snapshot cache.</EmptyStateLine>;
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
              <td className="py-2 pr-2 font-mono text-xs">
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
// Section 3: Maintenance Queue
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
}

function MaintenanceQueue({ items, isLoading }: MaintenanceQueueProps) {
  if (isLoading && items.length === 0) {
    return <LoadingSkeleton rows={4} />;
  }

  if (items.length === 0) {
    return <EmptyStateLine>No maintenance items.</EmptyStateLine>;
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
                Due: {formatDate(item.next_due_at)}
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
// Section 4: Energy chart (7d)
// ---------------------------------------------------------------------------

interface EnergyChartProps {
  dataPoints: HomeEnergyDataPoint[];
  topConsumers: HomeTopConsumer[];
  isLoading: boolean;
}

function EnergyChart({ dataPoints, topConsumers, isLoading }: EnergyChartProps) {
  if (isLoading && dataPoints.length === 0) {
    return <LoadingSkeleton rows={4} />;
  }

  if (dataPoints.length === 0) {
    return <EmptyStateLine>No energy data available.</EmptyStateLine>;
  }

  const maxKwh = Math.max(...dataPoints.map((d) => d.total_kwh), 0.001);
  const top3 = topConsumers.slice(0, 3);

  return (
    <div data-testid="energy-chart">
      {/* Spark bar chart */}
      <div
        className="flex items-end gap-1 h-20 mb-4"
        aria-label="7-day energy chart"
        data-testid="energy-bars"
      >
        {dataPoints.map((point) => {
          const heightPct = (point.total_kwh / maxKwh) * 100;
          const dateLabel = formatDate(point.timestamp);
          return (
            <div
              key={point.timestamp}
              className="flex-1 flex flex-col items-center gap-1"
              title={`${dateLabel}: ${point.total_kwh.toFixed(2)} kWh`}
            >
              <div className="w-full relative flex-1 flex items-end">
                <div
                  className="w-full bg-primary/60 rounded-sm"
                  style={{ height: `${heightPct}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
      {/* Top consumers */}
      {top3.length > 0 && (
        <div className="space-y-1" data-testid="top-consumers">
          <p className="text-xs text-muted-foreground font-medium mb-2">Top consumers</p>
          {top3.map((c) => (
            <div
              key={c.entity_id}
              className="flex items-center justify-between text-xs"
              data-testid="top-consumer-item"
            >
              <span className="truncate text-muted-foreground">
                {c.friendly_name ?? c.entity_id}
              </span>
              <span className="tabular-nums ml-2 shrink-0">
                {c.total_kwh.toFixed(1)} kWh ({c.percentage.toFixed(0)}%)
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section 5: HA Command Log
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
}

function CommandLog({ entries, isLoading }: CommandLogProps) {
  if (isLoading && entries.length === 0) {
    return <LoadingSkeleton rows={5} />;
  }

  if (entries.length === 0) {
    return <EmptyStateLine>No commands logged.</EmptyStateLine>;
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
            <p className="text-xs text-muted-foreground">{formatRelativeTime(entry.issued_at)}</p>
          </div>
          <CommandResultBadge result={entry.result} />
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Date/time helpers
// ---------------------------------------------------------------------------

/** Format an ISO timestamp as a relative time string (e.g. "3m ago"). */
function formatRelativeTime(isoStr: string): string {
  try {
    const diffMs = Date.now() - new Date(isoStr).getTime();
    const diffSec = Math.floor(diffMs / 1000);
    if (diffSec < 60) return `${diffSec}s ago`;
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHrs = Math.floor(diffMin / 60);
    if (diffHrs < 24) return `${diffHrs}h ago`;
    const diffDays = Math.floor(diffHrs / 24);
    return `${diffDays}d ago`;
  } catch {
    return isoStr;
  }
}

/** Format an ISO timestamp as a short date string (YYYY-MM-DD). */
function formatDate(isoStr: string): string {
  try {
    return new Date(isoStr).toISOString().slice(0, 10);
  } catch {
    return isoStr;
  }
}

// ---------------------------------------------------------------------------
// Main tab component
// ---------------------------------------------------------------------------

export default function ButlerHomeDevicesTab() {
  // Section 1: KPI strip — snapshot status
  const { data: snapshotStatus, isLoading: snapshotLoading } = useHomeSnapshotStatus();

  // Section 1 KPI: offline device count
  const { data: offlineDevices, isLoading: offlineLoading } = useHomeDevices({ health: "offline", page: 1, page_size: 1 });

  // Section 1 KPI: overdue maintenance count
  const { data: overdueItems, isLoading: overdueLoading } = useHomeMaintenance({ status: "overdue" });

  // Section 2: Full device inventory (page 1, first 50)
  const { data: deviceInventory, isLoading: devicesLoading } = useHomeDevices({ page: 1, page_size: 50 });

  // Section 3: All maintenance items (sorted by urgency server-side)
  const { data: maintenanceItems, isLoading: maintenanceLoading } = useHomeMaintenance();

  // Section 4: Energy time-series (7d, day granularity)
  const { data: energyData, isLoading: energyLoading } = useHomeEnergy({ period: "day" });

  // Section 4: Top consumers (7d)
  const { data: topConsumers, isLoading: consumersLoading } = useHomeEnergyTopConsumers();

  // Section 5: HA command log (last 20)
  const { data: commandLogResp, isLoading: commandLogLoading } = useHomeCommandLog({ limit: 20 });

  const kpiLoading = snapshotLoading || offlineLoading || overdueLoading;

  const offlineCount = offlineDevices?.meta.total_count;
  const overdueCount = overdueItems?.length;
  const devices = deviceInventory?.data ?? [];
  const maintenance = maintenanceItems ?? [];
  const energy = energyData ?? [];
  const consumers = topConsumers ?? [];
  const commandEntries = commandLogResp?.data ?? [];

  return (
    <div className="space-y-6" data-testid="home-devices-tab">
      {/* Section 1: KPI strip — full width */}
      <KpiStrip
        totalDevices={snapshotStatus?.total_entities}
        offlineCount={offlineCount}
        overdueCount={overdueCount}
        newestCapturedAt={snapshotStatus?.newest_captured_at}
        isLoading={kpiLoading}
      />

      {/* Sections 2–3: Device inventory (3col) + Maintenance queue (1col) */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        <Card className="lg:col-span-3" data-testid="device-inventory-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Device inventory</CardTitle>
          </CardHeader>
          <CardContent className="max-h-[480px] overflow-y-auto">
            <DeviceInventory devices={devices} isLoading={devicesLoading} />
          </CardContent>
        </Card>

        <Card className="lg:col-span-1" data-testid="maintenance-queue-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Maintenance queue</CardTitle>
          </CardHeader>
          <CardContent className="max-h-[480px] overflow-y-auto">
            <MaintenanceQueue items={maintenance} isLoading={maintenanceLoading} />
          </CardContent>
        </Card>
      </div>

      {/* Sections 4–5: Energy chart (2col) + Command log (2col) */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card data-testid="energy-chart-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Energy · 7d</CardTitle>
          </CardHeader>
          <CardContent>
            <EnergyChart
              dataPoints={energy}
              topConsumers={consumers}
              isLoading={energyLoading || consumersLoading}
            />
          </CardContent>
        </Card>

        <Card data-testid="command-log-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">HA command log</CardTitle>
          </CardHeader>
          <CardContent className="max-h-[400px] overflow-y-auto">
            <CommandLog entries={commandEntries} isLoading={commandLogLoading} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
