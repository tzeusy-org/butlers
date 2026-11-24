// @vitest-environment jsdom
/**
 * Tests for <HomeAwaySafetyPanel>: every away-safety value is typed by the owner, an unset one
 * shows its alert as off, and the panel offers no way to register Home's sensing group.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { HomeAwaySafetyPanel } from "@/components/home/HomeAwaySafetyPanel.tsx";

const saveFreezer = vi.fn();
const removeFreezer = vi.fn();
const armDoor = vi.fn();
const disarmDoor = vi.fn();
const saveBlind = vi.fn();
const clearBlind = vi.fn();
const refetch = vi.fn();
const configState = vi.hoisted(() => ({ current: {} as Record<string, unknown> }));
const removeState = vi.hoisted(() => ({ isError: false }));

const idle = (mutate: unknown) => ({ mutate, isPending: false, isError: false });

vi.mock("@/hooks/use-home-away-safety.ts", () => ({
  useHomeAwaySafety: () => ({ refetch, ...configState.current }),
  useSaveHomeFreezer: () => idle(saveFreezer),
  useDeleteHomeFreezer: () => ({ ...idle(removeFreezer), isError: removeState.isError }),
  useArmHomeAwayDoor: () => idle(armDoor),
  useDisarmHomeAwayDoor: () => idle(disarmDoor),
  useSaveHomeBlindDwell: () => idle(saveBlind),
  useClearHomeBlindDwell: () => idle(clearBlind),
}));

const UNCONFIGURED = {
  freezers: [],
  doors: [],
  blind_after_minutes: null,
  status: {
    freezer: "unconfigured",
    away_door: "unconfigured",
    blind: "unconfigured",
    sensing_group: "unconfigured",
  },
};

const CONFIGURED = {
  freezers: [
    {
      entity_id: "sensor.garage_freezer",
      owner_label: "Garage freezer",
      unit: "°C",
      max_c: -15,
      hysteresis_c: 2,
      dwell_minutes: 30,
      max_reading_age_minutes: 60,
      updated_at: null,
    },
  ],
  doors: [
    { entity_id: "binary_sensor.front_door", owner_label: "Front door", away_alert_after_minutes: 45 },
    { entity_id: "binary_sensor.back_door", owner_label: "Back door", away_alert_after_minutes: null },
  ],
  blind_after_minutes: 20,
  status: { freezer: "ok", away_door: "ok", blind: "ok", sensing_group: "registered" },
};

function type(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function fillFreezer(unit = "°C") {
  type("Temperature sensor ID", " sensor.chest_freezer ");
  type("Name in alerts", "  Chest freezer  ");
  if (unit) type("Sensor reports in", unit);
  type("Alert above (°C)", "-12");
  type("Clear this far below (°C)", "1.5");
  type("Warm for (minutes)", "20");
  type("Oldest usable reading (minutes)", "90");
}

beforeEach(() => {
  configState.current = { data: CONFIGURED };
  removeState.isError = false;
});

afterEach(() => {
  cleanup();
  for (const fn of [saveFreezer, removeFreezer, armDoor, disarmDoor, saveBlind, clearBlind]) {
    fn.mockReset();
  }
  refetch.mockReset();
});

describe("HomeAwaySafetyPanel", () => {
  it("shows every alert as off and unconfigured when nothing is declared", () => {
    configState.current = { data: UNCONFIGURED };
    render(<HomeAwaySafetyPanel />);

    const status = screen.getByTestId("home-away-safety-status").textContent ?? "";
    expect(status.match(/unconfigured, silent/g)).toHaveLength(3);
    expect(screen.getByText("No freezer declared. Freezer alerts are off.")).toBeTruthy();
    expect(screen.getByText("No wait set. This alert is off.")).toBeTruthy();
  });

  it("pre-fills no value: every freezer field starts empty", () => {
    configState.current = { data: UNCONFIGURED };
    render(<HomeAwaySafetyPanel />);

    for (const label of [
      "Alert above (°C)",
      "Clear this far below (°C)",
      "Warm for (minutes)",
      "Oldest usable reading (minutes)",
      "Minutes without sight",
    ]) {
      expect((screen.getByLabelText(label) as HTMLInputElement).value).toBe("");
    }
  });

  it("explains an unregistered sensing group and offers no way to register it", () => {
    configState.current = { data: UNCONFIGURED };
    render(<HomeAwaySafetyPanel />);

    expect(screen.getByTestId("home-away-sensing-group").textContent).toContain("not registered");
    expect(screen.queryByRole("button", { name: /register/i })).toBeNull();
  });

  it("saves a freezer with the owner's trimmed values", () => {
    render(<HomeAwaySafetyPanel />);
    fillFreezer();
    fireEvent.click(screen.getByRole("button", { name: "Save freezer" }));

    expect(saveFreezer.mock.calls[0][0]).toEqual({
      entity_id: "sensor.chest_freezer",
      owner_label: "Chest freezer",
      unit: "°C",
      max_c: -12,
      hysteresis_c: 1.5,
      dwell_minutes: 20,
      max_reading_age_minutes: 90,
    });
  });

  it("refuses an out-of-bounds freezer value and saves nothing", () => {
    render(<HomeAwaySafetyPanel />);
    fillFreezer();
    type("Alert above (°C)", "4");
    fireEvent.click(screen.getByRole("button", { name: "Save freezer" }));

    expect(screen.getByRole("alert").textContent).toContain("-30 to 0");
    expect(saveFreezer).not.toHaveBeenCalled();
  });

  it("starts with no unit and refuses to save until the owner chooses one", () => {
    render(<HomeAwaySafetyPanel />);
    expect((screen.getByLabelText("Sensor reports in") as HTMLSelectElement).value).toBe("");

    fillFreezer("");
    fireEvent.click(screen.getByRole("button", { name: "Save freezer" }));

    expect(screen.getByRole("alert").textContent).toContain("unit");
    expect(saveFreezer).not.toHaveBeenCalled();
  });

  it("saves the unit the owner chose", () => {
    render(<HomeAwaySafetyPanel />);
    fillFreezer("°F");
    fireEvent.click(screen.getByRole("button", { name: "Save freezer" }));

    expect(saveFreezer.mock.calls[0][0].unit).toBe("°F");
  });

  it("shows where a freezer alert clears to two decimals", () => {
    configState.current = {
      data: {
        ...CONFIGURED,
        freezers: [{ ...CONFIGURED.freezers[0], max_c: -12.1, hysteresis_c: 2.8 }],
      },
    };
    render(<HomeAwaySafetyPanel />);

    // -12.1 - 2.8 is -14.899999999999999 in floating point.
    expect(screen.getByTestId("home-away-freezer-list").textContent).toContain("clears at -14.9 °C");
  });

  it("refuses a sensor ID that is not a sensor", () => {
    render(<HomeAwaySafetyPanel />);
    fillFreezer();
    type("Temperature sensor ID", "binary_sensor.freezer");
    fireEvent.click(screen.getByRole("button", { name: "Save freezer" }));

    expect(screen.getByRole("alert").textContent).toContain("sensor ID");
    expect(saveFreezer).not.toHaveBeenCalled();
  });

  it("removes a freezer and says when removing failed", () => {
    removeState.isError = true;
    render(<HomeAwaySafetyPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Stop watching Garage freezer" }));

    expect(removeFreezer.mock.calls[0][0]).toBe("sensor.garage_freezer");
    expect(screen.getByRole("alert").textContent).toContain("still being watched");
  });

  it("arms a departure item only at 30 minutes or more", () => {
    render(<HomeAwaySafetyPanel />);
    type("Minutes before alerting for Back door", "29");
    fireEvent.click(screen.getByRole("button", { name: "Arm" }));
    expect(armDoor).not.toHaveBeenCalled();

    type("Minutes before alerting for Back door", "30");
    fireEvent.click(screen.getByRole("button", { name: "Arm" }));
    expect(armDoor.mock.calls[0][0]).toEqual({ entityId: "binary_sensor.back_door", minutes: 30 });
  });

  it("disarms an armed departure item", () => {
    render(<HomeAwaySafetyPanel />);
    expect(screen.getByText("alert after 45 min away")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Disarm Front door" }));

    expect(disarmDoor.mock.calls[0][0]).toBe("binary_sensor.front_door");
  });

  it("saves and clears the blind wait within bounds", () => {
    render(<HomeAwaySafetyPanel />);
    type("Minutes without sight", "4");
    fireEvent.click(screen.getByRole("button", { name: "Save wait" }));
    expect(saveBlind).not.toHaveBeenCalled();

    type("Minutes without sight", "5");
    fireEvent.click(screen.getByRole("button", { name: "Save wait" }));
    expect(saveBlind.mock.calls[0][0]).toBe(5);

    fireEvent.click(screen.getByRole("button", { name: "Turn off" }));
    expect(clearBlind).toHaveBeenCalledTimes(1);
  });

  it("says when the settings failed to load instead of showing them off", () => {
    configState.current = { isError: true };
    render(<HomeAwaySafetyPanel />);

    expect(screen.getByTestId("home-away-safety-error").textContent).toContain("away safety");
    expect(screen.queryByTestId("home-away-safety-status")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reload away safety" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("shows loading rather than unconfigured", () => {
    configState.current = { isLoading: true };
    render(<HomeAwaySafetyPanel />);

    expect(screen.getByText("Loading away safety")).toBeTruthy();
    expect(screen.queryByTestId("home-away-safety-status")).toBeNull();
  });
});
