import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

async function ownerTimeMessages(source: string, filePath: string) {
  const [result] = await new ESLint().lintText(source, { filePath });
  expect(result.fatalErrorCount, `${filePath}: ${source}`).toBe(0);
  expect(result.messages.filter((message) => message.ruleId === null)).toEqual([]);
  return result.messages.filter((message) =>
    message.ruleId === "no-restricted-properties" || message.ruleId === "owner-time/no-local-date-format",
  );
}

const restoredPatterns = [
  "const date = new Date(); export const a = date.toLocaleString();\n",
  "export const a = new Date().toLocaleString();\n",
  'export const a = { timeZone: "UTC" };\n',
];

describe("owner-time lint (bu-6jv4m.14)", { timeout: 60_000 }, () => {
  it("rejects locale date formatting and Intl.DateTimeFormat in production source", async () => {
    for (const source of [
      "export const a = (d: Date) => d.toLocaleDateString();\n",
      "export const a = (d: Date) => d.toLocaleTimeString([], {});\n",
      'export const a = new Intl.DateTimeFormat("en");\n',
      ...restoredPatterns,
      "export const a = (date: Date) => date.toLocaleString();\n",
      "const date = new Date(); const alias = date; export const a = alias.toLocaleString();\n",
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
    for (const source of restoredPatterns) {
      for (const filePath of ["src/lib/day-window.ts", "src/pages/ApprovalsPage.tsx"]) {
        expect(await ownerTimeMessages(source, filePath)).toEqual([]);
      }
    }
    expect(await ownerTimeMessages(
      "const date = new Date(); export const a = (date: number) => date.toLocaleString(); export const b = date;\n",
      "src/components/x/Foo.tsx",
    )).toEqual([]);
  });
});
