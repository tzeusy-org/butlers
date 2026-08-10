// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// BackupTile tests -- bu-ngfzz.6
//
// Coverage:
//   - Loading state: skeleton rendered, no content
//   - Error state: error message rendered, no content
//   - Unreachable source: graceful unavailable notice (not an error state)
//   - Reachable with last_backup_at: Time rendered with the timestamp
//   - Reachable without last_backup_at: "Never run" fallback
// ---------------------------------------------------------------------------

import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import type { ApiResponse, BackupFacts } from "@/api/types"
import { BackupTile } from "./BackupTile"

// ---------------------------------------------------------------------------
// Mock useBackupFacts
// ---------------------------------------------------------------------------

type HookResult = Partial<{
  isPending: boolean
  isError: boolean
  data: ApiResponse<BackupFacts>
}>

let mockResult: HookResult = { isPending: false }

vi.mock("@/hooks/use-system", () => ({
  useBackupFacts: () => mockResult,
}))

// ---------------------------------------------------------------------------
// Mock <Time> to sidestep date-fns-tz / ChroniclesTimezoneProvider
// ---------------------------------------------------------------------------

vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => (
    <time dateTime={value}>{value}</time>
  ),
}))

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeBackupFacts(overrides: Partial<BackupFacts> = {}): ApiResponse<BackupFacts> {
  return {
    data: {
      last_backup_at: "2026-05-01T02:00:00Z",
      last_backup_size_bytes: 1048576,
      backup_source_reachable: true,
      backup_history: [],
      ...overrides,
    },
    meta: {},
  }
}

function render(): string {
  return renderToStaticMarkup(<BackupTile />)
}

// ---------------------------------------------------------------------------
// 1. Loading state
// ---------------------------------------------------------------------------

describe("BackupTile -- loading state", () => {
  it("renders skeleton when isPending=true", () => {
    mockResult = { isPending: true }
    expect(render()).toContain("backup-tile-skeleton")
  })

  it("does not render content while loading", () => {
    mockResult = { isPending: true }
    const html = render()
    expect(html).not.toContain("backup-tile-content")
    expect(html).not.toContain("backup-tile-unavailable")
  })
})

// ---------------------------------------------------------------------------
// 2. Error state
// ---------------------------------------------------------------------------

describe("BackupTile -- error state", () => {
  it("renders error message when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).toContain("backup-tile-error")
  })

  it("renders error text when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).toContain("Could not load backup facts")
  })

  it("does not render content or unavailable state when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    const html = render()
    expect(html).not.toContain("backup-tile-content")
    expect(html).not.toContain("backup-tile-unavailable")
  })
})

// ---------------------------------------------------------------------------
// 3. Graceful unreachable state (backup_source_reachable === false)
// ---------------------------------------------------------------------------

describe("BackupTile -- backup source unreachable", () => {
  it("renders unavailable state when backup_source_reachable is false", () => {
    mockResult = {
      isPending: false,
      data: makeBackupFacts({ backup_source_reachable: false }),
    }
    expect(render()).toContain("backup-tile-unavailable")
  })

  it("shows 'Backup status unavailable' text", () => {
    mockResult = {
      isPending: false,
      data: makeBackupFacts({ backup_source_reachable: false }),
    }
    expect(render()).toContain("Backup status unavailable")
  })

  it("does not render error state when source is unreachable", () => {
    mockResult = {
      isPending: false,
      data: makeBackupFacts({ backup_source_reachable: false }),
    }
    expect(render()).not.toContain("backup-tile-error")
  })

  it("does not render content tile when source is unreachable", () => {
    mockResult = {
      isPending: false,
      data: makeBackupFacts({ backup_source_reachable: false }),
    }
    expect(render()).not.toContain("backup-tile-content")
  })
})

// ---------------------------------------------------------------------------
// 4. Reachable with last_backup_at
// ---------------------------------------------------------------------------

describe("BackupTile -- reachable with last_backup_at", () => {
  it("renders content container", () => {
    mockResult = { isPending: false, data: makeBackupFacts() }
    expect(render()).toContain("backup-tile-content")
  })

  it("renders a status badge", () => {
    mockResult = { isPending: false, data: makeBackupFacts() }
    expect(render()).toContain("backup-tile-status-badge")
  })

  it("renders the last_backup_at timestamp via <Time>", () => {
    mockResult = {
      isPending: false,
      data: makeBackupFacts({ last_backup_at: "2026-05-01T02:00:00Z" }),
    }
    expect(render()).toContain("2026-05-01T02:00:00Z")
  })

  it("does not show 'Never run' when last_backup_at is set", () => {
    mockResult = {
      isPending: false,
      data: makeBackupFacts({ last_backup_at: "2026-05-01T02:00:00Z" }),
    }
    expect(render()).not.toContain("Never run")
  })
})

// ---------------------------------------------------------------------------
// 5. Reachable without last_backup_at (never run)
// ---------------------------------------------------------------------------

describe("BackupTile -- reachable without last_backup_at", () => {
  it("renders 'Never run' when last_backup_at is null", () => {
    mockResult = {
      isPending: false,
      data: makeBackupFacts({ last_backup_at: null }),
    }
    expect(render()).toContain("Never run")
  })

  it("does not render a <time> element when last_backup_at is null", () => {
    mockResult = {
      isPending: false,
      data: makeBackupFacts({ last_backup_at: null }),
    }
    expect(render()).not.toContain("<time")
  })
})

// ---------------------------------------------------------------------------
// 6. Verified status badge (bu-9r3hd.5) -- never fabricates a green "Healthy"
// ---------------------------------------------------------------------------

describe("BackupTile -- verified status badge (bu-9r3hd.5)", () => {
  it("renders 'Healthy' when the artifact verified healthy and isn't stale", () => {
    mockResult = {
      isPending: false,
      data: makeBackupFacts({ last_backup_status: "healthy", backup_stale: false }),
    }
    expect(render()).toContain("Healthy")
  })

  it("renders 'Corrupt' when the artifact failed integrity verification", () => {
    mockResult = {
      isPending: false,
      data: makeBackupFacts({ last_backup_status: "corrupt" }),
    }
    const html = render()
    expect(html).toContain("Corrupt")
    expect(html).not.toContain(">Healthy<")
  })

  it("renders 'Empty' when the artifact is below the size floor", () => {
    mockResult = {
      isPending: false,
      data: makeBackupFacts({ last_backup_status: "empty" }),
    }
    expect(render()).toContain("Empty")
  })

  it("renders 'Stale' when the artifact is healthy but past the age threshold", () => {
    mockResult = {
      isPending: false,
      data: makeBackupFacts({ last_backup_status: "healthy", backup_stale: true }),
    }
    expect(render()).toContain("Stale")
  })

  it("renders 'Unverified' rather than a fabricated 'Healthy' when status fields are absent", () => {
    mockResult = {
      isPending: false,
      data: makeBackupFacts({ last_backup_status: undefined, backup_stale: undefined }),
    }
    const html = render()
    expect(html).toContain("Unverified")
    expect(html).not.toContain(">Healthy<")
  })
})

// ---------------------------------------------------------------------------
// 7. Restore drill row (bu-9r3hd.5)
// ---------------------------------------------------------------------------

describe("BackupTile -- restore drill row (bu-9r3hd.5)", () => {
  it("renders 'No drill yet' when restore_drill is absent", () => {
    mockResult = {
      isPending: false,
      data: makeBackupFacts({ restore_drill: undefined }),
    }
    expect(render()).toContain("backup-tile-drill-pending")
  })

  it("renders 'No drill yet' when restore_drill.result is 'pending'", () => {
    mockResult = {
      isPending: false,
      data: makeBackupFacts({
        restore_drill: { checked_at: null, result: "pending", detail: null },
      }),
    }
    expect(render()).toContain("backup-tile-drill-pending")
  })

  it("renders a pass row when restore_drill.result is 'pass'", () => {
    mockResult = {
      isPending: false,
      data: makeBackupFacts({
        restore_drill: {
          checked_at: "2026-07-04T02:00:00Z",
          result: "pass",
          detail: "restored 12 tables",
        },
      }),
    }
    const html = render()
    expect(html).toContain("backup-tile-drill-pass")
    expect(html).toContain("Passed")
  })

  it("renders a problem row with detail when restore_drill.result is 'fail'", () => {
    mockResult = {
      isPending: false,
      data: makeBackupFacts({
        restore_drill: {
          checked_at: "2026-07-04T02:00:00Z",
          result: "fail",
          detail: "restore failed: relation already exists",
        },
      }),
    }
    const html = render()
    expect(html).toContain("backup-tile-drill-problem")
    expect(html).toContain("Failed")
    expect(html).toContain("relation already exists")
  })

  it("renders a problem row when restore_drill.result is 'degraded'", () => {
    mockResult = {
      isPending: false,
      data: makeBackupFacts({
        restore_drill: { checked_at: null, result: "degraded", detail: "pool unavailable" },
      }),
    }
    const html = render()
    expect(html).toContain("backup-tile-drill-problem")
    expect(html).toContain("Unavailable")
  })

  it("withholds an unexpected degraded diagnostic from the UI", () => {
    const marker = "backup-tile-private-marker"
    mockResult = {
      isPending: false,
      data: makeBackupFacts({
        restore_drill: {
          checked_at: null,
          result: "degraded",
          detail: `postgresql://restore:${marker}@db.example.test/postgres`,
        },
      }),
    }

    const html = render()
    expect(html).toContain("restore drill ledger unavailable")
    expect(html).not.toContain(marker)
  })
})
