/**
 * TanStack Query hooks for the Home butler dashboard API.
 *
 * Query-key strategy:
 * - homeKeys.snapshotStatus()                 → HomeSnapshotStatus
 * - homeKeys.devices(params)                  → HomeDeviceInventoryResponse
 * - homeKeys.maintenance(params)              → HomeMaintenanceItem[]
 * - homeKeys.energy(params)                   → HomeEnergyDataPoint[]
 * - homeKeys.energyTopConsumers(params)       → HomeTopConsumer[]
 * - homeKeys.commandLog(params)               → { data: HomeCommandLogEntry[] }
 *
 * All hooks are read-only (no mutations). No new HTTP routes are added —
 * all data comes from the existing home butler API endpoints.
 */

import { useQuery } from "@tanstack/react-query";

import {
  getHomeSnapshotStatus,
  getHomeDevices,
  getHomeMaintenance,
  getHomeEnergy,
  getHomeEnergyTopConsumers,
  getHomeCommandLog,
} from "@/api/client.ts";
import type {
  HomeDeviceInventoryResponse,
  HomeEnergyDataPoint,
  HomeTopConsumer,
  HomeMaintenanceItem,
  HomeSnapshotStatus,
  HomeCommandLogEntry,
} from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Query key factory
// ---------------------------------------------------------------------------

export const homeKeys = {
  all: ["home"] as const,
  snapshotStatus: () => [...homeKeys.all, "snapshot-status"] as const,
  devices: (params?: {
    domain?: string;
    area?: string;
    health?: "healthy" | "offline";
    page?: number;
    page_size?: number;
  }) => [...homeKeys.all, "devices", params] as const,
  maintenance: (params?: {
    category?: string;
    status?: "overdue" | "due" | "upcoming" | "ok";
  }) => [...homeKeys.all, "maintenance", params] as const,
  energy: (params?: { period?: "day" | "hour"; start?: string; end?: string }) =>
    [...homeKeys.all, "energy", params] as const,
  energyTopConsumers: (params?: { start?: string; end?: string }) =>
    [...homeKeys.all, "energy-top-consumers", params] as const,
  commandLog: (params?: { limit?: number; domain?: string }) =>
    [...homeKeys.all, "command-log", params] as const,
};

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/** Fetch entity snapshot freshness and aggregate statistics. */
export function useHomeSnapshotStatus() {
  return useQuery<HomeSnapshotStatus>({
    queryKey: homeKeys.snapshotStatus(),
    queryFn: () => getHomeSnapshotStatus(),
    retry: false,
  });
}

/** Fetch paginated device inventory with optional domain/area/health filters. */
export function useHomeDevices(params?: {
  domain?: string;
  area?: string;
  health?: "healthy" | "offline";
  page?: number;
  page_size?: number;
}) {
  return useQuery<HomeDeviceInventoryResponse>({
    queryKey: homeKeys.devices(params),
    queryFn: () => getHomeDevices(params),
    retry: false,
  });
}

/** Fetch maintenance items with optional category/status filter. */
export function useHomeMaintenance(params?: {
  category?: string;
  status?: "overdue" | "due" | "upcoming" | "ok";
}) {
  return useQuery<HomeMaintenanceItem[]>({
    queryKey: homeKeys.maintenance(params),
    queryFn: () => getHomeMaintenance(params),
    retry: false,
  });
}

/** Fetch energy consumption time-series data for the given period. */
export function useHomeEnergy(params?: {
  period?: "day" | "hour";
  start?: string;
  end?: string;
}) {
  return useQuery<HomeEnergyDataPoint[]>({
    queryKey: homeKeys.energy(params),
    queryFn: () => getHomeEnergy(params),
    retry: false,
  });
}

/** Fetch top energy-consuming devices for the given period. */
export function useHomeEnergyTopConsumers(params?: { start?: string; end?: string }) {
  return useQuery<HomeTopConsumer[]>({
    queryKey: homeKeys.energyTopConsumers(params),
    queryFn: () => getHomeEnergyTopConsumers(params),
    retry: false,
  });
}

/** Fetch the HA command audit log. */
export function useHomeCommandLog(params?: { limit?: number; domain?: string }) {
  return useQuery<{ data: HomeCommandLogEntry[]; meta?: Record<string, unknown> }>({
    queryKey: homeKeys.commandLog(params),
    queryFn: () => getHomeCommandLog(params),
    retry: false,
  });
}
