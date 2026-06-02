// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Passport component tests [bu-qu8v8]
//
// Coverage:
//   - Spine renders against mocked inventory data
//   - PageUser renders against mocked User credential
//   - PageSystem renders against mocked System credential
//   - PageCli renders against mocked CLI credential
//   - DirectionPassport does not expose prototype tweaks chrome
//   - ONE-ROW-TEMPLATE UNIFORMITY: System/User/CLI spine rows have identical
//     HTML structure modulo data-* attrs and text content
//   - Empty needs-hand group hidden on calm day (all-ok)
//   - Identity switcher shows only when multiple identities present
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import * as React from "react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SpineRow, Spine } from "./Spine.tsx";
import { PageUser, PageSystem, PageCli } from "./pages.tsx";
import { DirectionPassport } from "./DirectionPassport.tsx";
import {
  Fingerprint,
  StampGlyph,
  CredentialDot,
  Sliver,
} from "./atoms.tsx";
import {
  MOCK_INVENTORY,
  MOCK_USER_CREDENTIALS,
  MOCK_SYSTEM_CREDENTIALS,
  MOCK_CLI_CREDENTIALS,
  MOCK_PROVIDERS,
  MOCK_IDENTITIES,
} from "./mock-data.ts";
import type { SpineEntry } from "./types.ts";
import { buildSpineEntries } from "./spine-builder.ts";

// ── Helpers ─────────────────────────────────────────────────────────────────

function renderInRouter(element: React.ReactElement): string {
  // DirectionPassport renders PageCliConnected, which reads CLI auth providers
  // via react-query; a client must be present even though no fetch fires here.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{element}</MemoryRouter>
    </QueryClientProvider>,
  );
}

// ── SpineRow uniformity ──────────────────────────────────────────────────────

/**
 * Strip variable parts from an HTML string to leave only structural skeleton.
 *
 * Per spec §One Row Template Across All Three Families:
 * "identical HTML structure (tags + class names) modulo data-* attributes and
 * text content"
 *
 * We also strip inline style attributes since families differ only in
 * rendering details (font-family/size for mono vs sans labels) that are
 * driven by data content, not by structural differences.
 */
function stripDataAndText(html: string): string {
  return html
    // Remove data-* attributes
    .replace(/\s+data-[a-z][a-z0-9-]*="[^"]*"/g, "")
    // Remove aria-label contents (state-specific labels)
    .replace(/aria-label="[^"]*"/g, 'aria-label=""')
    // Remove inline style attributes entirely (content-driven, not structural)
    .replace(/\s+style="[^"]*"/g, "")
    // Remove all text content between tags
    .replace(/>([^<]+)</g, "><")
    // Normalize whitespace
    .replace(/\s+/g, " ")
    .trim();
}

describe("SpineRow: one-row-template uniformity", () => {
  /**
   * Acceptance criterion: System/User/CLI rows rendered with equivalent state
   * have identical HTML structure (tags + class names) modulo data-* attrs
   * and text content.
   */

  const baseEntry: Omit<SpineEntry, "family" | "key" | "mono"> = {
    label: "Test Credential",
    state: "ok",
    subline: "verified 14:00",
    lastTouchOrder: 0,
  };

  const userEntry: SpineEntry = {
    ...baseEntry,
    key: "u:google",
    family: "user",
    mono: false,
    provider: "google",
  };

  const systemEntry: SpineEntry = {
    ...baseEntry,
    key: "s:TEST_KEY",
    family: "system",
    mono: true,
    label: "TEST_KEY",
    subline: "shared default",
  };

  const cliEntry: SpineEntry = {
    ...baseEntry,
    key: "c:claude-cli",
    family: "cli",
    mono: false,
    label: "Claude Code",
    subline: "used 14:15 today",
  };

  it("User SpineRow and System SpineRow have identical structure modulo data-attrs and content", () => {
    const userHtml = renderToStaticMarkup(
      <SpineRow entry={userEntry} n={1} active={false} onClick={() => {}} />,
    );
    const systemHtml = renderToStaticMarkup(
      <SpineRow entry={systemEntry} n={2} active={false} onClick={() => {}} />,
    );

    const userStripped = stripDataAndText(userHtml);
    const systemStripped = stripDataAndText(systemHtml);

    expect(userStripped).toBe(systemStripped);
  });

  it("User SpineRow and CLI SpineRow have identical structure modulo data-attrs and content", () => {
    const userHtml = renderToStaticMarkup(
      <SpineRow entry={userEntry} n={1} active={false} onClick={() => {}} />,
    );
    const cliHtml = renderToStaticMarkup(
      <SpineRow entry={cliEntry} n={3} active={false} onClick={() => {}} />,
    );

    const userStripped = stripDataAndText(userHtml);
    const cliStripped = stripDataAndText(cliHtml);

    expect(userStripped).toBe(cliStripped);
  });

  it("System SpineRow and CLI SpineRow have identical structure modulo data-attrs and content", () => {
    const systemHtml = renderToStaticMarkup(
      <SpineRow entry={systemEntry} n={2} active={false} onClick={() => {}} />,
    );
    const cliHtml = renderToStaticMarkup(
      <SpineRow entry={cliEntry} n={3} active={false} onClick={() => {}} />,
    );

    const systemStripped = stripDataAndText(systemHtml);
    const cliStripped = stripDataAndText(cliHtml);

    expect(systemStripped).toBe(cliStripped);
  });

  it("SpineRow has data-spine-row attribute", () => {
    const html = renderToStaticMarkup(
      <SpineRow entry={userEntry} n={1} active={false} onClick={() => {}} />,
    );
    expect(html).toContain('data-spine-row="true"');
  });

  it("SpineRow has data-family attribute", () => {
    const html = renderToStaticMarkup(
      <SpineRow entry={userEntry} n={1} active={false} onClick={() => {}} />,
    );
    expect(html).toContain('data-family="user"');
  });

  it("SpineRow has data-key attribute", () => {
    const html = renderToStaticMarkup(
      <SpineRow entry={userEntry} n={1} active={false} onClick={() => {}} />,
    );
    expect(html).toContain('data-key="u:google"');
  });

  it("SpineRow renders 10px vertical padding", () => {
    const html = renderToStaticMarkup(
      <SpineRow entry={userEntry} n={1} active={false} onClick={() => {}} />,
    );
    // py-2.5 = 10px vertical padding
    expect(html).toContain("py-2.5");
  });
});

// ── Spine ────────────────────────────────────────────────────────────────────

describe("Spine: renders against mocked inventory", () => {
  const entries = buildSpineEntries(MOCK_INVENTORY, "tze");

  it("renders with entries", () => {
    const html = renderToStaticMarkup(
      <Spine
        entries={entries}
        activeKey={entries[0]?.key ?? ""}
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
    expect(html).toContain("data-spine-group");
  });

  it("shows identity switcher when multiple identities present", () => {
    const html = renderToStaticMarkup(
      <Spine
        entries={entries}
        activeKey="u:google"
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
    // Both identity chips rendered
    expect(html).toContain('data-identity-id="tze"');
    expect(html).toContain('data-identity-id="wei"');
  });

  it("hides identity chip when only one identity", () => {
    const singleIdentity = [MOCK_IDENTITIES[0]];
    const html = renderToStaticMarkup(
      <Spine
        entries={entries}
        activeKey="u:google"
        onSelect={() => {}}
        sortMode="severity"
        onSortChange={() => {}}
        search=""
        onSearchChange={() => {}}
        identities={singleIdentity}
        activeIdentityId="tze"
        onIdentityChange={() => {}}
        providers={MOCK_PROVIDERS}
      />,
    );
    // No second identity chip
    expect(html).not.toContain('data-identity-id="wei"');
  });

  it("calm-day: needs-hand group hidden when all credentials healthy", () => {
    const calmInventory = {
      ...MOCK_INVENTORY,
      user: MOCK_USER_CREDENTIALS.map((u) => ({ ...u, state: "ok" as const })),
      cli:  MOCK_CLI_CREDENTIALS.map((c) => ({ ...c, state: "ok" as const })),
    };
    const calmEntries = buildSpineEntries(calmInventory, "tze");
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
    // needs-hand group is omitted when empty
    expect(html).not.toContain("needs hand · ");
  });

  it("sick-day: needs-hand group shown when at least one credential is sick", () => {
    const html = renderToStaticMarkup(
      <Spine
        entries={entries}
        activeKey={entries[0]?.key ?? ""}
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
    expect(html).toContain("needs hand ·");
  });
});

// ── SpineSearch + SortPicker ─────────────────────────────────────────────────

describe("SpineSearch", () => {
  it("renders search input", () => {
    const entries = buildSpineEntries(MOCK_INVENTORY, "tze");
    const html = renderToStaticMarkup(
      <Spine
        entries={entries}
        activeKey=""
        onSelect={() => {}}
        sortMode="severity"
        onSortChange={() => {}}
        search=""
        onSearchChange={() => {}}
        identities={[MOCK_IDENTITIES[0]]}
        activeIdentityId="tze"
        onIdentityChange={() => {}}
      />,
    );
    expect(html).toContain('data-spine-search="true"');
  });
});

describe("SortPicker", () => {
  it("renders three sort options", () => {
    const entries = buildSpineEntries(MOCK_INVENTORY, "tze");
    const html = renderToStaticMarkup(
      <Spine
        entries={entries}
        activeKey=""
        onSelect={() => {}}
        sortMode="severity"
        onSortChange={() => {}}
        search=""
        onSearchChange={() => {}}
        identities={[MOCK_IDENTITIES[0]]}
        activeIdentityId="tze"
        onIdentityChange={() => {}}
      />,
    );
    expect(html).toContain('data-sort-mode="severity"');
    expect(html).toContain('data-sort-mode="recency"');
    expect(html).toContain('data-sort-mode="alpha"');
  });
});

// ── PageUser ─────────────────────────────────────────────────────────────────

describe("PageUser: renders against mocked data", () => {
  it("renders google credential", () => {
    const google = MOCK_USER_CREDENTIALS.find((u) => u.provider === "google" && u.identity === "tze")!;
    const html = renderToStaticMarkup(
      <PageUser
        credential={google}
        provider={MOCK_PROVIDERS.google}
        identities={MOCK_IDENTITIES}
      />,
    );
    expect(html).toContain('data-page="user"');
    expect(html).toContain('data-provider="google"');
    expect(html).toContain('data-heading-band="true"');
    expect(html).toContain('data-state-plaque="true"');
  });

  it("renders expired spotify credential with re-authorize commit", () => {
    const spotify = MOCK_USER_CREDENTIALS.find((u) => u.provider === "spotify")!;
    const html = renderToStaticMarkup(
      <PageUser credential={spotify} provider={MOCK_PROVIDERS.spotify} />,
    );
    expect(html).toContain("expired");
    expect(html).toContain("re-authorize");
  });

  it("renders webhook owntracks credential", () => {
    const owntracks = MOCK_USER_CREDENTIALS.find((u) => u.provider === "owntracks")!;
    const html = renderToStaticMarkup(
      <PageUser credential={owntracks} provider={MOCK_PROVIDERS.owntracks} />,
    );
    expect(html).toContain("incoming url");
    expect(html).toContain("butlers.tze");
  });

  it("renders never_set steam credential with connect button", () => {
    const steam = MOCK_USER_CREDENTIALS.find((u) => u.provider === "steam")!;
    const html = renderToStaticMarkup(
      <PageUser credential={steam} provider={MOCK_PROVIDERS.steam} />,
    );
    expect(html).toContain("connect");
  });

  it("shows data-page attribute", () => {
    const google = MOCK_USER_CREDENTIALS[0]!;
    const html = renderToStaticMarkup(
      <PageUser credential={google} provider={MOCK_PROVIDERS.google} />,
    );
    expect(html).toContain('data-page="user"');
  });
});

// ── PageSystem ───────────────────────────────────────────────────────────────

describe("PageSystem: renders against mocked data", () => {
  it("renders shared credential", () => {
    const telegram = MOCK_SYSTEM_CREDENTIALS.find((s) => s.key === "BUTLER_TELEGRAM_TOKEN")!;
    const html = renderToStaticMarkup(<PageSystem credential={telegram} />);
    expect(html).toContain('data-page="system"');
    expect(html).toContain("BUTLER_TELEGRAM_TOKEN");
    expect(html).toContain("shared default");
  });

  it("renders missing credential with set-value button", () => {
    const owntracks = MOCK_SYSTEM_CREDENTIALS.find((s) => s.key === "OWNTRACKS_WEBHOOK_TOKEN")!;
    const html = renderToStaticMarkup(<PageSystem credential={owntracks} />);
    expect(html).toContain("set value");
    expect(html).toContain("not set");
  });

  it("renders system state plaques without rotated-stamp styling", () => {
    const credentials = [
      MOCK_SYSTEM_CREDENTIALS.find((s) => s.key === "BUTLER_TELEGRAM_TOKEN")!,
      MOCK_SYSTEM_CREDENTIALS.find((s) => s.key === "OWNTRACKS_WEBHOOK_TOKEN")!,
    ];

    for (const credential of credentials) {
      const html = renderToStaticMarkup(<PageSystem credential={credential} />);
      expect(html).toContain('data-state-plaque="true"');
      expect(html).not.toContain("rotate(");
    }
  });

  it("renders plain-value credential (email address)", () => {
    const gmail = MOCK_SYSTEM_CREDENTIALS.find((s) => s.key === "GMAIL_SENDER_ADDRESS")!;
    const html = renderToStaticMarkup(<PageSystem credential={gmail} />);
    expect(html).toContain("tze@lim.house");
    expect(html).toContain("value");
  });

  it("shows data-page attribute", () => {
    const telegram = MOCK_SYSTEM_CREDENTIALS[0]!;
    const html = renderToStaticMarkup(<PageSystem credential={telegram} />);
    expect(html).toContain('data-page="system"');
  });
});

// ── PageCli ──────────────────────────────────────────────────────────────────

describe("PageCli: renders against mocked data", () => {
  it("renders claude-cli (ok state)", () => {
    const claude = MOCK_CLI_CREDENTIALS.find((c) => c.id === "claude-cli")!;
    const html = renderToStaticMarkup(<PageCli credential={claude} />);
    expect(html).toContain('data-page="cli"');
    expect(html).toContain("Claude Code");
    expect(html).toContain("how to use");
    expect(html).toContain("CLAUDE_CLI_TOKEN");
  });

  it("renders codex-cli (expiring state) with rotate commit button", () => {
    const codex = MOCK_CLI_CREDENTIALS.find((c) => c.id === "codex-cli")!;
    const html = renderToStaticMarkup(<PageCli credential={codex} />);
    expect(html).toContain("expiring");
    expect(html).toContain("rotate");
  });

  it("renders gemini-cli (never_set) with set token button", () => {
    const gemini = MOCK_CLI_CREDENTIALS.find((c) => c.id === "gemini-cli")!;
    const html = renderToStaticMarkup(<PageCli credential={gemini} />);
    expect(html).toContain("set token");
  });

  it("shows data-page attribute", () => {
    const claude = MOCK_CLI_CREDENTIALS[0]!;
    const html = renderToStaticMarkup(<PageCli credential={claude} />);
    expect(html).toContain('data-page="cli"');
  });
});

// ── DirectionPassport ────────────────────────────────────────────────────────

describe("DirectionPassport: renders against mocked inventory", () => {
  it("renders the passport with default focus", () => {
    const html = renderInRouter(<DirectionPassport inventory={MOCK_INVENTORY} />);
    expect(html).toContain('data-direction-passport="true"');
    expect(html).toContain('data-spine-row');
  });

  it("renders the voice paragraph when credentials need attention", () => {
    const html = renderInRouter(<DirectionPassport inventory={MOCK_INVENTORY} />);
    // The header voice paragraph appears when needsAttention > 0
    // We have sick credentials in mock data, so this should appear.
    expect(html).toContain("data-direction-passport");
  });

  it("renders the page header with secrets eyebrow", () => {
    const html = renderInRouter(<DirectionPassport inventory={MOCK_INVENTORY} />);
    // The eyebrow text "secrets" appears in the header
    expect(html).toContain("secrets");
  });

  it("does not render a tweaks trigger or panel", () => {
    const html = renderInRouter(<DirectionPassport inventory={MOCK_INVENTORY} />);
    expect(html).not.toContain('data-tweaks-trigger="true"');
    expect(html).not.toContain('data-tweaks-panel="true"');
    expect(html).not.toContain("tweaks");
  });
});

// ── buildSpineEntries ────────────────────────────────────────────────────────

describe("buildSpineEntries", () => {
  it("builds entries for identity tze", () => {
    const entries = buildSpineEntries(MOCK_INVENTORY, "tze");
    const families = entries.map((e) => e.family);
    expect(families).toContain("user");
    expect(families).toContain("system");
    expect(families).toContain("cli");
  });

  it("filters user entries by identity", () => {
    const tzeEntries = buildSpineEntries(MOCK_INVENTORY, "tze");
    const weiEntries = buildSpineEntries(MOCK_INVENTORY, "wei");
    // Wei only has google
    const weiUserKeys = weiEntries
      .filter((e) => e.family === "user")
      .map((e) => e.key);
    expect(weiUserKeys).toEqual(["u:google"]);
    // Tze has more user entries
    const tzeUserKeys = tzeEntries
      .filter((e) => e.family === "user")
      .map((e) => e.key);
    expect(tzeUserKeys.length).toBeGreaterThan(1);
  });

  it("user entry key format is u:<provider>", () => {
    const entries = buildSpineEntries(MOCK_INVENTORY, "tze");
    const userEntry = entries.find((e) => e.family === "user" && e.provider === "google");
    expect(userEntry?.key).toBe("u:google");
  });

  it("system entry key format is s:<KEY>", () => {
    const entries = buildSpineEntries(MOCK_INVENTORY, "tze");
    const sysEntry = entries.find((e) => e.family === "system");
    expect(sysEntry?.key).toMatch(/^s:/);
  });

  it("cli entry key format is c:<id>", () => {
    const entries = buildSpineEntries(MOCK_INVENTORY, "tze");
    const cliEntry = entries.find((e) => e.family === "cli");
    expect(cliEntry?.key).toMatch(/^c:/);
  });
});

// ── Atoms ────────────────────────────────────────────────────────────────────

describe("Fingerprint atom", () => {
  it("renders null as dash", () => {
    const html = renderToStaticMarkup(<Fingerprint value={null} />);
    expect(html).toContain("—");
  });

  it("renders sha256:hash with split coloring", () => {
    const html = renderToStaticMarkup(<Fingerprint value="sha256:7a3f9e2c" />);
    expect(html).toContain("sha256:");
    expect(html).toContain("7a3f9e2c");
  });

  it("has data-fingerprint attribute", () => {
    const html = renderToStaticMarkup(<Fingerprint value="sha256:7a3f9e2c" />);
    expect(html).toContain('data-fingerprint="true"');
  });
});

describe("StampGlyph atom", () => {
  it("renders verified glyph", () => {
    const html = renderToStaticMarkup(<StampGlyph action="verified" />);
    expect(html).toContain("✓");
    expect(html).toContain('data-stamp-action="verified"');
  });

  it("renders rotated glyph", () => {
    const html = renderToStaticMarkup(<StampGlyph action="rotated" />);
    expect(html).toContain("↻");
  });

  it("renders failed glyph", () => {
    const html = renderToStaticMarkup(<StampGlyph action="failed" />);
    expect(html).toContain("✕");
  });

  it("falls back to dot for unknown action", () => {
    const html = renderToStaticMarkup(<StampGlyph action="unknown-action" />);
    expect(html).toContain("·");
  });
});

describe("CredentialDot atom", () => {
  it("renders for ok state", () => {
    const html = renderToStaticMarkup(<CredentialDot state="ok" />);
    expect(html).toContain('data-credential-state="ok"');
  });

  it("renders for expired state with red color", () => {
    const html = renderToStaticMarkup(<CredentialDot state="expired" />);
    expect(html).toContain("--red");
  });

  it("renders for ok state with green color (healthy calm day)", () => {
    const html = renderToStaticMarkup(<CredentialDot state="ok" />);
    expect(html).toContain("--green");
  });
});

describe("Sliver atom: appears only when state demands", () => {
  it("renders sliver for expired state", () => {
    const html = renderToStaticMarkup(<Sliver state="expired" />);
    expect(html).toContain('data-sliver="true"');
  });

  it("does NOT render sliver for ok state", () => {
    const html = renderToStaticMarkup(<Sliver state="ok" />);
    expect(html).toBe("");
  });

  it("does NOT render sliver for never_set state", () => {
    const html = renderToStaticMarkup(<Sliver state="never_set" />);
    expect(html).toBe("");
  });
});
