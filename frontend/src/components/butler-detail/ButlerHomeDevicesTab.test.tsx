// @vitest-environment jsdom
/**
 * ButlerHomeDevicesTab — RTL tests pinning all 5 sections.
 *
 * Tests:
 *  - All 5 sections render (KPI strip, device inventory, maintenance queue,
 *    energy chart, command log)
 *  - Loading states show loading placeholders, not empty-state text
 *  - Empty states shown when data is absent
 *  - KPI values render with data (offline count, overdue count, freshness)
 *  - Device inventory table renders rows
 *  - Maintenance queue renders items with status badges
 *  - Energy chart renders bars and top consumers
 *  - Command log renders entries
 *
 * bead: bu-11mug
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ButlerHomeDevicesTab from "./ButlerHomeDevicesTab";

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-home", () => ({
  useHomeSnapshotStatus: vi.fn(),
  useHomeDevices: vi.fn(),
  useHomeMaintenance: vi.fn(),
  useHomeEnergy: vi.fn(),
  useHomeEnergyTopConsumers: vi.fn(),
  useHomeCommandLog: vi.fn(),
}));

import {
  useHomeSnapshotStatus,
  useHomeDevices,
  useHomeMaintenance,
  useHomeEnergy,
  useHomeEnergyTopConsumers,
  useHomeCommandLog,
} from "@/hooks/use-home";

// ---------------------------------------------------------------------------
// Fixture data
// ---------------------------------------------------------------------------

const SNAPSHOT_STATUS = {
  total_entities: 42,
  domains: { light: 10, sensor: 20, switch: 12 },
  oldest_captured_at: "2026-05-10T06:00:00Z",
  newest_captured_at: "2026-05-10T07:55:00Z",
};

const OFFLINE_DEVICES_RESP = {
  data: [
    {
      entity_id: "sensor.basement_motion",
      state: "unavailable",
      friendly_name: "Basement Motion",
      area_name: "basement",
      domain: "sensor",
      last_updated: "2026-05-09T22:00:00Z",
      health_status: "offline" as const,
    },
  ],
  meta: { page: 1, page_size: 1, total_count: 1, total_pages: 1 },
};

const ALL_DEVICES_RESP = {
  data: [
    {
      entity_id: "light.living_room",
      state: "on",
      friendly_name: "Living Room Light",
      area_name: "living_room",
      domain: "light",
      last_updated: "2026-05-10T07:00:00Z",
      health_status: "healthy" as const,
    },
    {
      entity_id: "sensor.basement_motion",
      state: "unavailable",
      friendly_name: "Basement Motion",
      area_name: "basement",
      domain: "sensor",
      last_updated: "2026-05-09T22:00:00Z",
      health_status: "offline" as const,
    },
  ],
  meta: { page: 1, page_size: 50, total_count: 2, total_pages: 1 },
};

const OVERDUE_MAINTENANCE = [
  {
    id: "maint-1",
    name: "HVAC filter replacement",
    category: "HVAC",
    interval_days: 90,
    last_completed_at: "2025-11-01T00:00:00Z",
    next_due_at: "2026-01-30T00:00:00Z",
    status: "overdue" as const,
    notes: null,
  },
];

const ALL_MAINTENANCE = [
  {
    id: "maint-1",
    name: "HVAC filter replacement",
    category: "HVAC",
    interval_days: 90,
    last_completed_at: "2025-11-01T00:00:00Z",
    next_due_at: "2026-01-30T00:00:00Z",
    status: "overdue" as const,
    notes: null,
  },
  {
    id: "maint-2",
    name: "Smoke detector battery",
    category: "Safety",
    interval_days: 365,
    last_completed_at: null,
    next_due_at: "2026-06-01T00:00:00Z",
    status: "upcoming" as const,
    notes: null,
  },
];

const ENERGY_DATA = [
  { timestamp: "2026-05-04T00:00:00Z", total_kwh: 12.5, devices: {} },
  { timestamp: "2026-05-05T00:00:00Z", total_kwh: 14.2, devices: {} },
  { timestamp: "2026-05-06T00:00:00Z", total_kwh: 11.8, devices: {} },
  { timestamp: "2026-05-07T00:00:00Z", total_kwh: 13.1, devices: {} },
  { timestamp: "2026-05-08T00:00:00Z", total_kwh: 16.0, devices: {} },
  { timestamp: "2026-05-09T00:00:00Z", total_kwh: 10.4, devices: {} },
  { timestamp: "2026-05-10T00:00:00Z", total_kwh: 9.2, devices: {} },
];

const TOP_CONSUMERS = [
  { entity_id: "sensor.hvac_energy", friendly_name: "HVAC", total_kwh: 40.0, percentage: 45.2 },
  { entity_id: "sensor.water_heater_energy", friendly_name: "Water Heater", total_kwh: 22.0, percentage: 24.9 },
  { entity_id: "sensor.dryer_energy", friendly_name: "Dryer", total_kwh: 10.0, percentage: 11.3 },
];

const COMMAND_LOG_RESP = {
  data: [
    {
      id: 1,
      domain: "light",
      service: "turn_on",
      target: { entity_id: "light.living_room" },
      data: {},
      result: { success: true },
      context_id: "ctx-1",
      issued_at: "2026-05-10T07:30:00Z",
    },
    {
      id: 2,
      domain: "climate",
      service: "set_temperature",
      target: { area_id: "bedroom" },
      data: { temperature: 68 },
      result: { success: true },
      context_id: "ctx-2",
      issued_at: "2026-05-10T07:00:00Z",
    },
  ],
  meta: { total: 2, offset: 0, limit: 20 },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderTab() {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <ButlerHomeDevicesTab />
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Mock setup helpers
// ---------------------------------------------------------------------------

function setupWithData() {
  vi.mocked(useHomeSnapshotStatus).mockReturnValue({
    data: SNAPSHOT_STATUS,
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useHomeSnapshotStatus>);

  // useHomeDevices is called twice: once with health=offline, once without
  vi.mocked(useHomeDevices).mockImplementation((params) => {
    if (params?.health === "offline") {
      return {
        data: OFFLINE_DEVICES_RESP,
        isLoading: false,
        isError: false,
      } as ReturnType<typeof useHomeDevices>;
    }
    return {
      data: ALL_DEVICES_RESP,
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useHomeDevices>;
  });

  vi.mocked(useHomeMaintenance).mockImplementation((params) => {
    if (params?.status === "overdue") {
      return {
        data: OVERDUE_MAINTENANCE,
        isLoading: false,
        isError: false,
      } as ReturnType<typeof useHomeMaintenance>;
    }
    return {
      data: ALL_MAINTENANCE,
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useHomeMaintenance>;
  });

  vi.mocked(useHomeEnergy).mockReturnValue({
    data: ENERGY_DATA,
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useHomeEnergy>);

  vi.mocked(useHomeEnergyTopConsumers).mockReturnValue({
    data: TOP_CONSUMERS,
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useHomeEnergyTopConsumers>);

  vi.mocked(useHomeCommandLog).mockReturnValue({
    data: COMMAND_LOG_RESP,
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useHomeCommandLog>);
}

function setupEmpty() {
  vi.mocked(useHomeSnapshotStatus).mockReturnValue({
    data: { total_entities: 0, domains: {}, oldest_captured_at: null, newest_captured_at: null },
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useHomeSnapshotStatus>);

  vi.mocked(useHomeDevices).mockReturnValue({
    data: { data: [], meta: { page: 1, page_size: 50, total_count: 0, total_pages: 0 } },
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useHomeDevices>);

  vi.mocked(useHomeMaintenance).mockReturnValue({
    data: [],
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useHomeMaintenance>);

  vi.mocked(useHomeEnergy).mockReturnValue({
    data: [],
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useHomeEnergy>);

  vi.mocked(useHomeEnergyTopConsumers).mockReturnValue({
    data: [],
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useHomeEnergyTopConsumers>);

  vi.mocked(useHomeCommandLog).mockReturnValue({
    data: { data: [], meta: {} },
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useHomeCommandLog>);
}

function setupLoading() {
  vi.mocked(useHomeSnapshotStatus).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as ReturnType<typeof useHomeSnapshotStatus>);

  vi.mocked(useHomeDevices).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as ReturnType<typeof useHomeDevices>);

  vi.mocked(useHomeMaintenance).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as ReturnType<typeof useHomeMaintenance>);

  vi.mocked(useHomeEnergy).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as ReturnType<typeof useHomeEnergy>);

  vi.mocked(useHomeEnergyTopConsumers).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as ReturnType<typeof useHomeEnergyTopConsumers>);

  vi.mocked(useHomeCommandLog).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as ReturnType<typeof useHomeCommandLog>);
}

function setupErrorState() {
  vi.mocked(useHomeSnapshotStatus).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as ReturnType<typeof useHomeSnapshotStatus>);

  vi.mocked(useHomeDevices).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as ReturnType<typeof useHomeDevices>);

  vi.mocked(useHomeMaintenance).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as ReturnType<typeof useHomeMaintenance>);

  vi.mocked(useHomeEnergy).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as ReturnType<typeof useHomeEnergy>);

  vi.mocked(useHomeEnergyTopConsumers).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as ReturnType<typeof useHomeEnergyTopConsumers>);

  vi.mocked(useHomeCommandLog).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as ReturnType<typeof useHomeCommandLog>);
}

// ---------------------------------------------------------------------------
// Tests: all 5 sections present
// ---------------------------------------------------------------------------

describe("ButlerHomeDevicesTab — all 5 sections present", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the KPI strip section", () => {
    renderTab();
    expect(screen.getByTestId("kpi-strip")).toBeDefined();
  });

  it("renders the device inventory card", () => {
    renderTab();
    expect(screen.getByTestId("device-inventory-card")).toBeDefined();
  });

  it("renders the maintenance queue card", () => {
    renderTab();
    expect(screen.getByTestId("maintenance-queue-card")).toBeDefined();
  });

  it("renders the energy chart card", () => {
    renderTab();
    expect(screen.getByTestId("energy-chart-card")).toBeDefined();
  });

  it("renders the command log card", () => {
    renderTab();
    expect(screen.getByTestId("command-log-card")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: KPI values
// ---------------------------------------------------------------------------

describe("ButlerHomeDevicesTab — KPI values", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders 4 KPI items", () => {
    renderTab();
    const kpiItems = screen.getAllByTestId("kpi-item");
    expect(kpiItems.length).toBeGreaterThanOrEqual(4);
  });

  it("renders total device count KPI", () => {
    renderTab();
    const values = screen.getAllByTestId("kpi-value");
    const texts = values.map((v) => v.textContent ?? "");
    expect(texts.some((t) => t === "42")).toBe(true);
  });

  it("renders offline device count KPI", () => {
    renderTab();
    const values = screen.getAllByTestId("kpi-value");
    const texts = values.map((v) => v.textContent ?? "");
    expect(texts.some((t) => t === "1")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Tests: Device inventory
// ---------------------------------------------------------------------------

describe("ButlerHomeDevicesTab — device inventory", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders device rows", () => {
    renderTab();
    const rows = screen.getAllByTestId("device-inventory-row");
    expect(rows.length).toBeGreaterThanOrEqual(1);
  });

  it("renders device friendly name", () => {
    renderTab();
    expect(screen.getByText("Living Room Light")).toBeDefined();
  });

  it("renders offline health badge", () => {
    renderTab();
    const badges = screen.getAllByText("offline");
    expect(badges.length).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Tests: Maintenance queue
// ---------------------------------------------------------------------------

describe("ButlerHomeDevicesTab — maintenance queue", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders maintenance items", () => {
    renderTab();
    const items = screen.getAllByTestId("maintenance-item");
    expect(items.length).toBeGreaterThanOrEqual(1);
  });

  it("renders maintenance item name", () => {
    renderTab();
    expect(screen.getByText("HVAC filter replacement")).toBeDefined();
  });

  it("renders overdue status badge", () => {
    renderTab();
    const badges = screen.getAllByText("overdue");
    expect(badges.length).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Tests: Energy chart
// ---------------------------------------------------------------------------

describe("ButlerHomeDevicesTab — energy chart", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders energy bars", () => {
    renderTab();
    expect(screen.getByTestId("energy-bars")).toBeDefined();
  });

  it("renders top consumers", () => {
    renderTab();
    expect(screen.getByTestId("top-consumers")).toBeDefined();
  });

  it("renders top consumer items", () => {
    renderTab();
    const consumers = screen.getAllByTestId("top-consumer-item");
    expect(consumers.length).toBeGreaterThanOrEqual(1);
  });

  it("renders HVAC as top consumer in top consumers list", () => {
    renderTab();
    const consumersSection = screen.getByTestId("top-consumers");
    expect(consumersSection.textContent).toContain("HVAC");
  });
});

// ---------------------------------------------------------------------------
// Tests: Command log
// ---------------------------------------------------------------------------

describe("ButlerHomeDevicesTab — command log", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders command log entries", () => {
    renderTab();
    const entries = screen.getAllByTestId("command-log-entry");
    expect(entries.length).toBeGreaterThanOrEqual(1);
  });

  it("renders domain.service format", () => {
    renderTab();
    expect(screen.getByText("light.turn_on")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Empty states
// ---------------------------------------------------------------------------

describe("ButlerHomeDevicesTab — empty states", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupEmpty();
  });
  afterEach(() => cleanup());

  it("shows empty state for device inventory when no devices", () => {
    renderTab();
    expect(screen.queryByTestId("device-inventory-table")).toBeNull();
  });

  it("shows empty state for maintenance when no items", () => {
    renderTab();
    expect(screen.queryByTestId("maintenance-queue")).toBeNull();
  });

  it("shows empty state for energy chart when no data", () => {
    renderTab();
    expect(screen.queryByTestId("energy-bars")).toBeNull();
  });

  it("shows empty state for command log when no entries", () => {
    renderTab();
    expect(screen.queryByTestId("command-log")).toBeNull();
  });

  it("shows empty-state-line elements", () => {
    renderTab();
    const emptyLines = screen.getAllByTestId("empty-state-line");
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Tests: Loading states
// ---------------------------------------------------------------------------

describe("ButlerHomeDevicesTab — loading states", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupLoading();
  });
  afterEach(() => cleanup());

  it("shows loading placeholders while loading", () => {
    renderTab();
    const loadingLines = screen.getAllByTestId("loading-line");
    expect(loadingLines.length).toBeGreaterThanOrEqual(1);
  });

  it("does not show empty-state-line while loading", () => {
    renderTab();
    expect(screen.queryByTestId("empty-state-line")).toBeNull();
  });

  it("does not render device-inventory-table while loading", () => {
    renderTab();
    expect(screen.queryByTestId("device-inventory-table")).toBeNull();
  });

  it("does not render maintenance-queue while loading", () => {
    renderTab();
    expect(screen.queryByTestId("maintenance-queue")).toBeNull();
  });

  it("does not render energy-bars while loading", () => {
    renderTab();
    expect(screen.queryByTestId("energy-bars")).toBeNull();
  });

  it("does not render command-log while loading", () => {
    renderTab();
    expect(screen.queryByTestId("command-log")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: Error states
// ---------------------------------------------------------------------------

describe("ButlerHomeDevicesTab — error states", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupErrorState();
  });
  afterEach(() => cleanup());

  it("shows error-state-line elements when queries fail", () => {
    renderTab();
    const errorLines = screen.getAllByTestId("error-state-line");
    expect(errorLines.length).toBeGreaterThanOrEqual(1);
  });

  it("does not show empty-state-line elements when queries fail", () => {
    renderTab();
    expect(screen.queryByTestId("empty-state-line")).toBeNull();
  });

  it("does not render device-inventory-table when devices query fails", () => {
    renderTab();
    expect(screen.queryByTestId("device-inventory-table")).toBeNull();
  });

  it("does not render maintenance-queue when maintenance query fails", () => {
    renderTab();
    expect(screen.queryByTestId("maintenance-queue")).toBeNull();
  });

  it("does not render energy-bars when energy query fails", () => {
    renderTab();
    expect(screen.queryByTestId("energy-bars")).toBeNull();
  });

  it("does not render command-log when command-log query fails", () => {
    renderTab();
    expect(screen.queryByTestId("command-log")).toBeNull();
  });
});
