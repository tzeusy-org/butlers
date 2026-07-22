import { describe, expect, it } from "vitest"

import { formatCostDate, utcDateWindow } from "./use-spend"

describe("utcDateWindow", () => {
  it("uses the ledger UTC day rather than the owner-local day at rollover", () => {
    const window = utcDateWindow(7, new Date("2026-07-31T18:00:00.000Z"))

    expect(window.from.toISOString()).toBe("2026-07-25T00:00:00.000Z")
    expect(window.to.toISOString()).toBe("2026-07-31T23:59:59.999Z")
    expect(formatCostDate(window.from, "UTC")).toBe("2026-07-25")
    expect(formatCostDate(window.to, "UTC")).toBe("2026-07-31")
  })
})
