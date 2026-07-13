// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// DeploymentTile tests -- bu-hmdqz.1
//
// Coverage:
//   - Loading state: skeleton rendered, no content
//   - Error state: error message rendered, no content
//   - Empty ledger (current=null): neutral "never recorded" notice
//   - Last deploy failed: red badge regardless of commits-behind
//   - commits_behind_available=false: "unavailable" notice, never a
//     fabricated all-clear
//   - commits_behind_main > 0: red "serving <sha>, N commits behind" clause
//   - commits_behind_main === 0: green "up to date" badge
// ---------------------------------------------------------------------------

import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import type { ApiResponse, DeploymentFacts } from "@/api/types"
import { DeploymentTile } from "./DeploymentTile"

type HookResult = Partial<{
  isPending: boolean
  isError: boolean
  data: ApiResponse<DeploymentFacts>
}>

let mockResult: HookResult = { isPending: false }

vi.mock("@/hooks/use-system", () => ({
  useDeploymentFacts: () => mockResult,
}))

vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => <time dateTime={value}>{value}</time>,
}))

function makeFacts(overrides: Partial<DeploymentFacts> = {}): ApiResponse<DeploymentFacts> {
  return {
    data: {
      current: {
        id: "11111111-1111-1111-1111-111111111111",
        git_sha: "abc1234def",
        migration_head: "core_163",
        started_at: "2026-07-12T00:00:00Z",
        finished_at: "2026-07-12T00:00:00Z",
        result: "success",
      },
      recent: [],
      commits_behind_main: 0,
      commits_behind_available: true,
      ...overrides,
    },
    meta: {},
  }
}

function render(): string {
  return renderToStaticMarkup(<DeploymentTile />)
}

describe("DeploymentTile -- loading state", () => {
  it("renders skeleton when isPending=true", () => {
    mockResult = { isPending: true }
    expect(render()).toContain("deployment-tile-skeleton")
  })

  it("does not render content while loading", () => {
    mockResult = { isPending: true }
    const html = render()
    expect(html).not.toContain("deployment-tile-content")
    expect(html).not.toContain("deployment-tile-empty")
  })
})

describe("DeploymentTile -- error state", () => {
  it("renders error message when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).toContain("deployment-tile-error")
  })
})

describe("DeploymentTile -- empty ledger", () => {
  it("renders a neutral 'never recorded' notice, not a fabricated clean state", () => {
    mockResult = { isPending: false, data: makeFacts({ current: null }) }
    const html = render()
    expect(html).toContain("deployment-tile-empty")
    expect(html).toContain("No deployment recorded yet")
    expect(html).not.toContain("deployment-tile-red-badge")
    expect(html).not.toContain("deployment-tile-clean-badge")
  })
})

describe("DeploymentTile -- last deploy failed", () => {
  it("renders the red badge with 'last deploy failed', ignoring commits-behind", () => {
    mockResult = {
      isPending: false,
      data: makeFacts({
        current: {
          id: "1",
          git_sha: "abc1234def",
          migration_head: null,
          started_at: "2026-07-12T00:00:00Z",
          finished_at: "2026-07-12T00:00:00Z",
          result: "failed",
        },
        commits_behind_main: 0,
        commits_behind_available: true,
      }),
    }
    const html = render()
    expect(html).toContain("deployment-tile-red-badge")
    expect(html).toContain("last deploy failed")
  })
})

describe("DeploymentTile -- commits behind unavailable", () => {
  it("renders 'unavailable' rather than a fabricated up-to-date badge", () => {
    mockResult = {
      isPending: false,
      data: makeFacts({ commits_behind_main: null, commits_behind_available: false }),
    }
    const html = render()
    expect(html).toContain("deployment-tile-commits-unknown")
    expect(html).not.toContain("deployment-tile-clean-badge")
    expect(html).not.toContain("deployment-tile-red-badge")
  })
})

describe("DeploymentTile -- commits behind origin/main", () => {
  it("renders the red clause with sha and count when behind", () => {
    mockResult = {
      isPending: false,
      data: makeFacts({ commits_behind_main: 16, commits_behind_available: true }),
    }
    const html = render()
    expect(html).toContain("deployment-tile-red-badge")
    expect(html).toContain("serving abc1234, 16 commits behind origin/main")
  })

  it("uses singular 'commit' for exactly one", () => {
    mockResult = {
      isPending: false,
      data: makeFacts({ commits_behind_main: 1, commits_behind_available: true }),
    }
    const html = render()
    expect(html).toContain("1 commit behind origin/main")
    expect(html).not.toContain("1 commits behind")
  })
})

describe("DeploymentTile -- up to date", () => {
  it("renders the green badge when commits_behind_main is 0", () => {
    mockResult = {
      isPending: false,
      data: makeFacts({ commits_behind_main: 0, commits_behind_available: true }),
    }
    const html = render()
    expect(html).toContain("deployment-tile-clean-badge")
    expect(html).toContain("up to date with origin/main")
    expect(html).not.toContain("deployment-tile-red-badge")
  })

  it("always renders the serving sha and migration head", () => {
    mockResult = { isPending: false, data: makeFacts() }
    const html = render()
    expect(html).toContain("abc1234")
    expect(html).toContain("core_163")
  })
})

describe("DeploymentTile -- migration head unknown (bu-l94um)", () => {
  it("renders migration_head=null as an explicit unknown, never a calm value", () => {
    mockResult = {
      isPending: false,
      data: makeFacts({
        current: {
          id: "1",
          git_sha: "abc1234def",
          migration_head: null,
          started_at: "2026-07-12T00:00:00Z",
          finished_at: "2026-07-12T00:00:00Z",
          result: "success",
        },
      }),
    }
    const html = render()
    expect(html).toContain("deployment-tile-migration-head-unknown")
    expect(html).toContain("head unknown")
    // The amber emphasis distinguishes it from a real head, never blank/calm.
    expect(html).toContain("--amber-text")
  })

  it("does not render the unknown state when a real head is present", () => {
    mockResult = { isPending: false, data: makeFacts() }
    const html = render()
    expect(html).not.toContain("deployment-tile-migration-head-unknown")
  })
})
