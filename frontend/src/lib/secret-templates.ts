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
  { key: "ANTHROPIC_API_KEY", description: "Anthropic Claude API key", category: "core" },
  { key: "BUTLER_TELEGRAM_TOKEN", description: "Telegram bot token", category: "telegram" },
  { key: "BUTLER_TELEGRAM_CHAT_ID", description: "Telegram chat ID", category: "telegram" },
  { key: "BUTLER_EMAIL_ADDRESS", description: "Butler email address", category: "email" },
  { key: "BUTLER_EMAIL_PASSWORD", description: "Email account password or app password", category: "email" },
  { key: "BUTLER_EMAIL_SMTP_HOST", description: "SMTP server hostname", category: "email" },
  { key: "BUTLER_EMAIL_IMAP_HOST", description: "IMAP server hostname", category: "email" },
  { key: "GOOGLE_CLIENT_ID", description: "Google OAuth client ID", category: "google" },
  { key: "GOOGLE_CLIENT_SECRET", description: "Google OAuth client secret", category: "google" },
  { key: "GEMINI_API_KEY", description: "Google Gemini API key", category: "gemini" },
];

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
  if (upper.includes("ANTHROPIC") || upper.includes("DATABASE_URL") || upper.includes("SECRET_KEY")) {
    return "core";
  }
  return "general";
}
