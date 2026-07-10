// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// PageSystem — "used by" band consumer-label humanization [bu-aqoiq]
//
// Consumer names come from the backend's _SYSTEM_KEY_USED_BY map
// (secrets_v2.py) as raw module/subsystem tokens (email, telegram,
// blob_storage, oauth). This band must render them humanized — never raw
// snake_case — while preserving the bu-xzaxm/#3001 "usage not tracked" honesty
// copy for keys with no known consumer.
//
// Coverage:
//   - known label renders humanized (blob_storage → "Blob Storage",
//     oauth → "OAuth", email → "Email")
//   - unknown snake_case consumer falls back to Title Case sanely
//   - empty usedBy still renders the "usage not tracked" honesty line
//     (never a raw label, never a confident all-clear)
//   - the "*" sentinel keeps its serif "every butler…" copy (not humanized)
// ---------------------------------------------------------------------------

import { describe, expect, it, vi, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

// Mirror the sibling PageSystem tests' mocks so a bare render never hits the
// network in jsdom.
vi.mock("@/api/client.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client.ts")>()
  return {
    ...actual,
    setSystemCredential: vi.fn(),
    probeSystemCredential: vi.fn(),
    deleteSystemCredential: vi.fn(),
    getBreaksCatalogue: vi.fn().mockResolvedValue({ data: [], meta: {} }),
  }
})

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

vi.mock("@/hooks/use-butlers", () => ({
  useButlers: vi.fn(() => ({
    data: { data: [{ name: "general" }, { name: "calendar" }] },
    isLoading: false,
    error: null,
  })),
}))

import { PageSystem } from "./pages.tsx"
import { MOCK_SYSTEM_CREDENTIALS } from "./mock-data.ts"

const BASE = MOCK_SYSTEM_CREDENTIALS.find((s) => s.key === "BUTLER_TELEGRAM_TOKEN")!

function withUsedBy(usedBy: string[]): typeof BASE {
  return { ...BASE, usedBy }
}

function renderPage(credential: typeof BASE) {
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

describe("PageSystem: used-by band humanizes consumer labels [bu-aqoiq]", () => {
  it("renders a known snake_case consumer humanized (blob_storage → 'Blob Storage')", () => {
    renderPage(withUsedBy(["blob_storage"]))

    expect(screen.getByText("Blob Storage")).toBeTruthy()
    // The raw technical token must not leak into the UI.
    expect(screen.queryByText("blob_storage")).toBeNull()
  })

  it("renders the oauth consumer as 'OAuth' (not the mangled 'Oauth')", () => {
    renderPage(withUsedBy(["oauth"]))

    expect(screen.getByText("OAuth")).toBeTruthy()
    expect(screen.queryByText("oauth")).toBeNull()
    expect(screen.queryByText("Oauth")).toBeNull()
  })

  it("renders the email consumer as 'Email'", () => {
    renderPage(withUsedBy(["email"]))

    expect(screen.getByText("Email")).toBeTruthy()
  })

  it("falls back to Title Case for an unknown snake_case consumer", () => {
    renderPage(withUsedBy(["future_module"]))

    expect(screen.getByText("Future Module")).toBeTruthy()
    expect(screen.queryByText("future_module")).toBeNull()
  })

  it("still renders the 'usage not tracked' honesty line for an empty used-by list", () => {
    renderPage(withUsedBy([]))

    // Honesty copy shipped by bu-xzaxm / #3001 / #3008 must remain untouched.
    expect(screen.getByText("usage not tracked")).toBeTruthy()
  })

  it("keeps the serif 'every butler…' copy for the '*' sentinel (not humanized)", () => {
    renderPage(withUsedBy(["*"]))

    expect(screen.getByText("every butler that talks to a model.")).toBeTruthy()
    // The sentinel is never treated as a consumer token.
    expect(screen.queryByTestId("used-by-consumer")).toBeNull()
  })
})
