// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// PageUser — reauthorize button interactive tests [bu-f1loa]
//
// Coverage:
//   - Clicking "re-authorize" on an expired credential calls
//     reauthorizeUserCredential(provider, identity) and follows redirect_url
//   - Button is disabled and shows "redirecting…" while the request is pending
//   - Error message is shown and button re-enables when the request fails
// ---------------------------------------------------------------------------

import { describe, expect, it, vi, afterEach } from "vitest"
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

// ---------------------------------------------------------------------------
// Mock the API client — must appear before component import.
// Use importOriginal to preserve module shape; PageUser now also uses
// useProbeUserSecret/useRotateUserSecret/useDisconnectUserSecret which import
// from "@/api/client.ts" at module init time.
// ---------------------------------------------------------------------------

vi.mock("@/api/client.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client.ts")>()
  return {
    ...actual,
    reauthorizeUserCredential: vi.fn(),
    probeUserCredential: vi.fn(),
    rotateUserCredential: vi.fn(),
    disconnectUserCredential: vi.fn(),
  }
})

// The Spotify card authorizes through the connector PKCE start endpoint rather
// than the generalized dance, so that call is mocked too (it is reached via
// use-spotify.ts, which imports from the api barrel, not client.ts).
vi.mock("@/api/index.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/index.ts")>()
  return { ...actual, startSpotifyOAuth: vi.fn() }
})

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { reauthorizeUserCredential, ApiError } from "@/api/client.ts"
import { startSpotifyOAuth } from "@/api/index.ts"
import type { SpotifyOAuthStartResponse } from "@/api/types.ts"
const mockReauth = vi.mocked(reauthorizeUserCredential)
const mockSpotifyStart = vi.mocked(startSpotifyOAuth)

// ---------------------------------------------------------------------------
// Component + mock data — imported after mocks are established
// ---------------------------------------------------------------------------

import { PageUser } from "./pages.tsx"
import { MOCK_USER_CREDENTIALS, MOCK_PROVIDERS } from "./mock-data.ts"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// The generalized dance is exercised through Google. Spotify used to stand in
// here, but its connect control is now the connector-PKCE drawer rather than
// /oauth/<provider>/start (see DRAWER_CONNECT_PROVIDERS in pages.tsx), so it no
// longer renders a re-authorize pill at all — see the guard test at the bottom.
const GOOGLE = MOCK_USER_CREDENTIALS.find((u) => u.provider === "google")!
const EXPIRED_GOOGLE = { ...GOOGLE, state: "expired" as const }
const GOOGLE_PROVIDER = MOCK_PROVIDERS.google

const SPOTIFY = MOCK_USER_CREDENTIALS.find((u) => u.provider === "spotify")!
const SPOTIFY_PROVIDER = MOCK_PROVIDERS.spotify

function renderPageUser() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <PageUser credential={EXPIRED_GOOGLE} provider={GOOGLE_PROVIDER} />
    </QueryClientProvider>,
  )
}

function renderSpotifyPageUser() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <PageUser credential={SPOTIFY} provider={SPOTIFY_PROVIDER} />
    </QueryClientProvider>,
  )
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

afterEach(() => {
  vi.clearAllMocks()
  cleanup()
})

describe("PageUser: re-authorize button (expired credential)", () => {
  it("calls reauthorizeUserCredential with correct provider and identity on click", async () => {
    // Arrange: successful reauthorize that never resolves (stays pending)
    mockReauth.mockReturnValue(new Promise(() => {}))
    renderPageUser()

    // Act — use getAllByText to be resilient if React renders twice in jsdom
    const [btn] = screen.getAllByText("re-authorize")
    fireEvent.click(btn)

    // Assert: API called with the spotify credential's provider + identity
    expect(mockReauth).toHaveBeenCalledOnce()
    expect(mockReauth).toHaveBeenCalledWith("google", "tze")
  })

  it("shows 'redirecting…' and disables the button while pending", async () => {
    // Arrange: promise that never resolves so we stay in pending state
    mockReauth.mockReturnValue(new Promise(() => {}))
    renderPageUser()

    // Use getAllByText to be resilient if React StrictMode renders twice in jsdom
    const [btn] = screen.getAllByText("re-authorize")
    fireEvent.click(btn)

    // Button label should flip to "redirecting…"
    await waitFor(() => {
      expect(screen.getAllByText("redirecting…").length).toBeGreaterThan(0)
    })

    // All such buttons should be disabled
    const disabledBtns = screen.getAllByText("redirecting…").map((el) => el.closest("button"))
    expect(disabledBtns.every((b) => b?.disabled)).toBe(true)
  })

  it("follows redirect_url by setting window.location.href on success", async () => {
    const redirectUrl = "https://accounts.spotify.com/authorize?client_id=test"

    // Patch window.location.href (jsdom won't actually navigate)
    const locationDescriptor = Object.getOwnPropertyDescriptor(window, "location")
    const hrefSetter = vi.fn()
    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        ...window.location,
        set href(v: string) {
          hrefSetter(v)
        },
      },
    })

    mockReauth.mockResolvedValue({ data: { redirect_url: redirectUrl }, meta: {} })
    renderPageUser()

    fireEvent.click(screen.getByText("re-authorize"))

    await waitFor(() => {
      expect(hrefSetter).toHaveBeenCalledWith(redirectUrl)
    })

    // Restore
    if (locationDescriptor) {
      Object.defineProperty(window, "location", locationDescriptor)
    }
  })

  it("resolves an API-relative redirect_url against the API base before navigating", async () => {
    // Regression: the backend used to hand back a site-rooted "/api/oauth/…"
    // path that was navigated to verbatim. Under the deployment path mounts
    // (/butlers, /butlers-dev) the API lives at /butlers-api/api and
    // /butlers-dev-api/api, so that URL was a dead link. The backend now returns
    // the path below the API root and the client prepends its own base.
    const locationDescriptor = Object.getOwnPropertyDescriptor(window, "location")
    const hrefSetter = vi.fn()
    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        ...window.location,
        set href(v: string) {
          hrefSetter(v)
        },
      },
    })

    mockReauth.mockResolvedValue({
      data: { redirect_url: "/oauth/google/start?page_of_origin=secrets" },
      meta: {},
    })
    renderPageUser()

    fireEvent.click(screen.getByText("re-authorize"))

    await waitFor(() => {
      const apiBase = import.meta.env.VITE_API_URL ?? "/api"
      expect(hrefSetter).toHaveBeenCalledWith(
        `${apiBase}/oauth/google/start?page_of_origin=secrets`,
      )
    })

    if (locationDescriptor) {
      Object.defineProperty(window, "location", locationDescriptor)
    }
  })

  it("shows error message and re-enables button when request fails", async () => {
    mockReauth.mockRejectedValue(new Error("network timeout"))
    renderPageUser()

    const [btn] = screen.getAllByText("re-authorize")
    fireEvent.click(btn)

    // Error message should appear
    await waitFor(() => {
      expect(screen.getByText("network timeout")).toBeTruthy()
    })

    // Buttons should be re-enabled and show original label
    await waitFor(() => {
      expect(screen.getAllByText("re-authorize").length).toBeGreaterThan(0)
    })
    const [reenabled] = screen.getAllByText("re-authorize").map((el) => el.closest("button")!) as HTMLButtonElement[]
    expect(reenabled.disabled).toBe(false)
  })

  it("shows error and re-enables button when API returns no redirect_url", async () => {
    // Arrange: server returns 200 but with no redirect_url in the payload
    mockReauth.mockResolvedValue({ data: {} as never, meta: {} })
    renderPageUser()

    const [btn] = screen.getAllByText("re-authorize")
    fireEvent.click(btn)

    await waitFor(() => {
      expect(screen.getByText("No redirect URL returned from the server.")).toBeTruthy()
    })

    // Button should be re-enabled
    await waitFor(() => {
      expect(screen.getAllByText("re-authorize").length).toBeGreaterThan(0)
    })
    const [reenabled] = screen
      .getAllByText("re-authorize")
      .map((el) => el.closest("button")!) as HTMLButtonElement[]
    expect(reenabled.disabled).toBe(false)
  })

  it("surfaces an honest 'not yet available' notice (not a red error) on HTTP 501 [bu-atcfw]", async () => {
    // Backend returns 501 oauth_provider_not_configured for a catalog-oauth
    // provider with no wired OAuth integration (e.g. whatsapp). The connect
    // handler should show the honest message and NOT navigate the browser.
    mockReauth.mockRejectedValue(
      new ApiError(
        "oauth_provider_not_configured",
        "WhatsApp OAuth connect is not yet available.",
        501,
      ),
    )
    renderPageUser()

    const [btn] = screen.getAllByText("re-authorize")
    fireEvent.click(btn)

    // Honest message is rendered via the dedicated not-available slot.
    await waitFor(() => {
      const el = document.querySelector("[data-reauth-not-available]")
      expect(el).toBeTruthy()
      expect(el?.textContent).toContain("not yet available")
    })

    // Button re-enables (no spinner stuck); this is not a hard failure.
    const [reenabled] = screen
      .getAllByText("re-authorize")
      .map((el) => el.closest("button")!) as HTMLButtonElement[]
    expect(reenabled.disabled).toBe(false)
  })

  it("prevents double-submit: clicking again while pending does not call API twice", async () => {
    // Never-resolving promise keeps us in pending state
    mockReauth.mockReturnValue(new Promise(() => {}))
    renderPageUser()

    const [btn] = screen.getAllByText("re-authorize")
    fireEvent.click(btn)

    // Wait until pending state is active
    await waitFor(() => {
      expect(screen.getAllByText("redirecting…").length).toBeGreaterThan(0)
    })

    // A second click on a disabled button should not call the API again
    const [disabledBtn] = screen.getAllByText("redirecting…").map((el) => el.closest("button")!) as HTMLButtonElement[]
    fireEvent.click(disabledBtn)

    expect(mockReauth).toHaveBeenCalledOnce()
  })

  it("drives spotify through the connector PKCE start, not the generalized dance", async () => {
    // Spotify has two OAuth implementations: the connector PKCE flow
    // (POST /api/connectors/spotify/oauth/start, client_id only — what the
    // registered Spotify app's redirect URIs point at, and what is connected
    // today) and the generalized confidential-client dance, whose
    // SPOTIFY_OAUTH_CLIENT_ID/SECRET were never provisioned. bu-5gliy pointed
    // this pill at the latter, so it could only ever fail. The pill stays — a
    // sick credential must keep its commit action — but the flow behind it is
    // the connector's, and the provider URL it returns is absolute (no API base
    // to resolve against).
    const authUrl = "https://accounts.spotify.com/authorize?client_id=abc&code_challenge=xyz"
    mockSpotifyStart.mockResolvedValue({
      authorization_url: authUrl,
      state: "opaque-csrf-state",
    } satisfies SpotifyOAuthStartResponse)

    const locationDescriptor = Object.getOwnPropertyDescriptor(window, "location")
    const hrefSetter = vi.fn()
    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        ...window.location,
        set href(v: string) {
          hrefSetter(v)
        },
      },
    })

    renderSpotifyPageUser()

    const [btn] = screen.getAllByText("re-authorize")
    fireEvent.click(btn)

    await waitFor(() => {
      expect(hrefSetter).toHaveBeenCalledWith(authUrl)
    })
    expect(mockReauth).not.toHaveBeenCalled()

    if (locationDescriptor) {
      Object.defineProperty(window, "location", locationDescriptor)
    }
  })
})
