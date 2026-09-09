// @vitest-environment jsdom
/**
 * local-settings.ts — unit tests. No prior coverage existed; this pins the
 * read/write/fallback contract for both value kinds (bu-0ynlk.11 adds the
 * number variants, used to persist the chat dock's width).
 */

import { afterEach, describe, expect, it } from "vitest";
import {
  readBooleanSetting,
  writeBooleanSetting,
  readNumberSetting,
  writeNumberSetting,
} from "./local-settings.ts";

afterEach(() => {
  window.localStorage.clear();
});

describe("boolean settings", () => {
  it("round-trips a written value", () => {
    writeBooleanSetting("k", true);
    expect(readBooleanSetting("k", false)).toBe(true);
  });

  it("falls back when unset", () => {
    expect(readBooleanSetting("missing", true)).toBe(true);
  });

  it("falls back on a malformed stored value", () => {
    window.localStorage.setItem("k", "not-a-boolean");
    expect(readBooleanSetting("k", true)).toBe(true);
  });
});

describe("number settings", () => {
  it("round-trips a written value", () => {
    writeNumberSetting("width", 420);
    expect(readNumberSetting("width", 360)).toBe(420);
  });

  it("falls back when unset", () => {
    expect(readNumberSetting("missing", 360)).toBe(360);
  });

  it("falls back on a non-numeric stored value", () => {
    window.localStorage.setItem("width", "not-a-number");
    expect(readNumberSetting("width", 360)).toBe(360);
  });
});
