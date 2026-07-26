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

  it("rejects a raw numeric refetch interval in an arbitrary (previously unscoped) hook file", async () => {
    // bu-ep4ks.15: the lint used to be scoped to an 8-file allowlist -- this
    // proves it now applies to a file that was never on that list.
    const eslint = new ESLint();
    const [result] = await eslint.lintText(
      "export const query = { refetchInterval: 30_000 };\n",
      { filePath: "src/hooks/use-some-arbitrary-hook-not-on-any-allowlist.ts" },
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

  it("accepts a named poll-policy constant instead of a raw literal", async () => {
    const eslint = new ESLint();
    const [result] = await eslint.lintText(
      "const HEALTH_POLL_MS = 30_000;\nexport const query = { refetchInterval: HEALTH_POLL_MS };\n",
      { filePath: "src/hooks/use-health.ts" },
    );

    expect(
      result.messages.filter((m) => m.ruleId === "no-restricted-syntax"),
    ).toEqual([]);
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
