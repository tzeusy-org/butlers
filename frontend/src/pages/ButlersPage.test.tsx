import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import ButlersPage from "@/pages/ButlersPage";
import { resetUseButlersMock, setUseButlersState } from "@/test-utils/use-butlers";

vi.mock("@/hooks/use-butlers", () => ({
  useButlers: vi.fn(),
}));

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
});
