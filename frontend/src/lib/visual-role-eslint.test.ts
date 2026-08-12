import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

const CATEGORY_IDENTITY_TOKEN = `var(--${"category-1"})`;
const LEAKED_UI_SOURCE =
  `export const leaked = ${JSON.stringify(CATEGORY_IDENTITY_TOKEN)};\n`;
const LEAKED_IDENTITY_ALIAS_SOURCE = [
  `export const directAlias = ${JSON.stringify(`var(--${"color-category-1"})`)};`,
  `export const tailwindAlias = ${JSON.stringify(["text", "category-1"].join("-"))};`,
].join("\n");

describe("semantic visual-role lint", () => {
  it("rejects identity tokens in non-ButlerMark UI files", async () => {
    const eslint = new ESLint();
    const [result] = await eslint.lintText(LEAKED_UI_SOURCE, {
      filePath: "src/components/ui/IdentityLeak.tsx",
    });

    expect(result.messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          ruleId: "no-restricted-syntax",
          message: expect.stringContaining(
            "Butler identity tokens are private to ButlerMark",
          ),
        }),
      ]),
    );
  });

  it("rejects public identity aliases and their Tailwind utility forms", async () => {
    const eslint = new ESLint();
    const [result] = await eslint.lintText(LEAKED_IDENTITY_ALIAS_SOURCE, {
      filePath: "src/components/ui/IdentityAliasLeak.tsx",
    });

    const roleMessages = result.messages.filter(
      (message) =>
        message.ruleId === "no-restricted-syntax" &&
        message.message.includes("Butler identity"),
    );
    expect(roleMessages).toHaveLength(2);
  });

  it("keeps the canonical ButlerMark exemption narrow", async () => {
    const eslint = new ESLint();
    const [result] = await eslint.lintText(LEAKED_UI_SOURCE, {
      filePath: "src/components/ui/ButlerMark.tsx",
    });

    expect(
      result.messages.filter((message) => message.ruleId === "no-restricted-syntax"),
    ).toEqual([]);
  });
});
