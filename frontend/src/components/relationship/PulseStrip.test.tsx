import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { PulseStrip } from "@/components/relationship/PulseStrip";

vi.mock("@/hooks/use-entities", () => ({
  useEntityTimeline: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityCadence: vi.fn(() => ({
    data: {
      window_days: 30,
      window_started_at: "2026-08-17T00:00:00Z",
      window_ended_at: "2026-09-16T00:00:00Z",
      interaction_count: 0,
      completeness: "complete",
      has_more: false,
    },
    isLoading: false,
    isError: false,
  })),
  useEntityGifts: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityLoans: vi.fn(() => ({ data: [], isLoading: false })),
  useUpdateEntityDunbarTier: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

// Lazily-resolved references — must be fetched after vi.mock hoisting.
import * as useEntities from "@/hooks/use-entities";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

function render(props: {
  entityId: string;
  dunbarTier: number | null;
  isPinned: boolean;
  cadenceWindowDays?: number;
}): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <PulseStrip {...props} />
    </QueryClientProvider>,
  );
}

describe("PulseStrip", () => {
  it("renders all four stat tiles", () => {
    const html = render({ entityId: "e-1", dunbarTier: null, isPinned: false });
    expect(html).toContain("Dunbar tier");
    expect(html).toContain("Last interaction");
    expect(html).toContain("Last 30 days");
    expect(html).toContain("Open loops");
  });

  it("shows Unranked when no dunbar tier is set", () => {
    const html = render({ entityId: "e-1", dunbarTier: null, isPinned: false });
    expect(html).toContain("Unranked");
  });

  it("shows the tier label when a dunbar tier is set", () => {
    const html = render({ entityId: "e-1", dunbarTier: 5, isPinned: false });
    expect(html).toContain("Support 5");
  });

  it("shows None recorded when there are no timeline items", () => {
    const html = render({ entityId: "e-1", dunbarTier: null, isPinned: false });
    expect(html).toContain("None recorded");
  });

  it("renders Quiet only for complete zero-interaction evidence", () => {
    const html = render({ entityId: "e-1", dunbarTier: null, isPinned: false });
    expect(html).toContain("Last 30 days");
    expect(html).toContain(">Quiet<");
    expect(html).not.toContain(">Incomplete<");
  });

  it.each([
    {
      name: "paginated evidence",
      result: {
        data: {
          window_days: 30,
          window_started_at: "2026-08-17T00:00:00Z",
          window_ended_at: "2026-09-16T00:00:00Z",
          interaction_count: 200,
          completeness: "incomplete",
          has_more: true,
        },
        isLoading: false,
        isError: false,
      },
      expected: "Incomplete",
    },
    {
      name: "query failure",
      result: { data: undefined, isLoading: false, isError: true },
      expected: "Unavailable",
    },
    {
      name: "mismatched-window evidence",
      result: {
        data: {
          window_days: 14,
          window_started_at: "2026-09-02T00:00:00Z",
          window_ended_at: "2026-09-16T00:00:00Z",
          interaction_count: 0,
          completeness: "complete",
          has_more: false,
        },
        isLoading: false,
        isError: false,
      },
      expected: "Incomplete",
    },
  ])("shows typed attention for $name instead of Quiet", ({ result, expected }) => {
    vi.mocked(useEntities.useEntityCadence).mockReturnValueOnce(
      result as unknown as ReturnType<typeof useEntities.useEntityCadence>,
    );
    const html = render({ entityId: "e-1", dunbarTier: null, isPinned: false });
    expect(html).toContain(`>${expected}<`);
    expect(html).not.toContain(">Quiet<");
  });

  it("uses the refreshed window's label and matching count", () => {
    vi.mocked(useEntities.useEntityCadence).mockReturnValueOnce({
      data: {
        window_days: 14,
        window_started_at: "2026-09-02T00:00:00Z",
        window_ended_at: "2026-09-16T00:00:00Z",
        interaction_count: 2,
        completeness: "complete",
        has_more: false,
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useEntities.useEntityCadence>);

    const html = render({
      entityId: "e-1",
      dunbarTier: null,
      isPinned: false,
      cadenceWindowDays: 14,
    });
    expect(useEntities.useEntityCadence).toHaveBeenLastCalledWith("e-1", 14);
    expect(html).toContain("Last 14 days");
    expect(html).toContain(">2 interactions<");
    expect(html).not.toContain("Last 30 days");
  });

  it("shows None for open loops when gifts and loans are empty", () => {
    const html = render({ entityId: "e-1", dunbarTier: null, isPinned: false });
    // ">None<" rather than "None" to distinguish from "None recorded" (last interaction)
    expect(html).toContain(">None<");
  });

  it("does NOT show None for open loops while gifts are still loading", () => {
    // Regression: gifts loading, loans loaded → combined isLoading must suppress "None".
    // Cast via unknown: mock stubs only need the fields the component reads.
    vi.mocked(useEntities.useEntityGifts).mockReturnValueOnce(
      { data: undefined, isLoading: true } as unknown as ReturnType<typeof useEntities.useEntityGifts>,
    );
    vi.mocked(useEntities.useEntityLoans).mockReturnValueOnce(
      { data: [], isLoading: false } as unknown as ReturnType<typeof useEntities.useEntityLoans>,
    );
    const html = render({ entityId: "e-1", dunbarTier: null, isPinned: false });
    // The Open loops tile must show the loading placeholder, not "None"
    expect(html).toContain(">...<");
    expect(html).not.toContain(">None<");
  });

  it("does NOT show None for open loops while loans are still loading", () => {
    // Regression: loans loading, gifts loaded → combined isLoading must suppress "None".
    vi.mocked(useEntities.useEntityGifts).mockReturnValueOnce(
      { data: [], isLoading: false } as unknown as ReturnType<typeof useEntities.useEntityGifts>,
    );
    vi.mocked(useEntities.useEntityLoans).mockReturnValueOnce(
      { data: undefined, isLoading: true } as unknown as ReturnType<typeof useEntities.useEntityLoans>,
    );
    const html = render({ entityId: "e-1", dunbarTier: null, isPinned: false });
    expect(html).toContain(">...<");
    expect(html).not.toContain(">None<");
  });
});
