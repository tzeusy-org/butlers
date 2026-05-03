import { useMemo } from "react";
import { useParams } from "react-router";

import ContactDetailView from "@/components/relationship/ContactDetailView";
import { PulseStrip } from "@/components/relationship/PulseStrip";
import { Page } from "@/components/ui/page";
import { useContact } from "@/hooks/use-contacts";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Build a short subtitle from channel handles and contact info entries. */
function buildSubtitle(contact: {
  email: string | null;
  phone: string | null;
  contact_info?: { type: string; value: string | null; is_primary: boolean }[];
}): string | undefined {
  const parts: string[] = [];

  // Prefer contact_info entries when present (richer / more up to date)
  const info = contact.contact_info ?? [];
  if (info.length > 0) {
    const email = info.find((ci) => ci.type === "email" && ci.is_primary)
      ?? info.find((ci) => ci.type === "email");
    const telegram = info.find((ci) => ci.type === "telegram" && ci.is_primary)
      ?? info.find((ci) => ci.type === "telegram");

    if (email?.value) parts.push(email.value);
    if (telegram?.value) parts.push(telegram.value);
  } else {
    // Fall back to legacy flat fields
    if (contact.email) parts.push(contact.email);
    if (contact.phone) parts.push(contact.phone);
  }

  return parts.length > 0 ? parts.join(" · ") : undefined;
}

// ---------------------------------------------------------------------------
// ContactDetailPage
// ---------------------------------------------------------------------------

export default function ContactDetailPage() {
  const { contactId } = useParams<{ contactId: string }>();
  const { data: contact, isLoading, error } = useContact(contactId);

  const breadcrumbs = useMemo(
    () => [
      { label: "Contacts", href: "/contacts" },
      { label: contact?.full_name ?? contactId ?? "Contact" },
    ],
    [contact?.full_name, contactId],
  );

  const subtitle = contact ? buildSubtitle(contact) : undefined;

  return (
    <Page
      archetype="detail"
      title={contact?.full_name ?? contactId ?? "Contact"}
      description={subtitle}
      loading={isLoading}
      error={error ?? null}
      breadcrumbs={breadcrumbs}
    >
      {contact && (
        <>
          {/* Pulse strip — relationship closeness at-a-glance (entity-linked contacts only) */}
          {contact.entity_id && (
            <PulseStrip
              entityId={contact.entity_id}
              dunbarTier={null}
              isPinned={false}
            />
          )}

          {/* Primary: contact detail view (info, channels, entity link, add info form) */}
          <ContactDetailView contact={contact} />
        </>
      )}
    </Page>
  );
}
