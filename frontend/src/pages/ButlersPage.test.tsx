import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import ButlersPage from "@/pages/ButlersPage";
import { resetUseButlersMock, setUseButlersState } from "@/test-utils/use-butlers";

vi.mock("@/hooks/use-butlers", () => ({
  useButlers: vi.fn(),
}));

vi.mock("@/hooks/use-general", () => ({
  useRegistry: vi.fn(() => ({ data: undefined })),
}));

import { useRegistry } from "@/hooks/use-general";

function setRegistryState(entries: { name: string; eligibility_state: string }[]) {
  vi.mocked(useRegistry).mockReturnValue({
    data: { data: entries, meta: {} },
  } as ReturnType<typeof useRegistry>);
}

const setQueryState = setUseButlersState;

function renderPage(): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <ButlersPage />
    </MemoryRouter>,
  );
}

describe("ButlersPage", () => {
  beforeEach(() => {
    resetUseButlersMock();
    vi.mocked(useRegistry).mockReturnValue({ data: undefined } as ReturnType<typeof useRegistry>);
  });

  it("renders loading skeleton via Page primitive", () => {
    setQueryState({ isLoading: true });
    const html = renderPage();
    // Page primitive renders aria-label="Loading" when loading=true
    expect(html).toContain('aria-label="Loading"');
  });

  it("renders butler links to detail pages", () => {
    setQueryState({
      data: {
        data: [
          { name: "general", status: "ok", port: 40101, type: "butler" as const, sessions_24h: 0 },
          { name: "switchboard", status: "degraded", port: 40100, type: "butler" as const, sessions_24h: 0 },
        ],
        meta: {},
      },
    });

    const html = renderPage();

    expect(html).toContain("general");
    expect(html).toContain("switchboard");
    expect(html).toContain('href="/butlers/general"');
    expect(html).toContain('href="/butlers/switchboard"');
  });

  it("renders empty state when no butlers returned", () => {
    setQueryState({
      data: {
        data: [],
        meta: {},
      },
    });

    const html = renderPage();
    expect(html).toContain("No butlers found");
  });

  it("renders full-page error when no cached data exists", () => {
    setQueryState({
      isError: true,
      error: new Error("network offline"),
    });

    const html = renderPage();
    expect(html).toContain("Something went wrong");
    expect(html).toContain("network offline");
  });

  it("keeps cached butlers visible on refetch error", () => {
    setQueryState({
      data: {
        data: [{ name: "general", status: "ok", port: 40101, type: "butler" as const, sessions_24h: 0 }],
        meta: {},
      },
      isError: true,
      error: new Error("timed out"),
    });

    const html = renderPage();

    expect(html).toContain("Showing last known butler status.");
    expect(html).toContain("general");
    expect(html).toContain("timed out");
  });

  // -------------------------------------------------------------------------
  // Dispatch layout — 5 elements (bu-insd4.1)
  // -------------------------------------------------------------------------

  describe("Dispatch layout card elements", () => {
    const BUTLER = {
      name: "health",
      status: "ok",
      port: 40201,
      type: "butler" as const,
      description: "Tracks your wellness goals",
      sessions_24h: 7,
    };

    it("renders ButlerMark glyph (initial letter via title attribute)", () => {
      setQueryState({ data: { data: [BUTLER], meta: {} } });
      const html = renderPage();
      // ButlerMark renders title={name} — specific to the squircle element, not the link wrapper
      expect(html).toContain('title="health"');
    });

    it("renders name and status pill", () => {
      setQueryState({ data: { data: [BUTLER], meta: {} } });
      const html = renderPage();
      expect(html).toContain("health");
      // Status pill shows "Up" for ok/online
      expect(html).toContain("Up");
    });

    it("renders description text when present", () => {
      setQueryState({ data: { data: [BUTLER], meta: {} } });
      const html = renderPage();
      expect(html).toContain("Tracks your wellness goals");
    });

    it("suppresses description paragraph when absent", () => {
      const noDesc = { ...BUTLER, description: undefined };
      setQueryState({ data: { data: [noDesc], meta: {} } });
      const html = renderPage();
      // Description text must not appear when description is absent
      expect(html).not.toContain("Tracks your wellness goals");
    });

    it("renders sessions_24h count and open link", () => {
      setQueryState({ data: { data: [BUTLER], meta: {} } });
      const html = renderPage();
      expect(html).toContain("7");
      expect(html).toContain("sess");
      expect(html).toContain("open →");
    });

    it("renders eligibility chip from useRegistry when present", () => {
      setQueryState({ data: { data: [BUTLER], meta: {} } });
      setRegistryState([{ name: "health", eligibility_state: "active" }]);
      const html = renderPage();
      expect(html).toContain("Active");
    });

    it("omits eligibility chip when registry has no entry for the butler", () => {
      setQueryState({ data: { data: [BUTLER], meta: {} } });
      // registry returns entry for a different butler
      setRegistryState([{ name: "other", eligibility_state: "quarantined" }]);
      const html = renderPage();
      // "Quarantined" chip should not appear
      expect(html).not.toContain("Quarantined");
    });
  });

  // -------------------------------------------------------------------------
  // Real roster fixture — no mock data (bu-insd4.2)
  // -------------------------------------------------------------------------

  describe("real roster fixture", () => {
    /** Canonical 12 butlers as returned by GET /api/butlers. */
    const REAL_ROSTER = [
      "chronicler",
      "education",
      "finance",
      "general",
      "health",
      "home",
      "lifestyle",
      "messenger",
      "qa",
      "relationship",
      "switchboard",
      "travel",
    ].map((name) => ({
      name,
      status: "ok" as const,
      port: 40100,
      type: "butler" as const,
      sessions_24h: 0,
    }));

    it("renders all 12 canonical butlers by name", () => {
      setQueryState({ data: { data: REAL_ROSTER, meta: {} } });
      const html = renderPage();

      for (const { name } of REAL_ROSTER) {
        expect(html).toContain(name);
      }
    });

    it("renders detail-page links for every canonical butler", () => {
      setQueryState({ data: { data: REAL_ROSTER, meta: {} } });
      const html = renderPage();

      for (const { name } of REAL_ROSTER) {
        expect(html).toContain(`href="/butlers/${name}"`);
      }
    });

    it("renders an unfamiliar butler name without errors", () => {
      // If the API returns a butler that the front-end has never seen, the list
      // must still render. ButlerMark falls back to a hash-derived colour slot.
      const withUnknown = [
        ...REAL_ROSTER,
        { name: "future-butler", status: "ok" as const, port: 40199, type: "butler" as const, sessions_24h: 0 },
      ];
      setQueryState({ data: { data: withUnknown, meta: {} } });
      const html = renderPage();

      expect(html).toContain("future-butler");
      expect(html).toContain('href="/butlers/future-butler"');
      // All canonical butlers must still appear alongside the unknown one
      for (const { name } of REAL_ROSTER) {
        expect(html).toContain(name);
      }
    });
  });
});
