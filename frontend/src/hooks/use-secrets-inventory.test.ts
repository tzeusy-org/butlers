// ---------------------------------------------------------------------------
// use-secrets-inventory adapter unit tests [bu-nrgk9, bu-ey1hr, bu-1ehn0]
//
// Coverage:
//   - adaptInventoryResponse maps system credential raw.state to rowState
//   - adaptInventoryResponse maps user credential raw.state to state
//   - adaptInventoryResponse maps backend identities[] (name, role) directly
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest";

import { adaptInventoryResponse } from "@/hooks/use-secrets-inventory.ts";
import type { SecretsIdentityInfo, SecretsSystemRaw, SecretsUserRaw } from "@/api/types.ts";

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
