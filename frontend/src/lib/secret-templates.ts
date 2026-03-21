export type SecretCategory =
  | "telegram"
  | "email"
  | "google"
  | "home_assistant"
  | "storage"
  | "general";

export interface SecretTemplate {
  key: string;
  description: string;
  category: SecretCategory;
  /** When false, the value is visible in the UI (not redacted). Default: true. */
  is_sensitive?: boolean;
}

export const SECRET_TEMPLATES: SecretTemplate[] = [
  // Telegram — butler-owned bot credential
  { key: "BUTLER_TELEGRAM_TOKEN", description: "Telegram bot token (from @BotFather)", category: "telegram" },
  // Email — butler-owned mailbox credentials
  { key: "BUTLER_EMAIL_ADDRESS", description: "Butler email address", category: "email" },
  { key: "BUTLER_EMAIL_PASSWORD", description: "Butler email password or app password", category: "email" },
  // Google OAuth
  { key: "GOOGLE_OAUTH_CLIENT_ID", description: "Google OAuth client ID", category: "google" },
  { key: "GOOGLE_OAUTH_CLIENT_SECRET", description: "Google OAuth client secret", category: "google" },
  // S3-compatible blob storage (Garage, MinIO, AWS S3, etc.)
  { key: "BLOB_S3_ENDPOINT_URL", description: "S3-compatible endpoint URL (e.g. http://nas:9000)", category: "storage", is_sensitive: false },
  { key: "BLOB_S3_BUCKET", description: "S3 bucket name for blob storage", category: "storage", is_sensitive: false },
  { key: "BLOB_S3_REGION", description: "S3 region (e.g. garage, us-east-1)", category: "storage", is_sensitive: false },
  { key: "BLOB_S3_ACCESS_KEY_ID", description: "S3 blob storage access key ID", category: "storage" },
  { key: "BLOB_S3_SECRET_ACCESS_KEY", description: "S3 blob storage secret access key", category: "storage" },
];

/**
 * Owner identity credentials (Telegram API keys, user session, HA token,
 * Google OAuth refresh) are now managed as entity_info entries
 * on the owner entity. Configure them at /entities/{owner_entity_id} via the
 * "Credentials & Info" section.
 *
 * Contact-level channel identifiers (Telegram chat ID, email address, phone)
 * remain on the contact at /contacts/{owner_id}.
 *
 * Migrated keys (no longer shown here):
 *   TELEGRAM_CHAT_ID, USER_TELEGRAM_TOKEN, TELEGRAM_API_ID, TELEGRAM_API_HASH,
 *   TELEGRAM_USER_SESSION, USER_EMAIL_ADDRESS, USER_EMAIL_PASSWORD
 */

export const SECRET_CATEGORIES: SecretCategory[] = [
  "telegram",
  "email",
  "google",
  "home_assistant",
  "storage",
  "general",
];

export function categoryFromKey(key: string): SecretCategory {
  const upper = key.toUpperCase();
  if (upper.includes("TELEGRAM")) return "telegram";
  if (upper.includes("EMAIL") || upper.includes("SMTP") || upper.includes("IMAP")) return "email";
  if (upper.includes("GOOGLE") || upper.includes("GOOGLE_CLIENT")) return "google";
  if (upper.includes("HOME_ASSISTANT")) return "home_assistant";
  if (upper.includes("BLOB_S3") || upper.includes("S3_ACCESS") || upper.includes("S3_SECRET"))
    return "storage";
  return "general";
}
