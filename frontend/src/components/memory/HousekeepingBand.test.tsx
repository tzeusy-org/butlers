// @vitest-environment jsdom
/**
 * Component tests for HousekeepingBand — Band 4 of /memory (bu-2ix8d.8).
 *
 * Acceptance ((memory house-ledger redesign, graduated) prompts/07-housekeeping.md):
 *   - One HOUSEKEEPING eyebrow + the #housekeeping anchor (rail deep-link).
 *   - Retention: a single Save commit pill appears only when a row is dirty,
 *     PUTs the dirty rows, and the kind column is mono text (no free-text kind
 *     creation — inputs are TTL / max-rows only).
 *   - Compaction: bytes omitted (no em-dash) when null; empty → serif-italic
 *     "No sweeps recorded."
 *   - Embeddings: inline dry-run mono line, pill-morph re-embed confirm
 *     (arm-then-commit, no <dialog>), NO progress bar.
 *   - One commit-class action per sub-surface (one Save pill in retention; one
 *     re-embed in embeddings; compaction is read-only).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import HousekeepingBand from "@/components/memory/HousekeepingBand";
import {
  useMemoryCompactionLog,
  useMemoryRetentionPolicies,
  useUpdateMemoryRetentionPolicies,
} from "@/hooks/use-memory";
import { useReembedPending, useReembedRun } from "@/hooks/use-memory-reembed";
import { useButlers } from "@/hooks/use-butlers";
import type {
  CompactionLogEntry,
  MemoryRetentionPolicy,
  ReembedRunResult,
} from "@/api/types";

vi.mock("@/hooks/use-memory", () => ({
  useMemoryRetentionPolicies: vi.fn(),
  useUpdateMemoryRetentionPolicies: vi.fn(),
  useMemoryCompactionLog: vi.fn(),
}));
vi.mock("@/hooks/use-memory-reembed", () => ({
  useReembedPending: vi.fn(),
  useReembedRun: vi.fn(),
}));
vi.mock("@/hooks/use-butlers", () => ({
  useButlers: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

// ---------------------------------------------------------------------------
// Fixtures + mock wiring
// ---------------------------------------------------------------------------

function policy(overrides: Partial<MemoryRetentionPolicy> = {}): MemoryRetentionPolicy {
  return {
    kind: "event",
    ttl_days: 30,
    max_rows: 50000,
    updated_at: "2026-05-02T10:00:00Z",
    updated_by: "api",
    ...overrides,
  };
}

const updateMutate = vi.fn();
let reembedVariables: { dry_run?: boolean } | undefined;
const reembedMutate = vi.fn();

function wire(opts: {
  policies?: MemoryRetentionPolicy[];
  compaction?: CompactionLogEntry[];
  pendingTotal?: number;
  updatePending?: boolean;
  updateError?: boolean;
  reembedPending?: boolean;
  reembedResult?: ReembedRunResult;
} = {}) {
  vi.mocked(useMemoryRetentionPolicies).mockReturnValue({
    data: { data: opts.policies ?? [policy()] },
    isLoading: false,
  } as unknown as ReturnType<typeof useMemoryRetentionPolicies>);

  vi.mocked(useUpdateMemoryRetentionPolicies).mockReturnValue({
    mutate: updateMutate,
    isPending: opts.updatePending ?? false,
    isError: opts.updateError ?? false,
  } as unknown as ReturnType<typeof useUpdateMemoryRetentionPolicies>);

  vi.mocked(useMemoryCompactionLog).mockReturnValue({
    data: { data: opts.compaction ?? [] },
    isLoading: false,
  } as unknown as ReturnType<typeof useMemoryCompactionLog>);

  vi.mocked(useReembedPending).mockReturnValue({
    data: {
      data: {
        counts: { facts: opts.pendingTotal ?? 0 },
        total: opts.pendingTotal ?? 0,
        current_model: "text-embedding-3-small",
      },
    },
    isLoading: false,
  } as unknown as ReturnType<typeof useReembedPending>);

  vi.mocked(useReembedRun).mockReturnValue({
    mutate: (body: { dry_run?: boolean }, callbacks?: { onSuccess?: (r: { data: ReembedRunResult }) => void }) => {
      reembedVariables = body;
      reembedMutate(body);
      if (opts.reembedResult && callbacks?.onSuccess) {
        callbacks.onSuccess({ data: opts.reembedResult });
      }
    },
    get variables() {
      return reembedVariables;
    },
    isPending: opts.reembedPending ?? false,
    isError: false,
  } as unknown as ReturnType<typeof useReembedRun>);

  vi.mocked(useButlers).mockReturnValue({
    data: { data: [{ name: "lifestyle" }, { name: "general" }] },
  } as unknown as ReturnType<typeof useButlers>);
}

function render() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <MemoryRouter initialEntries={["/memory"]}>
        <HousekeepingBand />
      </MemoryRouter>,
    );
  });
  return { container, root };
}

describe("HousekeepingBand", () => {
  let mounted: { container: HTMLDivElement; root: Root } | null = null;

  beforeEach(() => {
    vi.resetAllMocks();
    updateMutate.mockReset();
    reembedMutate.mockReset();
    reembedVariables = undefined;
  });

  afterEach(() => {
    if (mounted) {
      act(() => mounted!.root.unmount());
      mounted.container.remove();
      mounted = null;
    }
    vi.restoreAllMocks();
  });

  it("renders one HOUSEKEEPING eyebrow and the #housekeeping anchor", () => {
    wire();
    mounted = render();
    const anchor = mounted.container.querySelector("#housekeeping");
    expect(anchor).not.toBeNull();
    expect((mounted.container.textContent ?? "").toLowerCase()).toContain("housekeeping");
  });

  // 1 · Retention ----------------------------------------------------------

  it("shows the kind as mono text and exposes only TTL/max-rows inputs (no kind input)", () => {
    wire({ policies: [policy({ kind: "fact" })] });
    mounted = render();
    expect(mounted.container.textContent).toContain("fact");
    const inputs = mounted.container.querySelectorAll("input");
    // exactly two editable cells per row: ttl_days + max_rows; kind is text.
    expect(inputs.length).toBe(2);
    const labels = Array.from(inputs).map((i) => i.getAttribute("aria-label"));
    expect(labels).toEqual(["fact ttl days", "fact max rows"]);
  });

  it("hides the Save pill until a row is dirty, then PUTs only the dirty rows", () => {
    wire({ policies: [policy({ kind: "event", ttl_days: 30, max_rows: 50000 })] });
    mounted = render();

    // No Save pill initially.
    const saveBefore = Array.from(mounted.container.querySelectorAll("button")).find(
      (b) => /save/i.test(b.textContent ?? ""),
    );
    expect(saveBefore).toBeUndefined();

    // Edit the TTL.
    const ttl = mounted.container.querySelector<HTMLInputElement>(
      'input[aria-label="event ttl days"]',
    )!;
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )!.set!;
      setter.call(ttl, "45");
      ttl.dispatchEvent(new Event("input", { bubbles: true }));
    });

    const saveAfter = Array.from(mounted.container.querySelectorAll("button")).find(
      (b) => /save/i.test(b.textContent ?? ""),
    );
    expect(saveAfter).toBeDefined();

    act(() => saveAfter!.click());
    expect(updateMutate).toHaveBeenCalledTimes(1);
    const arg = updateMutate.mock.calls[0][0] as {
      policies: { kind: string; ttl_days: number | null }[];
    };
    expect(arg.policies).toEqual([{ kind: "event", ttl_days: 45, max_rows: 50000 }]);
  });

  it("validates kinds with isValidRetentionKind and skips an invalid kind before PUT", () => {
    // Defensive client-side guard (bu-itute): rows are server-sourced, but a
    // kind outside the backend's accepted set must never reach the PUT. Edit
    // both an invalid and a valid row; only the valid kind is sent.
    wire({
      policies: [
        policy({ kind: "event", ttl_days: 30, max_rows: 50000 }),
        policy({ kind: "bogus", ttl_days: 10, max_rows: 100 }),
      ],
    });
    mounted = render();

    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      "value",
    )!.set!;
    const ttlEvent = mounted.container.querySelector<HTMLInputElement>(
      'input[aria-label="event ttl days"]',
    )!;
    const ttlBogus = mounted.container.querySelector<HTMLInputElement>(
      'input[aria-label="bogus ttl days"]',
    )!;
    act(() => {
      setter.call(ttlEvent, "45");
      ttlEvent.dispatchEvent(new Event("input", { bubbles: true }));
      setter.call(ttlBogus, "99");
      ttlBogus.dispatchEvent(new Event("input", { bubbles: true }));
    });

    const save = Array.from(mounted.container.querySelectorAll("button")).find(
      (b) => /save/i.test(b.textContent ?? ""),
    )!;
    act(() => save.click());

    expect(updateMutate).toHaveBeenCalledTimes(1);
    const arg = updateMutate.mock.calls[0][0] as {
      policies: { kind: string; ttl_days: number | null; max_rows: number | null }[];
    };
    // Valid kind sent; invalid "bogus" kind skipped (never reaches the PUT).
    expect(arg.policies).toEqual([{ kind: "event", ttl_days: 45, max_rows: 50000 }]);
    expect(arg.policies.map((p) => p.kind)).not.toContain("bogus");
    // The skipped kind is surfaced rather than silently dropped.
    expect((mounted.container.textContent ?? "").toLowerCase()).toContain("bogus");
  });

  it("does not call the PUT when every dirty kind is invalid", () => {
    wire({ policies: [policy({ kind: "bogus", ttl_days: 10, max_rows: 100 })] });
    mounted = render();
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      "value",
    )!.set!;
    const ttlBogus = mounted.container.querySelector<HTMLInputElement>(
      'input[aria-label="bogus ttl days"]',
    )!;
    act(() => {
      setter.call(ttlBogus, "99");
      ttlBogus.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const save = Array.from(mounted.container.querySelectorAll("button")).find(
      (b) => /save/i.test(b.textContent ?? ""),
    )!;
    act(() => save.click());
    expect(updateMutate).not.toHaveBeenCalled();
  });

  it("keeps exactly one Save commit pill across multiple dirty rows", () => {
    wire({
      policies: [policy({ kind: "event" }), policy({ kind: "fact", ttl_days: null, max_rows: null })],
    });
    mounted = render();
    const ttlEvent = mounted.container.querySelector<HTMLInputElement>(
      'input[aria-label="event ttl days"]',
    )!;
    const ttlFact = mounted.container.querySelector<HTMLInputElement>(
      'input[aria-label="fact ttl days"]',
    )!;
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      "value",
    )!.set!;
    act(() => {
      setter.call(ttlEvent, "10");
      ttlEvent.dispatchEvent(new Event("input", { bubbles: true }));
      setter.call(ttlFact, "20");
      ttlFact.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const saves = Array.from(mounted.container.querySelectorAll("button")).filter(
      (b) => /save/i.test(b.textContent ?? ""),
    );
    expect(saves.length).toBe(1);
  });

  // 2 · Compaction ---------------------------------------------------------

  it("omits the bytes clause (no em-dash) when bytes_freed is null", () => {
    wire({
      compaction: [
        { id: 1, ts: "2026-06-13T06:02:00Z", kind: "fact", rows_removed: 1204, bytes_freed: 3_250_586 },
        { id: 2, ts: "2026-06-13T06:02:00Z", kind: "embedding", rows_removed: 89, bytes_freed: null },
      ],
    });
    mounted = render();
    const text = mounted.container.textContent ?? "";
    expect(text).toContain("1,204 rows · 3.1 MB");
    expect(text).toContain("89 rows");
    // The null-bytes row must not introduce an em-dash filler.
    expect(text).not.toContain("89 rows · —");
    expect(text).not.toContain("—");
  });

  it("shows the serif-italic empty line when no sweeps recorded", () => {
    wire({ compaction: [] });
    mounted = render();
    expect(mounted.container.textContent).toContain("No sweeps recorded.");
  });

  // 3 · Embeddings ---------------------------------------------------------

  it("renders the dry-run result as one inline mono line (no modal)", () => {
    wire({
      pendingTotal: 412,
      reembedResult: {
        dry_run: true,
        current_model: "m",
        tiers_processed: ["facts", "rules"],
        counts: { facts: 400, rules: 12 },
        total: 412,
        errors: [],
      },
    });
    mounted = render();
    const dry = Array.from(mounted.container.querySelectorAll("button")).find(
      (b) => /dry run/i.test(b.textContent ?? ""),
    )!;
    act(() => dry.click());
    expect(reembedMutate).toHaveBeenCalledWith(expect.objectContaining({ dry_run: true }));
    expect(mounted.container.textContent).toContain(
      "would re-embed 412 rows across 2 tiers",
    );
    // No dialog element anywhere — the result is inline.
    expect(mounted.container.querySelector("dialog")).toBeNull();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });

  it("re-embed is an arm-then-commit pill-morph (no dialog, no mutate on first click)", () => {
    wire({ pendingTotal: 412 });
    mounted = render();
    const findReembed = () =>
      Array.from(mounted!.container.querySelectorAll("button")).find((b) =>
        /^re-embed/i.test((b.textContent ?? "").trim()),
      )!;

    // First click arms (morphs label) but does NOT mutate.
    act(() => findReembed().click());
    expect(reembedMutate).not.toHaveBeenCalled();
    expect(mounted.container.textContent).toContain("re-embed (confirm?)");
    expect(mounted.container.querySelector("dialog")).toBeNull();

    // Second click within the window commits a live run (dry_run=false).
    act(() => findReembed().click());
    expect(reembedMutate).toHaveBeenCalledWith(expect.objectContaining({ dry_run: false }));
  });

  it("shows a mono status line while running and NO progress bar", () => {
    wire({ pendingTotal: 412, reembedPending: true });
    reembedVariables = { dry_run: false };
    mounted = render();
    expect(mounted.container.textContent).toContain("composing…");
    // No <progress> and no role=progressbar anywhere in the band.
    expect(mounted.container.querySelector("progress")).toBeNull();
    expect(mounted.container.querySelector('[role="progressbar"]')).toBeNull();
  });

  it("shows the serif-italic 'All embeddings current.' line when zero drift", () => {
    wire({ pendingTotal: 0 });
    mounted = render();
    expect(mounted.container.textContent).toContain("All embeddings current.");
  });
});
