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

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { reauthorizeUserCredential } from "@/api/client.ts"
const mockReauth = vi.mocked(reauthorizeUserCredential)

// ---------------------------------------------------------------------------
// Component + mock data — imported after mocks are established
// ---------------------------------------------------------------------------

import { PageUser } from "./pages.tsx"
import { MOCK_USER_CREDENTIALS, MOCK_PROVIDERS } from "./mock-data.ts"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const SPOTIFY = MOCK_USER_CREDENTIALS.find((u) => u.provider === "spotify")!
const SPOTIFY_PROVIDER = MOCK_PROVIDERS.spotify

function renderPageUser() {
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
    expect(mockReauth).toHaveBeenCalledWith("spotify", "tze")
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
})
