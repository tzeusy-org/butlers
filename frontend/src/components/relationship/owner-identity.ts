/**
 * Owner identity setup predicate (bu-6m6ou0).
 *
 * The single source of truth for "does the owner still need identity setup":
 * OwnerSetupBanner renders its setup prompt from it, and EntityDetailPage uses
 * it to drive the Practical drawer's "(action needed)" state, so the two can
 * never disagree. Kept out of OwnerSetupBanner.tsx so that file stays
 * component-only (react-refresh/only-export-components).
 */

import type { EntityDetail, LinkedContactSummary } from "@/api/types";
import { useEntityLinkedContacts } from "@/hooks/use-entities";

/**
 * List the owner identity fields that are still missing.
 *
 * Returns null while the linked contacts are unknown (loading, or errored with
 * no cached result): a missing query result is not proof that Telegram facts
 * are absent. Otherwise returns the missing fields, empty when all are set.
 */
export function ownerIdentityMissing(
  entity: Pick<EntityDetail, "canonical_name">,
  contacts: LinkedContactSummary[] | undefined,
): string[] | null {
  if (contacts === undefined) return null;

  const nameIsPlaceholder =
    !entity.canonical_name?.trim() ||
    entity.canonical_name.trim().toLowerCase() === "owner";
  // Telegram has-handle facts surface as type "telegram_user_id" with the
  // "telegram:" prefix stripped from the display value. The deliverable chat ID
  // is numeric; a username handle is non-numeric — distinguish on that.
  const telegramValues = contacts
    .flatMap((c) => c.contact_info)
    .filter((e) => e.type === "telegram_user_id" && e.value)
    .map((e) => e.value!.trim());
  const hasTelegramChatId = telegramValues.some((v) => /^\d+$/.test(v));
  const hasTelegram = telegramValues.some((v) => !/^\d+$/.test(v));

  const missing: string[] = [];
  if (nameIsPlaceholder) missing.push("name");
  if (!hasTelegram) missing.push("Telegram handle");
  if (!hasTelegramChatId) missing.push("Telegram chat ID");
  return missing;
}

/**
 * Whether the owner entity needs identity setup, from the same linked-contacts
 * query the banner reads (shared react-query key, so no extra request).
 */
export function useOwnerIdentitySetup(entity: EntityDetail | undefined): {
  needsSetup: boolean;
  missing: string[] | null;
} {
  const linkedContacts = useEntityLinkedContacts(entity?.id);
  const missing = entity ? ownerIdentityMissing(entity, linkedContacts.data) : null;
  return { needsSetup: missing !== null && missing.length > 0, missing };
}
