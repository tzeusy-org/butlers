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
});
