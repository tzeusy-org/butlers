// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// PageSystem — Google OAuth app editor + shared-pool read-only behavior.
//
// The old /settings/owner page folded into /secrets: the Google app
// credentials (GOOGLE_OAUTH_CLIENT_ID/_SECRET) render an inline editor wired to
// the oauth PUT endpoint, while other shared-credential-pool rows (read_only)
// are surfaced read-only because the generic mutate path would target the
// wrong schema.
// ---------------------------------------------------------------------------

import { describe, expect, it, vi, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

// GoogleAppCredentials fetches status + saves via the api barrel.
vi.mock("@/api/index.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/index.ts")>()
  return {
    ...actual,
    getGoogleCredentialStatus: vi.fn(() =>
      Promise.resolve({
        client_id_configured: true,
        client_secret_configured: true,
        refresh_token_present: true,
        scope: "https://www.googleapis.com/auth/calendar.readonly",
        oauth_health: "connected",
        oauth_health_remediation: null,
        oauth_health_detail: null,
      }),
    ),
    upsertGoogleCredentials: vi.fn(() =>
      Promise.resolve({ success: true, message: "saved" }),
    ),
    getGoogleOAuthStartUrl: vi.fn(() => "/api/oauth/google/start?page_of_origin=secrets"),
  }
})

// PageSystem pulls the butler list for the override picker.
vi.mock("@/hooks/use-butlers", () => ({
  useButlers: vi.fn(() => ({ data: { data: [] }, isLoading: false, error: null })),
}))

import { PageSystem } from "./pages.tsx"
import type { SystemCredential } from "./types.ts"

const GOOGLE_CLIENT_ID: SystemCredential = {
  key: "GOOGLE_OAUTH_CLIENT_ID",
  category: "google",
  state: "ok",
  rowState: "shared",
  fingerprint: "ab12cd34",
  description: "Google OAuth client ID",
  source: "shared",
  target: "shared",
  lastVerified: "14:00 today",
  usedBy: [],
  breaks: [],
  test: null,
  audit: [],
  readOnly: true,
}

const SHARED_READONLY: SystemCredential = {
  ...GOOGLE_CLIENT_ID,
  key: "BUTLER_TELEGRAM_TOKEN",
  category: "telegram",
  description: "Telegram bot token",
}

function renderCred(credential: SystemCredential) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <PageSystem credential={credential} />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
  cleanup()
})

describe("PageSystem: Google OAuth app keys", () => {
  it("renders the paired client id/secret editor", () => {
    renderCred(GOOGLE_CLIENT_ID)
    expect(screen.getByText("client id")).toBeTruthy()
    expect(screen.getByText("client secret")).toBeTruthy()
    expect(screen.getByRole("button", { name: "save app credentials" })).toBeTruthy()
  })

  it("offers a (re-)authorize action", () => {
    renderCred(GOOGLE_CLIENT_ID)
    // connect/re-authorize depending on status; either label is the auth action.
    const auth =
      screen.queryByRole("button", { name: "re-authorize google" }) ??
      screen.queryByRole("button", { name: "connect google" })
    expect(auth).toBeTruthy()
  })

  it("suppresses the generic mutate controls (rotate/test/delete)", () => {
    renderCred(GOOGLE_CLIENT_ID)
    expect(screen.queryByRole("button", { name: "rotate" })).toBeNull()
    expect(screen.queryByRole("button", { name: "test" })).toBeNull()
    expect(screen.queryByRole("button", { name: "delete" })).toBeNull()
  })
})

describe("PageSystem: read-only shared-pool credential", () => {
  it("shows the read-only note and no mutate controls", () => {
    renderCred(SHARED_READONLY)
    expect(screen.getByText(/Managed in the shared credential store/i)).toBeTruthy()
    expect(screen.queryByRole("button", { name: "rotate" })).toBeNull()
    expect(screen.queryByRole("button", { name: "set value" })).toBeNull()
    expect(screen.queryByRole("button", { name: "delete" })).toBeNull()
    // The Google-only editor must not appear for a non-Google shared key.
    expect(screen.queryByRole("button", { name: "save app credentials" })).toBeNull()
  })
})
