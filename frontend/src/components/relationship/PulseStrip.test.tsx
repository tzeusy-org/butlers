import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { PulseStrip } from "@/components/relationship/PulseStrip";

vi.mock("@/hooks/use-entities", () => ({
  useEntityTimeline: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityGifts: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityLoans: vi.fn(() => ({ data: [], isLoading: false })),
  useUpdateEntityDunbarTier: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

// Lazily-resolved references — must be fetched after vi.mock hoisting.
// eslint-disable-next-line @typescript-eslint/consistent-type-imports
import * as useEntities from "@/hooks/use-entities";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

function render(props: { entityId: string; dunbarTier: number | null; isPinned: boolean }): string {
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
    expect(html).toContain("...");
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
    expect(html).toContain("...");
    expect(html).not.toContain(">None<");
  });
});
