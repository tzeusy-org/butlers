/**
 * SettingsLocationRetentionPage — /settings/location-retention
 *
 * REQ-chronicler-api-018 (bu-s11n0s.75, S5b): the owner sees the horizon, the expiry switch and
 * what forgetting means; turning expiry on or off is confirmed first and sent as a
 * compare-and-set; a horizon change keeps the switch; nothing moves before the server answers.
 */

// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe, toHaveNoViolations } from "jest-axe";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import type { LocationRetentionPolicy } from "@/api/location-retention";
import SettingsLocationRetentionPage from "@/pages/SettingsLocationRetentionPage";

expect.extend(toHaveNoViolations);

const api = vi.hoisted(() => ({
  getLocationRetention: vi.fn(),
  setLocationRetention: vi.fn(),
}));
vi.mock("@/api/location-retention", () => api);

function policy(over: Partial<LocationRetentionPolicy> = {}): LocationRetentionPolicy {
  return {
    days: 30,
    version: 4,
    updated_at: "2026-11-14T09:00:00Z",
    min_days: 1,
    max_days: 30,
    raw_age_basis: "device timestamp, or server receipt time",
    precision_after_forgetting: "approximately 150 m grid cells",
    covered_sources: ["owntracks.points", "owntracks.place_cluster", "owntracks.ssid_presence"],
    enforced: false,
    status: "not_enforced",
    ...over,
  };
}

function view() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SettingsLocationRetentionPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function expirySwitch() {
  await screen.findByTestId("expiry-state");
  return screen.getByRole("switch", { name: "Raw location expiry" });
}

beforeEach(() => {
  api.getLocationRetention.mockReset();
  api.setLocationRetention.mockReset();
});
afterEach(cleanup);

describe("reading", () => {
  it("shows expiry off, the horizon and what forgetting keeps", async () => {
    api.getLocationRetention.mockResolvedValue(policy());
    view();

    const sw = await expirySwitch();
    expect(sw.getAttribute("aria-checked")).toBe("false");
    expect(screen.getByTestId("expiry-state").textContent).toContain("Off");
    expect(screen.getByTestId("expiry-effect").textContent).toContain("kept indefinitely");
    expect((screen.getByLabelText("Horizon in days") as HTMLInputElement).value).toBe("30");
    expect(screen.getByText(/approximately 150 m grid cells/)).toBeTruthy();
    expect(screen.getByText(/device timestamp, or server receipt time/)).toBeTruthy();
    expect(screen.getByText("owntracks.ssid_presence")).toBeTruthy();
    expect(screen.getByText(/version 4/)).toBeTruthy();
  });

  it("says plainly when expiry is on", async () => {
    api.getLocationRetention.mockResolvedValue(
      policy({ enforced: true, status: "enforcing", days: 7 }),
    );
    view();
    const sw = await expirySwitch();
    expect(sw.getAttribute("aria-checked")).toBe("true");
    expect(screen.getByTestId("expiry-state").textContent).toContain("On");
    expect(screen.getByTestId("expiry-effect").textContent).toContain("older than 7 days");
  });

  it("offers a retry when the policy cannot load, and no switch", async () => {
    api.getLocationRetention.mockRejectedValue(new Error("boom"));
    view();
    expect(await screen.findByText("Could not load the retention policy.")).toBeTruthy();
    expect(screen.queryByRole("switch")).toBeNull();
  });
});

describe("the switch", () => {
  it("asks before turning expiry on and sends a compare-and-set", async () => {
    api.getLocationRetention.mockResolvedValue(policy());
    api.setLocationRetention.mockResolvedValue(
      policy({ enforced: true, status: "enforcing", version: 5 }),
    );
    view();
    const user = userEvent.setup();

    await user.click(await expirySwitch());
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(/cannot be undone/)).toBeTruthy();
    expect(api.setLocationRetention).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Turn expiry on" }));
    await waitFor(() =>
      expect(api.setLocationRetention).toHaveBeenCalledWith({
        days: 30,
        expected_version: 4,
        enforced: true,
      }),
    );
  });

  it("cancelling the confirmation sends nothing", async () => {
    api.getLocationRetention.mockResolvedValue(policy());
    view();
    const user = userEvent.setup();
    await user.click(await expirySwitch());
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Cancel" }),
    );
    expect(api.setLocationRetention).not.toHaveBeenCalled();
    expect(screen.getByRole("switch", { name: "Raw location expiry" }).getAttribute("aria-checked")).toBe(
      "false",
    );
  });

  it("asks before turning expiry off and says a fetched grant may still run", async () => {
    api.getLocationRetention.mockResolvedValue(policy({ enforced: true, status: "enforcing" }));
    api.setLocationRetention.mockResolvedValue(policy({ version: 5 }));
    view();
    const user = userEvent.setup();
    await user.click(await expirySwitch());
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(/already fetched may still/)).toBeTruthy();
    await user.click(within(dialog).getByRole("button", { name: "Turn expiry off" }));
    await waitFor(() =>
      expect(api.setLocationRetention).toHaveBeenCalledWith({
        days: 30,
        expected_version: 4,
        enforced: false,
      }),
    );
  });

  it("keeps showing what the server holds when a write is refused", async () => {
    api.getLocationRetention.mockResolvedValue(policy());
    api.setLocationRetention.mockRejectedValue(
      new ApiError("conflict", "Location retention policy changed; reload and retry", 409, {
        current_version: 6,
      }),
    );
    view();
    const user = userEvent.setup();
    await user.click(await expirySwitch());
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Turn expiry on",
      }),
    );
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("changed elsewhere");
    expect(screen.getByRole("switch", { name: "Raw location expiry" }).getAttribute("aria-checked")).toBe(
      "false",
    );
    // The refused write refetches, so the next attempt carries the current version.
    await waitFor(() => expect(api.getLocationRetention).toHaveBeenCalledTimes(2));
  });
});

describe("the horizon", () => {
  it("saves a new horizon without touching the switch", async () => {
    api.getLocationRetention.mockResolvedValue(policy());
    api.setLocationRetention.mockResolvedValue(policy({ days: 7, version: 5 }));
    view();
    const user = userEvent.setup();
    await expirySwitch();
    const input = screen.getByLabelText("Horizon in days");
    const save = screen.getByRole("button", { name: "Save horizon" });
    expect((save as HTMLButtonElement).disabled).toBe(true);

    await user.clear(input);
    await user.type(input, "7");
    await user.click(save);
    await waitFor(() =>
      expect(api.setLocationRetention).toHaveBeenCalledWith({ days: 7, expected_version: 4 }),
    );
  });

  it("refuses a horizon outside the bounds before sending", async () => {
    api.getLocationRetention.mockResolvedValue(policy());
    view();
    const user = userEvent.setup();
    await expirySwitch();
    const input = screen.getByLabelText("Horizon in days");
    for (const bad of ["0", "31", "2.5"]) {
      await user.clear(input);
      await user.type(input, bad);
      expect(
        (screen.getByRole("button", { name: "Save horizon" }) as HTMLButtonElement).disabled,
      ).toBe(true);
    }
    expect(api.setLocationRetention).not.toHaveBeenCalled();
  });

  it("asks before shortening the horizon while expiry is on", async () => {
    api.getLocationRetention.mockResolvedValue(policy({ enforced: true, status: "enforcing" }));
    api.setLocationRetention.mockResolvedValue(
      policy({ enforced: true, status: "enforcing", days: 7, version: 5 }),
    );
    view();
    const user = userEvent.setup();
    await expirySwitch();
    const input = screen.getByLabelText("Horizon in days");
    await user.clear(input);
    await user.type(input, "7");
    await user.click(screen.getByRole("button", { name: "Save horizon" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(api.setLocationRetention).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "Shorten to 7 days" }));
    await waitFor(() =>
      expect(api.setLocationRetention).toHaveBeenCalledWith({ days: 7, expected_version: 4 }),
    );
  });
});

it("has no axe violations", async () => {
  api.getLocationRetention.mockResolvedValue(policy());
  const { container } = view();
  await expirySwitch();
  expect(await axe(container)).toHaveNoViolations();
});
