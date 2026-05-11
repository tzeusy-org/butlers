// @vitest-environment jsdom
/**
 * ButlerApprovalsTab -- RTL tests pinning the approvals panel.
 *
 * Tests:
 *  - Renders the full-width panel container
 *  - High/medium/low severity dot colors differ via data-severity attribute
 *  - Empty state copy "No items pending review." renders when list is empty
 *  - Action link navigates to /approvals
 *  - Filtering: component passes butlerName to useApprovalActions so only
 *    this butler's actions are fetched
 *  - Loading state renders loading placeholder, not empty-state text
 *  - Title (tool_name) renders for each action
 *
 * bead: bu-iuol4.18
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import ButlerApprovalsTab from "./ButlerApprovalsTab"

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-approvals", () => ({
  useApprovalActions: vi.fn(),
}))

import { useApprovalActions } from "@/hooks/use-approvals"

// ---------------------------------------------------------------------------
// Fixture data
//
// All time values are relative to real Date.now() so that deriveSeverity()
// (which also uses Date.now()) produces deterministic severity classifications.
// ---------------------------------------------------------------------------

/** An action that expires in 30 minutes -- HIGH severity */
const HIGH_ACTION = {
  id: "action-high-001",
  tool_name: "send_telegram",
  tool_args: { message: "Hello" },
  status: "pending",
  requested_at: new Date(Date.now() - 10 * 60_000).toISOString(),  // 10 min ago
  expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),    // expires in 30 min
  agent_summary: "Send message to owner",
  session_id: "sess-001",
  decided_by: null,
  decided_at: null,
  execution_result: null,
  approval_rule_id: null,
  target_contact: null,
}

/** An action that expires in 6 hours -- MEDIUM severity */
const MEDIUM_ACTION = {
  id: "action-med-002",
  tool_name: "notify",
  tool_args: { contact_id: "c-123" },
  status: "pending",
  requested_at: new Date(Date.now() - 60 * 60_000).toISOString(),        // 1 hour ago
  expires_at: new Date(Date.now() + 6 * 60 * 60_000).toISOString(),      // expires in 6h
  agent_summary: "Notify contact",
  session_id: "sess-002",
  decided_by: null,
  decided_at: null,
  execution_result: null,
  approval_rule_id: null,
  target_contact: { id: "c-123", name: "Alice", roles: [] },
}

/** An action with no expiry -- LOW severity */
const LOW_ACTION = {
  id: "action-low-003",
  tool_name: "archive_email",
  tool_args: { email_id: "e-456" },
  status: "pending",
  requested_at: new Date(Date.now() - 2 * 60 * 60_000).toISOString(),    // 2 hours ago
  expires_at: null,
  agent_summary: null,
  session_id: "sess-003",
  decided_by: null,
  decided_at: null,
  execution_result: null,
  approval_rule_id: null,
  target_contact: null,
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

function renderTab(butlerName = "general") {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <MemoryRouter>
        <ButlerApprovalsTab butlerName={butlerName} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function setupWithActions(actions: typeof HIGH_ACTION[]) {
  vi.mocked(useApprovalActions).mockReturnValue({
    data: { data: actions, meta: { total: actions.length, offset: 0, limit: 50, has_more: false } },
    isLoading: false,
  } as unknown as ReturnType<typeof useApprovalActions>)
}

function setupEmpty() {
  vi.mocked(useApprovalActions).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 50, has_more: false } },
    isLoading: false,
  } as unknown as ReturnType<typeof useApprovalActions>)
}

function setupLoading() {
  vi.mocked(useApprovalActions).mockReturnValue({
    data: undefined,
    isLoading: true,
  } as unknown as ReturnType<typeof useApprovalActions>)
}

// ---------------------------------------------------------------------------
// Tests: container renders
// ---------------------------------------------------------------------------

describe("ButlerApprovalsTab -- container", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    setupWithActions([HIGH_ACTION])
  })
  afterEach(() => cleanup())

  it("renders the top-level tab container", () => {
    renderTab()
    expect(screen.getByTestId("butler-approvals-tab")).toBeDefined()
  })

  it("renders the approvals list when actions are present", () => {
    renderTab()
    expect(screen.getByTestId("approvals-list")).toBeDefined()
  })
})

// ---------------------------------------------------------------------------
// Tests: severity dot colors
// ---------------------------------------------------------------------------

describe("ButlerApprovalsTab -- severity dot rendering", () => {
  beforeEach(() => vi.resetAllMocks())
  afterEach(() => cleanup())

  it("renders a high-severity dot (data-severity=high) for an action expiring in 30 min", () => {
    setupWithActions([HIGH_ACTION])
    renderTab()
    const dots = screen.getAllByTestId("severity-dot")
    expect(dots[0].getAttribute("data-severity")).toBe("high")
  })

  it("renders a medium-severity dot (data-severity=medium) for an action expiring in 6h", () => {
    setupWithActions([MEDIUM_ACTION])
    renderTab()
    const dots = screen.getAllByTestId("severity-dot")
    expect(dots[0].getAttribute("data-severity")).toBe("medium")
  })

  it("renders a low-severity dot (data-severity=low) for an action with no expiry", () => {
    setupWithActions([LOW_ACTION])
    renderTab()
    const dots = screen.getAllByTestId("severity-dot")
    expect(dots[0].getAttribute("data-severity")).toBe("low")
  })

  it("severity dot data-severity values differ across high/medium/low actions", () => {
    setupWithActions([HIGH_ACTION, MEDIUM_ACTION, LOW_ACTION])
    renderTab()
    const dots = screen.getAllByTestId("severity-dot")
    const severities = dots.map((d) => d.getAttribute("data-severity"))
    expect(severities).toContain("high")
    expect(severities).toContain("medium")
    expect(severities).toContain("low")
  })

  it("high-severity dot has bg-destructive class", () => {
    setupWithActions([HIGH_ACTION])
    renderTab()
    const dot = screen.getAllByTestId("severity-dot")[0]
    expect(dot.className).toContain("bg-destructive")
  })

  it("medium-severity dot has bg-amber-500 class", () => {
    setupWithActions([MEDIUM_ACTION])
    renderTab()
    const dot = screen.getAllByTestId("severity-dot")[0]
    expect(dot.className).toContain("bg-amber-500")
  })

  it("low-severity dot has bg-muted-foreground class", () => {
    setupWithActions([LOW_ACTION])
    renderTab()
    const dot = screen.getAllByTestId("severity-dot")[0]
    expect(dot.className).toContain("bg-muted-foreground")
  })
})

// ---------------------------------------------------------------------------
// Tests: empty state
// ---------------------------------------------------------------------------

describe("ButlerApprovalsTab -- empty state", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    setupEmpty()
  })
  afterEach(() => cleanup())

  it("shows empty state text when there are no pending actions", () => {
    renderTab()
    expect(screen.getByTestId("approvals-empty")).toBeDefined()
  })

  it("empty state copy matches project voice (sentence case, no em-dashes)", () => {
    renderTab()
    const el = screen.getByTestId("approvals-empty")
    expect(el.textContent).toBe("No items pending review.")
  })

  it("does not render the approvals list when empty", () => {
    renderTab()
    expect(screen.queryByTestId("approvals-list")).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Tests: action link navigation
// ---------------------------------------------------------------------------

describe("ButlerApprovalsTab -- action link", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    setupWithActions([HIGH_ACTION, MEDIUM_ACTION])
  })
  afterEach(() => cleanup())

  it("renders an action link for each approval row", () => {
    renderTab()
    const links = screen.getAllByTestId("approval-action-link")
    expect(links.length).toBe(2)
  })

  it("action links navigate to /approvals", () => {
    renderTab()
    const links = screen.getAllByTestId("approval-action-link") as HTMLAnchorElement[]
    for (const link of links) {
      expect(link.getAttribute("href")).toBe("/approvals")
    }
  })
})

// ---------------------------------------------------------------------------
// Tests: butler-scoped filtering
// ---------------------------------------------------------------------------

describe("ButlerApprovalsTab -- butler-scoped filtering", () => {
  beforeEach(() => vi.resetAllMocks())
  afterEach(() => cleanup())

  it("calls useApprovalActions with the provided butlerName and status=pending", () => {
    setupWithActions([HIGH_ACTION])
    renderTab("finance")
    expect(vi.mocked(useApprovalActions)).toHaveBeenCalledWith(
      expect.objectContaining({ butler: "finance", status: "pending" }),
    )
  })

  it("calls useApprovalActions with a different butlerName when changed", () => {
    setupWithActions([])
    renderTab("general")
    expect(vi.mocked(useApprovalActions)).toHaveBeenCalledWith(
      expect.objectContaining({ butler: "general" }),
    )
  })

  it("renders only rows returned by the hook (server-side butler filter)", () => {
    // Hook returns two items (already butler-filtered by the server)
    setupWithActions([HIGH_ACTION, MEDIUM_ACTION])
    renderTab("finance")
    const rows = screen.getAllByTestId("approval-row")
    expect(rows.length).toBe(2)
  })
})

// ---------------------------------------------------------------------------
// Tests: loading state
// ---------------------------------------------------------------------------

describe("ButlerApprovalsTab -- loading state", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    setupLoading()
  })
  afterEach(() => cleanup())

  it("shows loading placeholder while fetching", () => {
    renderTab()
    expect(screen.getByTestId("approvals-loading")).toBeDefined()
  })

  it("does not show empty-state text while loading", () => {
    renderTab()
    expect(screen.queryByTestId("approvals-empty")).toBeNull()
  })

  it("does not render the approvals list while loading", () => {
    renderTab()
    expect(screen.queryByTestId("approvals-list")).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Tests: row content
// ---------------------------------------------------------------------------

describe("ButlerApprovalsTab -- row content", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    setupWithActions([HIGH_ACTION, LOW_ACTION])
  })
  afterEach(() => cleanup())

  it("renders a row for each action", () => {
    renderTab()
    const rows = screen.getAllByTestId("approval-row")
    expect(rows.length).toBe(2)
  })

  it("renders the tool_name as the row title", () => {
    renderTab()
    expect(screen.getByText("send_telegram")).toBeDefined()
    expect(screen.getByText("archive_email")).toBeDefined()
  })

  it("renders the agent_summary in the sub-line when present", () => {
    renderTab()
    expect(screen.getByText("Send message to owner")).toBeDefined()
  })

  it("falls back to short action ID when agent_summary is absent", () => {
    renderTab()
    // LOW_ACTION has agent_summary: null -- falls back to first 8 chars of id
    expect(screen.getByText(LOW_ACTION.id.slice(0, 8))).toBeDefined()
  })
})
