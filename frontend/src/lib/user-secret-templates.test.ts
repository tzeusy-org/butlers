import { describe, expect, it } from "vitest";

import {
  userSecretProvenanceForProvider,
  userSecretProvenanceForTypes,
} from "./user-secret-templates.ts";

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

describe("userSecretProvenanceForProvider [bu-iph56]", () => {
  it("resolves the source page for a provider whose whole grouping shares one", () => {
    expect(userSecretProvenanceForProvider("email")).toEqual({
      label: "Google App Passwords",
      url: "https://myaccount.google.com/apppasswords",
    });
  });

  it("withholds the link when the provider grouping mixes sources", () => {
    // Documented fidelity drop from bu-iph56: a telegram_bot row made up of
    // only the API id + hash used to show the Telegram API link, because the
    // inventory published the entity_info types behind the row. It no longer
    // does, so the grouping is resolved as a whole — and that grouping can
    // include the interactively-managed user session, which has no source
    // page. Withholding the link is the conservative direction: a wrong link
    // for part of a row is worse than no link.
    expect(userSecretProvenanceForProvider("telegram_bot")).toBeUndefined();
  });

  it("returns undefined for providers with no templates and for no provider", () => {
    expect(userSecretProvenanceForProvider("google")).toBeUndefined();
    expect(userSecretProvenanceForProvider("homeassistant")).toBeUndefined();
    expect(userSecretProvenanceForProvider(undefined)).toBeUndefined();
  });
});
