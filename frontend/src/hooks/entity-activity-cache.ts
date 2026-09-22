import type { QueryClient } from "@tanstack/react-query";

/** Cache families derived from canonical narrative and identity activity rows. */
export function entityActivityInvalidationKeys(
  entityId?: string,
): readonly unknown[][] {
  return entityId
    ? [
        ["entity-activity", entityId],
        ["entity-activity-bins", entityId],
      ]
    : [["entity-activity"], ["entity-activity-bins"]];
}

/** Refresh the canonical activity stream and its histogram as one cache family. */
export function invalidateEntityActivityFamily(
  queryClient: Pick<QueryClient, "invalidateQueries">,
  entityId?: string,
): void {
  for (const queryKey of entityActivityInvalidationKeys(entityId)) {
    void queryClient.invalidateQueries({ queryKey });
  }
}
