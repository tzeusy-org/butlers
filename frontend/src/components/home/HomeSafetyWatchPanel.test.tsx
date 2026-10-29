// @vitest-environment jsdom
/**
 * Tests for <HomeSafetyWatchPanel>: the owner declares which sensors guard a hazard and the name
 * alerts use; loading and failure are shown, never read as "nothing declared".
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { HomeSafetyWatchPanel } from "@/components/home/HomeSafetyWatchPanel.tsx";

const save = vi.fn();
const remove = vi.fn();
const refetch = vi.fn();
const watchState = vi.hoisted(() => ({ current: {} as Record<string, unknown> }));
const removeState = vi.hoisted(() => ({ isError: false }));

vi.mock("@/hooks/use-home-safety-watch.ts", () => ({
  useHomeSafetyWatch: () => ({ refetch, ...watchState.current }),
  useSaveHomeSafetyWatch: () => ({ mutate: save, isPending: false, isError: false }),
  useDeleteHomeSafetyWatch: () => ({
    mutate: remove,
    isPending: false,
    isError: removeState.isError,
  }),
}));

const KITCHEN = {
  entity_id: "binary_sensor.kitchen_leak",
  role: "leak",
  owner_label: "Kitchen leak sensor",
  updated_at: null,
};

beforeEach(() => {
  watchState.current = { data: [KITCHEN] };
  removeState.isError = false;
});

afterEach(() => {
  cleanup();
  save.mockReset();
  remove.mockReset();
  refetch.mockReset();
});

describe("HomeSafetyWatchPanel", () => {
  it("lists the declared sensors by the owner's own label", () => {
    render(<HomeSafetyWatchPanel />);

    expect(screen.getByText("Kitchen leak sensor")).toBeTruthy();
    expect(screen.getByText("binary_sensor.kitchen_leak")).toBeTruthy();
  });

  it("refuses an entity that is not a binary sensor and saves nothing", () => {
    render(<HomeSafetyWatchPanel />);
    fireEvent.change(screen.getByLabelText("Sensor entity ID"), {
      target: { value: "switch.freezer_plug" },
    });
    fireEvent.change(screen.getByLabelText("Name in alerts"), { target: { value: "Freezer" } });
    fireEvent.click(screen.getByRole("button", { name: "Save sensor" }));

    expect(screen.getByRole("alert").textContent).toContain("binary sensor ID");
    expect(save).not.toHaveBeenCalled();
  });

  it("saves the trimmed declaration with the chosen hazard", () => {
    render(<HomeSafetyWatchPanel />);
    fireEvent.change(screen.getByLabelText("Sensor entity ID"), {
      target: { value: " binary_sensor.boiler_co " },
    });
    fireEvent.change(screen.getByLabelText("Hazard"), { target: { value: "co" } });
    fireEvent.change(screen.getByLabelText("Name in alerts"), {
      target: { value: "  Boiler room CO  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save sensor" }));

    expect(save.mock.calls[0][0]).toEqual({
      entity_id: "binary_sensor.boiler_co",
      role: "co",
      owner_label: "Boiler room CO",
    });
  });

  it("removes a declared sensor", () => {
    render(<HomeSafetyWatchPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Stop watching Kitchen leak sensor" }));

    expect(remove.mock.calls[0][0]).toEqual({
      entityId: "binary_sensor.kitchen_leak",
      role: "leak",
    });
  });

  it("says when the list failed to load instead of showing it empty", () => {
    watchState.current = { isError: true };
    render(<HomeSafetyWatchPanel />);

    expect(screen.getByTestId("home-safety-watch-error").textContent).toContain("safety sensors");
    expect(screen.queryByText("No safety sensors declared yet.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reload safety sensors" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("shows loading rather than an empty list", () => {
    watchState.current = { isLoading: true };
    render(<HomeSafetyWatchPanel />);

    expect(screen.getByText("Loading safety sensors")).toBeTruthy();
    expect(screen.queryByText("No safety sensors declared yet.")).toBeNull();
  });

  it("says when removing a sensor failed instead of leaving it looking removed", () => {
    removeState.isError = true;
    render(<HomeSafetyWatchPanel />);

    expect(screen.getByTestId("home-safety-watch-remove-error").textContent).toContain(
      "still being watched",
    );
  });
});
