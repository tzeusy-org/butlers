import type { SecretEntry } from "@/api/types.ts";
import { categoryFromKey, SECRET_TEMPLATES } from "@/lib/secret-templates";

const CATEGORY_ORDER = ["core", "telegram", "email", "google", "gemini", "general"];

export type SecretRowState = "local" | "inherited" | "missing";

export interface SecretDisplayRow {
  key: string;
  category: string;
  description: string | null;
  source: string;
  rowState: SecretRowState;
  updatedAt: string | null;
  apiSecret: SecretEntry | null;
}

function normalizeSource(source: string): string {
  return source.trim().toLowerCase();
}

function isLocalSource(source: string): boolean {
  const normalized = normalizeSource(source);
  return normalized === "database" || normalized === "local";
}

function resolveRowState(secret: SecretEntry): SecretRowState {
  if (!secret.is_set) {
    return "missing";
  }
  if (isLocalSource(secret.source)) {
    return "local";
  }
  return "inherited";
}

export function buildSecretRows(secrets: SecretEntry[]): SecretDisplayRow[] {
  const byKey = new Map<string, SecretDisplayRow>();

  for (const template of SECRET_TEMPLATES) {
    byKey.set(template.key.toUpperCase(), {
      key: template.key,
      category: template.category,
      description: template.description,
      source: "null",
      rowState: "missing",
      updatedAt: null,
      apiSecret: null,
    });
  }

  for (const secret of secrets) {
    const normalizedKey = secret.key.toUpperCase();
    const existing = byKey.get(normalizedKey);
    byKey.set(normalizedKey, {
      key: secret.key,
      category: secret.category || existing?.category || categoryFromKey(secret.key),
      description: secret.description ?? existing?.description ?? null,
      source: secret.source,
      rowState: resolveRowState(secret),
      updatedAt: secret.updated_at,
      apiSecret: secret,
    });
  }

  return Array.from(byKey.values()).sort((a, b) => {
    const ai = CATEGORY_ORDER.indexOf(a.category);
    const bi = CATEGORY_ORDER.indexOf(b.category);
    if (ai !== -1 && bi !== -1 && ai !== bi) {
      return ai - bi;
    }
    if (ai !== -1 && bi === -1) {
      return -1;
    }
    if (bi !== -1 && ai === -1) {
      return 1;
    }
    if (a.category !== b.category) {
      return a.category.localeCompare(b.category);
    }
    return a.key.localeCompare(b.key);
  });
}
