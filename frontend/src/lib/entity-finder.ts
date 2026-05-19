/** Event dispatched to open the entity-first Cmd-K finder (bu-xfjwk). */
export const OPEN_ENTITY_FINDER_EVENT = "open-entity-finder";

/** Dispatch the event that opens the EntityFinder overlay. */
export function dispatchOpenEntityFinder() {
  window.dispatchEvent(new CustomEvent(OPEN_ENTITY_FINDER_EVENT));
}
