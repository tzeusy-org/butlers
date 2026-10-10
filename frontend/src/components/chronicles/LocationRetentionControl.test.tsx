// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/hooks/use-chronicles", () => ({
  useLocationRetention: vi.fn(), useUpdateLocationRetention: vi.fn(),
}));
vi.mock("@/components/ui/time", () => ({ Time: ({value}: {value: string}) => <time>{value}</time> }));
import { useLocationRetention, useUpdateLocationRetention } from "@/hooks/use-chronicles";
import { LocationRetentionControl } from "./LocationRetentionControl";

const mutate = vi.fn();
let query: ReturnType<typeof useLocationRetention>;
function policy(changes = {}) {
  return {days: 30, version: 1, precision_after_forgetting_m: 150,
    widening_restores_forgotten_points: false, status: "pending", blocked_count: 3,
    receipt: "attempt", ...changes};
}
function response(data: ReturnType<typeof policy> | undefined, isError = false) {
  query = {data: data ? {data} : undefined, isError, isLoading: false, refetch: vi.fn()} as unknown as ReturnType<typeof useLocationRetention>;
  vi.mocked(useLocationRetention).mockImplementation(() => query);
}
beforeEach(() => {
  mutate.mockReset();
  response(policy());
  vi.mocked(useUpdateLocationRetention).mockReturnValue({mutate, isPending: false, isError: false} as unknown as ReturnType<typeof useUpdateLocationRetention>);
});

it("separates deadline and measured deletion, including malformed or unavailable receipts", () => {
  let html = renderToStaticMarkup(<LocationRetentionControl />);
  expect(html).toContain("retention is 30 days. Forgetting also requires completed projection and verified dependent records");
  expect(html).toContain("Deletion is not confirmed");
  expect(html).toContain("3 overdue points await projection");
  response(policy({status: "complete", deleted_count: 7}));
  html = renderToStaticMarkup(<LocationRetentionControl />);
  expect(html).toContain("Deletion is not confirmed");
  expect(html).not.toContain("7 exact points were deleted");
  response(policy({status: "complete", deleted_count: 7, completion_at: "2026-10-01T00:00:00Z"}));
  html = renderToStaticMarkup(<LocationRetentionControl />);
  expect(html).toContain("7 exact points were deleted");
  response(undefined, true);
  html = renderToStaticMarkup(<LocationRetentionControl />);
  expect(html).toContain("could not be confirmed");
  expect(html).not.toContain("retention is 30 days");
});

it("requires shortening confirmation against the same server version and rejects invalid input", () => {
  (globalThis as typeof globalThis & {IS_REACT_ACT_ENVIRONMENT: boolean}).IS_REACT_ACT_ENVIRONMENT = true;
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const render = () => act(() => root.render(<LocationRetentionControl />));
  const input = () => container.querySelector("input")!;
  const save = () => [...container.querySelectorAll("button")].find(b => /Save retention|Confirm shorter/.test(b.textContent ?? ""))!;
  const change = (value: string) => act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input(), value);
    input().dispatchEvent(new Event("input", {bubbles: true}));
  });
  try {
    render();
    change("1.5");
    expect(save().disabled).toBe(true);
    change("1");
    act(() => save().click());
    expect(mutate).not.toHaveBeenCalled();
    expect(container.textContent).toContain("cannot be undone");
    // A polling race cannot silently reuse confirmation for a new policy.
    response(policy({version: 2})); render();
    act(() => save().click());
    expect(mutate).not.toHaveBeenCalled();
    act(() => save().click());
    expect(mutate.mock.calls[0][0]).toEqual({days: 1, version: 2});
  } finally { act(() => root.unmount()); container.remove(); }
});
