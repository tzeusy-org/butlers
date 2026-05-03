// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// DbSizeTile tests -- bu-ngfzz.5
//
// Coverage:
//   - Loading state: skeleton rendered, no content
//   - Error state: error message rendered, no content
//   - Happy path: total size humanized; schema breakdown rendered
//   - Happy path: empty schemas array -- no breakdown section
//   - humanizeBytes: unit tests for the helper function
// ---------------------------------------------------------------------------

import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import type { ApiResponse } from "@/api/types"
import type { DatabaseFacts, SchemaSize } from "@/api/types"
import { DbSizeTile } from "./DbSizeTile"
import { humanizeBytes } from "./db-size-utils"

// ---------------------------------------------------------------------------
// Mock useDatabaseFacts
// ---------------------------------------------------------------------------

type HookResult = Partial<{
  isPending: boolean
  isError: boolean
  data: ApiResponse<DatabaseFacts>
}>

let mockResult: HookResult = { isPending: false }

vi.mock("@/hooks/use-system", () => ({
  useInstanceFacts: () => ({ isPending: false }),
  useDatabaseFacts: () => mockResult,
}))

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeSchema(name: string, sizeBytes: number): SchemaSize {
  return { schema_name: name, size_bytes: sizeBytes, table_count: 5 }
}

function makeDatabaseFacts(
  overrides: Partial<DatabaseFacts> = {},
): ApiResponse<DatabaseFacts> {
  return {
    data: {
      total_size_bytes: 1_400_000_000, // 1.3 GB
      schemas: [
        makeSchema("general", 800_000_000),
        makeSchema("memory", 400_000_000),
        makeSchema("switchboard", 200_000_000),
      ],
      largest_tables: [],
      growth_rate_bytes_per_day: null,
      ...overrides,
    },
    meta: {},
  }
}

function render(): string {
  return renderToStaticMarkup(<DbSizeTile />)
}

// ---------------------------------------------------------------------------
// 1. humanizeBytes helper
// ---------------------------------------------------------------------------

describe("humanizeBytes", () => {
  it("formats 0 bytes as '0 B'", () => {
    expect(humanizeBytes(0)).toBe("0 B")
  })

  it("formats sub-kilobyte values as '... B'", () => {
    expect(humanizeBytes(512)).toBe("512 B")
  })

  it("formats kilobyte values as '... KB'", () => {
    expect(humanizeBytes(1_024)).toBe("1.0 KB")
    expect(humanizeBytes(1_536)).toBe("1.5 KB")
  })

  it("formats megabyte values as '... MB'", () => {
    expect(humanizeBytes(1_048_576)).toBe("1.0 MB")
    expect(humanizeBytes(42_700_000)).toBe("40.7 MB")
  })

  it("formats gigabyte values as '... GB'", () => {
    expect(humanizeBytes(1_400_000_000)).toBe("1.3 GB")
    expect(humanizeBytes(10_737_418_240)).toBe("10.0 GB")
  })
})

// ---------------------------------------------------------------------------
// 2. Loading state
// ---------------------------------------------------------------------------

describe("DbSizeTile -- loading state", () => {
  it("renders skeleton when isPending=true", () => {
    mockResult = { isPending: true }
    expect(render()).toContain("db-size-tile-skeleton")
  })

  it("does not render content while loading", () => {
    mockResult = { isPending: true }
    expect(render()).not.toContain("db-size-tile-content")
  })
})

// ---------------------------------------------------------------------------
// 3. Error state
// ---------------------------------------------------------------------------

describe("DbSizeTile -- error state", () => {
  it("renders error element when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).toContain("db-size-tile-error")
  })

  it("renders error text when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).toContain("Could not load database size")
  })

  it("does not render content when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).not.toContain("db-size-tile-content")
  })
})

// ---------------------------------------------------------------------------
// 4. Happy path
// ---------------------------------------------------------------------------

describe("DbSizeTile -- happy path", () => {
  it("renders the content container", () => {
    mockResult = { isPending: false, data: makeDatabaseFacts() }
    expect(render()).toContain("db-size-tile-content")
  })

  it("renders humanized total size", () => {
    mockResult = {
      isPending: false,
      data: makeDatabaseFacts({ total_size_bytes: 1_400_000_000 }),
    }
    // 1.3 GB
    expect(render()).toContain("1.3 GB")
  })

  it("renders schema names in the breakdown", () => {
    mockResult = { isPending: false, data: makeDatabaseFacts() }
    const html = render()
    expect(html).toContain("general")
    expect(html).toContain("memory")
    expect(html).toContain("switchboard")
  })

  it("renders no breakdown section when schemas list is empty", () => {
    mockResult = {
      isPending: false,
      data: makeDatabaseFacts({ schemas: [] }),
    }
    const html = render()
    expect(html).toContain("db-size-tile-content")
    expect(html).not.toContain("Largest schemas")
  })

  it("limits breakdown to top 5 schemas", () => {
    const schemas = Array.from({ length: 8 }, (_, i) =>
      makeSchema(`schema_${i}`, 100_000 * (8 - i)),
    )
    mockResult = { isPending: false, data: makeDatabaseFacts({ schemas }) }
    const html = render()
    // schemas 0-4 should appear; schema_5 through schema_7 should not
    expect(html).toContain("schema_0")
    expect(html).toContain("schema_4")
    expect(html).not.toContain("schema_5")
    expect(html).not.toContain("schema_7")
  })
})
