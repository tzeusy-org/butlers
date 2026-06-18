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
}

// NOTE: non-secret CHANNEL handles (telegram, telegram_chat_id, email,
// whatsapp_phone) are intentionally ABSENT — they belong in the relationship
// graph (entity_facts), not the secret store. Only secured credentials and the
// two whitelisted non-secret technical-config types (telegram_api_id,
// home_assistant_url) live in entity_info.
export const USER_SECRET_TEMPLATES: UserSecretTemplate[] = [
  // Telegram
  { type: "telegram_api_id", label: "Telegram API ID", description: "Telegram API application ID", category: "telegram", secured: false },
  { type: "telegram_api_hash", label: "Telegram API Hash", description: "Telegram API application hash", category: "telegram", secured: true },
  { type: "telegram_user_session", label: "Telegram User Session", description: "Telethon StringSession (managed via setup card)", category: "telegram", secured: true },
  // Home Assistant
  { type: "home_assistant_url", label: "Home Assistant URL", description: "HA instance base URL", category: "home_assistant", secured: false },
  { type: "home_assistant_token", label: "Home Assistant Token", description: "HA long-lived access token", category: "home_assistant", secured: true },
  // Email (user-scope) — only the password is a secret; the address is a contact channel.
  { type: "email_password", label: "Email Password", description: "Owner email password or app password", category: "email", secured: true },
];

export const USER_SECRET_CATEGORIES: UserSecretCategory[] = [
  "telegram",
  "home_assistant",
  "email",
  "whatsapp",
  "general",
];

/** Types that should always be masked in the UI. */
export const SECURED_USER_TYPES = new Set<string>([
  "telegram_api_hash",
  "telegram_user_session",
  "home_assistant_token",
  "email_password",
  "google_oauth_refresh",
]);

/** Entity_info types shown in the type dropdown (excludes session — managed interactively).
 *
 * Non-secret CHANNEL handles (telegram, telegram_chat_id, email, whatsapp_phone)
 * are intentionally excluded: they belong in the relationship graph
 * (entity_facts), not the secret store, and the backend rejects them with 422.
 * Only secured credentials and the whitelisted non-secret technical-config types
 * (telegram_api_id, home_assistant_url) are addable here. */
export const ENTITY_INFO_TYPES = [
  "telegram_api_id",
  "telegram_api_hash",
  // telegram_user_session — managed via the interactive Telegram Session Setup card
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

export function userCategoryFromType(type: string): UserSecretCategory {
  const template = USER_SECRET_TEMPLATES.find((t) => t.type === type);
  if (template) return template.category;
  return "general";
}

const USER_CATEGORY_ORDER = ["telegram", "home_assistant", "email", "whatsapp", "general"];

export function userCategoryIndex(category: string): number {
  const idx = USER_CATEGORY_ORDER.indexOf(category);
  return idx >= 0 ? idx : USER_CATEGORY_ORDER.length;
}

export function userCategoryLabel(category: string): string {
  const labels: Record<string, string> = {
    telegram: "Telegram",
    home_assistant: "Home Assistant",
    email: "Email",
    whatsapp: "WhatsApp",
    general: "General",
  };
  return labels[category] ?? category.charAt(0).toUpperCase() + category.slice(1);
}
