import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const ALLOWED_KINDS = new Set(["promise", "waiting_for", "follow_up", "obligation", "decision"]);
const ALLOWED_DIRECTIONS = new Set(["owner_to_other", "other_to_owner", "self"]);
const ALLOWED_LEVELS = new Set(["L0", "L1", "L2", "L3"]);
const FIXTURES = new Set(["populated", "empty"]);

function record(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`Route A receipt expected an object at ${label}.`);
  }
  return value;
}

function string(value, label) {
  if (typeof value !== "string" || !value) {
    throw new Error(`Route A receipt expected a nonempty string at ${label}.`);
  }
  return value;
}

function boolean(value, label) {
  if (typeof value !== "boolean") {
    throw new Error(`Route A receipt expected a boolean at ${label}.`);
  }
  return value;
}

function array(value, label) {
  if (!Array.isArray(value)) {
    throw new Error(`Route A receipt expected an array at ${label}.`);
  }
  return value;
}

function fixture(value) {
  if (!FIXTURES.has(value)) {
    throw new Error("Route A receipt requires the populated or empty fixture name.");
  }
  return value;
}

function projectCommitment(value) {
  const commitment = record(value, "commitment");
  const kind = string(commitment.kind, "commitment.kind");
  const direction = string(commitment.direction, "commitment.direction");
  const escalationLevel = string(commitment.escalation_level, "commitment.escalation_level");
  const fingerprint = string(commitment.fingerprint, "commitment.fingerprint");
  if (!ALLOWED_KINDS.has(kind) || !ALLOWED_DIRECTIONS.has(direction) || !ALLOWED_LEVELS.has(escalationLevel)) {
    throw new Error("Route A receipt received an unallowlisted commitment value.");
  }
  if (!fingerprint.startsWith("route-a-")) {
    throw new Error("Route A receipt received a non-synthetic commitment fingerprint.");
  }
  return {
    kind,
    direction,
    escalation_level: escalationLevel,
    fingerprint,
  };
}

/**
 * Project only fixture-safe API fields. Names, notes, summaries, message
 * context, timestamps, and any unknown response fields never reach a receipt.
 */
export function sanitizeMeetingPrepApiResponse(payload, expected) {
  const expectedFixture = fixture(expected.fixture);
  const data = record(record(payload, "response").data, "response.data");
  const eventId = string(data.event_id, "response.data.event_id");
  const hasPrepContext = boolean(data.has_prep_context, "response.data.has_prep_context");
  const attendees = array(data.attendees, "response.data.attendees");
  if (eventId !== expected.eventId || hasPrepContext !== expected.hasPrepContext) {
    throw new Error("Route A receipt response does not match its synthetic fixture contract.");
  }

  if (expectedFixture === "empty") {
    if (attendees.length !== 0) {
      throw new Error("Route A empty fixture must not expose attendees.");
    }
    return {
      receipt_version: 1,
      fixture: expectedFixture,
      event_id: eventId,
      has_prep_context: hasPrepContext,
      attendee_count: 0,
      commitments: [],
    };
  }

  if (attendees.length !== 1) {
    throw new Error("Route A populated fixture must expose exactly one synthetic attendee.");
  }
  const commitments = array(record(attendees[0], "response.data.attendees[0]").commitments, "commitments").map(
    projectCommitment,
  );
  if (commitments.length === 0) {
    throw new Error("Route A populated fixture must expose synthetic commitments.");
  }
  return {
    receipt_version: 1,
    fixture: expectedFixture,
    event_id: eventId,
    has_prep_context: hasPrepContext,
    attendee_count: 1,
    commitments,
  };
}

export async function writeSanitizedApiReceipt({ artifactDir, expected, payload }) {
  const receipt = sanitizeMeetingPrepApiResponse(payload, expected);
  const root = resolve(artifactDir);
  const destination = resolve(root, `api-${receipt.fixture}.json`);
  if (dirname(destination) !== root) {
    throw new Error("Route A receipt destination escaped its disposable artifact directory.");
  }
  await mkdir(root, { recursive: true });
  await writeFile(destination, `${JSON.stringify(receipt, null, 2)}\n`, "utf8");
  return receipt;
}
