import type { TimelineEvent, TimelineMachineClass } from "@/api/types";

/**
 * Read the server-owned presentation class with a compatibility fallback for
 * responses produced before `machine_class` was added. Never infer a class
 * from a summary or any prompt-shaped payload.
 */
export function timelineMachineClass(
  event: Pick<TimelineEvent, "machine_class" | "is_heartbeat">,
): TimelineMachineClass {
  switch (event.machine_class) {
    case "owner":
    case "heartbeat":
    case "maintenance":
      return event.machine_class;
    default:
      return event.is_heartbeat ? "heartbeat" : "owner";
  }
}

export function isMaintenanceEvent(event: TimelineEvent): boolean {
  return timelineMachineClass(event) === "maintenance";
}

export type MaintenanceRunStatus = "completed" | "failed" | "running" | "unknown";

/**
 * Interpret the session outcome strictly from the Timeline payload. A missing
 * or malformed value must remain visible rather than being inferred as a
 * completed run from its event type.
 */
export function maintenanceRunStatus(event: TimelineEvent): MaintenanceRunStatus {
  switch (event.data?.["success"]) {
    case true:
      return "completed";
    case false:
      return "failed";
    case null:
      return "running";
    default:
      return "unknown";
  }
}

export function isFailedMaintenanceEvent(event: TimelineEvent): boolean {
  return isMaintenanceEvent(event) && maintenanceRunStatus(event) === "failed";
}

export function isSuccessfulMaintenanceEvent(event: TimelineEvent): boolean {
  return isMaintenanceEvent(event) && maintenanceRunStatus(event) === "completed";
}
