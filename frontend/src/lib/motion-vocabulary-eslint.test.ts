import { ESLint } from "eslint"
import { beforeAll, describe, expect, it } from "vitest"

const eslint = new ESLint()
// Resolve the shared parser/config once as suite setup, rather than charging
// the first table's unchanged five-second item bound for cold initialization.
beforeAll(async () => { await eslint.calculateConfigForFile("src/components/example/MotionFixture.tsx") })
const motion = (result: ESLint.LintResult) => result.messages.filter(
  (message) => message.ruleId === "no-restricted-syntax" && message.message.startsWith("Motion Vocabulary:"),
)
const classToken = (suffix: string) => "transition-" + suffix
const duration = (value: string) => "duration-[" + value + "ms]"
const bad = [classToken("all"), "trans" + "ition", "animate-" + "bounce", ...["fast", "base", "slow"].map(classToken)]
async function check(tokens: string | string[], filePath = "src/components/example/MotionFixture.tsx") {
  const values = typeof tokens === "string" ? [tokens] : tokens
  for (const quote of ['"', '`']) {
    const source = values.map((token, index) => `export const fixture${index} = ${quote}${token}${quote}`).join("\n")
    const [result] = await eslint.lintText(source, { filePath })
    expect(result.fatalErrorCount).toBe(0)
    expect(motion(result).map((message) => message.line), `${filePath}: ${quote}`).toEqual(
      values.map((_, index) => index + 1),
    )
  }
}
async function allow(tokens: string[]) {
  for (const quote of ['"', '`']) {
    const source = tokens.map((token, index) => `export const fixture${index} = ${quote}${token}${quote}`).join("\n")
    const [result] = await eslint.lintText(source, { filePath: "src/components/example/MotionFixture.tsx" })
    expect(result.fatalErrorCount).toBe(0)
    expect(motion(result)).toEqual([])
  }
}

describe("Motion Vocabulary lint", () => {
  it("rejects forbidden whole class tokens and their variants without matching allowed substrings", async () => {
    await check(bad.flatMap((token) => [token, `hover:${token}`, `data-[state=open]:${token}`, `!${token}`, `${token}!`]))
    await allow(["transition-colors", "transition-opacity", "transition-transform", `my-${bad[0]}`, `${bad[0]}oy`, "some-animate-bounce-marker", "transition-fastest"])
  })

  it("rejects every layout property in an arbitrary list while retaining exact paint properties", async () => {
    await check(["width", "height", "max-height", "margin", "padding", "top", "left", "right", "bottom", "inset"].flatMap((property) => [
      classToken(`[${property}]`), `hover:${classToken(`[color,${property},opacity]`)}`, classToken(`[opacity_${property}]`),
    ]))
    await allow(["color,box-shadow", "color,background-color,border-color,fill,stroke,opacity,box-shadow", "opacity", "transform", "border-width"].map((properties) => classToken(`[${properties}]`)))
  })

  it("retains timing tiers and flags off-tier arbitrary milliseconds", async () => {
    await check(["0", "100", "120", "300", "500", "150.5", "-120", "+120"].map(duration))
    await allow([...["150", "200", "250"].map(duration), "duration-fast", "duration-base", "duration-slow", "duration-150", "duration-200", "duration-250"])
  })

  it("keeps named exceptions and easing while limiting a reasoned chevron exemption to one line", async () => {
    await allow(["animate-in", "animate-out", "animate-spin", "ease-in-out", "ease-linear", "ease-out-quart"])
    const source = [
      'export const chevron =',
      '// eslint-disable-next-line no-restricted-syntax -- Named Sidebar chevron: 120ms linear rotation.',
      '`transition-transform ' + duration("120") + ' ease-linear`',
      'export const unrelated = "' + duration("120") + '"',
    ].join('\n')
    const [result] = await eslint.lintText(source, { filePath: "src/components/layout/Sidebar.tsx" })
    expect(motion(result)).toEqual([expect.objectContaining({ line: 4 })])
    expect(result.messages.filter((message) => message.message.includes("Unused eslint-disable"))).toEqual([])
  })

  it("covers every effective flat-config override and detects removed family or representation controls", async () => {
    const paths = [
      "src/lib/example.ts", "src/pages/SomeOtherPage.tsx", "src/components/ui/button.tsx",
      "src/components/ui/ButlerMark.tsx", "src/components/secrets/passport/atoms.tsx",
      "src/pages/ApprovalsPage.tsx", "src/pages/SettingsConsolePage.tsx",
      "src/hooks/use-keyboard-shortcuts.ts", "src/hooks/use-register-shortcut.tsx",
      "src/components/ui/StateDot.tsx", "src/components/topology/TopologyGraph.tsx",
      "src/components/approvals/approval-teaching-digest.tsx", "src/components/approvals/autonomy-panel.tsx",
      "src/components/approvals/autonomy-suggestions-banner.tsx", "src/components/approvals/rule-promotion-banner.tsx",
      "src/components/approvals/rule-promotion-stats.tsx", "src/components/butler-detail/ButlerQaInvestigationsTab.tsx",
      "src/components/education/CurriculumActions.tsx", "src/components/education/QuizHistoryList.tsx",
      "src/components/ingestion/StatusBadge.tsx", "src/components/ingestion/TimelineTab.tsx",
      "src/components/ingestion/timeline/HourFlameStrip.tsx", "src/components/qa/PRPanel.tsx",
      "src/components/relationship/ContactChannelCard.tsx", "src/components/schedules/ScheduleTable.tsx",
      "src/components/timeline/TimelineLedger.tsx",
    ]
    for (const path of paths) await check(bad[0], path)
    const filePath = paths[1]
    const config = await eslint.calculateConfigForFile(filePath)
    const rule = config.rules["no-restricted-syntax"] as [string, ...Array<{ selector: string; message: string }>]
    for (const removed of ["all", "template"]) {
      const survivors = rule.slice(1).filter((entry) => {
        const selector = entry as { selector: string; message: string }
        return !selector.message.startsWith("Motion Vocabulary:") || (removed === "template" && !selector.selector.includes("TemplateElement"))
      })
      const mutant = new ESLint({ overrideConfig: { rules: { "no-restricted-syntax": ["error", ...survivors] } } })
      const [result] = await mutant.lintText('export const fixture = `' + bad[0] + '`', { filePath })
      expect(motion(result)).toEqual([])
    }
  })

  it("decodes static strings and templates and handles complete tokens beside dynamic expressions honestly", async () => {
    for (const quote of ['"', '`']) {
      const [result] = await eslint.lintText('export const fixture = ' + quote + 'transition\\u002dall' + quote, { filePath: "src/lib/example.ts" })
      expect(motion(result)).toHaveLength(1)
    }
    for (const source of [
      'const suffix = "-colors"; export const fixture = `transition${suffix}`',
      'const prefix = "my-"; export const fixture = `${prefix}transition-all`',
      'const n = 150; export const fixture = `duration-[${n}ms]`',
    ]) {
      const [result] = await eslint.lintText(source, { filePath: "src/lib/example.ts" })
      expect(motion(result)).toEqual([])
    }
    for (const source of [
      'const state = ""; export const fixture = `' + bad[0] + ' ${state}`',
      'const state = ""; export const fixture = `x ${state} ' + bad[0] + ' y`',
    ]) {
      const [result] = await eslint.lintText(source, { filePath: "src/lib/example.ts" })
      expect(motion(result)).toHaveLength(1)
    }
  })
})
