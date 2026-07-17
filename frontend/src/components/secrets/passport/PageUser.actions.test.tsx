// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// PageUser — rotate / disconnect / probe action wiring tests [bu-ayp6v.3]
//
// Coverage:
//   - test button calls useProbeUserSecret and the probe result surfaces in UI
//   - rotate button opens the value-entry panel; submitting calls useRotateUserSecret
//   - disconnect button opens the confirm panel; confirming calls useDisconnectUserSecret
//   - reveal value button is NOT rendered on PageUser (no user-secret reveal path)
//   - connect button on never_set credential calls reauthorizeUserCredential
// ---------------------------------------------------------------------------

import { describe, expect, it, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor, act, cleanup } from "@testing-library/react"
import * as React from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

// ---------------------------------------------------------------------------
// Mock the API client — use importOriginal so module shape is preserved.
// The mutations hook imports from "@/api/client.ts" (with extension), so the
// mock factory spreads the original and replaces only the mutation functions.
// ---------------------------------------------------------------------------

vi.mock("@/api/client.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client.ts")>()
  return {
    ...actual,
    reauthorizeUserCredential: vi.fn(),
    probeUserCredential: vi.fn(),
    rotateUserCredential: vi.fn(),
    disconnectUserCredential: vi.fn(),
    getTelegramSessionStatus: vi.fn(),
    telegramSendCode: vi.fn(),
    // ConfirmImpact (bu-cyyi3) fetches the breaks catalogue whenever the
    // disconnect confirm panel opens. Mock it so it resolves immediately
    // instead of hitting the real network in jsdom — the "yes, disconnect"
    // pill is disabled until this resolves.
    getBreaksCatalogue: vi.fn(),
  }
})

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

import {
  reauthorizeUserCredential,
  probeUserCredential,
  rotateUserCredential,
  disconnectUserCredential,
  getTelegramSessionStatus,
  telegramSendCode,
  getBreaksCatalogue,
} from "@/api/client.ts"
const mockReauth = vi.mocked(reauthorizeUserCredential)
const mockProbe = vi.mocked(probeUserCredential)
const mockRotate = vi.mocked(rotateUserCredential)
const mockDisconnect = vi.mocked(disconnectUserCredential)
const mockTelegramSessionStatus = vi.mocked(getTelegramSessionStatus)
const mockTelegramSendCode = vi.mocked(telegramSendCode)
const mockGetBreaksCatalogue = vi.mocked(getBreaksCatalogue)

beforeEach(() => {
  // Default: empty catalogue, resolved immediately — clears the
  // ConfirmImpact "loading" gate on the disconnect confirm button
  // synchronously after the panel opens.
  mockGetBreaksCatalogue.mockResolvedValue({ data: [], meta: {} })
  mockTelegramSessionStatus.mockResolvedValue({
    has_api_id: false,
    has_api_hash: false,
    has_session: false,
    has_scope_consent: false,
    ready: false,
  })
})

// ---------------------------------------------------------------------------
// Component + mock data
// ---------------------------------------------------------------------------

import { PageUser } from "./pages.tsx"
import { MOCK_USER_CREDENTIALS, MOCK_PROVIDERS } from "./mock-data.ts"
import type { ProviderInfo, UserCredential } from "./types.ts"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Google credential is in "ok" state — test + rotate + disconnect visible. */
const GOOGLE = MOCK_USER_CREDENTIALS.find((u) => u.provider === "google" && u.identity === "tze")!
const GOOGLE_PROVIDER = MOCK_PROVIDERS.google
const EMAIL: UserCredential = {
  ...GOOGLE,
  provider: "email",
  sourceTypes: ["email_password"],
}
const EMAIL_PROVIDER: ProviderInfo = {
  ...GOOGLE_PROVIDER,
  id: "email",
  label: "Email",
  kind: "token",
  authority: "mail.google.com",
}
const TELEGRAM: UserCredential = {
  ...GOOGLE,
  provider: "telegram_bot",
  sourceTypes: ["telegram_api_id", "telegram_api_hash"],
}
const TELEGRAM_PROVIDER = MOCK_PROVIDERS.telegram_bot
const UNMAPPED: UserCredential = {
  ...GOOGLE,
  sourceTypes: ["google_oauth_refresh"],
}

/** Steam credential is in "never_set" state — only connect visible. */
const STEAM = MOCK_USER_CREDENTIALS.find((u) => u.provider === "steam")!
const STEAM_PROVIDER = MOCK_PROVIDERS.steam

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
}

function renderInProvider(element: React.ReactElement) {
  const client = makeQueryClient()
  return render(
    <QueryClientProvider client={client}>{element}</QueryClientProvider>,
  )
}

function renderGoogle() {
  return renderInProvider(<PageUser credential={GOOGLE} provider={GOOGLE_PROVIDER} />)
}

function renderEmail() {
  return renderInProvider(<PageUser credential={EMAIL} provider={EMAIL_PROVIDER} />)
}

function renderTelegram() {
  return renderInProvider(<PageUser credential={TELEGRAM} provider={TELEGRAM_PROVIDER} />)
}

function renderUnmapped() {
  return renderInProvider(<PageUser credential={UNMAPPED} provider={GOOGLE_PROVIDER} />)
}

function renderSteam() {
  return renderInProvider(<PageUser credential={STEAM} provider={STEAM_PROVIDER} />)
}

// Helper: get a button by its accessible name.
function getBtn(label: string): HTMLButtonElement {
  return screen.getByRole("button", { name: label }) as HTMLButtonElement
}

function queryBtn(label: string): HTMLButtonElement | null {
  return screen.queryByRole("button", { name: label }) as HTMLButtonElement | null
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

afterEach(() => {
  vi.clearAllMocks()
  cleanup()
})

// ── Probe ────────────────────────────────────────────────────────────────────

// bu-eptoz: PageUser has exactly one test/probe control — the probe block's
// "run probe" / "probe again" button (ProbeResult in atoms.tsx). A duplicate
// footer "test" pill (CommitFooter) used to fire the identical handleProbe
// action; it's removed, so these click the probe-block button by its own
// label instead. GOOGLE already has a prior probe result, so its label is
// "probe again" (never-tested credentials show "run probe").
describe("PageUser: test button (probe)", () => {
  it("calls probeUserCredential with correct provider and identity", async () => {
    mockProbe.mockReturnValue(new Promise(() => {}))
    renderGoogle()

    await act(async () => {
      fireEvent.click(getBtn("probe again"))
    })

    expect(mockProbe).toHaveBeenCalledOnce()
    expect(mockProbe).toHaveBeenCalledWith("google", "tze")
  })

  it("shows 'testing…' while the probe is in flight", async () => {
    mockProbe.mockReturnValue(new Promise(() => {}))
    renderGoogle()

    await act(async () => {
      fireEvent.click(getBtn("probe again"))
    })

    await waitFor(() => {
      expect(getBtn("testing…")).toBeTruthy()
    })
  })

  it("surfaces probe result code in ProbeResult block when mutation succeeds", async () => {
    mockProbe.mockResolvedValue({
      data: { ok: false, code: 401, message: "refresh-token expired", at: "just now" },
      meta: {},
    })
    renderGoogle()

    await act(async () => {
      fireEvent.click(getBtn("probe again"))
    })

    // After success the block should show the returned status code
    await waitFor(() => {
      expect(screen.getByText("401")).toBeTruthy()
    })
  })

  it("test button is absent for never_set credential", () => {
    renderSteam()
    expect(queryBtn("run probe")).toBeNull()
    expect(queryBtn("probe again")).toBeNull()
  })
})

// ── Rotate ───────────────────────────────────────────────────────────────────

describe("PageUser: rotate button (value-entry panel)", () => {
  it("opens the rotate panel when rotate is clicked", () => {
    renderGoogle()

    fireEvent.click(getBtn("rotate"))

    expect(screen.getByPlaceholderText("paste token here")).toBeTruthy()
  })

  it("links to the email password source with the established external-link semantics", () => {
    renderEmail()

    fireEvent.click(getBtn("rotate"))

    const link = screen.getByRole("link", { name: "Google App Passwords" })
    expect(link.getAttribute("href")).toBe("https://myaccount.google.com/apppasswords")
    expect(link.getAttribute("target")).toBe("_blank")
    expect(link.getAttribute("rel")).toBe("noopener noreferrer")
  })

  it("routes Telegram to guided setup without exposing raw rotation", async () => {
    mockTelegramSendCode.mockResolvedValue({
      session_token: "telegram-session-token",
      phone_code_hash: "phone-code-hash",
    })
    renderTelegram()

    expect(queryBtn("rotate")).toBeNull()
    expect(screen.queryByPlaceholderText("paste token here")).toBeNull()

    const setupTrigger = getBtn("set up Telegram")
    fireEvent.click(setupTrigger)

    const apiIdInput = await screen.findByLabelText("Telegram API ID")
    const apiHashInput = screen.getByLabelText("Telegram API hash")
    const phoneInput = screen.getByLabelText("Telegram phone number")
    const sendCode = getBtn("Send code")

    expect(apiHashInput.getAttribute("type")).toBe("password")
    expect(sendCode.disabled).toBe(true)

    fireEvent.change(apiIdInput, { target: { value: "123456" } })
    fireEvent.change(apiHashInput, { target: { value: "guided-api-hash" } })
    fireEvent.change(phoneInput, { target: { value: "+15551234567" } })
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: "I acknowledge the account-wide Telegram ingestion scope described above.",
      }),
    )

    await waitFor(() => {
      expect(sendCode.disabled).toBe(false)
    })

    await act(async () => {
      fireEvent.click(sendCode)
    })

    expect(mockTelegramSendCode).toHaveBeenCalledOnce()
    expect(mockTelegramSendCode.mock.calls[0]?.[0]).toEqual({
      api_id: 123456,
      api_hash: "guided-api-hash",
      phone: "+15551234567",
      scope_consent: true,
    })
    expect(mockRotate).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole("button", { name: "Dismiss Telegram setup" }))
    await waitFor(() => {
      expect(document.activeElement).toBe(setupTrigger)
    })
  })

  it("does not show a provenance link for an unmapped credential type", () => {
    renderUnmapped()

    fireEvent.click(getBtn("rotate"))

    expect(document.querySelector('[data-provenance-line="true"]')).toBeNull()
  })

  it("calls rotateUserCredential with the entered value on submit", async () => {
    mockRotate.mockReturnValue(new Promise(() => {}))
    renderGoogle()

    fireEvent.click(getBtn("rotate"))

    const textarea = screen.getByPlaceholderText("paste token here")
    fireEvent.change(textarea, { target: { value: "new-secret-token" } })

    await act(async () => {
      fireEvent.click(getBtn("save"))
    })

    expect(mockRotate).toHaveBeenCalledOnce()
    expect(mockRotate).toHaveBeenCalledWith(
      "google",
      { value: "new-secret-token" },
      "tze",
    )
  })

  it("shows 'saving…' while the rotate mutation is pending", async () => {
    mockRotate.mockReturnValue(new Promise(() => {}))
    renderGoogle()

    fireEvent.click(getBtn("rotate"))
    const textarea = screen.getByPlaceholderText("paste token here")
    fireEvent.change(textarea, { target: { value: "abc" } })

    await act(async () => {
      fireEvent.click(getBtn("save"))
    })

    await waitFor(() => {
      expect(getBtn("saving…")).toBeTruthy()
    })
  })

  it("save button is disabled while value is empty", () => {
    renderGoogle()

    fireEvent.click(getBtn("rotate"))

    const saveBtn = getBtn("save")
    expect(saveBtn.disabled).toBe(true)
  })

  it("cancel closes the rotate panel without calling the API", () => {
    renderGoogle()

    fireEvent.click(getBtn("rotate"))
    expect(screen.getByPlaceholderText("paste token here")).toBeTruthy()

    fireEvent.click(getBtn("cancel"))

    expect(screen.queryByPlaceholderText("paste token here")).toBeNull()
    expect(mockRotate).not.toHaveBeenCalled()
  })

  it("closes rotate panel on success", async () => {
    mockRotate.mockResolvedValue({ data: { ...GOOGLE } as never, meta: {} })
    renderGoogle()

    fireEvent.click(getBtn("rotate"))
    const textarea = screen.getByPlaceholderText("paste token here")
    fireEvent.change(textarea, { target: { value: "abc" } })

    await act(async () => {
      fireEvent.click(getBtn("save"))
    })

    await waitFor(() => {
      expect(screen.queryByPlaceholderText("paste token here")).toBeNull()
    })
  })
})

// ── Disconnect ───────────────────────────────────────────────────────────────

describe("PageUser: disconnect button (confirm panel)", () => {
  it("opens the disconnect confirm panel when disconnect is clicked", () => {
    renderGoogle()

    fireEvent.click(getBtn("disconnect"))

    expect(
      screen.getByText("Remove this credential? The credential will be deleted and cannot be recovered."),
    ).toBeTruthy()
    expect(getBtn("yes, disconnect")).toBeTruthy()
  })

  it("calls disconnectUserCredential with correct provider and identity on confirm", async () => {
    mockDisconnect.mockReturnValue(new Promise(() => {}))
    renderGoogle()

    fireEvent.click(getBtn("disconnect"))

    // ConfirmImpact gates "yes, disconnect" until the breaks-catalogue fetch
    // resolves (bu-cyyi3 review follow-up) — an uninformed confirm must not
    // be clickable while impact is still loading.
    await waitFor(() => {
      expect(getBtn("yes, disconnect").disabled).toBe(false)
    })

    await act(async () => {
      fireEvent.click(getBtn("yes, disconnect"))
    })

    expect(mockDisconnect).toHaveBeenCalledOnce()
    expect(mockDisconnect).toHaveBeenCalledWith("google", "tze")
  })

  it("shows 'removing…' while disconnect is pending", async () => {
    mockDisconnect.mockReturnValue(new Promise(() => {}))
    renderGoogle()

    fireEvent.click(getBtn("disconnect"))

    await waitFor(() => {
      expect(getBtn("yes, disconnect").disabled).toBe(false)
    })

    await act(async () => {
      fireEvent.click(getBtn("yes, disconnect"))
    })

    await waitFor(() => {
      expect(getBtn("removing…")).toBeTruthy()
    })
  })

  it("cancel closes the confirm panel without calling the API", () => {
    renderGoogle()

    fireEvent.click(getBtn("disconnect"))
    expect(getBtn("yes, disconnect")).toBeTruthy()

    fireEvent.click(getBtn("cancel"))

    expect(queryBtn("yes, disconnect")).toBeNull()
    expect(mockDisconnect).not.toHaveBeenCalled()
  })

  it("disconnect button is absent for never_set credential", () => {
    renderSteam()
    expect(queryBtn("disconnect")).toBeNull()
  })
})

// ── Reveal value (removed) ────────────────────────────────────────────────────

describe("PageUser: reveal value is absent", () => {
  it("does not render a 'reveal value' button (no user-secret reveal path)", () => {
    renderGoogle()
    expect(queryBtn("reveal value")).toBeNull()
  })

  it("'reveal value' absent even when credential has a fingerprint (sha256:7a3f9e2c)", () => {
    // Google cred has fingerprint: "sha256:7a3f9e2c"
    renderGoogle()
    expect(queryBtn("reveal value")).toBeNull()
  })
})

// ── Connect (never_set) ───────────────────────────────────────────────────────

describe("PageUser: connect button (never_set credential)", () => {
  it("renders 'connect' button on never_set credential", () => {
    renderSteam()
    expect(getBtn("connect")).toBeTruthy()
  })

  it("calls reauthorizeUserCredential on connect click", async () => {
    mockReauth.mockReturnValue(new Promise(() => {}))
    renderSteam()

    fireEvent.click(getBtn("connect"))

    expect(mockReauth).toHaveBeenCalledOnce()
    expect(mockReauth).toHaveBeenCalledWith("steam", "tze")
  })

  it("shows 'redirecting…' while connect is pending", async () => {
    mockReauth.mockReturnValue(new Promise(() => {}))
    renderSteam()

    fireEvent.click(getBtn("connect"))

    await waitFor(() => {
      expect(getBtn("redirecting…")).toBeTruthy()
    })
  })
})
