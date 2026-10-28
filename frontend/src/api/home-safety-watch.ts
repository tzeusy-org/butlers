/** Owner-declared life-safety sensors; request and wire types come from OpenAPI. */
import { apiFetch } from "./client";
import type { components } from "./schema.gen";

export type HomeSafetyWatchEntry = components["schemas"]["SafetyWatchEntry"];
export type HomeSafetyWatchInput = components["schemas"]["SafetyWatchUpsertRequest"];
export type HomeSafetyRole = HomeSafetyWatchInput["role"];

export function getHomeSafetyWatch() {
  return apiFetch("/home/safety-watch");
}
export function putHomeSafetyWatch(input: HomeSafetyWatchInput) {
  return apiFetch("/home/safety-watch", { method: "PUT", body: input });
}
export function deleteHomeSafetyWatch(entityId: string, role: HomeSafetyRole) {
  return apiFetch("/home/safety-watch/{entity_id}/{role}", {
    method: "DELETE", path: { entity_id: String(entityId), role: String(role) },
  });
}
