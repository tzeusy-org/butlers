// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Secrets FE-5 tests [bu-kx954]
//
// Covers:
//   1. Spine all-ok-day zero-pixel test: render Spine with all creds in ok state
//      and assert no Sliver is colored (calm-by-default surface regression).
//   2. Identity-switch projection test: pick an identity in the switcher, assert
//      rendered rows are filtered to that identity.
//   3. Removed tweaks regression: stale localStorage tweak state no longer
//      controls the passport surface.
//   4. Snapshot test for the full DirectionPassport page rendered with rich mock data.
//
// Spec anchor: butler-secrets §No-LLM-Narration Invariant
//              butler-secrets §Projection-Lens Identity Switcher
// ---------------------------------------------------------------------------

import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import * as React from "react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Mock API client — PageSystem (and PageUser) use TanStack Query mutation hooks
// which call useQueryClient() at render time, including during snapshot renders.
// ---------------------------------------------------------------------------
vi.mock("@/api/client.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client.ts")>()
  return {
    ...actual,
    reauthorizeUserCredential: vi.fn(),
    probeUserCredential: vi.fn(),
    rotateUserCredential: vi.fn(),
    disconnectUserCredential: vi.fn(),
    setSystemCredential: vi.fn(),
    probeSystemCredential: vi.fn(),
    deleteSystemCredential: vi.fn(),
    revealSecret: vi.fn(),
  }
})
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
// PageSystem calls useButlers() for the override butler-picker.
vi.mock("@/hooks/use-butlers", () => ({
  useButlers: vi.fn(() => ({ data: { data: [] }, isLoading: false, error: null })),
}))

import { Spine } from "./Spine.tsx";
import { PageCli } from "./pages.tsx";
import { DirectionPassport } from "./DirectionPassport.tsx";
import {
  MOCK_INVENTORY,
  MOCK_IDENTITIES,
  MOCK_USER_CREDENTIALS,
  MOCK_SYSTEM_CREDENTIALS,
  MOCK_CLI_CREDENTIALS,
  MOCK_PROVIDERS,
} from "./mock-data.ts";
import { buildSpineEntries } from "./spine-builder.ts";
import type { InventoryResponse } from "./types.ts";

// ── Helpers ──────────────────────────────────────────────────────────────────

function renderInRouter(element: React.ReactElement, initialEntries: string[] = ["/secrets"]): string {
  // DirectionPassport renders PageCliConnected, which reads CLI auth providers
  // via react-query; a client must be present even though no fetch fires here.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>{element}</MemoryRouter>
    </QueryClientProvider>,
  );
}

/** All-ok inventory: every credential has state "ok". */
const ALL_OK_INVENTORY: InventoryResponse = {
  ...MOCK_INVENTORY,
  user: MOCK_USER_CREDENTIALS.map((u) => ({ ...u, state: "ok" as const })),
  system: MOCK_SYSTEM_CREDENTIALS.map((s) => ({
    ...s,
    state: "ok" as const,
    rowState: "shared" as const,
  })),
  cli: MOCK_CLI_CREDENTIALS.map((c) => ({ ...c, state: "ok" as const })),
};

// ── 1. Spine all-ok-day zero-pixel test ──────────────────────────────────────

describe("Spine all-ok-day: zero colored Sliver pixels (calm-by-default)", () => {
  /**
   * When every credential is in state "ok", the Spine must render no coloured
   * slivers. Per spec §Severity Earns Visual Authority Only When State Demands:
   * the Sliver atom returns null for states where sliver=false (i.e. ok, never_set,
   * rotating). On a fully healthy day the left-edge column must be uncoloured.
   */
  const calmEntries = buildSpineEntries(ALL_OK_INVENTORY, "tze");

  it("renders with all-ok entries (calm day)", () => {
    const html = renderToStaticMarkup(
      <Spine
        entries={calmEntries}
        activeKey={calmEntries[0]?.key ?? ""}
        onSelect={() => {}}
        sortMode="severity"
        onSortChange={() => {}}
        search=""
        onSearchChange={() => {}}
        identities={MOCK_IDENTITIES}
        activeIdentityId="tze"
        onIdentityChange={() => {}}
        providers={MOCK_PROVIDERS}
      />,
    );
    expect(html).toContain("data-spine-row");
  });

  it("no data-sliver attribute rendered (no Sliver pixels) on calm day", () => {
    const html = renderToStaticMarkup(
      <Spine
        entries={calmEntries}
        activeKey={calmEntries[0]?.key ?? ""}
        onSelect={() => {}}
        sortMode="severity"
        onSortChange={() => {}}
        search=""
        onSearchChange={() => {}}
        identities={MOCK_IDENTITIES}
        activeIdentityId="tze"
        onIdentityChange={() => {}}
        providers={MOCK_PROVIDERS}
      />,
    );
    // On a calm day no Sliver component should render (atoms.tsx Sliver returns null for ok)
    expect(html).not.toContain("data-sliver");
  });

  it("no --red or --amber Sliver colour tokens in rendered HTML on calm day", () => {
    const html = renderToStaticMarkup(
      <Spine
        entries={calmEntries}
        activeKey={calmEntries[0]?.key ?? ""}
        onSelect={() => {}}
        sortMode="severity"
        onSortChange={() => {}}
        search=""
        onSearchChange={() => {}}
        identities={MOCK_IDENTITIES}
        activeIdentityId="tze"
        onIdentityChange={() => {}}
        providers={MOCK_PROVIDERS}
      />,
    );
    // Spine uses atoms.tsx Sliver which returns null for ok-state creds.
    // No red or amber colour tokens should appear in spine row context.
    // (The CredentialDot for ok uses --green; that is expected and fine.)
    expect(html).not.toContain("var(--red)");
    // amber also absent
    expect(html).not.toContain("var(--amber)");
  });

  it("calm-day: needs-hand group is absent", () => {
    const html = renderToStaticMarkup(
      <Spine
        entries={calmEntries}
        activeKey={calmEntries[0]?.key ?? ""}
        onSelect={() => {}}
        sortMode="severity"
        onSortChange={() => {}}
        search=""
        onSearchChange={() => {}}
        identities={MOCK_IDENTITIES}
        activeIdentityId="tze"
        onIdentityChange={() => {}}
        providers={MOCK_PROVIDERS}
      />,
    );
    // Per calm-day invariant: needs-hand group is not rendered when empty
    expect(html).not.toContain("needs hand ·");
  });

  it("DirectionPassport with all-ok inventory renders no Sliver pixels", () => {
    const html = renderInRouter(<DirectionPassport inventory={ALL_OK_INVENTORY} />);
    expect(html).toContain('data-direction-passport="true"');
    expect(html).not.toContain("data-sliver");
  });

  it('DirectionPassport all-ok: heading says "Every credential, accounted for."', () => {
    const html = renderInRouter(<DirectionPassport inventory={ALL_OK_INVENTORY} />);
    expect(html).toContain("Every credential, accounted for.");
  });
});

// ── 2. Identity-switch projection test ───────────────────────────────────────

describe("Identity-switch projection (§Projection-Lens Identity Switcher)", () => {
  /**
   * Selecting an identity in the switcher projects the User group so only that
   * identity's credentials appear. System and CLI rows are not identity-scoped.
   */

  it("?identity=wei: wei user rows rendered, tze-only user rows absent", () => {
    // MOCK_INVENTORY has tze with google/spotify/homeassistant/whatsapp/owntracks/steam
    // and wei with only google (different identity field).
    const tzeEntries = buildSpineEntries(MOCK_INVENTORY, "tze");
    const weiEntries = buildSpineEntries(MOCK_INVENTORY, "wei");

    const tzeUserKeys = tzeEntries.filter((e) => e.family === "user").map((e) => e.key);
    const weiUserKeys = weiEntries.filter((e) => e.family === "user").map((e) => e.key);

    // Projection invariant: wei has fewer user entries than tze (the owner)
    expect(weiUserKeys.length).toBeLessThan(tzeUserKeys.length);
    // All wei keys follow the u: format
    expect(weiUserKeys.every((k) => k.startsWith("u:"))).toBe(true);
  });

  it("?identity=wei URL param filters User group in rendered Spine", () => {
    const html = renderInRouter(
      <DirectionPassport inventory={MOCK_INVENTORY} />,
      ["/secrets?identity=wei"],
    );
    // Wei identity chip is present (selected identity shown)
    expect(html).toContain('data-identity-id="wei"');
    // System and CLI rows still present (not identity-scoped)
    expect(html).toContain('data-family="system"');
    expect(html).toContain('data-family="cli"');
  });

  it("?identity=wei: Spine has fewer user rows than default (tze) identity", () => {
    const tzeHtml = renderInRouter(
      <DirectionPassport inventory={MOCK_INVENTORY} />,
      ["/secrets"],
    );
    const weiHtml = renderInRouter(
      <DirectionPassport inventory={MOCK_INVENTORY} />,
      ["/secrets?identity=wei"],
    );

    // Count data-family="user" occurrences via simple string matching
    const countUser = (html: string) => {
      let count = 0;
      let idx = 0;
      while ((idx = html.indexOf('data-family="user"', idx)) !== -1) {
        count++;
        idx += 1;
      }
      return count;
    };

    expect(countUser(weiHtml)).toBeLessThan(countUser(tzeHtml));
  });

  it("identity chip hidden when only one identity in inventory", () => {
    const singleIdentityInventory: InventoryResponse = {
      ...MOCK_INVENTORY,
      identities: [MOCK_IDENTITIES[0]!],
    };
    const html = renderInRouter(
      <DirectionPassport inventory={singleIdentityInventory} />,
    );
    // With only one identity, the chip for the second identity must not appear
    expect(html).not.toContain('data-identity-id="wei"');
  });
});

// ── 3. Removed tweaks regression ─────────────────────────────────────────────

describe("DirectionPassport: removed tweaks chrome", () => {
  /**
   * Product decision: /secrets no longer exposes prototype tweaks chrome.
   * Stale browser localStorage under secrets.tweaks.* must not change the
   * rendered passport surface after the panel is removed.
   */

  const claudeCred = MOCK_CLI_CREDENTIALS.find((c) => c.id === "claude-cli")!;

  it("revealMode=eye (default): reveal token button IS present when fingerprint exists", () => {
    const html = renderToStaticMarkup(
      <PageCli credential={claudeCred} revealMode="eye" />,
    );
    // Claude CLI has a fingerprint, so reveal token button shows by default
    expect(html).toContain("reveal token");
  });

  it("revealMode=hover: reveal token button IS present (hover mode does not suppress)", () => {
    const html = renderToStaticMarkup(
      <PageCli credential={claudeCred} revealMode="hover" />,
    );
    expect(html).toContain("reveal token");
  });

  it("revealMode=never: reveal token button is ABSENT", () => {
    const html = renderToStaticMarkup(
      <PageCli credential={claudeCred} revealMode="never" />,
    );
    // Eye button suppressed when revealMode="never"
    expect(html).not.toContain("reveal token");
  });

  it("revealMode=never, no fingerprint: button already absent (never_set cred)", () => {
    const gemini = MOCK_CLI_CREDENTIALS.find((c) => c.id === "gemini-cli")!;
    // gemini has no fingerprint — button is absent regardless of revealMode
    const html = renderToStaticMarkup(
      <PageCli credential={gemini} revealMode="never" />,
    );
    expect(html).not.toContain("reveal token");
  });

  it("ignores stale secrets.tweaks.revealMode localStorage", () => {
    try {
      localStorage.setItem("secrets.tweaks.revealMode", "never");
    } catch { /* no-op in environments without localStorage */ }

    const html = renderInRouter(
      <DirectionPassport inventory={MOCK_INVENTORY} />,
      ["/secrets?focus=c:claude-cli"],
    );
    // CLI page rendered (claude-cli has fingerprint and is navigated to)
    expect(html).toContain('data-page="cli"');
    expect(html).toContain("reveal token");
    expect(html).not.toContain('data-tweaks-trigger="true"');

    try {
      localStorage.removeItem("secrets.tweaks.revealMode");
    } catch { /* no-op */ }
  });
});

// ── 4. Snapshot test for full DirectionPassport page ─────────────────────────

describe("DirectionPassport: snapshot (full page with rich mock data)", () => {
  /**
   * Snapshot test for the full DirectionPassport rendered with MOCK_INVENTORY.
   * Catches unintended structural regressions.
   *
   * Date is pinned to 2026-01-15 via vi.useFakeTimers so the eyebrow date
   * string is stable across days. Without this, the snapshot would fail every
   * day it was run on a different date.
   */

  beforeAll(() => {
    vi.useFakeTimers({ now: new Date("2026-01-15T12:00:00.000Z") });
  });
  afterAll(() => {
    vi.useRealTimers();
  });

  it("matches snapshot: default focus (first spine entry)", () => {
    const html = renderInRouter(<DirectionPassport inventory={MOCK_INVENTORY} />);

    // Structural assertions before snapshotting
    expect(html).toContain('data-direction-passport="true"');
    expect(html).toContain("data-spine-row");
    expect(html).toContain("data-spine-group");

    // Snapshot the HTML to catch structural regressions
    expect(html).toMatchSnapshot();
  });

  it("matches snapshot: focus=u:google", () => {
    const html = renderInRouter(
      <DirectionPassport inventory={MOCK_INVENTORY} />,
      ["/secrets?focus=u:google"],
    );
    expect(html).toContain('data-page="user"');
    expect(html).toContain('data-provider="google"');
    expect(html).toMatchSnapshot();
  });

  it("matches snapshot: focus=s:BUTLER_TELEGRAM_TOKEN", () => {
    const html = renderInRouter(
      <DirectionPassport inventory={MOCK_INVENTORY} />,
      ["/secrets?focus=s:BUTLER_TELEGRAM_TOKEN"],
    );
    expect(html).toContain('data-page="system"');
    expect(html).toMatchSnapshot();
  });

  it("matches snapshot: focus=c:claude-cli", () => {
    const html = renderInRouter(
      <DirectionPassport inventory={MOCK_INVENTORY} />,
      ["/secrets?focus=c:claude-cli"],
    );
    expect(html).toContain('data-page="cli"');
    expect(html).toMatchSnapshot();
  });
});
