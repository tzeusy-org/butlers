import type { ReachClause } from "@/api/reach";

const SECURITY_KIND_LABEL: Record<string, string> = {
  new_sign_in: "new sign-in",
  password_changed: "password changed",
  mfa_changed: "two-step sign-in changed",
  recovery_changed: "recovery option changed",
  deletion_scheduled: "account deletion scheduled",
};

const CHANNEL_LABEL: Record<string, string> = {
  email: "email",
  telegram: "Telegram",
  whatsapp: "WhatsApp",
  dashboard: "dashboard",
};

/** The matchers of one clause, in words. All present matchers must hold together. */
export function describeClause(clause: ReachClause): string {
  const parts: string[] = [];
  if (clause.entity_id) parts.push(clause.entity_name ?? "a person you named");
  if (clause.sender_domain) parts.push(`anyone at ${clause.sender_domain}`);
  if (clause.security_kind) {
    parts.push(`account alert: ${SECURITY_KIND_LABEL[clause.security_kind] ?? clause.security_kind}`);
  }
  if (clause.channel) {
    const label = CHANNEL_LABEL[clause.channel] ?? clause.channel;
    parts.push(parts.length === 0 ? `anything on ${label}` : `on ${label}`);
  }
  return parts.join(", ");
}
