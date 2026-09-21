import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { sanitizeMeetingPrepApiResponse, writeSanitizedApiReceipt } from "./route-a-api-receipt.mjs";

const populated = {
  data: {
    event_id: "00000000-0000-4000-8000-000000000101",
    has_prep_context: true,
    source_butlers: ["relationship"],
    attendees: [
      {
        name: "Route A Synthetic Attendee",
        notes: ["must not reach a receipt"],
        commitments: [
          {
            kind: "promise",
            direction: "owner_to_other",
            summary: "must not reach a receipt",
            escalation_level: "L3",
            fingerprint: "route-a-owner-l3",
          },
        ],
      },
    ],
  },
};

test("projects only allowlisted synthetic populated fields", () => {
  const receipt = sanitizeMeetingPrepApiResponse(populated, {
    fixture: "populated",
    eventId: "00000000-0000-4000-8000-000000000101",
    hasPrepContext: true,
  });
  assert.deepEqual(receipt, {
    receipt_version: 1,
    fixture: "populated",
    event_id: "00000000-0000-4000-8000-000000000101",
    has_prep_context: true,
    attendee_count: 1,
    commitments: [
      {
        kind: "promise",
        direction: "owner_to_other",
        escalation_level: "L3",
        fingerprint: "route-a-owner-l3",
      },
    ],
  });
  assert.equal(JSON.stringify(receipt).includes("must not reach a receipt"), false);
  assert.equal(JSON.stringify(receipt).includes("Synthetic Attendee"), false);
});

test("writes populated and empty receipts only under the disposable artifact directory", async () => {
  const artifactDir = await mkdtemp(join(tmpdir(), "route-a-api-receipt-"));
  try {
    await writeSanitizedApiReceipt({
      artifactDir,
      payload: populated,
      expected: {
        fixture: "populated",
        eventId: "00000000-0000-4000-8000-000000000101",
        hasPrepContext: true,
      },
    });
    await writeSanitizedApiReceipt({
      artifactDir,
      payload: {
        data: {
          event_id: "00000000-0000-4000-8000-000000000102",
          has_prep_context: false,
          attendees: [],
        },
      },
      expected: {
        fixture: "empty",
        eventId: "00000000-0000-4000-8000-000000000102",
        hasPrepContext: false,
      },
    });
    const empty = JSON.parse(await readFile(join(artifactDir, "api-empty.json"), "utf8"));
    assert.deepEqual(empty.commitments, []);
    assert.equal(empty.attendee_count, 0);
  } finally {
    await rm(artifactDir, { recursive: true, force: true });
  }
});

test("refuses a non-synthetic commitment fingerprint", () => {
  const unsafe = structuredClone(populated);
  unsafe.data.attendees[0].commitments[0].fingerprint = "personal-fingerprint";
  assert.throws(
    () =>
      sanitizeMeetingPrepApiResponse(unsafe, {
        fixture: "populated",
        eventId: "00000000-0000-4000-8000-000000000101",
        hasPrepContext: true,
      }),
    /non-synthetic/,
  );
});
