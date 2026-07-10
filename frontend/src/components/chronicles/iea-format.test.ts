import { describe, expect, it } from "vitest"

import { deltaDirection, formatSeconds, formatSignedDelta } from "./iea-format"

describe("formatSeconds", () => {
  it("renders zero as 0m", () => {
    expect(formatSeconds(0)).toBe("0m")
  })

  it("renders sub-minute as seconds", () => {
    expect(formatSeconds(45)).toBe("45s")
  })

  it("renders minutes only under an hour", () => {
    expect(formatSeconds(25 * 60)).toBe("25m")
  })

  it("renders hours and padded minutes", () => {
    expect(formatSeconds(3600 + 5 * 60)).toBe("1h 05m")
  })

  it("renders whole hours without minutes", () => {
    expect(formatSeconds(2 * 3600)).toBe("2h")
  })

  it("clamps negatives to zero", () => {
    expect(formatSeconds(-100)).toBe("0m")
  })
})

describe("formatSignedDelta", () => {
  it("renders a positive delta with a plus", () => {
    expect(formatSignedDelta(3600)).toBe("+1h")
  })

  it("renders a negative delta with a real minus sign", () => {
    expect(formatSignedDelta(-45 * 60)).toBe("−45m")
  })

  it("renders sub-minute deltas as on par", () => {
    expect(formatSignedDelta(20)).toBe("on par")
    expect(formatSignedDelta(-20)).toBe("on par")
  })
})

describe("deltaDirection", () => {
  it("classifies up / down / flat", () => {
    expect(deltaDirection(3600)).toBe("up")
    expect(deltaDirection(-3600)).toBe("down")
    expect(deltaDirection(10)).toBe("flat")
  })
})
