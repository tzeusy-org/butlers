/**
 * Tests for the Sidebar path -> route-chunk-loader map (bu-ep4ks.15).
 *
 * "Every loader resolves to a page module with a default component export" is
 * a compile-time invariant, not a runtime one (bu-y9zpp7): shell-capability.ts
 * wraps each loader in `page(loader: () => Promise<{ default: ComponentType }>)`,
 * so a missing module or a module without a default component export fails
 * `tsc -b` (and `npm run build`). The type assertion below pins that loader
 * type so the gate cannot be silently widened. Importing every page graph at
 * runtime here was redundant and load-bound, and it timed out under the full
 * suite.
 */

import type { ComponentType } from "react";
import { describe, expect, expectTypeOf, it } from "vitest";

import { navSections } from "@/components/layout/nav-config";
import { SHELL_CAPABILITIES } from "@/lib/shell-capability";
import {
  type ChunkLoader,
  ROUTE_CHUNK_LOADERS,
  resolveRouteChunkLoader,
} from "./route-chunk-registry";

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

  it("types every loader as resolving to a default component export (checked by tsc -b)", () => {
    expectTypeOf<ChunkLoader>().toEqualTypeOf<() => Promise<{ default: ComponentType }>>();
  });
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
