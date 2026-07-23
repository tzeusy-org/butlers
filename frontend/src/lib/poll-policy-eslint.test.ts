import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

describe("poll-policy lint", () => {
  it("rejects a raw numeric refetch interval in a migrated hook", async () => {
    const eslint = new ESLint();
    const [result] = await eslint.lintText(
      "export const query = { refetchInterval: 30_000 };\n",
      { filePath: "src/hooks/use-notifications.ts" },
    );

    expect(result.messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          ruleId: "no-restricted-syntax",
          message: expect.stringContaining("refetchInterval must use a named poll-policy token"),
        }),
      ]),
    );
  });

  it("rejects a raw keydown listener outside the keyboard registries", async () => {
    const eslint = new ESLint();
    const [result] = await eslint.lintText(
      'window.addEventListener("keydown", () => {});\n',
      { filePath: "src/pages/KeyboardEscapeHatch.tsx" },
    );

    expect(result.messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          ruleId: "no-restricted-syntax",
          message: expect.stringContaining("Raw keydown listeners are forbidden"),
        }),
      ]),
    );
  });
});
