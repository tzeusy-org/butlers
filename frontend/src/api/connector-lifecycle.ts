import { apiFetch } from "./client";

/** The existing server route commits pause and attempts its best-effort audit. */
export function pauseConnector(type: string, identity: string) {
  return apiFetch("/ingestion/connectors/{connector_type}/{endpoint_identity}/pause", {
    method: "POST", path: { connector_type: String(type), endpoint_identity: String(identity) },
  });
}
/** Resume only: the server sets unknown until heartbeat, without forcing a poll. */
export function resumeConnector(type: string, identity: string) {
  return apiFetch("/ingestion/connectors/{connector_type}/{endpoint_identity}/run-now", {
    method: "POST", path: { connector_type: String(type), endpoint_identity: String(identity) },
  });
}
