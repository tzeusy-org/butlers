/**
 * User secret templates — known entity_info types for the owner entity.
 *
 * These are identity-bound credentials managed on the owner entity via
 * public.entity_info, as opposed to ecosystem-wide system secrets stored
 * in butler_secrets.
 *
 * SEAM LAW (RFC 0004 Amendment 3, bu-oluyt): public.entity_info holds ONLY
 * secured credentials plus a small whitelist of non-secret TECHNICAL config
 * (telegram_api_id, home_assistant_url). Non-secret CHANNEL handles (telegram
 * handle/chat id, email address, whatsapp phone, etc.) are NOT secrets — they
 * live in the relationship graph (relationship.entity_facts as has-* facts) and
 * are managed via the entity's contact channels (ContactChannelCard /
 * OwnerSetupBanner), never this secrets surface. The backend rejects a
 * non-secret channel written to entity_info with HTTP 422.
 */

import {
  GOOGLE_APP_PASSWORDS_PROVENANCE,
  type SecretProvenance,
} from "./secret-templates.ts";

export type UserSecretCategory =
  | "telegram"
  | "home_assistant"
  | "email"
  | "whatsapp"
  | "general";

export interface UserSecretTemplate {
  type: string;
  label: string;
  description: string;
  category: UserSecretCategory;
  secured: boolean;
  /** Static source page for a value the owner needs to obtain or regenerate. */
  provenance?: SecretProvenance;
}

const TELEGRAM_API_DEVELOPMENT_TOOLS_PROVENANCE: SecretProvenance = {
  label: "Telegram API development tools",
  url: "https://my.telegram.org/apps",
};

// NOTE: non-secret CHANNEL handles (telegram, telegram_chat_id, email,
// whatsapp_phone) are intentionally ABSENT — they belong in the relationship
// graph (entity_facts), not the secret store. Only secured credentials and the
// two whitelisted non-secret technical-config types (telegram_api_id,
// home_assistant_url) live in entity_info.
export const USER_SECRET_TEMPLATES: UserSecretTemplate[] = [
  // Telegram
  {
    type: "telegram_api_id",
    label: "Telegram API ID",
    description: "Telegram API application ID",
    category: "telegram",
    secured: false,
    provenance: TELEGRAM_API_DEVELOPMENT_TOOLS_PROVENANCE,
  },
  {
    type: "telegram_api_hash",
    label: "Telegram API Hash",
    description: "Telegram API application hash",
    category: "telegram",
    secured: true,
    provenance: TELEGRAM_API_DEVELOPMENT_TOOLS_PROVENANCE,
  },
  { type: "telegram_user_session", label: "Telegram User Session", description: "Telethon StringSession (managed via setup card)", category: "telegram", secured: true },
  // Home Assistant
  { type: "home_assistant_url", label: "Home Assistant URL", description: "HA instance base URL", category: "home_assistant", secured: false },
  { type: "home_assistant_token", label: "Home Assistant Token", description: "HA long-lived access token", category: "home_assistant", secured: true },
  // Email (user-scope) — only the password is a secret; the address is a contact channel.
  {
    type: "email_password",
    label: "Email Password",
    description: "Owner email password or app password",
    category: "email",
    secured: true,
    provenance: GOOGLE_APP_PASSWORDS_PROVENANCE,
  },
];

/**
 * Resolve an owner-credential source page from the raw entity_info types that
 * make up a Passport user row. Provider slugs are display-level groupings: for
 * example, Telegram API values and a user session share `telegram_bot`.
 * Show a link only when every contributing type resolves to the same static
 * source, so an unrelated session or unknown field never inherits an API link.
 */
export function userSecretProvenanceForTypes(
  types: readonly string[] | undefined,
): SecretProvenance | undefined {
  if (!types?.length) return undefined;

  let provenance: SecretProvenance | undefined;
  for (const type of types) {
    const candidate = USER_SECRET_TEMPLATES.find((template) => template.type === type)?.provenance;
    if (!candidate) return undefined;
    if (
      provenance
      && (provenance.label !== candidate.label || provenance.url !== candidate.url)
    ) {
      return undefined;
    }
    provenance = candidate;
  }
  return provenance;
}

/**
 * Entity_info types that make up each provider-level Passport row.
 *
 * The inventory no longer publishes the raw entity_info type behind a row
 * (bu-iph56), so provenance is resolved from the provider's whole possible
 * grouping rather than from the types a particular row happened to contain.
 * Only groupings whose members share one static source page resolve to a link;
 * `telegram_bot` deliberately does not, because it can include the
 * interactively-managed user session, which has no source page of its own.
 */
const USER_PROVIDER_TEMPLATE_TYPES: Record<string, readonly string[]> = {
  telegram_bot: ["telegram_api_id", "telegram_api_hash", "telegram_user_session"],
  homeassistant: ["home_assistant_url", "home_assistant_token"],
  email: ["email_password"],
};

/**
 * Resolve an owner-credential source page from a Passport provider slug.
 *
 * Conservative by construction: a provider whose grouping mixes sources — or
 * includes a type with no source page — resolves to undefined rather than
 * showing a link that is right for only part of the row.
 */
export function userSecretProvenanceForProvider(
  provider: string | undefined,
): SecretProvenance | undefined {
  if (!provider) return undefined;
  return userSecretProvenanceForTypes(USER_PROVIDER_TEMPLATE_TYPES[provider]);
}

/** Entity_info types shown in the type dropdown (excludes session — managed interactively).
 *
 * Non-secret CHANNEL handles (telegram, telegram_chat_id, email, whatsapp_phone)
 * are intentionally excluded: they belong in the relationship graph
 * (entity_facts), not the secret store, and the backend rejects them with 422.
 * Only secured credentials and the whitelisted non-secret technical-config types
 * (telegram_api_id, home_assistant_url) are addable here. */
export const ENTITY_INFO_TYPES = [
  "telegram_api_id",
  // Telegram API hash and user session — managed via the interactive Telegram Session Setup card
  "home_assistant_url",
  "home_assistant_token",
  "email_password",
  "google_oauth_refresh",
  "other",
] as const;

export function entityInfoTypeLabel(type: string): string {
  const template = USER_SECRET_TEMPLATES.find((t) => t.type === type);
  if (template) return template.label;
  switch (type) {
    case "google_oauth_refresh": return "Google OAuth Refresh";
    case "other": return "Other";
    default: return type;
  }
}
