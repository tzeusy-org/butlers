import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

async function lint(source: string, filePath: string) {
  const [result] = await new ESLint().lintText(source, { filePath });
  expect(result.fatalErrorCount, `${filePath}: ${source}`).toBe(0);
  return result.messages;
}

const TREND_RULE = "trend-chart/no-raw-import";
const CONSUMER_PATHS = ["src/lib/chart-shim.ts", "src/components/health/SomeChart.tsx"];

function expectRawImport(messages: ESLint.LintResult["messages"]) {
  expect(messages.filter((message) => message.ruleId === TREND_RULE)).toEqual([
    expect.objectContaining({
      severity: 2,
      message: expect.stringContaining("TimeSeriesChart"),
    }),
  ]);
}

describe("trend chart grammar lint", { timeout: 60_000 }, () => {
  it("rejects a smoothed monotone curve outside TimeSeriesChart", async () => {
    const messages = await lint(
      'export const A = () => <Line type="monotone" dataKey="v" />;\n',
      "src/components/health/SomeChart.tsx",
    );
    expect(messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ message: expect.stringContaining("Smoothed curve types") }),
      ]),
    );
  });

  it("rejects importing a raw recharts LineChart for a trend", async () => {
    const sources = [
      'import { LineChart } from "recharts";\nexport const A = LineChart;\n',
      'import { AreaChart as Trend } from "recharts"; export const A = Trend;',
      'import { BarChart, LineChart as Trend } from "recharts"; export const A = [BarChart, Trend];',
      'import { LineChart, AreaChart } from "recharts"; export const A = [LineChart, AreaChart];',
      'import {\n/* original name matters */ LineChart as Trend\n} from "recharts"; export const A = Trend;',
      'import { "LineChart" as Trend } from "recharts"; export const A = Trend;',
      'import type { LineChart as Trend } from "recharts"; export type A = Trend;',
    ];
    for (const filePath of CONSUMER_PATHS) {
      for (const source of sources) expectRawImport(await lint(source, filePath));
    }
  });

  it("rejects namespace imports without shadowing the Secrets SDK rule", async () => {
    for (const filePath of CONSUMER_PATHS) {
      for (const source of [
        'import * as R from "recharts"; export const A = R.LineChart;',
        'import * as Anything from "recharts";',
        'import type * as Charts from "recharts"; export type A = typeof Charts;',
      ]) expectRawImport(await lint(source, filePath));
    }
    for (const filePath of [
      "src/pages/Secrets/Widget.tsx",
      "src/pages/SecretsPage.tsx",
      "src/components/secrets/passport-atoms.tsx",
    ]) {
      const messages = await lint(
        'import * as R from "recharts"; import Anthropic from "@anthropic-ai/sdk"; export const A = [R, Anthropic];',
        filePath,
      );
      expectRawImport(messages);
      expect(messages.filter((message) => message.ruleId === "no-restricted-imports")).toEqual([
        expect.objectContaining({ message: expect.stringContaining("LLM SDK imports are forbidden") }),
      ]);
    }
  });

  it("rejects named, wildcard and namespace re-exports by their original names", async () => {
    for (const filePath of CONSUMER_PATHS) {
      for (const source of [
        'export { LineChart as Trend } from "recharts";',
        'export { AreaChart } from "recharts";',
        'export { BarChart, AreaChart as Trend } from "recharts";',
        'export { LineChart, AreaChart } from "recharts";',
        'export {\n/* outward alias is irrelevant */ AreaChart as Trend\n} from "recharts";',
        'export { "LineChart" as Trend } from "recharts";',
        'export type { LineChart as Trend } from "recharts";',
        'export * from "recharts";',
        'export * as Charts from "recharts";',
      ]) expectRawImport(await lint(source, filePath));
    }
  });

  it("rejects literal and static-template dynamic imports independently of consumption", async () => {
    for (const filePath of CONSUMER_PATHS) {
      for (const source of [
        'export const A = import("recharts");',
        "export const A = import('recharts');",
        'export async function load() { return await import("recharts"); }',
        'export const A = import("recharts").then((R) => R.LineChart);',
        'export async function load() { const { AreaChart } = await import("recharts"); return AreaChart; }',
        'export const A = (await import("recharts")).LineChart;',
        'export const A = import(`recharts`);',
        'export const A = import("re\\u0063harts");',
        'export const A = import(/* statically known */ "recharts");',
      ]) expectRawImport(await lint(source, filePath));
    }
    const source = 'export const A = import("recharts");';
    expect(await lint(source, CONSUMER_PATHS[0])).toEqual(await lint(source, CONSUMER_PATHS[0]));

    const [malformed] = await new ESLint().lintText('export const A = import("recharts";', {
      filePath: CONSUMER_PATHS[0],
    });
    expect(malformed.fatalErrorCount).toBe(1);
    expect(malformed.messages).toEqual([expect.objectContaining({ fatal: true, ruleId: null })]);
    expect(malformed.messages.filter((message) => message.ruleId === TREND_RULE)).toEqual([]);
  });

  it("allows the canonical primitive to own the raw chart", async () => {
    const rawSources = [
      'import { LineChart } from "recharts";\nexport const A = LineChart;\n',
      'import { AreaChart as Trend } from "recharts"; export const A = Trend;',
      'import * as R from "recharts"; export const A = R;',
      'import type * as R from "recharts"; export type A = typeof R;',
      'export { LineChart as Trend } from "recharts";',
      'export * from "recharts";',
      'export * as Charts from "recharts";',
      'export const A = import("recharts");',
      'export const A = import(`recharts`);',
    ];
    for (const filePath of ["src/components/ui/chart-shim.ts", "src/components/ui/TimeSeriesChart.tsx"]) {
      for (const source of rawSources) {
        const messages = await lint(source, filePath);
        expect(messages.filter((message) => message.ruleId === TREND_RULE)).toEqual([]);
        expect(messages.filter((message) => message.message.includes("TimeSeriesChart"))).toEqual([]);
      }
    }
    const smoothing = await lint(
      'export const A = () => <Line type="monotone" dataKey="v" />;\n',
      "src/components/ui/TimeSeriesChart.tsx",
    );
    expect(smoothing.filter((message) => message.message.includes("Smoothed curve types"))).toEqual([]);
    const accessibility = await lint(
      'export const A = () => <img src="/chart.svg" />;',
      "src/components/ui/TimeSeriesChart.tsx",
    );
    expect(accessibility.filter((message) => message.ruleId === "jsx-a11y/alt-text")).toEqual([
      expect.objectContaining({ severity: 2 }),
    ]);

    for (const source of [
      'import { BarChart, PieChart, ComposedChart, Tooltip } from "recharts"; export const A = [BarChart, PieChart, ComposedChart, Tooltip];',
      'export { BarChart as Bars, Tooltip } from "recharts";',
      'import { BarChart as LineChart } from "recharts"; export const A = LineChart;',
      'export { BarChart as LineChart } from "recharts";',
      'import type { TooltipProps } from "recharts"; export type A = TooltipProps;',
      'import * as R from "other-charts"; export const A = R;',
      'export * from "other-charts";',
      'export * as Charts from "other-charts";',
      'export const A = import("other-charts");',
      'export const A = import(`other-charts`);',
      'export const A = import("recharts/tools");',
      '// import * as R from "recharts";\nexport const A = \'import("recharts")\';',
      'vi.mock("recharts", () => ({})); export const A = "recharts";',
      'import "recharts";',
      'const moduleName = "recharts"; export const A = import(moduleName);',
      'const suffix = "charts"; export const A = import(`re${suffix}`);',
    ]) {
      const messages = await lint(source, CONSUMER_PATHS[0]);
      expect(messages.filter((message) => message.ruleId === TREND_RULE)).toEqual([]);
    }
  });
});

const ZERO_FILL_RULE = "count-bucket/no-zero-fill";

describe("count bucket zero-fill lint (bu-s11n0s.5.1)", { timeout: 60_000 }, () => {
  it("flags zero-filled bucket arrays in every source file and honours a reasoned disable", async () => {
    const zeroFills = (messages: ESLint.LintResult["messages"]) =>
      messages.filter((message) => message.ruleId === ZERO_FILL_RULE);
    for (const filePath of ["src/components/butlers/Strip.tsx", "src/lib/strip-derive.ts"]) {
      for (const source of [
        "export const A = Array(24).fill(0);",
        "export const A = new Array<number>(24).fill(0);",
        "const a = new Array(24); a.fill(0); export const A = a;",
        "export const A = (n: number) => { const a: number[] = []; a.length = n; return a.fill(0, 0); };",
        "export const A = Array.from({ length: 24 }, () => 0);",
        "export const A = Array.from({ length: 24 }, () => { return 0; });",
      ]) {
        expect(zeroFills(await lint(source, filePath)), source).toEqual([
          expect.objectContaining({ severity: 2, message: expect.stringContaining("buildBucketSeries") }),
        ]);
      }
      for (const source of [
        "export const A = Array<number | null>(24).fill(null);",
        "export const A = Array.from({ length: 24 }, (_, i) => i);",
        "export const A = Array(3).fill(1);",
        "export const A = Array.from({ length: 3 }, () => []);",
        "export const A = (s: string) => s.padStart(2, '0');",
        "// eslint-disable-next-line count-bucket/no-zero-fill -- LCS table, not a chart\nexport const A = Array(4).fill(0);",
      ]) expect(zeroFills(await lint(source, filePath)), source).toEqual([]);
    }
    const fixture = await lint("export const A = Array(24).fill(0);", "src/components/butlers/Strip.test.tsx");
    expect(zeroFills(fixture)).toEqual([]);
  });
});
