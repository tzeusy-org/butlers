/**
 * ButlerOverviewTab — identity card content tests.
 *
 * Pins the six required elements of the identity card:
 *   1. Butler name
 *   2. Status badge
 *   3. Description (serif italic, when present)
 *   4. Port
 *   5. Eligibility state (with quarantine reason when quarantined)
 *   6. 24h eligibility timeline (EligibilityTimeline)
 *
 * Bead: bu-8hbph.1
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import ButlerOverviewTab from "@/components/butler-detail/ButlerOverviewTab";

// ---------------------------------------------------------------------------
// Mock all hooks consumed by ButlerOverviewTab
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-butlers", () => ({
  useButler: vi.fn(),
}));

vi.mock("@/hooks/use-general", () => ({
  useRegistry: vi.fn(),
  useSetEligibility: vi.fn(),
}));

vi.mock("@/hooks/use-costs", () => ({
  useCostSummary: vi.fn(),
}));

vi.mock("@/hooks/use-notifications", () => ({
  useButlerNotifications: vi.fn(),
}));

// Stub EligibilityTimeline to avoid additional hook chains in SSR tests
vi.mock("@/components/butler-detail/EligibilityTimeline", () => ({
  default: ({ butlerName }: { butlerName: string }) => (
    <div data-testid="eligibility-timeline" data-butler={butlerName} />
  ),
}));

// Stub NotificationFeed to avoid dependency chain
vi.mock("@/components/notifications/notification-feed", () => ({
  NotificationFeed: () => <div data-testid="notification-feed" />,
}));

import { useButler } from "@/hooks/use-butlers";
import { useRegistry, useSetEligibility } from "@/hooks/use-general";
import { useCostSummary } from "@/hooks/use-costs";
import { useButlerNotifications } from "@/hooks/use-notifications";

// ---------------------------------------------------------------------------
// Shared mock data
// ---------------------------------------------------------------------------

const BUTLER_OK = {
  name: "general",
  status: "ok",
  port: 40101,
  type: "butler" as const,
  description: "Your everyday assistant for general tasks",
  sessions_24h: 3,
};

const REGISTRY_ACTIVE = {
  name: "general",
  eligibility_state: "active",
  quarantine_reason: null,
};

const REGISTRY_QUARANTINED = {
  name: "general",
  eligibility_state: "quarantined",
  quarantine_reason: "Health check failed: timeout after 30s",
};

// ---------------------------------------------------------------------------
// Setup helpers
// ---------------------------------------------------------------------------

function setupDefaultMocks() {
  vi.mocked(useButler).mockReturnValue({
    data: { data: BUTLER_OK, meta: {} },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn().mockResolvedValue(undefined),
  } as ReturnType<typeof useButler>);

  vi.mocked(useRegistry).mockReturnValue({
    data: { data: [REGISTRY_ACTIVE], meta: {} },
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useRegistry>);

  vi.mocked(useSetEligibility).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as ReturnType<typeof useSetEligibility>);

  vi.mocked(useCostSummary).mockReturnValue({
    data: undefined,
    isLoading: false,
  } as ReturnType<typeof useCostSummary>);

  vi.mocked(useButlerNotifications).mockReturnValue({
    data: { data: [], meta: {} },
    isLoading: false,
  } as ReturnType<typeof useButlerNotifications>);
}

function renderTab(butlerName = "general"): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <ButlerOverviewTab butlerName={butlerName} />
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ButlerOverviewTab — identity card", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupDefaultMocks();
  });

  // -------------------------------------------------------------------------
  // Element 1: Butler name
  // -------------------------------------------------------------------------

  it("renders the butler name in the identity card", () => {
    const html = renderTab();
    expect(html).toContain("general");
  });

  // -------------------------------------------------------------------------
  // Element 2: Status badge
  // -------------------------------------------------------------------------

  it("renders the status badge (ButlerStatusBadge) for an ok butler", () => {
    const html = renderTab();
    // ButlerStatusBadge renders "Up" for status=ok
    expect(html).toContain("Up");
  });

  it("renders 'Down' status badge for a down butler", () => {
    vi.mocked(useButler).mockReturnValue({
      data: { data: { ...BUTLER_OK, status: "error" }, meta: {} },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn().mockResolvedValue(undefined),
    } as ReturnType<typeof useButler>);

    const html = renderTab();
    expect(html).toContain("Down");
  });

  // -------------------------------------------------------------------------
  // Element 3: Description (serif italic when present)
  // -------------------------------------------------------------------------

  it("renders the description when present", () => {
    const html = renderTab();
    expect(html).toContain("Your everyday assistant for general tasks");
  });

  it("renders description with serif italic styling", () => {
    const html = renderTab();
    // CardDescription receives className="italic font-[family-name:var(--font-serif,serif)]"
    // which ends up in the rendered class attribute
    expect(html).toContain("italic");
    expect(html).toContain("font-serif");
  });

  it("omits description when not present in butler data", () => {
    vi.mocked(useButler).mockReturnValue({
      data: { data: { ...BUTLER_OK, description: undefined }, meta: {} },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn().mockResolvedValue(undefined),
    } as ReturnType<typeof useButler>);

    const html = renderTab();
    expect(html).not.toContain("Your everyday assistant for general tasks");
  });

  // -------------------------------------------------------------------------
  // Element 4: Port
  // -------------------------------------------------------------------------

  it("renders the port number in the identity card", () => {
    const html = renderTab();
    expect(html).toContain("Port");
    expect(html).toContain("40101");
  });

  // -------------------------------------------------------------------------
  // Element 5: Eligibility state
  // -------------------------------------------------------------------------

  it("renders Active eligibility badge when state is active", () => {
    const html = renderTab();
    expect(html).toContain("Active");
  });

  it("renders Quarantined eligibility badge when state is quarantined", () => {
    vi.mocked(useRegistry).mockReturnValue({
      data: { data: [REGISTRY_QUARANTINED], meta: {} },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useRegistry>);

    const html = renderTab();
    expect(html).toContain("Quarantined");
  });

  it("renders quarantine reason as muted text when present", () => {
    vi.mocked(useRegistry).mockReturnValue({
      data: { data: [REGISTRY_QUARANTINED], meta: {} },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useRegistry>);

    const html = renderTab();
    expect(html).toContain("Health check failed: timeout after 30s");
  });

  it("omits quarantine reason when state is active", () => {
    const html = renderTab();
    // No quarantine_reason on the active registry entry
    expect(html).not.toContain("Health check failed");
  });

  it("omits eligibility row when no registry entry exists for this butler", () => {
    vi.mocked(useRegistry).mockReturnValue({
      data: { data: [{ name: "other", eligibility_state: "active", quarantine_reason: null }], meta: {} },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useRegistry>);

    const html = renderTab();
    // No eligibility row should render when registry entry is missing
    expect(html).not.toContain("Eligibility");
  });

  // -------------------------------------------------------------------------
  // Element 6: 24h eligibility timeline
  // -------------------------------------------------------------------------

  it("renders the EligibilityTimeline when registry entry exists", () => {
    const html = renderTab();
    expect(html).toContain('data-testid="eligibility-timeline"');
    expect(html).toContain('data-butler="general"');
  });

  it("omits EligibilityTimeline when no registry entry exists for this butler", () => {
    vi.mocked(useRegistry).mockReturnValue({
      data: { data: [], meta: {} },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useRegistry>);

    const html = renderTab();
    expect(html).not.toContain('data-testid="eligibility-timeline"');
  });

  // -------------------------------------------------------------------------
  // Loading state
  // -------------------------------------------------------------------------

  it("renders loading skeleton when butler data is loading", () => {
    vi.mocked(useButler).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn().mockResolvedValue(undefined),
    } as ReturnType<typeof useButler>);

    const html = renderTab();
    // The skeleton renders without crashing; content elements are absent
    expect(html).not.toContain("40101");
    expect(html).not.toContain("Your everyday assistant");
  });
});
