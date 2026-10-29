import { describe, expect, it } from "vitest";

import type {
  SecretsCliDetail,
  SecretsCredentialTestOutcome,
  SecretsSystemCredentialDetail,
} from "./types.ts";

// These retained assertions target the original content-blind keys. Derive
// that projection from native wire types; other serialized fields stay strict.
const SYSTEM_TEST_OUTCOME = {
  ok: true,
  code: null,
  at: null,
} satisfies Pick<NonNullable<SecretsSystemCredentialDetail["test"]>, "ok" | "code" | "at">;

const CLI_TEST_OUTCOME = {
  ok: true,
  code: null,
  at: null,
} satisfies Pick<NonNullable<SecretsCliDetail["test"]>, "ok" | "code" | "at">;

describe("content-blind credential detail outcome types", () => {
  it("uses the same outcome shape for system and CLI detail consumers", () => {
    expect(SYSTEM_TEST_OUTCOME).toEqual(CLI_TEST_OUTCOME);
  });

  it("requires backend-emitted nullable code and at keys", () => {
    const completeOutcome = {
      ok: false,
      code: 503,
      at: null,
    } satisfies Pick<SecretsCredentialTestOutcome, "ok" | "code" | "at">;

    // @ts-expect-error code is always serialized, even when null
    const missingCode: Pick<SecretsCredentialTestOutcome, "ok" | "code" | "at"> = { ok: true, at: null };
    // @ts-expect-error at is always serialized, even when null
    const missingAt: Pick<SecretsCredentialTestOutcome, "ok" | "code" | "at"> = { ok: true, code: null };

    expect(completeOutcome).toEqual({ ok: false, code: 503, at: null });
    expect(missingCode).toEqual({ ok: true, at: null });
    expect(missingAt).toEqual({ ok: true, code: null });
  });
});
