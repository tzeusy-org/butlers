/** Construct the only allowed dashboard navigation target for a Bead. */
export function beadDetailPath(id: string): string {
  return `/beads/${encodeURIComponent(id)}`;
}
