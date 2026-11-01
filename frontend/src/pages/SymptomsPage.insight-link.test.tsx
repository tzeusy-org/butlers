// @vitest-environment jsdom

import type { ComponentType } from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router";

import type { InsightCandidate, SymptomParams } from "@/api/types";
import { insightHref } from "@/lib/health-insight-links";
import { getShellCapability } from "@/lib/shell-capability";

let SymptomsRoute: ComponentType;

beforeAll(async () => {
  ({ default: SymptomsRoute } = await getShellCapability("/health/symptoms")!.loader());
});

const useSymptoms = vi.fn().mockReturnValue({
  data: { data: [], meta: { total: 100, has_more: true } },
  isLoading: false,
});

vi.mock("@/hooks/use-health", () => ({
  useSymptoms: (params: SymptomParams) => useSymptoms(params),
  useConditions: () => ({ data: { data: [] } }),
  useCreateSymptom: () => ({ mutateAsync: vi.fn() }),
  useUpdateSymptom: () => ({ mutateAsync: vi.fn() }),
  useDeleteSymptom: () => ({ mutateAsync: vi.fn() }),
}));

function candidate(metadata: InsightCandidate["metadata"]): InsightCandidate {
  return {
    id: "symptom-insight",
    origin_butler: "health",
    priority: 70,
    category: "symptom-trend",
    dedup_key: "health:symptom-trend:headache:week",
    cooldown_days: null,
    expires_at: null,
    message: "A symptom trend",
    channel: null,
    metadata,
    created_at: null,
    status: "pending",
    delivered_at: null,
    delivery_attempt_count: 0,
  };
}

function href(metadata: InsightCandidate["metadata"]) {
  return insightHref(candidate(metadata), new Set())!;
}

function page(initialEntry: string, nextEntry = href({ name: "Migraine" })) {
  return (
    <MemoryRouter initialEntries={[initialEntry]}>
      <Link to={nextEntry}>Next insight</Link>
      <Routes>
        <Route path="/health/symptoms" element={<SymptomsRoute />} />
      </Routes>
    </MemoryRouter>
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("symptom insight to the real page filter", () => {
  it("round-trips a known name through an encoded same-origin link and the query hook", () => {
    const name = "Headache & nausea / left? #é=50%";
    const destination = href({ name, href: "https://invalid.example/elsewhere" });
    expect(destination).toContain("/health/symptoms?name=");
    const url = new URL(destination, "https://dashboard.example");
    expect(url.origin).toBe("https://dashboard.example");
    expect(url.pathname).toBe("/health/symptoms");
    expect([...url.searchParams]).toEqual([["name", name]]);

    render(page(destination));

    expect(screen.getByPlaceholderText("Filter by name...")).toHaveProperty("value", name);
    expect(useSymptoms).toHaveBeenLastCalledWith(expect.objectContaining({ name, offset: 0 }));
  });

  it("loads a different insight name and resets pagination after manual filtering", () => {
    render(page(href({ name: "Headache" })));
    fireEvent.change(screen.getByPlaceholderText("Filter by name..."), {
      target: { value: "Manual search" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(useSymptoms).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50 }));

    fireEvent.click(screen.getByRole("link", { name: "Next insight" }));

    expect(screen.getByPlaceholderText("Filter by name...")).toHaveProperty("value", "Migraine");
    expect(useSymptoms).toHaveBeenLastCalledWith(
      expect.objectContaining({ name: "Migraine", offset: 0 }),
    );
  });

  it("keeps manual edits across rerenders without a query and allows clearing them", () => {
    const view = render(page("/health/symptoms"));
    expect(useSymptoms).toHaveBeenLastCalledWith(expect.objectContaining({ name: undefined }));
    fireEvent.change(screen.getByPlaceholderText("Filter by name..."), {
      target: { value: "Manual search" },
    });
    view.rerender(page("/health/symptoms"));
    expect(screen.getByPlaceholderText("Filter by name...")).toHaveProperty("value", "Manual search");
    expect(useSymptoms).toHaveBeenLastCalledWith(
      expect.objectContaining({ name: "Manual search" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(useSymptoms).toHaveBeenLastCalledWith(expect.objectContaining({ name: undefined }));
  });

  it.each([null, { name: 7 }, { name: "   " }])(
    "keeps missing or malformed metadata unfiltered (%j)",
    (metadata) => {
      expect(href(metadata)).toBe("/health/symptoms");
      render(page(href(metadata)));
      expect(screen.getByPlaceholderText("Filter by name...")).toHaveProperty("value", "");
      expect(useSymptoms).toHaveBeenLastCalledWith(expect.objectContaining({ name: undefined }));
    },
  );
});
