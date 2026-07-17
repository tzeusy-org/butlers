export type SecretCategory =
  | "telegram"
  | "email"
  | "google"
  | "home_assistant"
  | "general";

/** "Where to get this" provenance line — a label + URL pointing at the
 * console/page an owner would visit to obtain or regenerate the value.
 * Static data only, hand-curated per key; never LLM-generated. */
export interface SecretProvenance {
  label: string;
  url: string;
}

export const GOOGLE_APP_PASSWORDS_PROVENANCE: SecretProvenance = {
  label: "Google App Passwords",
  url: "https://myaccount.google.com/apppasswords",
};

export interface SecretTemplate {
  key: string;
  description: string;
  category: SecretCategory;
  /** When false, the value is visible in the UI (not redacted). Default: true. */
  is_sensitive?: boolean;
  /** "Where to get this" sourcing hint, rendered in the add-panel and the
   * rotate/set-value inline panels. Omit when there's no fixed console/page
   * to point at (e.g. a value the owner already knows, like an address). */
  provenance?: SecretProvenance;
}

export const SECRET_TEMPLATES: SecretTemplate[] = [
  // Telegram — butler-owned bot credential
  {
    key: "BUTLER_TELEGRAM_TOKEN",
    description: "Telegram bot token (from @BotFather)",
    category: "telegram",
    provenance: { label: "@BotFather", url: "https://t.me/BotFather" },
  },
  // Email — butler-owned mailbox credentials
  { key: "BUTLER_EMAIL_ADDRESS", description: "Butler email address", category: "email" },
  {
    key: "BUTLER_EMAIL_PASSWORD",
    description: "Butler email password or app password",
    category: "email",
    provenance: GOOGLE_APP_PASSWORDS_PROVENANCE,
  },
  // Google OAuth
  {
    key: "GOOGLE_OAUTH_CLIENT_ID",
    description: "Google OAuth client ID",
    category: "google",
    provenance: { label: "Google Cloud Console → Credentials", url: "https://console.cloud.google.com/apis/credentials" },
  },
  {
    key: "GOOGLE_OAUTH_CLIENT_SECRET",
    description: "Google OAuth client secret",
    category: "google",
    provenance: { label: "Google Cloud Console → Credentials", url: "https://console.cloud.google.com/apis/credentials" },
  },
  // Blob storage (S3-compatible) — self-hosted Garage in prod, MinIO in dev.
  // No public web console; provenance points at the setup doc (endpoint,
  // bucket, region are plain config; the two credential keys come from
  // Bitwarden per that doc).
  {
    key: "BLOB_S3_ENDPOINT_URL",
    description: "S3-compatible endpoint URL",
    category: "general",
    is_sensitive: false,
    provenance: { label: "Blob storage setup guide", url: "https://github.com/Tzeusy/butlers/blob/main/docs/data_and_storage/blob-storage.md" },
  },
  {
    key: "BLOB_S3_BUCKET",
    description: "Bucket name",
    category: "general",
    is_sensitive: false,
    provenance: { label: "Blob storage setup guide", url: "https://github.com/Tzeusy/butlers/blob/main/docs/data_and_storage/blob-storage.md" },
  },
  {
    key: "BLOB_S3_REGION",
    description: "Region (e.g. garage, us-east-1)",
    category: "general",
    is_sensitive: false,
    provenance: { label: "Blob storage setup guide", url: "https://github.com/Tzeusy/butlers/blob/main/docs/data_and_storage/blob-storage.md" },
  },
  {
    key: "BLOB_S3_ACCESS_KEY_ID",
    description: "S3 access key ID",
    category: "general",
    provenance: { label: "Bitwarden (see setup guide)", url: "https://github.com/Tzeusy/butlers/blob/main/docs/data_and_storage/blob-storage.md" },
  },
  {
    key: "BLOB_S3_SECRET_ACCESS_KEY",
    description: "S3 secret access key",
    category: "general",
    provenance: { label: "Bitwarden (see setup guide)", url: "https://github.com/Tzeusy/butlers/blob/main/docs/data_and_storage/blob-storage.md" },
  },
];

/**
 * Owner identity credentials are surfaced and configured on /secrets. Google
 * app credentials (client ID + secret) are shared system secrets edited on the
 * Google system-credential pages; Google account refresh tokens live on
 * companion entity_info rows.
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
  "general",
];

export function categoryFromKey(key: string): SecretCategory {
  const upper = key.toUpperCase();
  if (upper.includes("TELEGRAM")) return "telegram";
  if (upper.includes("EMAIL") || upper.includes("SMTP") || upper.includes("IMAP")) return "email";
  if (upper.includes("GOOGLE") || upper.includes("GOOGLE_CLIENT")) return "google";
  if (upper.includes("HOME_ASSISTANT")) return "home_assistant";
  return "general";
}
