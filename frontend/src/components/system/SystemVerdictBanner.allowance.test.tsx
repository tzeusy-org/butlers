// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import { SystemVerdictBanner } from "@/components/system/SystemVerdictBanner";
import { useModelAllowance } from "@/hooks/use-model-catalog";

vi.mock("@/hooks/use-model-catalog", () => ({ useModelAllowance: vi.fn() }));
vi.mock("@/hooks/use-butler-status-board", () => ({
  useButlerStatusBoard: () => ({
    aggregates: {
      total: 1, offline: 0, quarantined: 0, overdue: 0,
      sourcesPartiallyDegraded: false, isLoading: false, isError: false,
    },
  }),
}));

const ok = { isLoading: false, isPending: false, isError: false, isForbidden: false };
vi.mock("@/hooks/use-system", () => {
  const ok = { isLoading: false, isPending: false, isError: false, isForbidden: false };
  const backup = {
    backup_source_reachable: true, last_backup_at: new Date().toISOString(),
    last_backup_status: "ok", backup_stale: false,
    restore_drill: { result: "pass" }, last_run: { result: "ok" },
  };
  const hook = (data: unknown) => () => ({ ...ok, data: { data } });
  return {
    useInstanceFacts: hook({ version: "1", uptime_seconds: 90000 }),
    useDatabaseFacts: hook({}),
    useBackupFacts: hook(backup),
    useEgressFacts: hook({}),
    useHealthPosture: () => ({ ...ok, data: { security: {} } }),
    useInsightDeliveryState: hook({ failed: 0 }),
    useDriftFacts: hook({ drift_check_available: true, is_drifted: false, drifted: [] }),
    useDeploymentFacts: hook({
      current: { result: "ok", serving_mode: "image" },
      commits_behind_available: true, commits_behind_main: 0,
    }),
  };
});

function setAllowance(value: Record<string, unknown>) {
  vi.mocked(useModelAllowance).mockReturnValue({ ...ok, ...value } as never);
}

function renderBanner() {
  return render(<MemoryRouter><SystemVerdictBanner /></MemoryRouter>);
}

beforeEach(() => vi.useFakeTimers({ now: Date.parse("2026-10-18T12:00:00Z"), toFake: ["Date"] }));
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("SystemVerdictBanner allowance source", () => {
  it("names an exhausted account with a door to the Models tab", () => {
    setAllowance({
      data: { data: { available: true, accounts: [{
        account_key: "acct-a", state: "exhausted", reset_at: "2026-10-18T14:05:00Z",
        reset_source: "parsed", first_seen_at: "x", updated_at: "x", entries: [],
      }] } },
    });
    const { container } = renderBanner();
    expect(container.textContent).toContain("acct-a out of allowance until");
    expect(container.querySelector('a[href="/settings/models"]')).not.toBeNull();
    expect(container.textContent).not.toContain("Instance healthy");
  });

  it("treats an unknown-state account as unknown, never all-clear", () => {
    setAllowance({
      data: { data: { available: true, accounts: [{
        account_key: "acct-u", state: "unknown", reset_at: null,
        reset_source: "unknown", first_seen_at: "x", updated_at: "x", entries: [],
      }] } },
    });
    const { container } = renderBanner();
    expect(container.textContent).toContain("acct-u allowance unknown");
    expect(container.textContent).not.toContain("Instance healthy");
  });

  it("reads a passed reset as should be back, like the strip", () => {
    setAllowance({
      data: { data: { available: true, accounts: [{
        account_key: "acct-p", state: "exhausted", reset_at: "2026-10-18T11:00:00Z",
        reset_source: "parsed", first_seen_at: "x", updated_at: "x", entries: [],
      }] } },
    });
    const { container } = renderBanner();
    expect(container.textContent).toContain("acct-p out of allowance, should be back");
    expect(container.textContent).not.toContain("reset time unknown");
  });

  it("never reads all-clear when the allowance table is unreadable", () => {
    setAllowance({ data: { data: { available: false, accounts: [] } } });
    const { container } = renderBanner();
    expect(container.textContent).toContain("allowance unknown");
    expect(container.textContent).not.toContain("Instance healthy");
  });

  it("reads allowance unknown on a fetch error", () => {
    setAllowance({ isError: true, data: undefined });
    const { container } = renderBanner();
    expect(container.textContent).toContain("allowance unknown");
  });

  it("stays silent for a non-owner (403) and all-clear is allowed", () => {
    setAllowance({ isError: true, isForbidden: true, data: undefined });
    const { container } = renderBanner();
    expect(container.textContent).not.toContain("allowance");
    expect(container.textContent).toContain("Instance healthy");
  });

  it("is not all-clear while the allowance source is loading", () => {
    setAllowance({ isLoading: true, isPending: true, data: undefined });
    const { container } = renderBanner();
    expect(container.textContent).not.toContain("Instance healthy");
  });
});
