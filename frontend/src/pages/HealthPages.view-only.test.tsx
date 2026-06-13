// @vitest-environment jsdom

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// bu-7oyhi.2 — honest "view-only / butler-managed" framing for the 6 health
// pages. These pages are an observability surface: the Health butler owns all
// writes via its own MCP tools/conversation. The dashboard must NOT present any
// affordance implying the user can add/edit/delete records here (nothing would
// persist — there are no health mutation endpoints).
//
// These tests assert two contracts per page:
//   1. The "Managed by the Health butler" view-only note renders.
//   2. No add/edit/delete/"New X"/"Save" mutation affordance is present.
// ---------------------------------------------------------------------------

// All health pages read through these hooks. Stub them with a loaded-but-empty
// shape so the pages render their normal (non-loading) chrome without needing a
// QueryClient or network.
vi.mock("@/hooks/use-health", () => {
  const empty = {
    data: { data: [], meta: { total: 0, has_more: false } },
    isLoading: false,
  };
  return {
    useMeasurements: () => ({ data: { data: [] }, isLoading: false }),
    useMedications: () => ({ data: { data: [] }, isLoading: false }),
    useMedicationDoses: () => ({ data: [], isLoading: false }),
    useConditions: () => empty,
    useSymptoms: () => empty,
    useMeals: () => empty,
    useResearch: () => empty,
  };
});

import ConditionsPage from "./ConditionsPage";
import MeasurementsPage from "./MeasurementsPage";
import MealsPage from "./MealsPage";
import MedicationsPage from "./MedicationsPage";
import ResearchPage from "./ResearchPage";
import SymptomsPage from "./SymptomsPage";

afterEach(cleanup);

const PAGES: Array<{ name: string; Component: () => React.ReactElement }> = [
  { name: "Medications", Component: MedicationsPage },
  { name: "Conditions", Component: ConditionsPage },
  { name: "Symptoms", Component: SymptomsPage },
  { name: "Research", Component: ResearchPage },
  { name: "Meals", Component: MealsPage },
  { name: "Measurements", Component: MeasurementsPage },
];

// Mutation affordances that would be dishonest on a read-only surface. The
// pages legitimately contain data-filter/pagination controls (Previous, Next,
// Clear, All, Active, Show raw data, meal-type/measurement-type tabs) — those
// are allowed. We assert specifically that there is no control whose label
// implies persisting a NEW record or editing/deleting an existing one.
const FORBIDDEN_BUTTON_LABELS =
  /\b(add|new|create|edit|delete|remove|save|log\s|record\s+a|update)\b/i;

describe.each(PAGES)("$name health page — view-only / butler-managed", ({ Component }) => {
  it("renders exactly one butler-managed view-only note", () => {
    const { container } = render(<Component />);
    const notes = container.querySelectorAll('[data-testid="butler-managed-note"]');
    expect(notes.length).toBe(1);
    const text = notes[0].textContent ?? "";
    expect(text).toMatch(/managed by the Health butler/i);
    expect(text).toMatch(/read-only view/i);
  });

  it("exposes no add/edit/delete mutation affordance", () => {
    const { container } = render(<Component />);
    const buttons = Array.from(container.querySelectorAll("button"));
    const offenders = buttons
      .map((b) => b.textContent?.trim() ?? "")
      .filter((label) => label.length > 0 && FORBIDDEN_BUTTON_LABELS.test(label));
    expect(offenders).toEqual([]);

    // No native form submit controls either.
    const submits = container.querySelectorAll(
      'button[type="submit"], input[type="submit"]',
    );
    expect(submits.length).toBe(0);
  });
});

describe("Health page descriptions — honest framing", () => {
  it("Medications page does not use imperative 'Manage medications' copy", () => {
    const { container } = render(<MedicationsPage />);
    // The old copy "Manage medications and track dose adherence." dishonestly
    // implied the user could manage medications from this read-only page.
    expect(container.textContent ?? "").not.toMatch(/Manage medications/i);
  });

  it("Symptoms/Meals/Measurements pages drop imperative 'Track ...' lead copy", () => {
    for (const Component of [SymptomsPage, MealsPage, MeasurementsPage]) {
      const { container } = render(<Component />);
      const heading = container.querySelector("h1");
      // The H1 description paragraph is the sibling <p> directly under the
      // header block; it must not start with the imperative "Track".
      const desc = heading?.parentElement?.querySelector("p")?.textContent ?? "";
      expect(desc).not.toMatch(/^Track /i);
      cleanup();
    }
  });
});
