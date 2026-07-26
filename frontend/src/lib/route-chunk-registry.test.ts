/**
 * Tests for the Sidebar path -> route-chunk-loader map (bu-ep4ks.15).
 */

import { describe, expect, it } from "vitest";

import { navSections } from "@/components/layout/nav-config";
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

  it("has no entry for a path the Sidebar does not actually render", () => {
    const sidebarPaths = new Set(allSidebarPaths());
    const extra = Object.keys(ROUTE_CHUNK_LOADERS).filter((p) => !sidebarPaths.has(p));
    expect(extra).toEqual([]);
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

  it("returns null for an unmapped path (detail routes are out of scope)", () => {
    expect(resolveRouteChunkLoader("/butlers/some-butler")).toBeNull();
    expect(resolveRouteChunkLoader("/not-a-real-route")).toBeNull();
  });
});
