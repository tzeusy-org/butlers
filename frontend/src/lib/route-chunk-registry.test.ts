/**
 * Tests for the Sidebar path -> route-chunk-loader map (bu-ep4ks.15).
 */

import { describe, expect, it } from "vitest";

import { navSections } from "@/components/layout/nav-config";
import { SHELL_CAPABILITIES } from "@/lib/shell-capability";
import { ROUTE_CHUNK_LOADERS, resolveRouteChunkLoader } from "./route-chunk-registry";

/** Every path the Sidebar can actually navigate to, flat vs. group children. */
function allSidebarPaths(): string[] {
  const paths: string[] = [];
  for (const section of navSections) {
    for (const item of section.items) {
      if (item.kind === "group") {
        for (const child of item.children) paths.push(child.path);
      } else {
        paths.push(item.path);
      }
    }
  }
  return paths;
}

describe("ROUTE_CHUNK_LOADERS", () => {
  it("covers every path the Sidebar renders a NavLink for", () => {
    const missing = allSidebarPaths().filter((p) => !(p in ROUTE_CHUNK_LOADERS));
    expect(missing).toEqual([]);
  });

  it("includes every static globally discoverable capability, including contextual subroutes", () => {
    const expected = SHELL_CAPABILITIES.filter((capability) => !capability.dynamic).map(
      (capability) => capability.path,
    );
    expect(Object.keys(ROUTE_CHUNK_LOADERS).sort()).toEqual(expected.sort());
  });

  it(
    "every registered loader resolves to a module with a default export",
    async () => {
      // Real page modules -- each pulls in its full dependency chain, so
      // this is inherently slower than a typical unit test, especially
      // alongside the rest of a large concurrent suite run.
      for (const [path, loader] of Object.entries(ROUTE_CHUNK_LOADERS)) {
        const mod = (await loader()) as { default?: unknown };
        expect(mod.default, `loader for ${path} has no default export`).toBeDefined();
      }
    },
    20_000,
  );
});

describe("resolveRouteChunkLoader", () => {
  it("resolves a mapped path to its loader", () => {
    expect(resolveRouteChunkLoader("/butlers")).toBe(ROUTE_CHUNK_LOADERS["/butlers"]);
  });

  it("resolves contextual detail routes while rejecting unknown paths", () => {
    expect(resolveRouteChunkLoader("/butlers/some-butler")).toEqual(expect.any(Function));
    expect(resolveRouteChunkLoader("/not-a-real-route")).toBeNull();
  });
});
