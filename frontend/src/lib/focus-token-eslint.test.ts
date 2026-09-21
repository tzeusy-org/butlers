import { ESLint } from "eslint"
import { describe, expect, it } from "vitest"

async function restrictedSyntaxMessages(source: string) {
  const eslint = new ESLint()
  const [result] = await eslint.lintText(source, {
    filePath: "src/components/example/FocusFixture.tsx",
  })
  return result.messages.filter((message) => message.ruleId === "no-restricted-syntax")
}

describe("focus-token lint", () => {
  it("rejects the legacy low-contrast ring vocabulary repo-wide", async () => {
    const messages = await restrictedSyntaxMessages(
      'export const className = "focus-visible:ring-' + 'ring/50"\n',
    )

    expect(messages).toEqual([
      expect.objectContaining({ message: expect.stringContaining("below the WCAG 1.4.11") }),
    ])
  })

  it("rejects an unguarded outline reset repo-wide", async () => {
    const messages = await restrictedSyntaxMessages('export const className = "outline-' + 'none"\n')

    expect(messages).toEqual([
      expect.objectContaining({ message: expect.stringContaining("no local replacement") }),
    ])
  })

  it("allows an outline reset paired with the measured focus token", async () => {
    const messages = await restrictedSyntaxMessages(
      'export const className = "outline-' + 'none focus-visible:ring-focus"\n',
    )

    expect(messages).toEqual([])
  })

  it.each(["ring-0", "ring-transparent", "ring-destructive"])(
    "rejects %s as an invalid outline-reset replacement",
    async (replacement) => {
      const outlineReset = "outline-" + "none"
      const messages = await restrictedSyntaxMessages(
        `export const className = "${outlineReset} focus-visible:${replacement}"\n`,
      )

      expect(messages).toEqual([
        expect.objectContaining({ message: expect.stringContaining("no local replacement") }),
      ])
    },
  )
})
