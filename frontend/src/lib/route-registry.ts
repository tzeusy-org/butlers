/** Static discovery projection of the shell capability manifest. */

import {
  SHELL_CAPABILITIES,
  type ShellCapability,
} from "@/lib/shell-capability";

export interface RouteEntry {
  path: string;
  label: string;
  section: string;
  butler?: string;
  keywords?: readonly string[];
  chord?: string;
  loader: ShellCapability["loader"];
}

/**
 * Dynamic destinations are intentionally not shown as literal `/ :id` rows
 * in Cmd-K. They remain in the manifest and are reachable through search or
 * contextual links, while static global/contextual capabilities form the
 * command and help projection.
 */
export const ALL_ROUTES: RouteEntry[] = SHELL_CAPABILITIES.filter(
  (capability) => !capability.dynamic,
).map((capability) => ({
  path: capability.path,
  label: capability.label,
  section: capability.placement?.group ?? capability.placement?.section ?? capability.family,
  butler: capability.placement?.butler,
  keywords: capability.keywords,
  chord: capability.chord,
  loader: capability.loader,
}));

export const G_CHORD_ROUTES: Record<string, string> = Object.fromEntries(
  ALL_ROUTES.filter((route): route is RouteEntry & { chord: string } => !!route.chord).map(
    (route) => [route.chord, route.path],
  ),
);
