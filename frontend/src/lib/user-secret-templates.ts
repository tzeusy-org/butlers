/**
 * User secret templates — known entity_info types for the owner entity.
 *
 * These are identity-bound credentials managed on the owner entity via
 * public.entity_info, as opposed to ecosystem-wide system secrets stored
 * in butler_secrets.
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

export const USER_SECRET_TEMPLATES: UserSecretTemplate[] = [
  // Telegram
  { type: "telegram", label: "Telegram Handle", description: "Telegram @username", category: "telegram", secured: false },
  { type: "telegram_chat_id", label: "Telegram Chat ID", description: "Bot-to-owner chat numeric ID", category: "telegram", secured: false },
  { type: "telegram_api_id", label: "Telegram API ID", description: "Telegram API application ID", category: "telegram", secured: false },
  { type: "telegram_api_hash", label: "Telegram API Hash", description: "Telegram API application hash", category: "telegram", secured: true },
  { type: "telegram_user_session", label: "Telegram User Session", description: "Telethon StringSession (managed via setup card)", category: "telegram", secured: true },
  // Home Assistant
  { type: "home_assistant_url", label: "Home Assistant URL", description: "HA instance base URL", category: "home_assistant", secured: false },
  { type: "home_assistant_token", label: "Home Assistant Token", description: "HA long-lived access token", category: "home_assistant", secured: true },
  // Email (user-scope)
  { type: "email", label: "Email Address", description: "Owner email address", category: "email", secured: false },
  { type: "email_password", label: "Email Password", description: "Owner email password or app password", category: "email", secured: true },
  // WhatsApp
  { type: "whatsapp_phone", label: "WhatsApp Phone", description: "E.164 phone number for WhatsApp", category: "whatsapp", secured: false },
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

/** Entity_info types shown in the type dropdown (excludes session — managed interactively). */
export const ENTITY_INFO_TYPES = [
  "telegram",
  "telegram_chat_id",
  "telegram_api_id",
  "telegram_api_hash",
  // telegram_user_session — managed via the interactive Telegram Session Setup card
  "home_assistant_url",
  "home_assistant_token",
  "email",
  "email_password",
  "whatsapp_phone",
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
