// ---------------------------------------------------------------------------
// use-secrets-inventory adapter unit tests [bu-nrgk9, bu-ey1hr, bu-1ehn0, bu-ej5dr]
//
// Coverage:
//   - adaptInventoryResponse maps system credential raw.state to rowState
//   - adaptInventoryResponse maps user credential raw.state to state
//   - adaptInventoryResponse maps backend identities[] (name, role) directly
//   - adaptInventoryResponse uses backend providers when present [bu-ej5dr]
//   - adaptInventoryResponse returns empty providers map when providers absent [bu-lbhxu]
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest";

import { adaptInventoryResponse } from "@/hooks/use-secrets-inventory.ts";
import type { SecretsIdentityInfo, SecretsProviderInfo, SecretsSystemRaw, SecretsUserRaw } from "@/api/types.ts";

function makeSystem(overrides: Partial<SecretsSystemRaw> & Pick<SecretsSystemRaw, "key" | "state">): SecretsSystemRaw {
  return {
    category: "core",
    description: null,
    fingerprint: null,
    last_verified: null,
    butler: "shared",
    test: null,
    ...overrides,
  };
}

function makeUser(overrides: Partial<SecretsUserRaw> & Pick<SecretsUserRaw, "entity_id" | "state">): SecretsUserRaw {
  return {
    id: "u1",
    type: "google_oauth_refresh",
    fingerprint: null,
    last_verified: null,
    label: null,
    test: null,
    ...overrides,
  };
}

function makeIdentity(overrides: Pick<SecretsIdentityInfo, "entity_id" | "name" | "role">): SecretsIdentityInfo {
  return { ...overrides };
}

describe("adaptInventoryResponse: system credential rowState", () => {
  it("maps 'shared' state to rowState 'shared'", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [makeSystem({ key: "ANTHROPIC_API_KEY", state: "shared" })],
      user: [],
      identities: [],
    });
    expect(result.system[0].rowState).toBe("shared");
  });

  it("maps 'missing' state to rowState 'missing' (regression guard)", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [makeSystem({ key: "OWNTRACKS_TOKEN", state: "missing" })],
      user: [],
      identities: [],
    });
    expect(result.system[0].rowState).toBe("missing");
  });

  it("maps 'local' state to rowState 'local'", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [makeSystem({ key: "SOME_KEY", state: "local" })],
      user: [],
      identities: [],
    });
    expect(result.system[0].rowState).toBe("local");
  });
});

describe("adaptInventoryResponse: user credential state", () => {
  it("passes user raw.state through as state", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [],
      user: [makeUser({ entity_id: "tze", state: "ok" })],
      identities: [],
    });
    expect(result.user[0].state).toBe("ok");
  });

  it("normalizes backend warning states into passport states", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [],
      user: [
        makeUser({ id: "u-warn", entity_id: "tze", state: "warn" }),
        makeUser({ id: "u-failing", entity_id: "wei", state: "failing" }),
      ],
      identities: [],
    });

    expect(result.user.map((credential) => credential.state)).toEqual(["warn", "failed"]);
  });
});

describe("adaptInventoryResponse: user provider derivation", () => {
  const backendProviders: Record<string, SecretsProviderInfo> = {
    google:        { id: "google",        label: "Google",         glyph: "G", kind: "oauth",   authority: "accounts.google.com",  brief: "Calendar, Gmail, Drive read.",    cadence: "on demand · refreshes hourly" },
    homeassistant: { id: "homeassistant", label: "Home Assistant", glyph: "H", kind: "token",   authority: "home.lim.local",       brief: "Smart-home state, sensors.",       cadence: "poll · 30s" },
    telegram_bot:  { id: "telegram_bot",  label: "Telegram Bot",   glyph: "T", kind: "token",   authority: "api.telegram.org",     brief: "Bot inbound + outbound.",          cadence: "webhook + poll · 30s" },
    github: {
      id: "github",
      label: "GitHub",
      glyph: "#",
      kind: "token",
      authority: "api.github.com",
      brief: "Repo access via Personal Access Token.",
      cadence: "on demand",
    },
  };

  it("maps live backend credential types to provider catalog slugs", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [],
      user: [
        makeUser({ id: "u-ha", entity_id: "tze", state: "warn", type: "home_assistant_token" }),
        makeUser({ id: "u-tg", entity_id: "tze", state: "warn", type: "telegram_user_session" }),
        makeUser({ id: "u-gh", entity_id: "tze", state: "warn", type: "github_token" }),
        makeUser({ id: "u-go", entity_id: "tze", state: "ok", type: "google_oauth_refresh" }),
      ],
      identities: [],
      providers: backendProviders,
    });

    expect(result.user.map((credential) => credential.provider)).toEqual([
      "homeassistant",
      "telegram_bot",
      "github",
      "google",
    ]);
    for (const credential of result.user) {
      expect(result.providers[credential.provider]?.kind).toBeDefined();
    }
  });

  it("adds generic provider metadata for unknown credential families", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [],
      user: [makeUser({ entity_id: "tze", state: "warn", type: "custom_service_token" })],
      identities: [],
      providers: {},
    });

    expect(result.user[0].provider).toBe("custom");
    expect(result.providers.custom).toMatchObject({
      id: "custom",
      label: "Custom",
      kind: "token",
    });
  });

  it("groups multiple raw credential components into one provider row per identity", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [],
      user: [
        makeUser({ id: "tg-hash", entity_id: "tze", state: "warn", type: "telegram_api_hash" }),
        makeUser({ id: "tg-session", entity_id: "tze", state: "warn", type: "telegram_user_session" }),
        makeUser({ id: "google", entity_id: "tze", state: "ok", type: "google_oauth_refresh" }),
      ],
      identities: [],
      providers: {
        google:       { id: "google",       label: "Google",       glyph: "G", kind: "oauth",  authority: "accounts.google.com", brief: "Calendar, Gmail, Drive read.", cadence: "on demand · refreshes hourly" },
        telegram_bot: { id: "telegram_bot", label: "Telegram Bot", glyph: "T", kind: "token",  authority: "api.telegram.org",    brief: "Bot inbound + outbound.",      cadence: "webhook + poll · 30s" },
      },
    });

    expect(result.user.map((credential) => credential.provider)).toEqual(["telegram_bot", "google"]);
    expect(result.user.filter((credential) => credential.provider === "telegram_bot")).toHaveLength(1);
    expect(result.user[0].state).toBe("warn");
  });
});

describe("adaptInventoryResponse: system credential grouping", () => {
  it("groups duplicate cli-auth system keys into one CLI row", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [
        makeSystem({
          key: "cli-auth/codex",
          category: "cli-auth",
          state: "warn",
          butler: "lifestyle",
          description: "CLI auth token for Codex (OpenAI)",
        }),
        makeSystem({
          key: "cli-auth/codex",
          category: "cli-auth",
          state: "warn",
          butler: "switchboard",
          description: "CLI auth token for Codex (OpenAI)",
        }),
      ],
      user: [],
      identities: [],
    });

    expect(result.system.find((credential) => credential.key === "cli-auth/codex")).toBeUndefined();
    expect(result.cli).toHaveLength(1);
    expect(result.cli[0]).toMatchObject({
      id: "cli-auth/codex",
      label: "CLI auth token for Codex (OpenAI)",
      state: "warn",
    });
  });

  it("promotes cli-auth system rows into CLI runtime credentials", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [
        makeSystem({
          key: "cli-auth/codex",
          category: "cli-auth",
          state: "warn",
          butler: "lifestyle",
          description: "CLI auth token for Codex (OpenAI)",
          fingerprint: "abc12345",
        }),
      ],
      user: [],
      identities: [],
    });

    expect(result.system.find((credential) => credential.key === "cli-auth/codex")).toBeUndefined();
    expect(result.cli).toHaveLength(1);
    expect(result.cli[0]).toMatchObject({
      id: "cli-auth/codex",
      label: "CLI auth token for Codex (OpenAI)",
      state: "warn",
      fingerprint: "abc12345",
    });
  });
});

describe("adaptInventoryResponse: provider-managed system credentials are hidden", () => {
  it("drops owntracks and spotify rows from the system family", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [
        makeSystem({ key: "owntracks_webhook_token", category: "owntracks", state: "shared" }),
        makeSystem({ key: "SPOTIFY_ACCESS_TOKEN", category: "spotify", state: "shared" }),
        makeSystem({ key: "SPOTIFY_CLIENT_ID", category: "spotify", state: "shared" }),
        makeSystem({ key: "GOOGLE_OAUTH_CLIENT_ID", category: "google", state: "shared" }),
      ],
      user: [],
      identities: [],
    });

    const systemKeys = result.system.map((credential) => credential.key);
    expect(systemKeys).not.toContain("owntracks_webhook_token");
    expect(systemKeys).not.toContain("SPOTIFY_ACCESS_TOKEN");
    expect(systemKeys).not.toContain("SPOTIFY_CLIENT_ID");
    // Genuine hand-set system config is untouched.
    expect(systemKeys).toContain("GOOGLE_OAUTH_CLIENT_ID");
  });

  it("does not promote provider-managed rows into any other family", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [
        makeSystem({ key: "owntracks_webhook_token", category: "owntracks", state: "shared" }),
      ],
      user: [],
      identities: [],
    });

    expect(result.system).toHaveLength(0);
    expect(result.cli).toHaveLength(0);
    expect(result.user).toHaveLength(0);
  });
});

describe("adaptInventoryResponse: identity mapping from backend", () => {
  it("maps backend identities[] to frontend Identity[] with real names and roles", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [],
      user: [
        makeUser({ id: "u1", entity_id: "tze-uuid", state: "ok" }),
        makeUser({ id: "u2", entity_id: "wei-uuid", state: "ok" }),
      ],
      identities: [
        makeIdentity({ entity_id: "tze-uuid", name: "Tze", role: "owner" }),
        makeIdentity({ entity_id: "wei-uuid", name: "Wei", role: "member" }),
      ],
    });
    expect(result.identities).toHaveLength(2);
    expect(result.identities[0].id).toBe("tze-uuid");
    expect(result.identities[0].label).toBe("Tze");
    expect(result.identities[0].role).toBe("owner");
    expect(result.identities[1].id).toBe("wei-uuid");
    expect(result.identities[1].label).toBe("Wei");
    expect(result.identities[1].role).toBe("member");
  });

  it("returns empty identities when backend sends empty array", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [],
      user: [],
      identities: [],
    });
    expect(result.identities).toHaveLength(0);
  });
});

describe("adaptInventoryResponse: provider catalog from backend [bu-ej5dr, bu-lbhxu]", () => {
  const backendProviders: Record<string, SecretsProviderInfo> = {
    google: {
      id: "google",
      label: "Google (from backend)",
      glyph: "G",
      kind: "oauth",
      authority: "accounts.google.com",
      brief: "Backend-sourced brief.",
      cadence: "on demand",
    },
  };

  it("uses backend providers when present in the response", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [],
      user: [],
      identities: [],
      providers: backendProviders,
    });
    expect(result.providers).toEqual(backendProviders);
    expect(result.providers["google"].label).toBe("Google (from backend)");
  });

  it("returns empty providers map when providers field is absent", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [],
      user: [],
      identities: [],
      // providers field intentionally omitted
    });
    expect(result.providers).toEqual({});
  });

  it("returns empty providers map when providers is undefined", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [],
      user: [],
      identities: [],
      providers: undefined,
    });
    expect(result.providers).toEqual({});
  });
});

// ---------------------------------------------------------------------------
// shared-public pool target routing [bu-91noc]
// ---------------------------------------------------------------------------

describe("adaptInventoryResponse: shared-public target routing", () => {
  it("sets target='shared-public' for rows with butler='shared-public'", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [makeSystem({ key: "TELEGRAM_TOKEN", state: "ok", butler: "shared-public" })],
      user: [],
      identities: [],
    });
    expect(result.system[0].target).toBe("shared-public");
  });

  it("shared-public rows adapt to rowState='shared' (not 'local')", () => {
    // Regression guard: rowStateFromSystemRaw must treat butler="shared-public"
    // as a shared row, not a local override.  Misclassification would display
    // these rows as "local override" in the passport UI.
    const result = adaptInventoryResponse({
      cli: [],
      system: [makeSystem({ key: "TELEGRAM_TOKEN", state: "ok", butler: "shared-public" })],
      user: [],
      identities: [],
    });
    expect(result.system[0].rowState).toBe("shared");
  });

  it("sets target='shared' for rows with butler='shared' (legacy switchboard rows)", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [makeSystem({ key: "SOME_SWITCH_KEY", state: "ok", butler: "shared" })],
      user: [],
      identities: [],
    });
    expect(result.system[0].target).toBe("shared");
  });

  it("shared-public rows have readOnly=false (editable)", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [
        makeSystem({ key: "TELEGRAM_TOKEN", state: "ok", butler: "shared-public", read_only: false }),
      ],
      user: [],
      identities: [],
    });
    expect(result.system[0].readOnly).toBe(false);
  });

  it("preserves target='shared-public' when groupSystemCredentials merges credentials", () => {
    // Two rows with the same key — one shared-public, one ok state from same pool
    const result = adaptInventoryResponse({
      cli: [],
      system: [
        makeSystem({ key: "SHARED_KEY", state: "ok", butler: "shared-public" }),
        makeSystem({ key: "SHARED_KEY", state: "warn", butler: "shared-public" }),
      ],
      user: [],
      identities: [],
    });
    // After grouping there should be one merged credential
    const matching = result.system.filter((c) => c.key === "SHARED_KEY");
    expect(matching).toHaveLength(1);
    expect(matching[0].target).toBe("shared-public");
  });

  it("shared-public wins over shared when both contribute to the same key", () => {
    // Edge case: same key in both shared and shared-public pools
    const result = adaptInventoryResponse({
      cli: [],
      system: [
        makeSystem({ key: "DUP_KEY", state: "ok", butler: "shared" }),
        makeSystem({ key: "DUP_KEY", state: "ok", butler: "shared-public" }),
      ],
      user: [],
      identities: [],
    });
    const matching = result.system.filter((c) => c.key === "DUP_KEY");
    expect(matching).toHaveLength(1);
    expect(matching[0].target).toBe("shared-public");
  });
});
