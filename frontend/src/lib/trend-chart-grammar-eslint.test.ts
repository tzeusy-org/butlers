import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

async function lint(source: string, filePath: string) {
  const [result] = await new ESLint().lintText(source, { filePath });
  return result.messages.filter((m) => m.ruleId === "no-restricted-syntax");
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
    const messages = await lint(
      'import { LineChart } from "recharts";\nexport const A = LineChart;\n',
      "src/components/health/SomeChart.tsx",
    );
    expect(messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ message: expect.stringContaining("TimeSeriesChart") }),
      ]),
    );
  });

  it("allows the canonical primitive to own the raw chart", async () => {
    const messages = await lint(
      'import { LineChart } from "recharts";\nexport const A = LineChart;\n',
      "src/components/ui/TimeSeriesChart.tsx",
    );
    expect(messages.filter((m) => m.message.includes("TimeSeriesChart"))).toEqual([]);
  });
  // REQ-dashboard-design-language-007: genuine configured zero-fill dataflow rejection.
  it("rejects only zero-fill flows feeding count strips under actual configuration", async () => {
    const negative = [
      'const zero = Array(24).fill(0); export const A = () => <BucketStrip buckets={zero} />;',
      'const zero = new Array(24).fill(0); const alias=zero; export const A=()=> <ActivityStripe counts={alias}/>;',
      'let rows; rows = Array.from({length:24},()=>0); export const A=()=> <Sparkline data={rows}/>;',
      'const rows=()=>Array(24).fill(0); const alias=rows(); export const A=()=> <BucketStrip buckets={alias}/>;',
      'function rows(){return Array(24).fill(0)} export const A=()=> <ConnectorHistogram data={rows()}/>;',
    ]
    for (const source of negative) {
      const [result] = await new ESLint().lintText(source, { filePath: "src/components/health/CountFixture.tsx" })
      expect(result.messages.some(message => message.ruleId === "count-truth/source-keys")).toBe(true)
    }
    for (const [source, filePath] of [
      ['const pagination = Array(24).fill(0); export const A=()=> <div>{pagination.length}</div>;', "src/components/health/CountFixture.tsx"],
      ['export function unrelated(){return Array(24).fill(0)}', "src/components/health/CountFixture.tsx"],
      ['const rows=Array.from({length:24},()=>0); export const A=()=> <BucketStrip buckets={rows}/>;', "src/lib/bucket-series.ts"],
      ['export const A=()=> <BucketStrip buckets={sourceBuckets}/>;', "src/components/health/CountFixture.tsx"],
    ]) {
      const [result] = await new ESLint().lintText(source, { filePath })
      expect(result.messages.filter(message => message.ruleId === "count-truth/source-keys")).toEqual([])
    }
  })

});
