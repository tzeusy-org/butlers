// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";

afterEach(() => { vi.unstubAllEnvs(); vi.resetModules(); });
it("does not register preview URLs or expose preview capabilities by default", async () => {
  vi.stubEnv("VITE_D19_PREVIEW", "");
  vi.resetModules();
  const { d19Routes } = await import("./routes");
  const { SHELL_CAPABILITIES } = await import("@/lib/shell-capability");
  expect(d19Routes).toEqual([]);
  expect(SHELL_CAPABILITIES.some((c) => c.path.startsWith("/d19"))).toBe(false);
  expect(SHELL_CAPABILITIES.find((c) => c.path === "/")?.label).toBe("Overview");
});
it("registers only prefixed destinations on explicit opt-in and withholds page context", async () => {
  vi.stubEnv("VITE_D19_PREVIEW", "true");
  vi.resetModules();
  const { d19Routes } = await import("./routes");
  const { SHELL_CAPABILITIES, resolveShellWarmup } = await import("@/lib/shell-capability");
  expect(d19Routes.map((r) => r.path)).toEqual(["/d19"]);
  const previews = SHELL_CAPABILITIES.filter((c) => c.path.startsWith("/d19"));
  expect(previews).toHaveLength(7);
  expect(previews.every((c) => c.contextPolicy === "none" && c.placement === null)).toBe(true);
  expect(resolveShellWarmup("/d19/goals/stored-id")?.queryKey).toEqual(["goals", "detail", "stored-id"]);
  expect(resolveShellWarmup("/d19/regions")?.queryKey).toEqual(["butlers"]);
  expect(SHELL_CAPABILITIES.find((c) => c.path === "/")?.label).toBe("Overview");
});
