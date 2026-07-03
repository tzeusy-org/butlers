// ---------------------------------------------------------------------------
// session-buckets.test.ts
//
// Extracted from use-butler-status-board.test.ts (bu-86c4c.17): this utility
// is independent of the status-board hook (which now consumes the
// consolidated GET /api/butlers/board response directly), but is kept and
// tested standalone since ActivityStripe's alignment contract depends on it.
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"

import { bucketSessionsByHour } from "@/lib/session-buckets"

describe("bucketSessionsByHour", () => {
  it("returns an array of 24 zeroes when no sessions match", () => {
    const stripe = bucketSessionsByHour([], "my-butler")
    expect(stripe).toHaveLength(24)
    expect(stripe.every((v) => v === 0)).toBe(true)
  })

  it("counts sessions for the correct butler only", () => {
    const now = new Date()
    const oneHourAgo = new Date(now.getTime() - 60 * 60 * 1000)
    const sessions = [
      { butler: "my-butler", started_at: oneHourAgo.toISOString() },
      { butler: "other-butler", started_at: oneHourAgo.toISOString() },
    ]
    const stripe = bucketSessionsByHour(sessions, "my-butler")
    expect(stripe.reduce((s, v) => s + v, 0)).toBe(1)
  })

  it("places sessions in the correct hour slot (oldest=slot 0)", () => {
    const now = new Date()
    // UTC-floor to avoid edge-case issues with current hour boundary
    const windowEnd = Math.floor(now.getTime() / (3600 * 1000)) * (3600 * 1000) + 3600 * 1000
    const windowStart = windowEnd - 24 * 3600 * 1000

    // Session at slot 0 (oldest = first hour of the window)
    const slot0Time = new Date(windowStart + 1000) // 1 second into slot 0
    // Session at slot 23 (newest = last hour of the window)
    const slot23Time = new Date(windowEnd - 1000) // 1 second before window end

    const sessions = [
      { butler: "b", started_at: slot0Time.toISOString() },
      { butler: "b", started_at: slot23Time.toISOString() },
    ]
    const stripe = bucketSessionsByHour(sessions, "b", now)
    expect(stripe[0]).toBe(1)
    expect(stripe[23]).toBe(1)
    // All other slots are 0
    for (let i = 1; i < 23; i++) {
      expect(stripe[i]).toBe(0)
    }
  })

  it("ignores sessions outside the 24h window", () => {
    const now = new Date()
    const old = new Date(now.getTime() - 25 * 60 * 60 * 1000)
    const sessions = [{ butler: "b", started_at: old.toISOString() }]
    const stripe = bucketSessionsByHour(sessions, "b", now)
    expect(stripe.reduce((s, v) => s + v, 0)).toBe(0)
  })

  it("ignores sessions with unparseable started_at", () => {
    const sessions = [{ butler: "b", started_at: "not-a-date" }]
    const stripe = bucketSessionsByHour(sessions, "b")
    expect(stripe.every((v) => v === 0)).toBe(true)
  })
})
