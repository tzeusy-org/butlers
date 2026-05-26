// ---------------------------------------------------------------------------
// use-secrets-inventory adapter unit tests [bu-nrgk9, bu-ey1hr]
//
// Coverage:
//   - adaptInventoryResponse maps system credential raw.state to rowState
//   - adaptInventoryResponse maps user credential raw.state to state
//   - adaptInventoryResponse derives identities from user entity_ids
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest";

import { adaptInventoryResponse } from "@/hooks/use-secrets-inventory.ts";
import type { SecretsSystemRaw, SecretsUserRaw, SecretsCliRaw } from "@/api/types.ts";

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

function makeCli(overrides: Partial<SecretsCliRaw> & Pick<SecretsCliRaw, "key" | "state">): SecretsCliRaw {
  return {
    category: "runtime",
    description: null,
    fingerprint: null,
    last_verified: null,
    test: null,
    ...overrides,
  };
}

describe("adaptInventoryResponse: system credential rowState", () => {
  it("maps 'shared' state to rowState 'shared'", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [makeSystem({ key: "ANTHROPIC_API_KEY", state: "shared" })],
      user: [],
    });
    expect(result.system[0].rowState).toBe("shared");
  });

  it("maps 'missing' state to rowState 'missing' (regression guard)", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [makeSystem({ key: "OWNTRACKS_TOKEN", state: "missing" })],
      user: [],
    });
    expect(result.system[0].rowState).toBe("missing");
  });

  it("maps 'local' state to rowState 'local'", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [makeSystem({ key: "SOME_KEY", state: "local" })],
      user: [],
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
    });
    expect(result.user[0].state).toBe("ok");
  });
});

describe("adaptInventoryResponse: identity derivation", () => {
  it("derives one identity per unique entity_id", () => {
    const result = adaptInventoryResponse({
      cli: [],
      system: [],
      user: [
        makeUser({ id: "u1", entity_id: "tze", state: "ok" }),
        makeUser({ id: "u2", entity_id: "tze", state: "ok" }),
        makeUser({ id: "u3", entity_id: "wei", state: "ok" }),
      ],
    });
    expect(result.identities).toHaveLength(2);
    expect(result.identities[0].id).toBe("tze");
    expect(result.identities[0].role).toBe("owner");
    expect(result.identities[1].id).toBe("wei");
    expect(result.identities[1].role).toBe("member");
  });
});
