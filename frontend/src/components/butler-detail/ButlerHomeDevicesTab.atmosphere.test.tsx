// @vitest-environment jsdom

import { createElement, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const homeMocks = vi.hoisted(() => ({
  useHomeSnapshotStatus: vi.fn(),
  useHomeDevices: vi.fn(),
  useHomeMaintenance: vi.fn(),
  useHomeEnergy: vi.fn(),
  useHomeEnergyTopConsumers: vi.fn(),
  useHomeCommandLog: vi.fn(),
  useHomeAtmosphereCurrent: vi.fn(),
  useUpdateHomeAtmosphereLocation: vi.fn(),
  mutateAtmosphereLocation: vi.fn(),
}));

vi.mock("recharts", () => ({
  AreaChart: ({ children }: { children?: ReactNode }) => createElement("div", null, children),
  Area: () => null,
  XAxis: () => null,
  YAxis: () => null,
  Tooltip: () => null,
  ResponsiveContainer: ({ children }: { children?: ReactNode }) =>
    createElement("div", null, children),
}));

vi.mock("@/hooks/use-home", () => homeMocks);

import ButlerHomeDevicesTab from "./ButlerHomeDevicesTab.tsx";

const unconfigured = {
  configured: false,
  latitude: null,
  longitude: null,
  stale: false,
  source_error: false,
  last_error: null,
};

const configured = {
  configured: true,
  latitude: 1.3521,
  longitude: 103.8198,
  stale: false,
  source_error: false,
  last_error: null,
};

function setupHomeData(atmosphere = unconfigured) {
  homeMocks.useHomeSnapshotStatus.mockReturnValue({
    data: { total_entities: 0, domains: {}, oldest_captured_at: null, newest_captured_at: null },
    isLoading: false,
    isError: false,
  });
  homeMocks.useHomeDevices.mockReturnValue({
    data: { data: [], meta: { page: 1, page_size: 50, total_count: 0, total_pages: 0 } },
    isLoading: false,
    isError: false,
  });
  homeMocks.useHomeMaintenance.mockReturnValue({ data: [], isLoading: false, isError: false });
  homeMocks.useHomeEnergy.mockReturnValue({ data: [], isLoading: false, isError: false });
  homeMocks.useHomeEnergyTopConsumers.mockReturnValue({
    data: [],
    isLoading: false,
    isError: false,
  });
  homeMocks.useHomeCommandLog.mockReturnValue({
    data: { data: [] },
    isLoading: false,
    isError: false,
  });
  homeMocks.useHomeAtmosphereCurrent.mockReturnValue({
    data: atmosphere,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });
  homeMocks.useUpdateHomeAtmosphereLocation.mockReturnValue({
    mutate: homeMocks.mutateAtmosphereLocation,
    isPending: false,
  });
}

function renderTab() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <ButlerHomeDevicesTab />
    </QueryClientProvider>,
  );
  return {
    ...view,
    rerenderTab: () =>
      view.rerender(
        <QueryClientProvider client={queryClient}>
          <ButlerHomeDevicesTab />
        </QueryClientProvider>,
      ),
  };
}

async function enterCoordinates(user: ReturnType<typeof userEvent.setup>) {
  const latitude = screen.getByLabelText("Latitude");
  const longitude = screen.getByLabelText("Longitude");
  await user.clear(latitude);
  await user.type(latitude, "1.3521");
  await user.clear(longitude);
  await user.type(longitude, "103.8198");
}

beforeEach(() => {
  vi.clearAllMocks();
  setupHomeData();
});

afterEach(() => cleanup());

describe("ButlerHomeDevicesTab atmosphere location panel", () => {
  it("shows an unconfigured owner form with native numeric coordinate fields", () => {
    renderTab();

    expect(screen.getByText("Atmosphere location")).toBeTruthy();
    expect(screen.getByText("No home location is configured yet.")).toBeTruthy();
    expect(screen.getByLabelText("Latitude").getAttribute("type")).toBe("number");
    expect(screen.getByLabelText("Latitude").getAttribute("min")).toBe("-90");
    expect(screen.getByLabelText("Longitude").getAttribute("max")).toBe("180");
    expect(screen.getByRole("button", { name: "Save home location" })).toBeTruthy();
  });

  it("hydrates the controlled form from the configured current location", () => {
    setupHomeData(configured);
    renderTab();

    expect(screen.getByText("Home location is configured.")).toBeTruthy();
    expect((screen.getByLabelText("Latitude") as HTMLInputElement).value).toBe("1.3521");
    expect((screen.getByLabelText("Longitude") as HTMLInputElement).value).toBe("103.8198");
  });

  it("shows an honest stale source error without discarding the saved configuration", () => {
    setupHomeData({
      ...configured,
      stale: true,
      source_error: true,
      last_error: "Open-Meteo timed out",
    });
    renderTab();

    expect(screen.getByRole("alert").textContent).toContain(
      "The atmosphere feed is stale and its source last failed.",
    );
    expect(screen.getByRole("alert").textContent).toContain("Open-Meteo timed out");
    expect((screen.getByLabelText("Latitude") as HTMLInputElement).value).toBe("1.3521");
  });

  it("sends exact coordinates and communicates the pending and scheduled-refresh success states", async () => {
    const user = userEvent.setup();
    const { rerenderTab } = renderTab();

    await enterCoordinates(user);
    await user.click(screen.getByRole("button", { name: "Save home location" }));

    const [coordinates, callbacks] = homeMocks.mutateAtmosphereLocation.mock.calls[0];
    expect(coordinates).toEqual({ latitude: 1.3521, longitude: 103.8198 });

    homeMocks.useUpdateHomeAtmosphereLocation.mockReturnValue({
      mutate: homeMocks.mutateAtmosphereLocation,
      isPending: true,
    });
    rerenderTab();
    expect(screen.getByRole("status").textContent).toContain("Saving home location...");
    expect(
      (screen.getByRole("button", { name: "Saving home location..." }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);

    await act(async () => {
      callbacks.onSuccess({ latitude: 1.3521, longitude: 103.8198 });
    });
    expect(screen.getByRole("status").textContent).toContain(
      "Home location saved. The next scheduled refresh will pick up this change.",
    );
  });

  it.each([
    [
      "a validation rejection",
      { status: 422, message: "Latitude must be less than or equal to 90" },
      "Check the coordinate values and try again.",
    ],
    [
      "an owner-service rejection",
      { status: 503, message: "No owner entity found" },
      "The owner profile is unavailable. Try again after it is restored.",
    ],
    [
      "a network failure",
      new Error("Network unavailable"),
      "Check your connection and try again.",
    ],
  ])("retains entered values after %s", async (_label, error, recoveryCopy) => {
    const user = userEvent.setup();
    renderTab();

    await enterCoordinates(user);
    await user.click(screen.getByRole("button", { name: "Save home location" }));
    const [, callbacks] = homeMocks.mutateAtmosphereLocation.mock.calls[0];

    await act(async () => {
      callbacks.onError(error);
    });

    expect(screen.getByRole("alert").textContent).toContain(recoveryCopy);
    expect((screen.getByLabelText("Latitude") as HTMLInputElement).value).toBe("1.3521");
    expect((screen.getByLabelText("Longitude") as HTMLInputElement).value).toBe("103.8198");
  });

  it("rejects out-of-range values in the client before sending the request", async () => {
    const user = userEvent.setup();
    renderTab();

    await user.clear(screen.getByLabelText("Latitude"));
    await user.type(screen.getByLabelText("Latitude"), "91");
    await user.clear(screen.getByLabelText("Longitude"));
    await user.type(screen.getByLabelText("Longitude"), "103.8198");
    await user.click(screen.getByRole("button", { name: "Save home location" }));

    expect(screen.getByRole("alert").textContent).toContain(
      "Latitude must be between -90 and 90.",
    );
    expect(homeMocks.mutateAtmosphereLocation).not.toHaveBeenCalled();
  });

  it("announces loading rather than treating it as an unconfigured feed", () => {
    setupHomeData();
    homeMocks.useHomeAtmosphereCurrent.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    renderTab();

    expect(screen.getByRole("status").textContent).toContain("Loading saved home location...");
    expect(screen.queryByText("No home location is configured yet.")).toBeNull();
  });

  it("offers an actionable retry when the saved location cannot load", async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    setupHomeData();
    homeMocks.useHomeAtmosphereCurrent.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Home API unavailable"),
      refetch,
    });
    renderTab();

    expect(screen.getByRole("alert").textContent).toContain(
      "Couldn't load the saved home location.",
    );
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});
