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
  // Telegram — bot and user-client credentials
  { key: "BUTLER_TELEGRAM_TOKEN", description: "Telegram bot token (from @BotFather)", category: "telegram" },
  { key: "BUTLER_TELEGRAM_CHAT_ID", description: "Telegram chat ID for bot messages", category: "telegram" },
  { key: "USER_TELEGRAM_TOKEN", description: "User Telegram bot token (for user-scoped ops)", category: "telegram" },
  { key: "TELEGRAM_API_ID", description: "Telegram API ID (from my.telegram.org)", category: "telegram" },
  { key: "TELEGRAM_API_HASH", description: "Telegram API hash (from my.telegram.org)", category: "telegram" },
  { key: "TELEGRAM_USER_SESSION", description: "Telegram user-client session string", category: "telegram" },
  // Email — butler and user credentials
  { key: "BUTLER_EMAIL_ADDRESS", description: "Butler email address", category: "email" },
  { key: "BUTLER_EMAIL_PASSWORD", description: "Butler email password or app password", category: "email" },
  { key: "USER_EMAIL_ADDRESS", description: "User email address", category: "email" },
  { key: "USER_EMAIL_PASSWORD", description: "User email password or app password", category: "email" },
  // Google OAuth
  { key: "GOOGLE_OAUTH_CLIENT_ID", description: "Google OAuth client ID", category: "google" },
  { key: "GOOGLE_OAUTH_CLIENT_SECRET", description: "Google OAuth client secret", category: "google" },
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
