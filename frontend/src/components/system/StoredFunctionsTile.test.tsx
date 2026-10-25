// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"
import type { ApiResponse, StoredFunctionEntry, StoredFunctionFacts } from "@/api/types"
import { StoredFunctionsTile } from "./StoredFunctionsTile"

type HookResult = Partial<{ isPending: boolean; isError: boolean; data: ApiResponse<StoredFunctionFacts> }>
let mockResult: HookResult = { isPending: false }
vi.mock("@/hooks/use-system", () => ({ useStoredFunctionFacts: () => ({
  ...mockResult, status: mockResult.isPending ? "pending" : mockResult.isError ? "error" : "success",
  fetchStatus: "idle", isPlaceholderData: false, refetch: vi.fn(),
}) }))
function fact(overrides: Partial<StoredFunctionEntry> = {}): StoredFunctionEntry {
  return { function: "public.fixed(uuid)", status: "conforming", expected_digest: "a".repeat(64),
    actual_digest: "b".repeat(64), owner_ok: true, settings_ok: true, acl_ok: true, ...overrides }
}
function show(overrides: Partial<StoredFunctionFacts> = {}) {
  mockResult = { data: { data: { checked_at: "2026-10-25T00:00:00Z", status: "conforming",
    catalog_check_available: true, objects_ok: true, functions: [fact()], unpinned_definers: [],
    definer_check_available: true, ...overrides }, meta: {} } }
  return renderToStaticMarkup(<StoredFunctionsTile />)
}
function render() { return renderToStaticMarkup(<StoredFunctionsTile />) }

describe("Database authority actual catalog facts", () => {
  it("renders skeleton while loading", () => {
    mockResult = { isPending: true }
    expect(render()).toContain("stored-functions-tile-skeleton")
  })
  it("never renders conformance or mismatch while loading", () => {
    mockResult = { isPending: true }
    expect(render()).not.toContain("stored-functions-tile-clean")
    expect(render()).not.toContain("stored-functions-tile-drifted")
    expect(render()).not.toContain("stored-functions-tile-unavailable")
  })
  it("renders query errors distinctly", () => {
    mockResult = { isError: true }
    expect(render()).toContain("stored-functions-tile-error")
  })
  it("never renders conformance on query error", () => {
    mockResult = { isError: true }
    expect(render()).not.toContain("stored-functions-tile-clean")
    expect(render()).not.toContain("stored-functions-tile-unavailable")
  })
  it("renders unavailable rather than conformance", () => {
    const html = show({ catalog_check_available: false })
    expect(html).toContain("stored-functions-tile-unavailable")
    expect(html).not.toContain("stored-functions-tile-clean")
  })
  it("explains unavailable authority without offering old bootstrap repair", () => {
    const html = show({ status: "unavailable", objects_ok: null })
    expect(html).toContain("not a clean bill of health")
    expect(html).not.toContain("init-db.sql")
  })
  it("renders current conforming overload count", () => {
    expect(show()).toContain("Catalog conforms to source (1 function overloads)")
    expect(show()).toContain("stored-functions-tile-clean-badge")
  })
  it("does not render mismatch when actual complete facts conform", () => {
    expect(show()).not.toContain("stored-functions-tile-drifted")
  })
  it("renders mismatches and the controlled maintenance remedy", () => {
    const html = show({ status: "mismatch", functions: [fact({ status: "mismatch" })] })
    expect(html).toContain("stored-functions-tile-drifted-badge")
    expect(html).toContain("controlled migrator before serving")
  })
  it("names exact source overloads instead of obsolete init line numbers", () => {
    const html = show({ status: "mismatch", functions: [fact({ status: "mismatch" })] })
    expect(html).toContain("public.fixed(uuid)")
    expect(html).not.toContain("init-db.sql")
  })
  it("distinguishes owner settings and privilege mismatch facts", () => {
    const html = show({ status: "mismatch", functions: [fact({ owner_ok: false, settings_ok: false, acl_ok: false })] })
    expect(html).toContain("owner mismatch")
    expect(html).toContain("settings mismatch")
    expect(html).toContain("privileges mismatch")
    expect(html).not.toContain("stored-functions-tile-clean-badge")
  })
  it("never renders actual or expected digests", () => {
    const html = show({ status: "mismatch", functions: [fact({ status: "mismatch" })] })
    expect(html).not.toContain("a".repeat(64))
    expect(html).not.toContain("b".repeat(64))
  })
  it("names missing required functions as an authority failure", () => {
    const html = show({ status: "mismatch", functions: [fact({ status: "missing", actual_digest: null })] })
    expect(html).toContain("stored-functions-tile-not-deployed-badge")
    expect(html).toContain("public.fixed(uuid): missing")
    expect(html).toContain("stored-functions-tile-drifted-badge")
  })
  it("cannot render a green badge for a contradictory missing function", () => {
    const html = show({ functions: [fact({ status: "missing" })] })
    expect(html).not.toContain("stored-functions-tile-clean-badge")
    expect(html).toContain("border-[var(--red)]/40")
  })
  it("retains both missing and mismatched overload facts", () => {
    const html = show({ status: "mismatch", functions: [fact({ status: "mismatch" }),
      fact({ function: "public.second()", status: "missing", actual_digest: null })] })
    expect(html).toContain("public.fixed(uuid)")
    expect(html).toContain("public.second(): missing")
  })
  it("preserves independent unpinned definer owner path and remedy", () => {
    const html = show({ unpinned_definers: [
      { function: "restore_drill_executor.is_due(integer)", owner: "restore_drill_executor_owner",
        search_path: "pg_catalog, public, pg_temp", remedy: "controlled_migrator" },
      { function: "public.second()", owner: "normal_owner", search_path: null, remedy: "controlled_migrator" },
    ] })
    expect(html).toContain("2 unpinned definers")
    expect(html).toContain("owner restore_drill_executor_owner")
    expect(html).toContain("search_path unset")
    expect(html).toContain("operator maintenance prerequisite")
    expect(html).toContain("stored-functions-tile-clean-badge")
  })
  it("preserves independent unavailable definer observation and clean absence", () => {
    const degraded = show({ definer_check_available: false })
    expect(degraded).toContain("stored-functions-tile-definer-unavailable")
    expect(degraded).not.toContain("stored-functions-tile-unpinned")
    const clean = show()
    expect(clean).not.toContain("stored-functions-tile-definer-unavailable")
    expect(clean).not.toContain("stored-functions-tile-unpinned")
  })
  it("requires positive object facts even if the status claims conformance", () => {
    for (const objects_ok of [false, null]) {
      expect(show({ objects_ok })).not.toContain("stored-functions-tile-clean-badge")
      expect(show({ objects_ok })).toContain("Object ownership or privileges do not conform")
    }
  })
  it("does not interpret an unknown status as conformance", () => {
    expect(show({ status: "unknown" })).toContain("stored-functions-tile-unavailable")
    expect(show({ status: "unknown" })).not.toContain("stored-functions-tile-clean-badge")
  })
})
