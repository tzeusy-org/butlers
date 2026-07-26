import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

describe("no-window-confirm lint (bu-ep4ks.11)", () => {
  it("rejects window.confirm in a file migrated onto ConfirmDialog", async () => {
    const eslint = new ESLint();
    const [result] = await eslint.lintText(
      'function f() { if (!window.confirm("sure?")) return; }\n',
      { filePath: "src/pages/QaOverviewPage.tsx" },
    );

    expect(result.messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          ruleId: "no-restricted-syntax",
          message: expect.stringContaining("window.confirm is banned in this file"),
        }),
      ]),
    );
  });

  it("rejects a bare confirm() call in the same scope", async () => {
    const eslint = new ESLint();
    const [result] = await eslint.lintText(
      'function f() { if (!confirm("sure?")) return; }\n',
      { filePath: "src/pages/QaOverviewPage.tsx" },
    );

    expect(result.messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          ruleId: "no-restricted-syntax",
          message: expect.stringContaining("window.confirm is banned in this file"),
        }),
      ]),
    );
  });

  it("flags window.confirm in EntityDetailPage.tsx (bu-3dp0c: migrated off the scope-cut, ban is now repo-wide)", async () => {
    const eslint = new ESLint();
    const [result] = await eslint.lintText(
      'function f() { if (!window.confirm("sure?")) return; }\n',
      { filePath: "src/pages/EntityDetailPage.tsx" },
    );

    expect(result.messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          ruleId: "no-restricted-syntax",
          message: expect.stringContaining("window.confirm is banned in this file"),
        }),
      ]),
    );
  });

  it("flags window.confirm in an arbitrary .tsx file not covered by any earlier scoped allowlist (repo-wide)", async () => {
    const eslint = new ESLint();
    const [result] = await eslint.lintText(
      'function f() { if (!window.confirm("sure?")) return; }\n',
      { filePath: "src/pages/SomeUnrelatedPage.tsx" },
    );

    expect(result.messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          ruleId: "no-restricted-syntax",
          message: expect.stringContaining("window.confirm is banned in this file"),
        }),
      ]),
    );
  });
});
