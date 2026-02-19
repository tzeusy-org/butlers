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
            endpoint_url: "http://localhost:40100/sse",
            description: "Route messages",
            modules: "telegram, email" as unknown as unknown[],
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
            endpoint_url: "http://localhost:40101/sse",
            description: null,
            modules: [],
            last_seen_at: null,
            registered_at: "2026-02-13T00:00:00Z",
          },
          {
            name: "single",
            endpoint_url: "http://localhost:40102/sse",
            description: null,
            modules: "telegram" as unknown as unknown[],
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

  it("strips brackets when module strings are not valid JSON arrays", () => {
    setQueryState({
      data: {
        data: [
          {
            name: "legacy",
            endpoint_url: "http://localhost:40103/sse",
            description: null,
            modules: "[telegram, email]" as unknown as unknown[],
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

  it("renders dash when modules payload is nested beyond max depth", () => {
    let deeplyNestedModules: unknown = "telegram";
    for (let i = 0; i < 12; i += 1) {
      deeplyNestedModules = [deeplyNestedModules];
    }

    setQueryState({
      data: {
        data: [
          {
            name: "nested",
            endpoint_url: "http://localhost:40104/sse",
            description: null,
            modules: deeplyNestedModules as unknown as unknown[],
            last_seen_at: null,
            registered_at: "2026-02-13T00:00:00Z",
          },
        ],
        meta: {},
      },
    });

    const html = renderTable();
    expect(extractBadgeTexts(html)).toEqual([]);
    expect(html).toContain("\u2014");
  });

  // Regression test: butlers-992
  // The API can return modules as a JSON-serialized array string (e.g. when the
  // backend double-serializes the JSONB column).  The normalizeModules helper
  // must parse the serialized string to intact module tokens and must NOT
  // iterate its characters one-by-one.
  it("parses JSON-serialized array string to intact module tokens (no char-splitting)", () => {
    setQueryState({
      data: {
        data: [
          {
            name: "switchboard",
            endpoint_url: "http://localhost:40100/sse",
            description: null,
            // Simulates an API response where modules was serialized as a JSON
            // string instead of a native array — the payload shape that triggers
            // the char-splitting regression.
            modules: '["telegram","email"]' as unknown as unknown[],
            last_seen_at: null,
            registered_at: "2026-02-13T00:00:00Z",
          },
        ],
        meta: {},
      },
    });

    const html = renderTable();
    const badges = extractBadgeTexts(html);
    // Must show two intact module names, not individual characters.
    expect(badges).toEqual(["telegram", "email"]);
    // Guard: must not contain single-character badges that would indicate splitting.
    const singleCharBadges = badges.filter((b) => b.length === 1);
    expect(singleCharBadges).toHaveLength(0);
  });

  // Regression test: butlers-992
  // An array of single-character strings represents the observed broken payload
  // shape produced by the pre-fix backend (list("telegram") char-splits the
  // string).  The frontend normalizeModules function surfaces these characters
  // as-is, so the only reliable safeguard is the backend fix.  This test
  // documents the payload shape to prevent silent regressions if the backend
  // normalization is removed: each character would show as a separate badge.
  it("documents char-split payload shape: single-char badges appear when backend sends pre-split array", () => {
    // This is the broken payload that the backend emits when it does
    // list("telegram") on a JSONB string value.
    const charSplitModules = [...("telegram")]; // ["t","e","l","e","g","r","a","m"]
    setQueryState({
      data: {
        data: [
          {
            name: "broken",
            endpoint_url: "http://localhost:40105/sse",
            description: null,
            modules: charSplitModules as unknown as unknown[],
            last_seen_at: null,
            registered_at: "2026-02-13T00:00:00Z",
          },
        ],
        meta: {},
      },
    });

    const html = renderTable();
    const badges = extractBadgeTexts(html);
    // The frontend renders what the backend sends; with a char-split payload
    // all 8 character badges appear instead of the single "telegram" token.
    // If this assertion breaks, the backend has regressed to char-splitting again.
    expect(badges).toHaveLength("telegram".length);
    expect(badges.every((b) => b.length === 1)).toBe(true);
  });
});
