export type SecretCategory =
  | "core"
  | "telegram"
  | "email"
  | "google"
  | "gemini"
  | "general";

export interface SecretTemplate {
  key: string;
  description: string;
  category: SecretCategory;
}

export const SECRET_TEMPLATES: SecretTemplate[] = [
  // Core — LLM API keys
  { key: "ANTHROPIC_API_KEY", description: "Anthropic Claude API key", category: "core" },
  { key: "OPENAI_API_KEY", description: "OpenAI API key", category: "core" },
  { key: "GOOGLE_API_KEY", description: "Google API key (Maps, etc.)", category: "core" },
  { key: "GEMINI_API_KEY", description: "Google Gemini API key", category: "gemini" },
  // Telegram — butler-owned bot credential
  { key: "BUTLER_TELEGRAM_TOKEN", description: "Telegram bot token (from @BotFather)", category: "telegram" },
  // Email — butler-owned mailbox credentials
  { key: "BUTLER_EMAIL_ADDRESS", description: "Butler email address", category: "email" },
  { key: "BUTLER_EMAIL_PASSWORD", description: "Butler email password or app password", category: "email" },
  // Google OAuth
  { key: "GOOGLE_OAUTH_CLIENT_ID", description: "Google OAuth client ID", category: "google" },
  { key: "GOOGLE_OAUTH_CLIENT_SECRET", description: "Google OAuth client secret", category: "google" },
];

/**
 * Owner identity credentials (email, telegram handle, API keys, etc.) are now
 * managed as secured contact_info entries on the owner contact. Configure them
 * at /contacts/{owner_id} via "Add contact info".
 *
 * Migrated keys (no longer shown here):
 *   TELEGRAM_CHAT_ID, USER_TELEGRAM_TOKEN, TELEGRAM_API_ID, TELEGRAM_API_HASH,
 *   TELEGRAM_USER_SESSION, USER_EMAIL_ADDRESS, USER_EMAIL_PASSWORD
 */

export const SECRET_CATEGORIES: SecretCategory[] = [
  "core",
  "telegram",
  "email",
  "google",
  "gemini",
  "general",
];

export function categoryFromKey(key: string): SecretCategory {
  const upper = key.toUpperCase();
  if (upper.includes("TELEGRAM")) return "telegram";
  if (upper.includes("EMAIL") || upper.includes("SMTP") || upper.includes("IMAP")) return "email";
  if (upper.includes("GOOGLE") || upper.includes("GOOGLE_CLIENT")) return "google";
  if (upper.includes("GEMINI")) return "gemini";
  if (
    upper.includes("ANTHROPIC")
    || upper.includes("OPENAI")
    || upper.includes("DATABASE_URL")
    || upper.includes("SECRET_KEY")
  ) {
    return "core";
  }
  return "general";
}
