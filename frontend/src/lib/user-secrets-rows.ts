/**
 * Adapter: map EntityInfoEntry[] into SecretDisplayRow[] for SecretsTable reuse.
 *
 * Follows the same merge pattern as buildSecretRows() in secrets-rows.ts:
 * template rows are created first, then actual entries override them.
 */

import type { EntityInfoEntry } from "@/api/types.ts";
import type { SecretDisplayRow } from "@/lib/secrets-rows";
import {
  USER_SECRET_TEMPLATES,
  userCategoryFromType,
  entityInfoTypeLabel,
  userCategoryIndex,
} from "@/lib/user-secret-templates";

export function buildUserSecretRows(entries: EntityInfoEntry[]): SecretDisplayRow[] {
  const byType = new Map<string, SecretDisplayRow>();

  // Seed with known templates (missing state)
  for (const template of USER_SECRET_TEMPLATES) {
    byType.set(template.type, {
      key: template.type,
      category: template.category,
      description: template.description,
      source: "null",
      rowState: "missing",
      updatedAt: null,
      apiSecret: null,
      entityInfoEntry: null,
    });
  }

  // Overlay actual entries
  for (const entry of entries) {
    const existing = byType.get(entry.type);
    byType.set(entry.type, {
      key: entry.type,
      category: existing?.category ?? userCategoryFromType(entry.type),
      description: existing?.description ?? entityInfoTypeLabel(entry.type),
      source: "owner",
      rowState: "local",
      updatedAt: null, // entity_info has no updated_at
      apiSecret: null,
      entityInfoEntry: entry,
    });
  }

  return Array.from(byType.values()).sort((a, b) => {
    const ai = userCategoryIndex(a.category);
    const bi = userCategoryIndex(b.category);
    if (ai !== bi) return ai - bi;
    return a.key.localeCompare(b.key);
  });
}
