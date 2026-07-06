// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// PageSystem — commit-action button wiring tests [bu-ayp6v.4]
//
// Coverage:
//   - set value button (missing cred) opens value-entry panel → useSetSystemSecret target="shared"
//   - rotate button (present cred) opens value-entry panel → useSetSystemSecret target="shared"
//   - override · per butler button opens butler-picker panel → useSetSystemSecret target="<butler>"
//   - delete button opens confirm panel → useDeleteSystemSecret with correct target
//   - delete on local override passes per-butler target
//   - test button calls useProbeSystemSecret
//   - 429 rate-limit handled gracefully (non-blocking hint, no crash)
// ---------------------------------------------------------------------------

import { describe, expect, it, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor, act, cleanup } from "@testing-library/react"
import * as React from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

// ---------------------------------------------------------------------------
// Mock API client
// ---------------------------------------------------------------------------

vi.mock("@/api/client.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client.ts")>()
  return {
    ...actual,
    setSystemCredential: vi.fn(),
    probeSystemCredential: vi.fn(),
    deleteSystemCredential: vi.fn(),
    // ConfirmImpact (bu-cyyi3) fetches the breaks catalogue whenever a
    // delete/rotate confirm panel opens. Mock it so it resolves immediately
    // instead of hitting the real network in jsdom — the "yes, delete" pill
    // is disabled until this resolves, so an unmocked/slow call would hang
    // these tests. Empty catalogue is the correct default here: these tests
    // exercise the mutation wiring, not the catalogue contents.
    getBreaksCatalogue: vi.fn(),
  }
})

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

// PageSystem uses useButlers() to populate the override butler-picker.
// Provide a stable list of two butlers for tests that exercise the picker.
vi.mock("@/hooks/use-butlers", () => ({
  useButlers: vi.fn(() => ({
    data: { data: [{ name: "general" }, { name: "calendar" }] },
    isLoading: false,
    error: null,
  })),
}))

import {
  setSystemCredential,
  probeSystemCredential,
  deleteSystemCredential,
  getBreaksCatalogue,
  ApiError,
} from "@/api/client.ts"
const mockSet = vi.mocked(setSystemCredential)
const mockProbe = vi.mocked(probeSystemCredential)
const mockDelete = vi.mocked(deleteSystemCredential)
const mockGetBreaksCatalogue = vi.mocked(getBreaksCatalogue)

// ---------------------------------------------------------------------------
// Component + mock data
// ---------------------------------------------------------------------------

import { PageSystem } from "./pages.tsx"
import { MOCK_SYSTEM_CREDENTIALS } from "./mock-data.ts"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** BUTLER_TELEGRAM_TOKEN — shared credential, fingerprint set, not plain value. */
const TELEGRAM = MOCK_SYSTEM_CREDENTIALS.find((s) => s.key === "BUTLER_TELEGRAM_TOKEN")!
/** OWNTRACKS_WEBHOOK_TOKEN — missing credential (rowState="missing"). */
const MISSING = MOCK_SYSTEM_CREDENTIALS.find((s) => s.key === "OWNTRACKS_WEBHOOK_TOKEN")!
/** GMAIL_SENDER_ADDRESS — plain value credential (plainValue present). */
const PLAIN = MOCK_SYSTEM_CREDENTIALS.find((s) => s.key === "GMAIL_SENDER_ADDRESS")!
/** Local override credential (rowState="local", target=butler name). */
const LOCAL_OVERRIDE: typeof TELEGRAM = {
  ...TELEGRAM,
  rowState: "local",
  target: "calendar",
}

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
}

function renderInProvider(element: React.ReactElement) {
  const client = makeQueryClient()
  return render(
    <QueryClientProvider client={client}>{element}</QueryClientProvider>,
  )
}

function renderTelegram() {
  return renderInProvider(<PageSystem credential={TELEGRAM} />)
}

function renderMissing() {
  return renderInProvider(<PageSystem credential={MISSING} />)
}

function renderLocalOverride() {
  return renderInProvider(<PageSystem credential={LOCAL_OVERRIDE} />)
}

function getBtn(label: string): HTMLButtonElement {
  return screen.getByRole("button", { name: label }) as HTMLButtonElement
}

function queryBtn(label: string): HTMLButtonElement | null {
  return screen.queryByRole("button", { name: label }) as HTMLButtonElement | null
}

beforeEach(() => {
  // Default: empty catalogue, resolved immediately — clears the
  // ConfirmImpact "loading" gate on any confirm button synchronously after
  // the panel opens. Individual tests override this if they care about the
  // catalogue response itself.
  mockGetBreaksCatalogue.mockResolvedValue({ data: [], meta: {} })
})

afterEach(() => {
  vi.clearAllMocks()
  cleanup()
})

// ── Set value (missing credential) ───────────────────────────────────────────

describe("PageSystem: set value button (missing credential)", () => {
  it("renders 'set value' commit button on missing credential", () => {
    renderMissing()
    expect(getBtn("set value")).toBeTruthy()
  })

  it("opens the value-entry panel when set value is clicked", () => {
    renderMissing()

    fireEvent.click(getBtn("set value"))

    expect(screen.getByPlaceholderText("paste value here")).toBeTruthy()
  })

  it("calls setSystemCredential with target='shared' on submit", async () => {
    mockSet.mockReturnValue(new Promise(() => {}))
    renderMissing()

    fireEvent.click(getBtn("set value"))
    const textarea = screen.getByPlaceholderText("paste value here")
    fireEvent.change(textarea, { target: { value: "my-secret-token" } })

    await act(async () => {
      fireEvent.click(getBtn("save"))
    })

    expect(mockSet).toHaveBeenCalledOnce()
    expect(mockSet).toHaveBeenCalledWith("OWNTRACKS_WEBHOOK_TOKEN", {
      value: "my-secret-token",
      target: "shared",
    })
  })

  it("shows 'saving…' while the set mutation is pending", async () => {
    mockSet.mockReturnValue(new Promise(() => {}))
    renderMissing()

    fireEvent.click(getBtn("set value"))
    const textarea = screen.getByPlaceholderText("paste value here")
    fireEvent.change(textarea, { target: { value: "abc" } })

    await act(async () => {
      fireEvent.click(getBtn("save"))
    })

    await waitFor(() => {
      expect(getBtn("saving…")).toBeTruthy()
    })
  })

  it("save button is disabled when value is empty", () => {
    renderMissing()

    fireEvent.click(getBtn("set value"))

    expect(getBtn("save").disabled).toBe(true)
  })

  it("cancel closes the value-entry panel without calling the API", () => {
    renderMissing()

    fireEvent.click(getBtn("set value"))
    expect(screen.getByPlaceholderText("paste value here")).toBeTruthy()

    fireEvent.click(getBtn("cancel"))

    expect(screen.queryByPlaceholderText("paste value here")).toBeNull()
    expect(mockSet).not.toHaveBeenCalled()
  })
})

// ── Rotate (present credential) ───────────────────────────────────────────────

describe("PageSystem: rotate button (present credential)", () => {
  it("renders 'rotate' button on shared credential", () => {
    renderTelegram()
    expect(getBtn("rotate")).toBeTruthy()
  })

  it("opens the value-entry panel when rotate is clicked", () => {
    renderTelegram()

    fireEvent.click(getBtn("rotate"))

    expect(screen.getByPlaceholderText("paste value here")).toBeTruthy()
  })

  it("calls setSystemCredential with target='shared' on submit", async () => {
    mockSet.mockReturnValue(new Promise(() => {}))
    renderTelegram()

    fireEvent.click(getBtn("rotate"))
    const textarea = screen.getByPlaceholderText("paste value here")
    fireEvent.change(textarea, { target: { value: "new-token-value" } })

    await act(async () => {
      fireEvent.click(getBtn("save"))
    })

    expect(mockSet).toHaveBeenCalledOnce()
    expect(mockSet).toHaveBeenCalledWith("BUTLER_TELEGRAM_TOKEN", {
      value: "new-token-value",
      target: "shared",
    })
  })

  it("closes the panel on success", async () => {
    mockSet.mockResolvedValue({ data: TELEGRAM as never, meta: {} })
    renderTelegram()

    fireEvent.click(getBtn("rotate"))
    const textarea = screen.getByPlaceholderText("paste value here")
    fireEvent.change(textarea, { target: { value: "abc" } })

    await act(async () => {
      fireEvent.click(getBtn("save"))
    })

    await waitFor(() => {
      expect(screen.queryByPlaceholderText("paste value here")).toBeNull()
    })
  })
})

// ── Override · per butler ─────────────────────────────────────────────────────

describe("PageSystem: override · per butler button", () => {
  it("renders 'override · per butler' button on shared credential", () => {
    renderTelegram()
    expect(getBtn("override · per butler")).toBeTruthy()
  })

  it("does NOT render 'override · per butler' when credential is already a local override", () => {
    renderLocalOverride()
    expect(queryBtn("override · per butler")).toBeNull()
  })

  it("opens the override panel with a butler picker", () => {
    renderTelegram()

    fireEvent.click(getBtn("override · per butler"))

    expect(screen.getByPlaceholderText("paste override value here")).toBeTruthy()
    expect(screen.getByRole("combobox")).toBeTruthy()
  })

  it("calls setSystemCredential with the selected butler as target", async () => {
    mockSet.mockReturnValue(new Promise(() => {}))
    renderTelegram()

    fireEvent.click(getBtn("override · per butler"))

    // Select "calendar" in the butler picker
    const picker = screen.getByRole("combobox")
    fireEvent.change(picker, { target: { value: "calendar" } })

    const textarea = screen.getByPlaceholderText("paste override value here")
    fireEvent.change(textarea, { target: { value: "per-butler-token" } })

    await act(async () => {
      fireEvent.click(getBtn("save override"))
    })

    expect(mockSet).toHaveBeenCalledOnce()
    expect(mockSet).toHaveBeenCalledWith("BUTLER_TELEGRAM_TOKEN", {
      value: "per-butler-token",
      target: "calendar",
    })
  })

  it("save override button is disabled when value is empty", () => {
    renderTelegram()

    fireEvent.click(getBtn("override · per butler"))

    expect(getBtn("save override").disabled).toBe(true)
  })

  it("cancel closes the override panel without calling the API", () => {
    renderTelegram()

    fireEvent.click(getBtn("override · per butler"))
    expect(screen.getByPlaceholderText("paste override value here")).toBeTruthy()

    fireEvent.click(getBtn("cancel"))

    expect(screen.queryByPlaceholderText("paste override value here")).toBeNull()
    expect(mockSet).not.toHaveBeenCalled()
  })
})

// ── Probe (test button) ────────────────────────────────────────────────────────

// bu-eptoz: PageSystem has exactly one test/probe control — the probe
// block's "run probe" / "probe again" button (ProbeResult in atoms.tsx). A
// duplicate footer "test" pill (CommitFooter) used to fire the identical
// handleProbe action; it's removed, so these click the probe-block button
// by its own label instead. TELEGRAM already has a prior probe result, so
// its label is "probe again" (never-tested credentials show "run probe").
describe("PageSystem: test button (probe)", () => {
  it("calls probeSystemCredential with the credential key", async () => {
    mockProbe.mockReturnValue(new Promise(() => {}))
    renderTelegram()

    await act(async () => {
      fireEvent.click(getBtn("probe again"))
    })

    expect(mockProbe).toHaveBeenCalledOnce()
    expect(mockProbe).toHaveBeenCalledWith("BUTLER_TELEGRAM_TOKEN")
  })

  it("shows 'testing…' while the probe is in flight", async () => {
    mockProbe.mockReturnValue(new Promise(() => {}))
    renderTelegram()

    await act(async () => {
      fireEvent.click(getBtn("probe again"))
    })

    await waitFor(() => {
      expect(getBtn("testing…")).toBeTruthy()
    })
  })

  it("test button is absent on missing credential", () => {
    renderMissing()
    expect(queryBtn("run probe")).toBeNull()
    expect(queryBtn("probe again")).toBeNull()
  })

  it("test button is absent for plainValue credentials", () => {
    renderInProvider(<PageSystem credential={PLAIN} />)
    expect(queryBtn("run probe")).toBeNull()
    expect(queryBtn("probe again")).toBeNull()
  })

  it("handles HTTP 429 rate-limit gracefully — shows hint, does not crash", async () => {
    mockProbe.mockRejectedValue(new ApiError("RATE_LIMITED", "Too Many Requests", 429))
    renderTelegram()

    await act(async () => {
      fireEvent.click(getBtn("probe again"))
    })

    await waitFor(() => {
      expect(screen.getByText("try again in a moment")).toBeTruthy()
    })
  })
})

// ── Delete ──────────────────────────────────────────────────────────────────────

describe("PageSystem: delete button", () => {
  it("opens the delete confirm panel when delete is clicked", () => {
    renderTelegram()

    fireEvent.click(getBtn("delete"))

    expect(
      screen.getByText("Remove this shared credential? The credential will be deleted and cannot be recovered."),
    ).toBeTruthy()
    expect(getBtn("yes, delete")).toBeTruthy()
  })

  it("disables 'yes, delete' while ConfirmImpact is still loading impact — an uninformed confirm must not be clickable", () => {
    // Breaks-catalogue fetch never resolves — ConfirmImpact stays in "loading".
    mockGetBreaksCatalogue.mockReturnValue(new Promise(() => {}))
    renderTelegram()

    fireEvent.click(getBtn("delete"))

    expect(screen.getByText("checking impact…")).toBeTruthy()
    expect(getBtn("yes, delete").disabled).toBe(true)
  })

  it("calls deleteSystemCredential with target='shared' for shared credential", async () => {
    mockDelete.mockReturnValue(new Promise(() => {}))
    renderTelegram()

    fireEvent.click(getBtn("delete"))

    // ConfirmImpact gates "yes, delete" until the breaks-catalogue fetch
    // resolves (bu-cyyi3 review follow-up) — an uninformed confirm must not
    // be clickable while impact is still loading.
    await waitFor(() => {
      expect(getBtn("yes, delete").disabled).toBe(false)
    })

    await act(async () => {
      fireEvent.click(getBtn("yes, delete"))
    })

    expect(mockDelete).toHaveBeenCalledOnce()
    expect(mockDelete).toHaveBeenCalledWith("BUTLER_TELEGRAM_TOKEN", "shared")
  })

  it("calls deleteSystemCredential with per-butler target for local override", async () => {
    mockDelete.mockReturnValue(new Promise(() => {}))
    renderLocalOverride()

    fireEvent.click(getBtn("delete"))

    await waitFor(() => {
      expect(getBtn("yes, remove override").disabled).toBe(false)
    })

    await act(async () => {
      fireEvent.click(getBtn("yes, remove override"))
    })

    expect(mockDelete).toHaveBeenCalledOnce()
    expect(mockDelete).toHaveBeenCalledWith("BUTLER_TELEGRAM_TOKEN", "calendar")
  })

  it("local override confirm panel mentions the butler name", () => {
    renderLocalOverride()

    fireEvent.click(getBtn("delete"))

    expect(
      screen.getByText(/Remove per-butler override for calendar\? The override will be deleted/),
    ).toBeTruthy()
  })

  it("shows 'deleting…' while the delete mutation is pending", async () => {
    mockDelete.mockReturnValue(new Promise(() => {}))
    renderTelegram()

    fireEvent.click(getBtn("delete"))

    await waitFor(() => {
      expect(getBtn("yes, delete").disabled).toBe(false)
    })

    await act(async () => {
      fireEvent.click(getBtn("yes, delete"))
    })

    await waitFor(() => {
      expect(getBtn("deleting…")).toBeTruthy()
    })
  })

  it("cancel closes the confirm panel without calling the API", () => {
    renderTelegram()

    fireEvent.click(getBtn("delete"))
    expect(getBtn("yes, delete")).toBeTruthy()

    fireEvent.click(getBtn("cancel"))

    expect(queryBtn("yes, delete")).toBeNull()
    expect(mockDelete).not.toHaveBeenCalled()
  })

  it("delete button is absent for missing credential", () => {
    renderMissing()
    expect(queryBtn("delete")).toBeNull()
  })
})

// ── shared-public target routing [bu-91noc] ─────────────────────────────────

/** A shared-public credential (public.butler_secrets pool, not switchboard). */
const SHARED_PUBLIC: typeof TELEGRAM = {
  ...TELEGRAM,
  key: "TELEGRAM_TOKEN",
  target: "shared-public",
  rowState: "shared",
  readOnly: false,
}

function renderSharedPublic() {
  return renderInProvider(<PageSystem credential={SHARED_PUBLIC} />)
}

// ── State plaque honesty [bu-qvnce.2] ────────────────────────────────────────

describe("PageSystem: state plaque reflects probe failure, not just row presence", () => {
  it("renders a red 'failing' plaque when the credential's last probe failed", () => {
    renderInProvider(<PageSystem credential={{ ...TELEGRAM, state: "failed" }} />)

    const plaque = document.querySelector("[data-state-plaque]") as HTMLElement
    expect(plaque).toBeTruthy()
    expect(screen.getByText("failing")).toBeTruthy()
    expect(plaque.style.color).toBe("var(--red)")
  })

  it("still renders a green 'shared default' plaque when the row is healthy", () => {
    renderTelegram()

    const plaque = document.querySelector("[data-state-plaque]") as HTMLElement
    expect(screen.getByText("shared default")).toBeTruthy()
    expect(plaque.style.color).toBe("var(--green)")
  })
})

// ── Probe latency honesty [bu-qvnce.2] ───────────────────────────────────────

describe("PageSystem: probe latency reflects the real server value", () => {
  it("shows the real latency the server reports instead of a fabricated 0ms", async () => {
    mockProbe.mockResolvedValue({
      data: { ok: true, code: null, message: null, at: "just now", latency_ms: 143 },
      meta: {},
    } as never)
    renderTelegram()

    await act(async () => {
      fireEvent.click(getBtn("probe again"))
    })

    await waitFor(() => {
      expect(screen.getByText("143ms")).toBeTruthy()
    })
    expect(screen.queryByText("0ms")).toBeNull()
  })

  it("renders no latency figure at all when the server omits it (never fabricates 0ms)", async () => {
    mockProbe.mockResolvedValue({
      data: { ok: true, code: null, message: null, at: "just now", latency_ms: null },
      meta: {},
    } as never)
    renderTelegram()

    await act(async () => {
      fireEvent.click(getBtn("probe again"))
    })

    await waitFor(() => {
      expect(screen.getByText("at just now")).toBeTruthy()
    })
    expect(screen.queryByText(/^\d+ms$/)).toBeNull()
  })
})

// ── Audit link honesty [bu-qvnce.2] ──────────────────────────────────────────

describe("PageSystem: audit cross-reference link", () => {
  it("prefixes the audit-log deep link with 's:' so it resolves instead of 422ing", () => {
    renderTelegram()

    const link = screen.getByRole("link", { name: /\/audit\?key=/ }) as HTMLAnchorElement
    expect(link.getAttribute("href")).toBe(`/audit-log?key=s:${TELEGRAM.key}`)
  })
})

describe("PageSystem: shared-public routing [bu-91noc]", () => {
  it("renders the generic editor (rotate button) for shared-public rows", () => {
    renderSharedPublic()
    expect(getBtn("rotate")).toBeTruthy()
  })

  it("calls setSystemCredential with target='shared-public' on rotate submit", async () => {
    mockSet.mockReturnValue(new Promise(() => {}))
    renderSharedPublic()

    fireEvent.click(getBtn("rotate"))
    const textarea = screen.getByPlaceholderText("paste value here")
    fireEvent.change(textarea, { target: { value: "new-public-secret" } })

    await act(async () => {
      fireEvent.click(getBtn("save"))
    })

    expect(mockSet).toHaveBeenCalledOnce()
    expect(mockSet).toHaveBeenCalledWith("TELEGRAM_TOKEN", {
      value: "new-public-secret",
      target: "shared-public",
    })
  })

  it("calls deleteSystemCredential with target='shared-public' for shared-public row", async () => {
    mockDelete.mockReturnValue(new Promise(() => {}))
    renderSharedPublic()

    fireEvent.click(getBtn("delete"))

    await waitFor(() => {
      expect(getBtn("yes, delete").disabled).toBe(false)
    })

    await act(async () => {
      fireEvent.click(getBtn("yes, delete"))
    })

    expect(mockDelete).toHaveBeenCalledOnce()
    expect(mockDelete).toHaveBeenCalledWith("TELEGRAM_TOKEN", "shared-public")
  })
})
