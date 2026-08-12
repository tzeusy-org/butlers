import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

const CATEGORY_IDENTITY_TOKEN = `var(--${"category-1"})`;
const LEAKED_UI_SOURCE =
  `export const leaked = ${JSON.stringify(CATEGORY_IDENTITY_TOKEN)};\n`;

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
