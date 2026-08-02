import { describe, expect, it } from "vitest";

import { classifySendError } from "./send-error-utils.ts";

describe("classifySendError", () => {
  it("does not offer a replay while the durable ingest handoff is still active", () => {
    expect(
      classifySendError(
        {
          code: "INGEST_IN_PROGRESS",
          message: "This message is already being submitted.",
        },
        "Send this once.",
        "message-123",
      ),
    ).toEqual({
      kind: "pending",
      message: "This message is already being submitted.",
    });
  });
});
