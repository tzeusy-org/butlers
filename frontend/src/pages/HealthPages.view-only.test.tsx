// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// bu-7oyhi.2 / bu-aisjm / bu-a7vw9 / bu-gk38e / bu-5oeoq / bu-wamzk —
// health-page write surfaces.
//
// As of bu-aisjm the Medications page has direct dashboard CRUD (add/edit/
// delete wired to /api/health/medications), so it is NO LONGER view-only.
// As of bu-a7vw9 the Conditions page also has direct dashboard CRUD wired to
// /api/health/conditions, so it is NO LONGER view-only either.
// As of bu-gk38e the Symptoms page also has direct dashboard CRUD wired to
// /api/health/symptoms, so it is NO LONGER view-only either.
// As of bu-5oeoq the Meals page also has direct dashboard CRUD wired to
// /api/health/meals, so it is NO LONGER view-only either.
// As of bu-wamzk the Research page also has direct dashboard CRUD wired to
// /api/health/research, so it is NO LONGER view-only either.
//
// The remaining ONE page (Measurements) is still an observability surface: the
// Health butler owns all writes via its own MCP tools/conversation, and the
// dashboard must NOT present any affordance implying the user can add/edit/
// delete records here.
//
// These tests assert:
//   - For the 1 not-yet-converted page: the "Managed by the Health butler"
//     view-only note renders AND no add/edit/delete affordance is present.
//   - For Medications, Conditions, Symptoms, Meals, and Research: NO view-only
//     note AND add/edit/delete affordances exist.
// ---------------------------------------------------------------------------

// All health pages read through these hooks. Stub them with a loaded shape so
// the pages render their normal (non-loading) chrome without needing a real
// QueryClient or network. Medications returns one row so the per-card edit /
// delete affordances render. The mutation hooks are stubbed as no-op mutations.
vi.mock("@/hooks/use-health", () => {
  const noopMutation = () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isPending: false,
  });
  return {
    useMeasurements: () => ({ data: { data: [] }, isLoading: false }),
    useMedications: () => ({
      data: {
        data: [
          {
            id: "med-1",
            name: "Vitamin D",
            dosage: "1000IU",
            frequency: "daily",
            schedule: [],
            active: true,
            notes: null,
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
          },
        ],
        meta: { total: 1, has_more: false },
      },
      isLoading: false,
    }),
    useMedicationDoses: () => ({ data: [], isLoading: false }),
    useCreateMedication: noopMutation,
    useUpdateMedication: noopMutation,
    useDeleteMedication: noopMutation,
    useConditions: () => ({
      data: {
        data: [
          {
            id: "cond-1",
            name: "Hypertension",
            status: "managed",
            diagnosed_at: null,
            notes: null,
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
          },
        ],
        meta: { total: 1, has_more: false },
      },
      isLoading: false,
    }),
    useCreateCondition: noopMutation,
    useUpdateCondition: noopMutation,
    useDeleteCondition: noopMutation,
    useSymptoms: () => ({
      data: {
        data: [
          {
            id: "sym-1",
            name: "Headache",
            severity: 7,
            condition_id: null,
            occurred_at: "2026-01-01T00:00:00Z",
            notes: null,
            created_at: "2026-01-01T00:00:00Z",
          },
        ],
        meta: { total: 1, has_more: false },
      },
      isLoading: false,
    }),
    useCreateSymptom: noopMutation,
    useUpdateSymptom: noopMutation,
    useDeleteSymptom: noopMutation,
    useMeals: () => ({
      data: {
        data: [
          {
            id: "meal-1",
            type: "lunch",
            description: "Grilled chicken salad",
            nutrition: null,
            eaten_at: "2026-01-01T12:00:00Z",
            notes: null,
            created_at: "2026-01-01T12:00:00Z",
          },
        ],
        meta: { total: 1, has_more: false },
      },
      isLoading: false,
    }),
    useCreateMeal: noopMutation,
    useUpdateMeal: noopMutation,
    useDeleteMeal: noopMutation,
    useResearch: () => ({
      data: {
        data: [
          {
            id: "research-1",
            title: "Magnesium and sleep",
            content: "Studies suggest magnesium improves sleep latency.",
            tags: ["sleep"],
            source_url: null,
            condition_id: null,
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
          },
        ],
        meta: { total: 1, has_more: false },
      },
      isLoading: false,
    }),
    useCreateResearch: noopMutation,
    useUpdateResearch: noopMutation,
    useDeleteResearch: noopMutation,
  };
});

import ConditionsPage from "./ConditionsPage";
import MeasurementsPage from "./MeasurementsPage";
import MealsPage from "./MealsPage";
import MedicationsPage from "./MedicationsPage";
import ResearchPage from "./ResearchPage";
import SymptomsPage from "./SymptomsPage";

afterEach(cleanup);

// Only the 1 not-yet-converted page remains view-only. Medications,
// Conditions, Symptoms, Meals, and Research are asserted separately below to
// have CRUD affordances.
const PAGES: Array<{ name: string; Component: () => React.ReactElement }> = [
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

describe("Medications health page — direct CRUD (bu-aisjm)", () => {
  it("does NOT render the butler-managed view-only note", () => {
    const { container } = render(<MedicationsPage />);
    const notes = container.querySelectorAll('[data-testid="butler-managed-note"]');
    expect(notes.length).toBe(0);
  });

  it("exposes add, edit, and delete affordances", () => {
    render(<MedicationsPage />);
    // Add affordance in the tracker toolbar.
    expect(screen.getByRole("button", { name: /add medication/i })).toBeTruthy();
    // Per-card edit + delete affordances (one medication row is mocked).
    expect(screen.getByRole("button", { name: /edit vitamin d/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /delete vitamin d/i })).toBeTruthy();
  });
});

describe("Conditions health page — direct CRUD (bu-a7vw9)", () => {
  it("does NOT render the butler-managed view-only note", () => {
    const { container } = render(<ConditionsPage />);
    const notes = container.querySelectorAll('[data-testid="butler-managed-note"]');
    expect(notes.length).toBe(0);
  });

  it("exposes add, edit, and delete affordances", () => {
    render(<ConditionsPage />);
    // Add affordance in the tracker toolbar.
    expect(screen.getByRole("button", { name: /add condition/i })).toBeTruthy();
    // Per-row edit + delete affordances (one condition row is mocked).
    expect(screen.getByRole("button", { name: /edit hypertension/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /delete hypertension/i })).toBeTruthy();
  });
});

describe("Symptoms health page — direct CRUD (bu-gk38e)", () => {
  it("does NOT render the butler-managed view-only note", () => {
    const { container } = render(<SymptomsPage />);
    const notes = container.querySelectorAll('[data-testid="butler-managed-note"]');
    expect(notes.length).toBe(0);
  });

  it("exposes add, edit, and delete affordances", () => {
    render(<SymptomsPage />);
    // Add affordance in the tracker toolbar.
    expect(screen.getByRole("button", { name: /log symptom/i })).toBeTruthy();
    // Per-row edit + delete affordances (one symptom row is mocked).
    expect(screen.getByRole("button", { name: /edit headache/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /delete headache/i })).toBeTruthy();
  });
});

describe("Meals health page — direct CRUD (bu-5oeoq)", () => {
  it("does NOT render the butler-managed view-only note", () => {
    const { container } = render(<MealsPage />);
    const notes = container.querySelectorAll('[data-testid="butler-managed-note"]');
    expect(notes.length).toBe(0);
  });

  it("exposes add, edit, and delete affordances", () => {
    render(<MealsPage />);
    // Add affordance in the tracker toolbar.
    expect(screen.getByRole("button", { name: /log meal/i })).toBeTruthy();
    // Per-row edit + delete affordances (one meal row is mocked).
    expect(screen.getByRole("button", { name: /edit grilled chicken salad/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /delete grilled chicken salad/i })).toBeTruthy();
  });
});

describe("Research health page — direct CRUD (bu-wamzk)", () => {
  it("does NOT render the butler-managed view-only note", () => {
    const { container } = render(<ResearchPage />);
    const notes = container.querySelectorAll('[data-testid="butler-managed-note"]');
    expect(notes.length).toBe(0);
  });

  it("exposes add, edit, and delete affordances", () => {
    render(<ResearchPage />);
    // Add affordance in the tracker toolbar.
    expect(screen.getByRole("button", { name: /add research/i })).toBeTruthy();
    // Per-row edit + delete affordances (one research row is mocked).
    expect(screen.getByRole("button", { name: /edit magnesium and sleep/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /delete magnesium and sleep/i })).toBeTruthy();
  });
});

describe("Health page descriptions — honest framing", () => {
  it("Measurements page drops imperative 'Track ...' lead copy", () => {
    for (const Component of [MeasurementsPage]) {
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
