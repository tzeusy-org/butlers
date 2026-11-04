/**
 * SettingsProjectionsPage — /settings/projections
 *
 * The owner's list of ambient glance grants: last-fetch column, one-step
 * token rotate, and revoke behind a confirm. REQ-ambient-projections-001/004.
 */

// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { axe, toHaveNoViolations } from "jest-axe";

import SettingsProjectionsPage from "@/pages/SettingsProjectionsPage";
import type { MintedProjection, ProjectionGrant } from "@/api/projections";

expect.extend(toHaveNoViolations);

const NOW = Date.parse("2026-11-03T12:00:00Z");

function grant(over: Partial<ProjectionGrant>): ProjectionGrant {
  return {
    id: "g-1",
    label: "Hall display",
    surface_kind: "eink",
    audience_class: "household",
    route_scope: "glance",
    expected_fetch_interval_seconds: 300,
    created_at: "2026-11-01T00:00:00Z",
    revoked_at: null,
    last_fetch_at: "2026-11-03T11:58:00Z",
    last_fetch_status: "ok",
    ...over,
  };
}

const hooks = vi.hoisted(() => ({
  useProjections: vi.fn(),
  useRevokeProjection: vi.fn(),
  useRotateProjection: vi.fn(),
}));
vi.mock("@/hooks/use-projections", () => hooks);

const revoke = vi.fn();
const rotate = vi.fn();

function listing(rows: ProjectionGrant[]) {
  hooks.useProjections.mockReturnValue({
    data: { data: rows },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  });
}

function view() {
  return render(
    <MemoryRouter>
      <SettingsProjectionsPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  revoke.mockReset();
  rotate.mockReset();
  hooks.useRevokeProjection.mockReturnValue({ mutate: revoke, isPending: false });
  hooks.useRotateProjection.mockReturnValue({ mutate: rotate, isPending: false });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("SettingsProjectionsPage", () => {
  it("lists each grant with its last fetch and flags a lapsed surface", () => {
    listing([
      grant({}),
      grant({ id: "g-2", label: "Kitchen panel", last_fetch_at: null }),
    ]);
    view();
    const fresh = screen.getByRole("row", { name: /Hall display/ });
    expect(within(fresh).getByText(/2m ago/)).toBeTruthy();
    expect(fresh.getAttribute("data-lapsed")).toBe("false");
    const lapsed = screen.getByRole("row", { name: /Kitchen panel/ });
    expect(within(lapsed).getByText(/never fetched/i)).toBeTruthy();
    expect(lapsed.getAttribute("data-lapsed")).toBe("true");
  });

  it("revokes only after the owner confirms", () => {
    listing([grant({})]);
    view();
    fireEvent.click(screen.getByRole("button", { name: "Revoke Hall display" }));
    expect(revoke).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("revoke-projection-dialog-confirm"));
    expect(revoke.mock.calls[0][0]).toBe("g-1");
  });

  it("rotates in one step and shows the new token once", () => {
    listing([grant({})]);
    const minted: MintedProjection = {
      ...grant({}),
      token: "synthetic-new-token",
      path: "/api/projections/g-1/glance",
    };
    rotate.mockImplementation((_id: string, opts: { onSuccess: (r: unknown) => void }) =>
      opts.onSuccess({ data: minted }),
    );
    view();
    expect(screen.queryByText("synthetic-new-token")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Rotate token for Hall display" }));
    expect(rotate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("rotate-projection-dialog-confirm"));
    expect(rotate.mock.calls[0][0]).toBe("g-1");
    expect(screen.getByText("synthetic-new-token")).toBeTruthy();
    expect(screen.getByText(/shown once/i)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText("synthetic-new-token")).toBeNull();
  });

  it("offers no actions on a revoked grant", () => {
    listing([grant({ revoked_at: "2026-11-02T00:00:00Z" })]);
    view();
    expect(screen.getByText(/revoked/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Revoke|Rotate/ })).toBeNull();
  });

  it("says so when there are no grants, and when the list fails", () => {
    listing([]);
    const { unmount } = view();
    expect(screen.getByText(/No projection grants/i)).toBeTruthy();
    unmount();
    hooks.useProjections.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: vi.fn(),
    });
    view();
    expect(screen.getByText(/Could not load projections/i)).toBeTruthy();
  });

  it("has no axe violations", async () => {
    listing([grant({}), grant({ id: "g-2", label: "Old", revoked_at: "2026-11-02T00:00:00Z" })]);
    const { container } = view();
    expect(await axe(container)).toHaveNoViolations();
  });
});
