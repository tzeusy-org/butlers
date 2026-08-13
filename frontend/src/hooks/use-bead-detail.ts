/** Read-only query for one snapshot-backed dashboard Bead detail. */

import { useQuery } from "@tanstack/react-query";

import { getBeadDetail } from "@/api";

export function useBeadDetail(beadId: string | null) {
  return useQuery({
    queryKey: ["beads", "detail", beadId],
    queryFn: () => getBeadDetail(beadId ?? ""),
    enabled: beadId != null && beadId.length > 0,
  });
}
