// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PropsWithChildren } from "react";

import { useInfraConditionSuppressionCounts } from "./use-healing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("uses exact filtered totals beyond the global sample and keeps unavailable totals unknown", async () => {
  const totals = new Map([["older & /condition", 3], ["busy", 137], ["zero", 0]]);
  const history = [
    ...Array.from({ length: 100 }, (_, i) => `newer-${i}`),
    ...Array.from({ length: 137 }, () => "busy"),
    ...Array.from({ length: 3 }, () => "older & /condition"),
  ].map((fingerprint, id) => ({
    id: String(id), fingerprint, butler_name: "qa", decision: "infra_condition_open",
    reason: null, attempt_id: null, created_at: "2026-10-20T00:00:00Z",
  }));
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  let held = true;
  let outcome: "success" | "http-error" | "missing" | "negative" | "fractional" = "success";
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    if (held) await pending;
    if (outcome === "http-error") return Response.json({ detail: "unavailable" }, { status: 503 });
    const params = new URL(String(input), location.href).searchParams;
    const fingerprint = params.get("fingerprint");
    // Complete newest-first history: the unfiltered latest 100 excludes both matches.
    const matching = history.filter((event) =>
      event.decision === params.get("decision") && (!fingerprint || event.fingerprint === fingerprint),
    );
    const total = matching.length;
    const data = matching.slice(0, Number(params.get("limit")));
    const size = data.length;
    return Response.json({ data, meta: {
      total: outcome === "missing" ? undefined : outcome === "negative" ? -1 : outcome === "fractional" ? 1.5 : total,
      limit: size, offset: 0, has_more: total > size,
    } });
  });
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const hook = renderHook(({ fingerprints }) => useInfraConditionSuppressionCounts(fingerprints), {
    initialProps: { fingerprints: ["busy", "zero", "older & /condition", "busy"] }, wrapper,
  });
  expect(hook.result.current.isLoading).toBe(true);
  expect(hook.result.current.counts.size).toBe(0);
  held = false;
  release();
  await waitFor(() => expect(hook.result.current.counts.size).toBe(3));
  for (const [fingerprint, total] of totals) expect(hook.result.current.counts.get(fingerprint)).toBe(total);
  expect(hook.result.current.isError).toBe(false);
  expect(fetch).toHaveBeenCalledTimes(3);
  for (const [input] of fetch.mock.calls) {
    const params = new URL(String(input), location.href).searchParams;
    expect(params.get("decision")).toBe("infra_condition_open");
    expect(totals.has(params.get("fingerprint")!)).toBe(true);
    expect(Number(params.get("limit"))).toBe(1);
  }

  // A failed refetch must not advertise previously cached totals as current.
  outcome = "http-error";
  await client.invalidateQueries();
  await waitFor(() => expect(hook.result.current.isError).toBe(true));
  expect(hook.result.current.counts.size).toBe(0);
  expect(hook.result.current.isLoading).toBe(false);
  hook.unmount();
  client.clear();

  // Both a first-fetch error and malformed success metadata mean unknown,
  // never a count of the returned sample or an invented zero.
  for (const unavailable of ["http-error", "missing", "negative", "fractional"] as const) {
    outcome = unavailable;
    const failed = renderHook(() => useInfraConditionSuppressionCounts(["busy"]), { wrapper });
    await waitFor(() => expect(failed.result.current.isError, unavailable).toBe(true));
    expect(failed.result.current.counts.size, unavailable).toBe(0);
    failed.unmount();
    client.clear();
  }
  const calls = fetch.mock.calls.length;
  const empty = renderHook(() => useInfraConditionSuppressionCounts([]), { wrapper });
  expect(empty.result.current.counts.size).toBe(0);
  expect(empty.result.current.isLoading).toBe(false);
  expect(empty.result.current.isError).toBe(false);
  expect(fetch.mock.calls.length).toBe(calls);
  empty.unmount();
  client.clear();
});
