import { describe, expect, it } from "vitest";

import { userSecretProvenanceForTypes } from "./user-secret-templates.ts";

describe("userSecretProvenanceForTypes", () => {
  it("resolves the shared provenance for email-password and Telegram API fields", () => {
    expect(userSecretProvenanceForTypes(["email_password"])).toEqual({
      label: "Google App Passwords",
      url: "https://myaccount.google.com/apppasswords",
    });
    expect(
      userSecretProvenanceForTypes(["telegram_api_id", "telegram_api_hash"]),
    ).toEqual({
      label: "Telegram API development tools",
      url: "https://my.telegram.org/apps",
    });
  });

  it("does not infer a provenance link for unmapped or mixed field types", () => {
    expect(
      userSecretProvenanceForTypes(["telegram_user_session"]),
    ).toBeUndefined();
    expect(
      userSecretProvenanceForTypes([
        "telegram_api_id",
        "telegram_user_session",
      ]),
    ).toBeUndefined();
    expect(
      userSecretProvenanceForTypes(["google_oauth_refresh"]),
    ).toBeUndefined();
  });
});
