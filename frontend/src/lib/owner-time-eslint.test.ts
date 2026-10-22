import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

async function ownerTimeMessages(source: string, filePath: string) {
  const [result] = await new ESLint().lintText(source, { filePath });
  expect(result.fatalErrorCount, `${filePath}: ${source}`).toBe(0);
  return result.messages.filter((message) => message.ruleId === "no-restricted-properties");
}

describe("owner-time lint (bu-6jv4m.14)", { timeout: 60_000 }, () => {
  it("rejects locale date formatting and Intl.DateTimeFormat in production source", async () => {
    for (const source of [
      "export const a = (d: Date) => d.toLocaleDateString();\n",
      "export const a = (d: Date) => d.toLocaleTimeString([], {});\n",
      'export const a = new Intl.DateTimeFormat("en");\n',
    ]) {
      expect(await ownerTimeMessages(source, "src/components/x/Foo.tsx")).toEqual([
        expect.objectContaining({ message: expect.stringContaining("formatOwnerDateTime") }),
      ]);
    }
  });

  it("leaves numeric grouping, tests, and the calendar-boundary files alone", async () => {
    expect(
      await ownerTimeMessages("export const a = (n: number) => n.toLocaleString();\n", "src/components/x/Foo.tsx"),
    ).toEqual([]);
    const locale = "export const a = (d: Date) => d.toLocaleDateString();\n";
    expect(await ownerTimeMessages(locale, "src/components/x/Foo.test.tsx")).toEqual([]);
    expect(await ownerTimeMessages(locale, "src/lib/day-window.ts")).toEqual([]);
  });
});
