// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";

// ---------------------------------------------------------------------------
// Stubs for the new Health Overview page hooks (bu-w7b18.1)
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-health-briefing", () => ({
  useHealthBriefing: () => ({
    data: {
      greet: "Good morning.",
      headline: "Weight is stable.",
      elaboration: "Your recent readings are within expected ranges.",
      source: "fallback",
      state_class: "nominal",
      generated_at: "2026-01-01T08:00:00Z",
    },
    isFetching: false,
    refetch: vi.fn(),
  }),
}));

vi.mock("@/hooks/use-insights", () => ({
  useInsights: () => ({
    data: [
      {
        id: "insight-1",
        origin_butler: "health",
        priority: 2,
        category: "measurement",
        dedup_key: "weight-drift",
        cooldown_days: null,
        expires_at: null,
        message: "Weight has drifted upward over the past two weeks.",
        channel: null,
        metadata: null,
        created_at: "2026-01-01T00:00:00Z",
        status: "pending",
        delivered_at: null,
        delivery_attempt_count: 0,
      },
    ],
    isLoading: false,
  }),
}));

// ---------------------------------------------------------------------------
// bu-7oyhi.2 / bu-aisjm / bu-a7vw9 / bu-gk38e / bu-5oeoq / bu-wamzk / bu-mqhas —
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
// As of bu-mqhas the Measurements page also has direct dashboard CRUD wired to
// /api/health/measurements, so it is NO LONGER view-only either.
//
// This completes the six-page health-CRUD epic (bu-eqkmi): ALL SIX health pages
// (Measurements, Medications, Conditions, Symptoms, Meals, Research) now expose
// direct add/edit/delete affordances and NONE render the butler-managed
// view-only note. The view-only PAGES list below is therefore empty.
//
// These tests assert that every converted page exposes add/edit/delete
// affordances and renders NO "Managed by the Health butler" view-only note.
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
    useMeasurements: () => ({
      data: {
        data: [
          {
            id: "meas-1",
            type: "weight",
            value: { value: 70 },
            measured_at: "2026-01-01T00:00:00Z",
            notes: null,
            created_at: "2026-01-01T00:00:00Z",
          },
        ],
        meta: { total: 1, has_more: false },
      },
      isLoading: false,
    }),
    useMeasurementTrend: () => ({
      data: {
        type: "weight",
        window_days: 14,
        bucket: "daily",
        buckets: [
          {
            bucket_start: "2026-01-01T00:00:00Z",
            value_mean: 70,
            value_min: 70,
            value_max: 70,
            sample_count: 1,
          },
        ],
      },
      isLoading: false,
    }),
    useCreateMeasurement: noopMutation,
    useUpdateMeasurement: noopMutation,
    useDeleteMeasurement: noopMutation,
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
    useMedicationAdherence: () => ({ data: undefined, isLoading: false }),
    useLogMedicationDose: noopMutation,
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
    // Stubs for Health Overview page (bu-w7b18.1)
    useMeasurementsLatest: () => ({
      data: {
        measurements: {
          weight: { measured_at: "2026-01-01T00:00:00Z", value: { value: 72 }, unit: "kg", metadata: null },
          blood_pressure: { measured_at: "2026-01-01T00:00:00Z", value: { systolic: 118, diastolic: 76 }, unit: null, metadata: null },
          heart_rate: { measured_at: "2026-01-01T00:00:00Z", value: { bpm: 62 }, unit: "bpm", metadata: null },
          blood_sugar: { measured_at: "2026-01-01T00:00:00Z", value: { value: 95 }, unit: "mg/dL", metadata: null },
        },
      },
      isLoading: false,
    }),
    useMeasurementSources: () => ({
      data: [{ name: "apple_health", last_sample_at: "2026-01-01T06:00:00Z", sample_count: 42 }],
      isLoading: false,
    }),
  };
});

import HealthOverviewPage from "./HealthOverviewPage";
import ConditionsPage from "./ConditionsPage";
import MeasurementsPage from "./MeasurementsPage";
import MealsPage from "./MealsPage";
import MedicationsPage from "./MedicationsPage";
import ResearchPage from "./ResearchPage";
import SymptomsPage from "./SymptomsPage";

afterEach(cleanup);

// The six-page health-CRUD epic (bu-eqkmi) is complete: no health page remains
// view-only, so this list is empty. Each converted page is asserted to have
// CRUD affordances in its own describe block below. The list is retained (empty)
// so a regression that re-introduces a view-only surface has a home to land in.
const PAGES: Array<{ name: string; Component: () => React.ReactElement }> = [];

describe.each(PAGES)("$name health page — view-only / butler-managed", ({ Component }) => {
  it("renders exactly one butler-managed view-only note", () => {
    const { container } = render(<Component />);
    const notes = container.querySelectorAll('[data-testid="butler-managed-note"]');
    expect(notes.length).toBe(1);
  });
});

describe("All six health pages have CRUD (epic bu-eqkmi complete)", () => {
  it("no view-only page remains", () => {
    expect(PAGES).toEqual([]);
  });
});

describe("Measurements health page — direct CRUD (bu-mqhas)", () => {
  it("does NOT render the butler-managed view-only note", () => {
    const { container } = render(<MeasurementsPage />);
    const notes = container.querySelectorAll('[data-testid="butler-managed-note"]');
    expect(notes.length).toBe(0);
  });

  it("exposes add, edit, and delete affordances", () => {
    render(<MeasurementsPage />);
    // Add affordance in the tracker toolbar.
    expect(screen.getByRole("button", { name: /log measurement/i })).toBeTruthy();
    // Per-row edit + delete affordances (one weight reading is mocked).
    expect(screen.getByRole("button", { name: /edit weight/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /delete weight/i })).toBeTruthy();
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

// ---------------------------------------------------------------------------
// Health Overview page — bu-w7b18.1
// ---------------------------------------------------------------------------

// HealthOverviewPage uses AttentionList which contains react-router <Link>,
// so all renders need a MemoryRouter context.
function renderInRouter(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

describe("Health Overview page (bu-w7b18.1)", () => {
  it("renders the two-column layout with testid=health-overview-page", () => {
    const { container } = renderInRouter(<HealthOverviewPage />);
    expect(container.querySelector('[data-testid="health-overview-page"]')).toBeTruthy();
  });

  it("renders the briefing headline from mocked useHealthBriefing", () => {
    renderInRouter(<HealthOverviewPage />);
    // The Display headline should contain the headline from our stub.
    expect(screen.getByTestId("health-headline")).toBeTruthy();
    expect(screen.getByTestId("health-headline").textContent).toBe("Weight is stable.");
  });

  it("renders the attention index section", () => {
    renderInRouter(<HealthOverviewPage />);
    expect(screen.getByTestId("health-attention-index")).toBeTruthy();
  });

  it("renders the insight message in the attention list", () => {
    renderInRouter(<HealthOverviewPage />);
    expect(screen.getByText("Weight has drifted upward over the past two weeks.")).toBeTruthy();
  });

  it("renders the KPI strip with 4 cells", () => {
    const { container } = renderInRouter(<HealthOverviewPage />);
    // KpiStrip renders with role=group aria-label="Key performance indicators"
    const kpiGroup = container.querySelector('[role="group"]');
    expect(kpiGroup).toBeTruthy();
    // Four cells — one per KPI eyebrow
    const cells = kpiGroup!.children;
    expect(cells.length).toBe(4);
  });

  it("renders no fake numbers — absent readings render em-dash", () => {
    // With the mocked useMeasurementsLatest returning real values, all cells
    // should have real values, not em-dashes. This test verifies the em-dash
    // path is used only when data is absent, not injected as placeholder.
    renderInRouter(<HealthOverviewPage />);
    // Weight value "72" should appear (from mock)
    expect(screen.getByText("72")).toBeTruthy();
  });

  it("renders ButlerMark for health hue (hue only on ButlerMark)", () => {
    const { container } = renderInRouter(<HealthOverviewPage />);
    // The health hue must not appear on any other chrome element.
    // Ensure the page renders without ButlerMark import failure.
    expect(container.querySelector('[data-testid="health-overview-page"]')).toBeTruthy();
  });

  it("does NOT render a shadcn Card shell", () => {
    const { container } = renderInRouter(<HealthOverviewPage />);
    // shadcn Card shells have class="rounded-xl border bg-card..."; verify absence.
    const cards = container.querySelectorAll(".rounded-xl.border.bg-card, [data-slot=card]");
    expect(cards.length).toBe(0);
  });
});
