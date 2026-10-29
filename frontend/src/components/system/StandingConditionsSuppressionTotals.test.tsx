// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { ConditionEntry } from "@/api/types";
import { StandingConditionsTile } from "./StandingConditionsTile";

const ledgers = vi.hoisted(() => ({ infra: [] as ConditionEntry[], owner: [] as ConditionEntry[] }));
vi.mock("@/hooks/use-system", () => ({
  useSystemConditions: (params: { ledger?: string }) => ({
    isPending: false, isError: false,
    data: { data: { conditions_available: true, conditions: params.ledger === "owner" ? ledgers.owner : ledgers.infra } },
  }),
}));
vi.mock("@/hooks/use-entities", () => ({
  useRelationshipEntitiesByIds: () => ({ data: { items: [] } }),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function condition(id: string, fingerprint: string, ledger = "infra"): ConditionEntry {
  return {
attention: null,
    id, fingerprint, ledger, source: "infra_state", episode: 1, state: "open",
    first_detected_at: "2026-10-20T00:00:00Z", last_confirmed_at: "2026-10-20T00:00:00Z",
    last_escalated_at: null, next_reescalate_at: null, escalation_level: "L1",
    resolved_at: null, recovered_after_s: null, summary: id, metadata: null,
  };
}

it("renders exact infra-only totals through the actual hook, with truthful loading and failures", async () => {
  ledgers.infra = [condition("older outage", "older"), condition("busy outage", "busy"), condition("zero outage", "zero")];
  ledgers.owner = [condition("owner concern", "older", "owner")];
  const history = [
    ...Array.from({ length: 100 }, (_, i) => `newer-${i}`),
    ...Array.from({ length: 137 }, () => "busy"),
    ...Array.from({ length: 3 }, () => "older"),
  ].map((fingerprint, id) => ({
    id: String(id), fingerprint, butler_name: "qa", decision: "infra_condition_open",
    reason: null, attempt_id: null, created_at: "2026-10-20T00:00:00Z",
  }));
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  let held = true;
  let outcome: "success" | "error" | "zero" = "success";
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    if (held) await pending;
    if (outcome === "error") return Response.json({ detail: "unavailable" }, { status: 503 });
    const params = new URL(String(input), location.href).searchParams;
    const fingerprint = params.get("fingerprint");
    const matching = outcome === "zero" ? [] : history.filter((event) =>
      event.decision === params.get("decision") && (!fingerprint || event.fingerprint === fingerprint),
    );
    const total = matching.length;
    const data = matching.slice(0, Number(params.get("limit")));
    return Response.json({
      data,
      meta: { total, limit: data.length, offset: 0, has_more: total > data.length },
    });
  });
  vi.stubGlobal("fetch", fetch);
  function mount() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const rendered = render(<QueryClientProvider client={client}><StandingConditionsTile /></QueryClientProvider>);
    return { client, ...rendered };
  }
  const first = mount();
  expect(screen.getByRole("status").textContent).toContain("Loading QA dispatch suppression counts");
  expect(screen.queryByTestId("condition-suppressed-count")).toBeNull();
  held = false;
  release();
  await waitFor(() => expect(screen.getAllByTestId("condition-suppressed-count")).toHaveLength(2));
  expect(screen.getAllByTestId("condition-suppressed-count").map((node) => node.textContent)).toEqual([
    expect.stringContaining("3 QA dispatches suppressed"), expect.stringContaining("137 QA dispatches suppressed"),
  ]);
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.queryByTestId("standing-conditions-suppression-degraded")).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(3);

  outcome = "error";
  await first.client.invalidateQueries();
  await screen.findByTestId("standing-conditions-suppression-degraded");
  expect(screen.queryByTestId("condition-suppressed-count")).toBeNull();
  expect(screen.getByText("owner concern")).toBeTruthy();
  outcome = "zero";
  await first.client.invalidateQueries();
  await waitFor(() => expect(screen.queryByTestId("standing-conditions-suppression-degraded")).toBeNull());
  expect(screen.queryByTestId("condition-suppressed-count")).toBeNull();
  first.unmount();
  first.client.clear();

  outcome = "error";
  const initialError = mount();
  await screen.findByTestId("standing-conditions-suppression-degraded");
  expect(screen.queryByTestId("condition-suppressed-count")).toBeNull();
  expect(screen.queryByTestId("standing-conditions-empty")).toBeNull();
  initialError.unmount();
  initialError.client.clear();

  // Owner-only rows must not create suppression requests or error warnings.
  ledgers.infra = [];
  const calls = fetch.mock.calls.length;
  const ownerOnly = mount();
  expect(screen.getByText("owner concern")).toBeTruthy();
  expect(screen.queryByTestId("condition-suppressed-count")).toBeNull();
  expect(screen.queryByTestId("standing-conditions-suppression-degraded")).toBeNull();
  expect(fetch.mock.calls.length).toBe(calls);
  ownerOnly.unmount();
  ownerOnly.client.clear();
});
