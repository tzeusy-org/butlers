// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// SecurityPostureTile tests -- bu-dl98i.1.4, bu-dl98i.6.3, bu-zxxyo
//
// Coverage:
//   - Loading state: skeleton rendered, no content
//   - Error state: error message rendered, no content
//   - Happy path (api key enabled, export secret set): both badges "secure"
//   - Happy path (api key disabled, export secret missing): both badges "insecure"
//   - Mixed state: api key disabled, export secret set
//   - Mixed state: api key enabled, export secret missing
//   - Infra defaults: insecure_infra_defaults=true shows "Insecure defaults active"
//   - Infra defaults: insecure_infra_defaults=false shows "Hardened"
//   - Infra defaults: absent security field defaults to insecure (honest default)
//   - DB role enforcement: database_authority=unavailable shows "Unavailable"
//   - DB role enforcement: database_authority=admitted shows "Admitted"
//   - DB role enforcement: absent field defaults to insecure (honest default)
//   - No secret material rendered (invariant)
// ---------------------------------------------------------------------------

import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import type { HealthResponse } from "@/api/types"
import { SecurityPostureTile } from "./SecurityPostureTile"

// ---------------------------------------------------------------------------
// Mock useHealthPosture
// ---------------------------------------------------------------------------

type HookResult = Partial<{
  isPending: boolean
  isError: boolean
  data: HealthResponse
}>

let mockResult: HookResult = { isPending: false }

vi.mock("@/hooks/use-system", () => ({
  useHealthPosture: () => ({
    ...mockResult,
    status: mockResult.isPending ? "pending" : mockResult.isError ? "error" : "success",
    fetchStatus: "idle",
    isPlaceholderData: false,
    refetch: vi.fn(),
  }),
}))

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeHealthResponse(
  authOverrides: Partial<HealthResponse["auth"]> = {},
  securityOverrides: Partial<NonNullable<HealthResponse["security"]>> = {},
): HealthResponse {
  return {
    status: "ok",
    auth: {
      api_key_auth_enabled: true,
      owner_auth_enabled: true,
      owner_auth_available: true,
      export_secret_insecure_default: false,
      ...authOverrides,
    },
    security: {
      insecure_infra_defaults: false,
      database_authority: "admitted",
      ...securityOverrides,
    },
  }
}

function render(): string {
  return renderToStaticMarkup(<SecurityPostureTile />)
}

// ---------------------------------------------------------------------------
// 1. Loading state
// ---------------------------------------------------------------------------

describe("SecurityPostureTile -- loading state", () => {
  it("renders skeleton when isPending=true", () => {
    mockResult = { isPending: true }
    expect(render()).toContain("security-posture-tile-skeleton")
  })

  it("does not render content while loading", () => {
    mockResult = { isPending: true }
    expect(render()).not.toContain("security-posture-tile-content")
  })
})

// ---------------------------------------------------------------------------
// 2. Error state
// ---------------------------------------------------------------------------

describe("SecurityPostureTile -- error state", () => {
  it("renders error message when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).toContain("security-posture-tile-error")
  })

  it("renders error text when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).toContain("Could not load security posture")
  })

  it("does not render content when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).not.toContain("security-posture-tile-content")
  })
})

// ---------------------------------------------------------------------------
// 3. Happy path -- both secure
// ---------------------------------------------------------------------------

describe("SecurityPostureTile -- both fields secure", () => {
  it("renders content container", () => {
    mockResult = {
      isPending: false,
      data: makeHealthResponse({ api_key_auth_enabled: true, export_secret_insecure_default: false }),
    }
    expect(render()).toContain("security-posture-tile-content")
  })

  it("shows 'Enabled' for available owner authentication", () => {
    mockResult = {
      isPending: false,
      data: makeHealthResponse({ api_key_auth_enabled: true, export_secret_insecure_default: false }),
    }
    expect(render()).toContain("Enabled")
  })

  it("shows 'Configured' for export secret when not insecure", () => {
    mockResult = {
      isPending: false,
      data: makeHealthResponse({ api_key_auth_enabled: true, export_secret_insecure_default: false }),
    }
    expect(render()).toContain("Configured")
  })
})

// ---------------------------------------------------------------------------
// 4. Both fields insecure
// ---------------------------------------------------------------------------

describe("SecurityPostureTile -- both fields insecure", () => {
  it("shows 'Unavailable' when owner authentication cannot reach authoritative state", () => {
    mockResult = {
      isPending: false,
      data: makeHealthResponse({ owner_auth_available: false, api_key_auth_enabled: false, export_secret_insecure_default: true }),
    }
    const html = render()
    expect(html).toContain("Unavailable")
  })

  it("shows 'Insecure default' when export secret is missing", () => {
    mockResult = {
      isPending: false,
      data: makeHealthResponse({ api_key_auth_enabled: false, export_secret_insecure_default: true }),
    }
    expect(render()).toContain("Insecure default")
  })
})

// ---------------------------------------------------------------------------
// 5. Mixed states
// ---------------------------------------------------------------------------

describe("SecurityPostureTile -- mixed states", () => {
  it("shows 'Enabled' and 'Insecure default' when key set but export missing", () => {
    mockResult = {
      isPending: false,
      data: makeHealthResponse({ api_key_auth_enabled: true, export_secret_insecure_default: true }),
    }
    const html = render()
    expect(html).toContain("Enabled")
    expect(html).toContain("Insecure default")
  })

  it("shows passkey protection as enabled when the optional API key is absent", () => {
    mockResult = {
      isPending: false,
      data: makeHealthResponse({ api_key_auth_enabled: false, export_secret_insecure_default: false }),
    }
    const html = render()
    expect(html).toContain("Owner authentication")
    expect(html).toContain("Enabled")
    expect(html).not.toContain("network-only")
    expect(html).toContain("Configured")
  })
})

// ---------------------------------------------------------------------------
// 6. Infra defaults indicator (bu-dl98i.6.3)
// ---------------------------------------------------------------------------

describe("SecurityPostureTile -- infra defaults indicator", () => {
  it("shows 'Insecure defaults active' when insecure_infra_defaults=true", () => {
    mockResult = {
      isPending: false,
      data: makeHealthResponse({}, { insecure_infra_defaults: true }),
    }
    expect(render()).toContain("Insecure defaults active")
  })

  it("shows 'Hardened' when insecure_infra_defaults=false", () => {
    mockResult = {
      isPending: false,
      data: makeHealthResponse({}, { insecure_infra_defaults: false }),
    }
    expect(render()).toContain("Hardened")
  })

  it("defaults to insecure when security field is absent (honest default)", () => {
    mockResult = {
      isPending: false,
      data: { status: "ok", auth: { api_key_auth_enabled: true, export_secret_insecure_default: false } },
    }
    expect(render()).toContain("Insecure defaults active")
  })
})

// ---------------------------------------------------------------------------
// 7. DB role enforcement indicator (bu-zxxyo)
// ---------------------------------------------------------------------------

describe("SecurityPostureTile -- DB role enforcement indicator", () => {
  it("shows 'Unavailable' when database_authority=unavailable", () => {
    mockResult = {
      isPending: false,
      data: makeHealthResponse({}, { database_authority: "unavailable" }),
    }
    expect(render()).toContain("Unavailable")
  })

  it("shows 'Admitted' when database_authority=admitted", () => {
    mockResult = {
      isPending: false,
      data: makeHealthResponse({}, { database_authority: "admitted" }),
    }
    expect(render()).toContain("Admitted")
  })

  it("defaults to insecure when security field is absent (honest default)", () => {
    mockResult = {
      isPending: false,
      data: { status: "ok", auth: { api_key_auth_enabled: true, export_secret_insecure_default: false } },
    }
    // Missing diagnostics cannot render an admitted state.
    expect(render()).toContain("Unknown")
  })

  it("renders the role enforcement row with correct testId", () => {
    mockResult = {
      isPending: false,
      data: makeHealthResponse({}, { database_authority: "admitted" }),
    }
    expect(render()).toContain("posture-role-enforcement")
  })

  it("shows 'Admitted' badge when role enforcement is active", () => {
    mockResult = {
      isPending: false,
      data: makeHealthResponse({}, { database_authority: "admitted" }),
    }
    const html = render()
    expect(html).toContain("posture-role-enforcement")
    expect(html).toContain("Admitted")
  })
})

// ---------------------------------------------------------------------------
// 9. No secret material invariant
// ---------------------------------------------------------------------------

describe("SecurityPostureTile -- no secret material", () => {
  it("never renders secret values in the tile output", () => {
    const CANARY_SECRET = "CANARY_SECRET_VALUE_XYZ_DO_NOT_SHOW"
    // Even if somehow a canary leaked into the health response as status text,
    // the tile should not propagate it — but since our type has only booleans
    // in auth, the canary must not appear.
    mockResult = {
      isPending: false,
      data: {
        status: "ok",
        // auth and security fields are booleans; no path for the canary to sneak in
        auth: { api_key_auth_enabled: true, export_secret_insecure_default: false,
          ...{ api_key: CANARY_SECRET } },
        security: { insecure_infra_defaults: false, database_authority: "admitted" },
      },
    }
    expect(JSON.stringify(mockResult.data)).toContain(CANARY_SECRET)
    const html = render()
    expect(html).toContain("Unavailable")
    expect(html).not.toContain(CANARY_SECRET)
  })
})
