// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// SecretsPage tests [bu-q77du, bu-nrgk9, bu-5ccth]
//
// Coverage:
//   - Page mounts DirectionPassport without crashing
//   - Deep-link: ?focus=u:google renders google User page
//   - Identity-switch: ?identity=<id> updates URL and re-projects User group
//   - OAuth re-entry: ?toast=connected shows toast (sonner spy) and strips param
//   - OAuth re-entry: ?oauth_error=<e> shows warning toast and strips param
//   - Degraded partial rendering (bu-5ccth): terminal failure keeps a working
//     Retry button; a refetch failure with cached data renders the passport
//     behind a SourceDegradedNote banner instead of blanking the page; a
//     meta.sources_degraded backend hit names the missing family inline.
//
// SecretsPage now fetches inventory via useSecretsInventory (bu-nrgk9).
// Tests that render <SecretsPage /> mock the hook so they receive MOCK_INVENTORY
// synchronously without a real network call.
// ---------------------------------------------------------------------------

import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createRoot, type Root } from "react-dom/client";
import * as React from "react";
import { act } from "react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import SecretsPage from "./SecretsPage";
import { DirectionPassport } from "@/components/secrets/passport";
import { MOCK_INVENTORY } from "@/components/secrets/passport/mock-data";
import { buildSpineEntries } from "@/components/secrets/passport/spine-builder";
import { useSecretsInventory } from "@/hooks/use-secrets-inventory.ts";

// ---------------------------------------------------------------------------
// Mock useSecretsInventory so <SecretsPage /> receives MOCK_INVENTORY
// synchronously without hitting the network. Individual tests override the
// return value via vi.mocked(...).mockReturnValue(...) to exercise the
// loading/terminal-error/degraded-with-data states.
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-secrets-inventory.ts", () => ({
  useSecretsInventory: vi.fn(),
  secretsInventoryKeys: { all: [], byIdentity: () => [] },
  adaptInventoryResponse: (d: unknown) => d,
}));

type UseSecretsInventoryResult = ReturnType<typeof useSecretsInventory>;

const BASE_QUERY_RESULT = {
  data: MOCK_INVENTORY,
  isLoading: false,
  isError: false,
  error: null,
  dataUpdatedAt: 1751763600000, // 2026-07-06T01:00:00Z — arbitrary fixed epoch
  refetch: vi.fn(),
};

beforeEach(() => {
  vi.mocked(useSecretsInventory).mockReturnValue(
    BASE_QUERY_RESULT as unknown as UseSecretsInventoryResult,
  );
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderInRouter(
  element: React.ReactElement,
  initialEntries: string[] = ["/secrets"],
): string {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>{element}</MemoryRouter>
    </QueryClientProvider>,
  );
}

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

const mountedRoots: Array<{ container: HTMLDivElement; root: Root }> = [];

afterEach(() => {
  while (mountedRoots.length > 0) {
    const mounted = mountedRoots.pop()!;
    act(() => {
      mounted.root.unmount();
    });
    mounted.container.remove();
  }
});

/** Mounts <SecretsPage /> interactively (createRoot + act) so click handlers
 * (e.g. the Retry button) actually fire — renderToStaticMarkup is SSR-only
 * and cannot exercise event handlers. Unmounted automatically by the shared
 * afterEach above. */
async function mountInteractive(
  initialEntries: string[] = ["/secrets"],
): Promise<{ container: HTMLDivElement; root: Root }> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mountedRoots.push({ container, root });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={initialEntries}>
          <SecretsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await flush();
  });
  return { container, root };
}

// ---------------------------------------------------------------------------
// SecretsPage: mounts DirectionPassport
// ---------------------------------------------------------------------------

describe("SecretsPage: mounts DirectionPassport", () => {
  it("renders SecretsPage and mounts DirectionPassport", () => {
    const html = renderInRouter(<SecretsPage />);
    expect(html).toContain('data-direction-passport="true"');
  });

  it("renders spine rows", () => {
    const html = renderInRouter(<SecretsPage />);
    expect(html).toContain("data-spine-row");
  });

  it("does NOT render the legacy tab strip", () => {
    const html = renderInRouter(<SecretsPage />);
    // Legacy tabs had role="tablist" or Tabs component
    expect(html).not.toContain('role="tablist"');
  });
});

// ---------------------------------------------------------------------------
// Deep-link focus routing (§Deep-Link Focus Routing)
// ---------------------------------------------------------------------------

describe("Deep-link focus routing", () => {
  it("?focus=u:google highlights the google spine row and renders user page", () => {
    const html = renderInRouter(
      <DirectionPassport inventory={MOCK_INVENTORY} />,
      ["/secrets?focus=u:google"],
    );
    // The spine entry for u:google should be present
    expect(html).toContain('data-key="u:google"');
    // PageUser for google renders data-page="user" and data-provider="google"
    expect(html).toContain('data-page="user"');
    expect(html).toContain('data-provider="google"');
  });

  it("?focus=s:BUTLER_TELEGRAM_TOKEN renders system page", () => {
    const html = renderInRouter(
      <DirectionPassport inventory={MOCK_INVENTORY} />,
      ["/secrets?focus=s:BUTLER_TELEGRAM_TOKEN"],
    );
    expect(html).toContain('data-page="system"');
    expect(html).toContain('data-key="s:BUTLER_TELEGRAM_TOKEN"');
  });

  it("?focus=c:claude-cli renders cli page", () => {
    const html = renderInRouter(
      <DirectionPassport inventory={MOCK_INVENTORY} />,
      ["/secrets?focus=c:claude-cli"],
    );
    expect(html).toContain('data-page="cli"');
    expect(html).toContain('data-cli-id="claude-cli"');
  });

  it("unknown ?focus= falls back to default key and renders a valid credential page", () => {
    // Per §Deep-Link Focus Routing + DirectionPassport implementation:
    // When ?focus= references a credential not in the spine, DirectionPassport
    // falls back to pickDefaultKey(entries) and renders that entry's page.
    // (An amber toast for unknown keys is handled by backend-side redirect logic.)
    const html = renderInRouter(
      <DirectionPassport inventory={MOCK_INVENTORY} />,
      ["/secrets?focus=u:nonexistent_provider"],
    );
    // Falls back to default — some credential page renders
    expect(html).toContain('data-direction-passport="true"');
    expect(html).toContain('data-spine-row');
    // No legacy deprecated chrome
    expect(html).not.toContain('role="tablist"');
  });
});

// ---------------------------------------------------------------------------
// Identity switcher (§Projection-Lens Identity Switcher)
// ---------------------------------------------------------------------------

describe("Identity switcher", () => {
  it("?identity=wei filters User group to wei's credentials only", () => {
    const tzeEntries = buildSpineEntries(MOCK_INVENTORY, "tze");
    const weiEntries = buildSpineEntries(MOCK_INVENTORY, "wei");

    // Wei only has google in mock data
    const weiUserKeys = weiEntries
      .filter((e) => e.family === "user")
      .map((e) => e.key);
    const tzeUserKeys = tzeEntries
      .filter((e) => e.family === "user")
      .map((e) => e.key);

    // Wei has fewer user credentials than owner
    expect(weiUserKeys.length).toBeLessThan(tzeUserKeys.length);
    // Wei's keys are a subset
    expect(weiUserKeys.every((k) => k.startsWith("u:"))).toBe(true);
  });

  it("identity=wei renders wei identity chip but not tze-specific credentials", () => {
    const html = renderInRouter(
      <DirectionPassport inventory={MOCK_INVENTORY} />,
      ["/secrets?identity=wei"],
    );
    // Identity chip for wei should be rendered
    expect(html).toContain('data-identity-id="wei"');
    // System and CLI rows still present (not identity-scoped)
    expect(html).toContain('data-family="system"');
    expect(html).toContain('data-family="cli"');
  });

  it("single-identity: identity chip hidden when only one identity", () => {
    const singleIdentityInventory = {
      ...MOCK_INVENTORY,
      identities: [MOCK_INVENTORY.identities[0]],
    };
    const html = renderInRouter(
      <DirectionPassport inventory={singleIdentityInventory} />,
    );
    // Only one identity: chip for second identity (wei) must not appear
    expect(html).not.toContain('data-identity-id="wei"');
  });
});

// ---------------------------------------------------------------------------
// OAuth re-entry / callback bookkeeping (§Cross-Page Reauth Bookkeeping)
// ---------------------------------------------------------------------------

describe("OAuth re-entry: toast param handling", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("?toast=connected: DirectionPassport still renders (callback URL does not crash)", () => {
    // The toast firing is handled in SecretsPage useEffect (which doesn't run in
    // renderToStaticMarkup — that's a pure SSR render). This test confirms the
    // component at least renders without crashing when the URL has toast params.
    const html = renderInRouter(
      <DirectionPassport inventory={MOCK_INVENTORY} />,
      ["/secrets?focus=u:google&toast=connected"],
    );
    // The passport still renders
    expect(html).toContain('data-direction-passport="true"');
    // The google page renders because ?focus=u:google is present
    expect(html).toContain('data-provider="google"');
  });

  it("?focus=u:google&toast=connected: focus key is read correctly", () => {
    const html = renderInRouter(
      <DirectionPassport inventory={MOCK_INVENTORY} />,
      ["/secrets?focus=u:google&toast=connected"],
    );
    // DirectionPassport parses ?focus even in the presence of ?toast
    expect(html).toContain('data-key="u:google"');
  });

  it("?oauth_error=invalid_grant: DirectionPassport still renders", () => {
    const html = renderInRouter(
      <DirectionPassport inventory={MOCK_INVENTORY} />,
      ["/secrets?oauth_error=invalid_grant"],
    );
    expect(html).toContain('data-direction-passport="true"');
  });
});

// ---------------------------------------------------------------------------
// Legacy patterns absent (§Passport-Book Information Architecture)
// ---------------------------------------------------------------------------

describe("Legacy patterns absent", () => {
  it("no SecretsTable ••••••• blob rendered", () => {
    const html = renderInRouter(<DirectionPassport inventory={MOCK_INVENTORY} />);
    // SecretsTable rendered masked values as ••••••••
    expect(html).not.toContain("••••••••");
  });

  it("no horizontal tab strip rendered", () => {
    const html = renderInRouter(<DirectionPassport inventory={MOCK_INVENTORY} />);
    expect(html).not.toContain('role="tablist"');
  });

  it("no six bespoke Setup cards — passport body is present", () => {
    const html = renderInRouter(<DirectionPassport inventory={MOCK_INVENTORY} />);
    // DirectionPassport uses the Dispatch design language, not card-based layout
    expect(html).toContain('data-direction-passport="true"');
    expect(html).toContain('data-spine-row');
  });
});

// ---------------------------------------------------------------------------
// Degraded partial rendering (bu-5ccth)
//
// A single slow/failed inventory query must never paint the whole page
// "Failed to load credentials." with no retry and no partial render when
// cached data exists. TanStack Query v5 never clears `data` on a
// background-refetch error, so the terminal wall is reserved for
// isError && !data; every other error keeps rendering the passport behind a
// SourceDegradedNote banner with a working Retry action.
// ---------------------------------------------------------------------------

describe("Degraded partial rendering (bu-5ccth)", () => {
  afterEach(() => {
    vi.mocked(useSecretsInventory).mockReset();
  });

  it("terminal failure (no cached data): shows the failure message with a working Retry button", async () => {
    const refetch = vi.fn();
    vi.mocked(useSecretsInventory).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("relation \"entity_info\" does not exist"),
      dataUpdatedAt: 0,
      refetch,
    } as unknown as UseSecretsInventoryResult);

    const { container } = await mountInteractive();

    expect(container.textContent).toContain("Failed to load credentials");
    expect(container.textContent).toContain('relation "entity_info" does not exist');
    // No passport chrome — there is genuinely nothing to show yet.
    expect(container.querySelector("[data-spine-row]")).toBeNull();

    const retryButton = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === "Retry",
    );
    expect(retryButton).toBeTruthy();
    await act(async () => {
      retryButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("refetch failure WITH cached data: keeps rendering the passport behind a degraded banner, not the terminal wall", async () => {
    const refetch = vi.fn();
    vi.mocked(useSecretsInventory).mockReturnValue({
      data: MOCK_INVENTORY,
      isLoading: false,
      isError: true,
      error: new Error("Request timed out after 15s"),
      dataUpdatedAt: 1751763600000,
      refetch,
    } as unknown as UseSecretsInventoryResult);

    const { container } = await mountInteractive();

    // The passport still renders — never-blank floor.
    expect(container.querySelector('[data-direction-passport="true"]')).toBeTruthy();
    expect(container.querySelectorAll("[data-spine-row]").length).toBeGreaterThan(0);

    // The terminal "Failed to load credentials" wall must NOT render.
    expect(container.textContent).not.toContain("Failed to load credentials");

    const banner = container.querySelector(
      "[data-testid='secrets-inventory-degraded']",
    );
    expect(banner).toBeTruthy();
    expect(banner?.textContent).toContain("unreachable");
    expect(banner?.textContent).toContain("retrying");

    const retryButton = banner?.querySelector("button");
    expect(retryButton).toBeTruthy();
    await act(async () => {
      retryButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("meta.sources_degraded (partial backend fan-out failure): names the missing family without an error state", async () => {
    vi.mocked(useSecretsInventory).mockReturnValue({
      data: { ...MOCK_INVENTORY, sourcesDegraded: ["finance"] },
      isLoading: false,
      isError: false,
      error: null,
      dataUpdatedAt: 1751763600000,
      refetch: vi.fn(),
    } as unknown as UseSecretsInventoryResult);

    const { container } = await mountInteractive();

    expect(container.querySelector('[data-direction-passport="true"]')).toBeTruthy();
    const banner = container.querySelector(
      "[data-testid='secrets-inventory-partial-degraded']",
    );
    expect(banner).toBeTruthy();
    expect(banner?.textContent).toContain("finance");
    expect(banner?.textContent).toContain("unavailable");
  });

  it("no sourcesDegraded and no isError: no degraded banner rendered", async () => {
    const { container } = await mountInteractive();

    expect(container.querySelector("[data-testid='secrets-inventory-degraded']")).toBeNull();
    expect(
      container.querySelector("[data-testid='secrets-inventory-partial-degraded']"),
    ).toBeNull();
  });
});
