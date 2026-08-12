/** Route-chunk projection of the shell capability manifest. */

import {
  SHELL_CAPABILITIES,
  resolveShellCapability,
  type ShellCapability,
} from "@/lib/shell-capability";

export type ChunkLoader = ShellCapability["loader"];

/** Static destinations have an exact key for inexpensive registry inspection. */
export const ROUTE_CHUNK_LOADERS: Record<string, ChunkLoader> = Object.fromEntries(
  SHELL_CAPABILITIES.filter((capability) => !capability.dynamic).map((capability) => [
    capability.path,
    capability.loader,
  ]),
);

/** Resolve static paths and parameterized contextual destinations alike. */
export function resolveRouteChunkLoader(pathname: string): ChunkLoader | null {
  return resolveShellCapability(pathname)?.loader ?? null;
}
