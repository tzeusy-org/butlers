import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import RegistryTable from "@/components/switchboard/RegistryTable";
import { useRegistry } from "@/hooks/use-general";

vi.mock("@/hooks/use-general", () => ({
  useRegistry: vi.fn(),
}));

type UseRegistryResult = ReturnType<typeof useRegistry>;

function setQueryState(state: Partial<UseRegistryResult>) {
  vi.mocked(useRegistry).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...state,
  } as UseRegistryResult);
}

function renderTable(): string {
  return renderToStaticMarkup(<RegistryTable />);
}

function extractBadgeTexts(html: string): string[] {
  return Array.from(
    html.matchAll(/data-slot="badge"[^>]*>([^<]*)<\/span>/g),
    (match) => match[1],
  );
}

describe("RegistryTable", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders module names as full tokens when modules value is a string", () => {
    setQueryState({
      data: {
        data: [
          {
            name: "switchboard",
            endpoint_url: "http://localhost:8100/sse",
            description: "Route messages",
            modules: "telegram, email",
            last_seen_at: null,
            registered_at: "2026-02-13T00:00:00Z",
          },
        ],
        meta: {},
      },
    });

    const html = renderTable();
    expect(extractBadgeTexts(html)).toEqual(["telegram", "email"]);
  });

  it("renders dash for empty modules and keeps single module intact", () => {
    setQueryState({
      data: {
        data: [
          {
            name: "empty",
            endpoint_url: "http://localhost:8101/sse",
            description: null,
            modules: [],
            last_seen_at: null,
            registered_at: "2026-02-13T00:00:00Z",
          },
          {
            name: "single",
            endpoint_url: "http://localhost:8102/sse",
            description: null,
            modules: "telegram",
            last_seen_at: null,
            registered_at: "2026-02-13T00:00:00Z",
          },
        ],
        meta: {},
      },
    });

    const html = renderTable();
    expect(extractBadgeTexts(html)).toEqual(["telegram"]);
    expect(html).toContain("\u2014");
  });
});
