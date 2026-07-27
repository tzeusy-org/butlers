import { ESLint } from "eslint"
import { describe, expect, it } from "vitest"

describe("blue/purple status-color lint", () => {
  it("rejects raw blue, sky, and purple classes in an audited status file", async () => {
    const eslint = new ESLint()
    const [result] = await eslint.lintText(
      'export const statusClass = "border-blue-500 text-purple-600 bg-sky-500"\n',
      { filePath: "src/components/ingestion/StatusBadge.tsx" },
    )

    expect(result.messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          ruleId: "no-restricted-syntax",
          message: expect.stringContaining("Raw blue/purple Tailwind status classes are banned"),
        }),
      ]),
    )
  })

  it("allows a documented non-status exception in an audited file", async () => {
    const eslint = new ESLint()
    const [result] = await eslint.lintText(
      "// eslint-disable-next-line no-restricted-syntax -- fixed labeled category, not operational status\n" +
        'export const categoryClass = "bg-blue-500"\n',
      { filePath: "src/components/ingestion/StatusBadge.tsx" },
    )

    expect(result.messages.filter((message) => message.ruleId === "no-restricted-syntax")).toEqual([])
  })

  it("keeps the transitional guard scoped to audited files", async () => {
    const eslint = new ESLint()
    const [result] = await eslint.lintText(
      'export const categoryClass = "bg-blue-500"\n',
      { filePath: "src/components/unreviewed/CategoryLegend.tsx" },
    )

    expect(result.messages.filter((message) => message.ruleId === "no-restricted-syntax")).toEqual([])
  })
})
