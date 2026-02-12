import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import ButlersPage from "@/pages/ButlersPage";
import { useButlers } from "@/hooks/use-butlers";

vi.mock("@/hooks/use-butlers", () => ({
  useButlers: vi.fn(),
}));

type UseButlersResult = ReturnType<typeof useButlers>;

function setQueryState(state: Partial<UseButlersResult>) {
  vi.mocked(useButlers).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...state,
  } as UseButlersResult);
}

function renderPage(): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <ButlersPage />
    </MemoryRouter>,
  );
}

describe("ButlersPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders explicit loading state", () => {
    setQueryState({ isLoading: true });
    const html = renderPage();
    expect(html).toContain("Loading butlers...");
  });

  it("renders butler links to detail pages", () => {
    setQueryState({
      data: {
        data: [
          { name: "general", status: "ok", port: 8101 },
          { name: "switchboard", status: "degraded", port: 8100 },
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

  it("renders explicit empty state", () => {
    setQueryState({
      data: {
        data: [],
        meta: {},
      },
    });

    const html = renderPage();
    expect(html).toContain("No butlers found");
  });

  it("renders explicit error state", () => {
    setQueryState({
      isError: true,
      error: new Error("network offline"),
    });

    const html = renderPage();
    expect(html).toContain("Failed to load butlers.");
    expect(html).toContain("network offline");
  });

  it("keeps cached butlers visible on refetch error", () => {
    setQueryState({
      data: {
        data: [{ name: "general", status: "ok", port: 8101 }],
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
