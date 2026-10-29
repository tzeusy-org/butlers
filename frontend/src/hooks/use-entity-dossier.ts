/** TanStack Query hook for the entity graph dossier (bu-8cdl1.8, REQ-entity-graph-011). */

import { useQuery } from "@tanstack/react-query";

import { getEntityDossier } from "@/api/entity-dossier.ts";

// No fleet event or interval refreshes this: the dossier is a read of a write-behind projection,
// loaded on open and re-read by the panel's retry (a read, never a write).
export function useEntityDossier(entityId: string) {
  return useQuery({
    queryKey: ["entity-dossier", entityId],
    queryFn: () => getEntityDossier(entityId),
    retry: false,
    select: (response) => response.data,
  });
}
