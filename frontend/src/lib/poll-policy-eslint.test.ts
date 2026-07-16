import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

describe("poll-policy lint", () => {
  it("rejects a raw numeric refetch interval in the migrated ingestion hook", async () => {
    const eslint = new ESLint();
    const [result] = await eslint.lintText(
      "export const query = { refetchInterval: 30_000 };\n",
      { filePath: "src/hooks/use-ingestion-events.ts" },
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
});
