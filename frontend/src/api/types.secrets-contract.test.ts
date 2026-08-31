import { describe, expect, it } from "vitest";

import type {
  SecretsCliDetail,
  SecretsCredentialTestOutcome,
  SecretsSystemCredentialDetail,
} from "./types.ts";

const SYSTEM_TEST_OUTCOME = {
  ok: true,
  code: null,
  at: null,
} satisfies NonNullable<SecretsSystemCredentialDetail["test"]>;

const CLI_TEST_OUTCOME = {
  ok: true,
  code: null,
  at: null,
} satisfies NonNullable<SecretsCliDetail["test"]>;

describe("content-blind credential detail outcome types", () => {
  it("uses the same outcome shape for system and CLI detail consumers", () => {
    expect(SYSTEM_TEST_OUTCOME).toEqual(CLI_TEST_OUTCOME);
  });

  it("requires backend-emitted nullable code and at keys", () => {
    const completeOutcome = {
      ok: false,
      code: 503,
      at: null,
    } satisfies SecretsCredentialTestOutcome;

    // @ts-expect-error code is always serialized, even when null
    const missingCode: SecretsCredentialTestOutcome = { ok: true, at: null };
    // @ts-expect-error at is always serialized, even when null
    const missingAt: SecretsCredentialTestOutcome = { ok: true, code: null };

    expect(completeOutcome).toEqual({ ok: false, code: 503, at: null });
    expect(missingCode).toEqual({ ok: true, at: null });
    expect(missingAt).toEqual({ ok: true, code: null });
  });
});
