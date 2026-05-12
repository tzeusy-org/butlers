// @vitest-environment jsdom
/**
 * ButlerHomeDevicesTab — RTL tests pinning the restyled 4-col panel grid.
 *
 * Tests:
 *  - All sections render (KPI strip, device inventory, maintenance queue,
 *    command log, energy chart, top consumers)
 *  - KPI values render correctly (total devices, offline count, overdue count)
 *  - Device inventory table renders rows and device names
 *  - Maintenance queue renders items and status badges (with/without overdue)
 *  - Recent commands list renders entries
 *  - Energy chart renders with/without data
 *  - Top consumers list renders with consumer items
 *  - Loading states show placeholders, not empty-state text
 *  - Empty states shown when data is absent
 *  - Error states render error lines
 *
 * bead: bu-iuol4.32
 */

import { createElement, type ReactNode } from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Mock recharts — avoids SVG/canvas complexity in jsdom
// ---------------------------------------------------------------------------

vi.mock("recharts", () => {
  const AreaChart = ({
    children,
  }: {
    data?: Array<Record<string, unknown>>;
    children?: ReactNode;
  }) => createElement("div", { "data-testid": "recharts-area-chart" }, children);

  const Area = ({ dataKey }: { dataKey: string }) =>
    createElement("div", { "data-testid": `recharts-area-${dataKey}` });

  const XAxis = () => null;
  const YAxis = () => null;
  const Tooltip = () => null;
  const ResponsiveContainer = ({ children }: { children?: ReactNode }) =>
    createElement(
      "div",
      { "data-testid": "recharts-responsive-container" },
      children,
    );

  return { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer };
});

// Stub <Time> to avoid date-formatting complexity in unit tests
vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => createElement("time", { dateTime: value }, value),
}));

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

import ButlerHomeDevicesTab from "./ButlerHomeDevicesTab";

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
  {
    entity_id: "sensor.water_heater_energy",
    friendly_name: "Water Heater",
    total_kwh: 22.0,
    percentage: 24.9,
  },
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
  } as unknown as ReturnType<typeof useHomeSnapshotStatus>);

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
  } as unknown as ReturnType<typeof useHomeCommandLog>);
}

function setupEmpty() {
  vi.mocked(useHomeSnapshotStatus).mockReturnValue({
    data: {
      total_entities: 0,
      domains: {},
      oldest_captured_at: null,
      newest_captured_at: null,
    },
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useHomeSnapshotStatus>);

  vi.mocked(useHomeDevices).mockReturnValue({
    data: { data: [], meta: { page: 1, page_size: 50, total_count: 0, total_pages: 0 } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useHomeDevices>);

  vi.mocked(useHomeMaintenance).mockReturnValue({
    data: [],
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useHomeMaintenance>);

  vi.mocked(useHomeEnergy).mockReturnValue({
    data: [],
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useHomeEnergy>);

  vi.mocked(useHomeEnergyTopConsumers).mockReturnValue({
    data: [],
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useHomeEnergyTopConsumers>);

  vi.mocked(useHomeCommandLog).mockReturnValue({
    data: { data: [], meta: {} },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useHomeCommandLog>);
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
// Tests: all sections present
// ---------------------------------------------------------------------------

describe("ButlerHomeDevicesTab — all sections present", () => {
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

  it("renders the command log card", () => {
    renderTab();
    expect(screen.getByTestId("command-log-card")).toBeDefined();
  });

  it("renders the energy chart card", () => {
    renderTab();
    expect(screen.getByTestId("energy-chart-card")).toBeDefined();
  });

  it("renders the top consumers card", () => {
    renderTab();
    expect(screen.getByTestId("top-consumers-card")).toBeDefined();
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

  it("renders 4 KPI cells", () => {
    renderTab();
    const kpiItems = screen.getAllByTestId("kpi-item");
    expect(kpiItems.length).toBeGreaterThanOrEqual(4);
  });

  it("renders total device count KPI", () => {
    renderTab();
    expect(screen.getByText("42")).toBeDefined();
  });

  it("renders offline device count KPI", () => {
    const { container } = renderTab();
    const kpiStrip = container.querySelector('[data-testid="kpi-strip"]');
    expect(kpiStrip).not.toBeNull();
    // Offline count is 1 — assert it appears in the strip (offline + overdue may both be 1)
    expect(kpiStrip!.innerHTML).toContain(">1<");
  });

  it("renders overdue maintenance count KPI", () => {
    const { container } = renderTab();
    // OVERDUE_MAINTENANCE has 1 item — assert the rendered value is exactly "1"
    // within the KPI strip, not just that the strip exists.
    const kpiStrip = container.querySelector('[data-testid="kpi-strip"]');
    expect(kpiStrip).not.toBeNull();
    expect(kpiStrip!.innerHTML).toContain(">1<");
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

  it("renders maintenance queue when only non-overdue items present", () => {
    vi.mocked(useHomeMaintenance).mockImplementation((params) => {
      if (params?.status === "overdue") {
        return {
          data: [],
          isLoading: false,
          isError: false,
        } as unknown as ReturnType<typeof useHomeMaintenance>;
      }
      return {
        data: [ALL_MAINTENANCE[1]], // only the upcoming item
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useHomeMaintenance>;
    });
    renderTab();
    expect(screen.getByTestId("maintenance-queue")).toBeDefined();
    expect(screen.getByText("Smoke detector battery")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Recent commands
// ---------------------------------------------------------------------------

describe("ButlerHomeDevicesTab — recent commands", () => {
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
// Tests: Energy chart (with/without data)
// ---------------------------------------------------------------------------

describe("ButlerHomeDevicesTab — energy chart", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the recharts area chart when data is present", () => {
    renderTab();
    expect(screen.getByTestId("energy-chart")).toBeDefined();
    expect(screen.getByTestId("recharts-area-chart")).toBeDefined();
  });

  it("shows empty state when energy data is empty", () => {
    vi.mocked(useHomeEnergy).mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useHomeEnergy>);
    renderTab();
    expect(screen.queryByTestId("energy-chart")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: Top consumers list
// ---------------------------------------------------------------------------

describe("ButlerHomeDevicesTab — top consumers", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders top consumers list", () => {
    renderTab();
    expect(screen.getByTestId("top-consumers")).toBeDefined();
  });

  it("renders top consumer items", () => {
    renderTab();
    const items = screen.getAllByTestId("top-consumer-item");
    expect(items.length).toBeGreaterThanOrEqual(1);
  });

  it("renders HVAC as top consumer", () => {
    renderTab();
    const consumersSection = screen.getByTestId("top-consumers");
    expect(consumersSection.textContent).toContain("HVAC");
  });

  it("shows empty state when no consumer data", () => {
    vi.mocked(useHomeEnergyTopConsumers).mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useHomeEnergyTopConsumers>);
    renderTab();
    expect(screen.queryByTestId("top-consumers")).toBeNull();
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
    expect(screen.queryByTestId("energy-chart")).toBeNull();
  });

  it("shows empty state for command log when no entries", () => {
    renderTab();
    expect(screen.queryByTestId("command-log")).toBeNull();
  });

  it("shows empty state for top consumers when no data", () => {
    renderTab();
    expect(screen.queryByTestId("top-consumers")).toBeNull();
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

  it("does not render energy-chart while loading", () => {
    renderTab();
    expect(screen.queryByTestId("energy-chart")).toBeNull();
  });

  it("does not render command-log while loading", () => {
    renderTab();
    expect(screen.queryByTestId("command-log")).toBeNull();
  });

  it("does not render top-consumers while loading", () => {
    renderTab();
    expect(screen.queryByTestId("top-consumers")).toBeNull();
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

  it("does not render energy-chart when energy query fails", () => {
    renderTab();
    expect(screen.queryByTestId("energy-chart")).toBeNull();
  });

  it("does not render command-log when command-log query fails", () => {
    renderTab();
    expect(screen.queryByTestId("command-log")).toBeNull();
  });

  it("does not render top-consumers when top-consumers query fails", () => {
    renderTab();
    expect(screen.queryByTestId("top-consumers")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: KPI strip error states — per-cell error handling
// ---------------------------------------------------------------------------

describe("ButlerHomeDevicesTab — KPI strip error states", () => {
  afterEach(() => cleanup());

  it("shows error indicator in total-devices cell when snapshot query fails", () => {
    vi.resetAllMocks();
    setupWithData();
    vi.mocked(useHomeSnapshotStatus).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    } as ReturnType<typeof useHomeSnapshotStatus>);

    const { container } = renderTab();
    const kpiStrip = container.querySelector('[data-testid="kpi-strip"]');
    expect(kpiStrip).not.toBeNull();
    // snapshot error affects 2 cells: total devices + last snapshot — verify each message
    expect(kpiStrip!.textContent).toContain("Failed to load device count.");
    expect(kpiStrip!.textContent).toContain("Failed to load snapshot time.");
  });

  it("shows error indicator in offline-count cell when offline devices query fails", () => {
    vi.resetAllMocks();
    setupWithData();
    vi.mocked(useHomeDevices).mockImplementation((params) => {
      if (params?.health === "offline") {
        return {
          data: undefined,
          isLoading: false,
          isError: true,
        } as ReturnType<typeof useHomeDevices>;
      }
      return {
        data: ALL_DEVICES_RESP,
        isLoading: false,
        isError: false,
      } as ReturnType<typeof useHomeDevices>;
    });

    const { container } = renderTab();
    const kpiStrip = container.querySelector('[data-testid="kpi-strip"]');
    expect(kpiStrip).not.toBeNull();
    // verify the offline cell specifically shows its error message
    expect(kpiStrip!.textContent).toContain("Failed to load offline count.");
  });

  it("shows error indicator in overdue-count cell when overdue maintenance query fails", () => {
    vi.resetAllMocks();
    setupWithData();
    vi.mocked(useHomeMaintenance).mockImplementation((params) => {
      if (params?.status === "overdue") {
        return {
          data: undefined,
          isLoading: false,
          isError: true,
        } as ReturnType<typeof useHomeMaintenance>;
      }
      return {
        data: ALL_MAINTENANCE,
        isLoading: false,
        isError: false,
      } as ReturnType<typeof useHomeMaintenance>;
    });

    const { container } = renderTab();
    const kpiStrip = container.querySelector('[data-testid="kpi-strip"]');
    expect(kpiStrip).not.toBeNull();
    const errorLines = kpiStrip!.querySelectorAll('[data-testid="error-state-line"]');
    expect(errorLines.length).toBeGreaterThanOrEqual(1);
  });

  it("shows error indicator in last-snapshot cell when snapshot query fails", () => {
    vi.resetAllMocks();
    setupWithData();
    vi.mocked(useHomeSnapshotStatus).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    } as ReturnType<typeof useHomeSnapshotStatus>);

    const { container } = renderTab();
    const kpiStrip = container.querySelector('[data-testid="kpi-strip"]');
    expect(kpiStrip).not.toBeNull();
    // last-snapshot cell (4th panel) shows its specific error message
    const kpiItems = kpiStrip!.querySelectorAll('[data-testid="kpi-item"]');
    expect(kpiItems[3].textContent).toContain("Failed to load snapshot time.");
  });

  it("does not show error indicators in KPI strip when all queries succeed", () => {
    vi.resetAllMocks();
    setupWithData();

    const { container } = renderTab();
    const kpiStrip = container.querySelector('[data-testid="kpi-strip"]');
    expect(kpiStrip).not.toBeNull();
    const errorLines = kpiStrip!.querySelectorAll('[data-testid="error-state-line"]');
    expect(errorLines.length).toBe(0);
  });

  it("does not show KpiCell values when snapshot query fails", () => {
    vi.resetAllMocks();
    setupWithData();
    vi.mocked(useHomeSnapshotStatus).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    } as ReturnType<typeof useHomeSnapshotStatus>);

    const { container } = renderTab();
    const kpiStrip = container.querySelector('[data-testid="kpi-strip"]');
    expect(kpiStrip).not.toBeNull();
    // Total devices count (42) should not appear in the KPI strip when snapshot errors
    expect(kpiStrip!.textContent).not.toContain("42");
  });
});
