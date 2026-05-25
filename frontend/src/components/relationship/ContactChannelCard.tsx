/**
 * ContactChannelCard — entity detail contact-channel card.
 *
 * Renders one collapsed row per linked contact when an entity has linked
 * contacts. Expand-on-click shows full channel list with channel-specific
 * actions (edit, delete) via COMPAT-ONLY contact-keyed endpoints.
 *
 * Data source:
 *   Primary:  GET /relationship/entities/{entityId}/linked-contacts
 *             Returns enriched LinkedContactSummary with contact_info[],
 *             labels[], and preferred_channel. No N+1 getContact() fanout
 *             is needed for the collapsed view.
 *
 * COMPAT-ONLY reads/writes (temporary, during migration window):
 *   - createContactInfo, patchContactInfo, deleteContactInfo — write to
 *     contact_keyed endpoints. Remove when bu-k9ylx (write-path cut-over)
 *     completes.
 *   - patchContact (preferred_channel) — contact-keyed write. Remove when
 *     bu-k9ylx completes.
 *   - revealContactSecret — contact-keyed reveal for secured=true entries
 *     that still live in contact_info. Remove when bu-pl8fy (secured
 *     migration to entity_info) completes.
 *
 * See: docs/reports/contact-detail-parity-inventory-2026-05-25.md
 */

import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, ChevronRight, Pencil, Plus, Trash2, X } from "lucide-react";
import { toast } from "sonner";

import type { ContactInfoEntry, Label, LinkedContactSummary } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label as FormLabel } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { categoryHueVar } from "@/components/ui/ButlerMark";
import { ENTITY_BADGE_TEXT } from "@/lib/entity-model";
import { useEntityLinkedContacts } from "@/hooks/use-entities";
import {
  useCreateContactInfo,
  useDeleteContactInfo,
  usePatchContact,
  usePatchContactInfo,
  useRevealContactSecret,
} from "@/hooks/use-contacts";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/**
 * Auto-hide timeout (ms) for revealed secrets.
 * After this duration, the revealed value is masked again without requiring
 * an explicit "Hide" click. MUST NOT be set to 0 — secrets must not persist
 * visibly indefinitely.
 */
const REVEAL_AUTO_HIDE_MS = 30_000;

// COMPAT-ONLY: contact_info types currently writable via contact-keyed endpoints.
// Will shrink as bu-k9ylx and bu-e2ja9 land.
const CONTACT_INFO_TYPES = [
  "email",
  "phone",
  "telegram",
  "telegram_chat_id",
  "website",
  "home_assistant_url",
  "other",
] as const;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function contactInfoTypeLabel(type: string): string {
  switch (type) {
    case "email": return "Email";
    case "phone": return "Phone";
    case "telegram": return "Telegram";
    case "telegram_chat_id": return "Telegram Chat ID";
    case "website": return "Website";
    case "home_assistant_url": return "Home Assistant URL";
    case "other": return "Other";
    default: return type;
  }
}

function inputPlaceholder(type: string): string {
  switch (type) {
    case "email": return "you@example.com";
    case "telegram": return "@handle";
    case "telegram_chat_id": return "123456789";
    case "home_assistant_url": return "http://homeassistant.local:8123";
    default: return "";
  }
}

function sanitizePhoneHref(phone: string): string {
  return phone.replace(/[\s()]/g, "");
}

function labelStyle(label: Label): string {
  return label.color ?? categoryHueVar(label.name);
}

// ---------------------------------------------------------------------------
// SecuredChannelEntry — click-to-reveal with auto-hide timer
// ---------------------------------------------------------------------------

function SecuredChannelEntry({
  entry,
  contactId,
}: {
  entry: ContactInfoEntry;
  contactId: string;
}) {
  const [revealed, setRevealed] = useState<string | null>(null);
  const [isRevealing, setIsRevealing] = useState(false);
  const autoHideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // COMPAT-ONLY: revealContactSecret used for secured=true contact_info entries.
  // After bu-pl8fy migrates these to entity_info, switch to revealEntitySecret.
  const revealMutation = useRevealContactSecret();

  // Auto-hide the revealed secret 30s after it becomes visible.
  // useEffect ensures the timer is reset whenever `revealed` changes and is
  // cleaned up on unmount (no stale-closure leak, no overlapping timers).
  useEffect(() => {
    if (revealed === null) return;
    const id = setTimeout(() => setRevealed(null), REVEAL_AUTO_HIDE_MS);
    autoHideTimerRef.current = id;
    return () => {
      clearTimeout(id);
      autoHideTimerRef.current = null;
    };
  }, [revealed]);

  function handleReveal() {
    if (isRevealing || revealed !== null) return;
    setIsRevealing(true);
    revealMutation.mutate(
      { contactId, infoId: entry.id },
      {
        onSuccess: (data) => {
          // IMPORTANT: never log the secret value
          setRevealed(data.value ?? "");
          setIsRevealing(false);
          // Auto-hide is managed by the useEffect above.
        },
        onError: () => {
          setIsRevealing(false);
        },
      },
    );
  }

  function handleHide() {
    // Clearing the timer here is redundant (useEffect cleanup handles it on
    // the next render), but makes the intent explicit.
    if (autoHideTimerRef.current !== null) {
      clearTimeout(autoHideTimerRef.current);
      autoHideTimerRef.current = null;
    }
    setRevealed(null);
  }

  return (
    <span className="inline-flex items-center gap-2">
      {revealed !== null ? (
        <>
          <span data-testid="revealed-secret" className="text-sm font-mono">{revealed}</span>
          <Button
            variant="outline"
            size="sm"
            className="h-6 px-2 text-xs"
            onClick={handleHide}
          >
            Hide
          </Button>
        </>
      ) : (
        <>
          <span
            data-testid="masked-secret"
            className="text-muted-foreground text-sm font-mono tracking-widest"
          >
            ••••••••
          </span>
          <Button
            variant="outline"
            size="sm"
            className="h-6 px-2 text-xs"
            onClick={handleReveal}
            disabled={isRevealing}
          >
            {isRevealing ? "Revealing..." : "Reveal"}
          </Button>
        </>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// ChannelValue — renders non-secured channel value with appropriate href
// ---------------------------------------------------------------------------

function ChannelValue({ entry }: { entry: ContactInfoEntry }) {
  const val = entry.value;
  if (!val) return <span className="text-muted-foreground italic text-sm">—</span>;
  if (entry.type === "email") {
    return (
      <a href={`mailto:${val}`} className="text-sm text-primary hover:underline">
        {val}
      </a>
    );
  }
  if (entry.type === "phone") {
    return (
      <a href={`tel:${sanitizePhoneHref(val)}`} className="text-sm text-primary hover:underline">
        {val}
      </a>
    );
  }
  return <span className="text-sm">{val}</span>;
}

// ---------------------------------------------------------------------------
// ExpandedContactInfoRow — editable channel row with edit/delete actions
// COMPAT-ONLY: mutations use contact-keyed endpoints per migration inventory.
// ---------------------------------------------------------------------------

function ExpandedContactInfoRow({
  entry,
  contactId,
}: {
  entry: ContactInfoEntry;
  contactId: string;
}) {
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(entry.value ?? "");

  // COMPAT-ONLY: patchContactInfo/deleteContactInfo via contact-keyed endpoints.
  // Remove after bu-k9ylx (write-path cut-over) and bu-e2ja9 (drop) complete.
  const patchInfo = usePatchContactInfo();
  const deleteInfo = useDeleteContactInfo();

  function handleDelete() {
    if (!window.confirm(`Delete this ${contactInfoTypeLabel(entry.type)} entry?`)) return;
    deleteInfo.mutate(
      { contactId, infoId: entry.id },
      {
        onSuccess: () => toast.success(`Removed ${contactInfoTypeLabel(entry.type)} entry.`),
        onError: (err) =>
          toast.error(`Failed to delete: ${err instanceof Error ? err.message : "Unknown error"}`),
      },
    );
  }

  function handleSaveEdit() {
    const trimmed = editValue.trim();
    if (!trimmed) {
      toast.error("Value cannot be empty.");
      return;
    }
    if (trimmed === entry.value) {
      setEditing(false);
      return;
    }
    patchInfo.mutate(
      { contactId, infoId: entry.id, request: { value: trimmed } },
      {
        onSuccess: () => {
          toast.success(`Updated ${contactInfoTypeLabel(entry.type)} entry.`);
          setEditing(false);
        },
        onError: (err) =>
          toast.error(`Failed to update: ${err instanceof Error ? err.message : "Unknown error"}`),
      },
    );
  }

  return (
    <div className="flex items-center gap-2 group py-1">
      <span className="text-muted-foreground text-xs w-32 shrink-0">
        {contactInfoTypeLabel(entry.type)}
        {entry.is_primary && (
          <span className="ml-1 text-blue-500">(primary)</span>
        )}
      </span>
      {editing ? (
        <div className="flex items-center gap-1 flex-1">
          <Input
            className="h-7 text-sm flex-1"
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            disabled={patchInfo.isPending}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSaveEdit();
              if (e.key === "Escape") setEditing(false);
            }}
          />
          <Button
            variant="ghost"
            size="sm"
            className="h-7 w-7 p-0"
            onClick={handleSaveEdit}
            disabled={patchInfo.isPending}
          >
            <Check className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 w-7 p-0"
            onClick={() => {
              setEditValue(entry.value ?? "");
              setEditing(false);
            }}
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      ) : (
        <>
          <span className="flex-1">
            {entry.secured ? (
              <SecuredChannelEntry entry={entry} contactId={contactId} />
            ) : (
              <ChannelValue entry={entry} />
            )}
          </span>
          <span className="flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
            {!entry.secured && (
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0"
                onClick={() => {
                  setEditValue(entry.value ?? "");
                  setEditing(true);
                }}
                title="Edit"
              >
                <Pencil className="h-3 w-3" />
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              className="h-6 w-6 p-0 text-destructive hover:text-destructive"
              onClick={handleDelete}
              disabled={deleteInfo.isPending}
              title="Delete"
            >
              <Trash2 className="h-3 w-3" />
            </Button>
          </span>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AddChannelInfoForm — inline add form for a linked contact
// COMPAT-ONLY: createContactInfo uses contact-keyed endpoint.
// Remove after bu-k9ylx (write-path cut-over) completes.
// ---------------------------------------------------------------------------

function AddChannelInfoForm({
  contactId,
  existingEntries,
  onDone,
}: {
  contactId: string;
  existingEntries: ContactInfoEntry[];
  onDone: () => void;
}) {
  // COMPAT-ONLY: createContactInfo via contact-keyed endpoint.
  const createInfo = useCreateContactInfo();
  const [type, setType] = useState<string>("email");
  const [value, setValue] = useState("");
  const [isPrimary, setIsPrimary] = useState(false);

  // CHILD_TO_PARENT_TYPE: telegram handle is a child of telegram_chat_id
  const CHILD_TO_PARENT_TYPE: Record<string, string> = {
    telegram: "telegram_chat_id",
  };

  const parentType = CHILD_TO_PARENT_TYPE[type] ?? null;
  const parentCandidates = parentType
    ? existingEntries.filter((e) => e.type === parentType)
    : [];
  const [parentId, setParentId] = useState<string | null>(null);

  function handleTypeChange(newType: string) {
    setType(newType);
    setValue("");
    const pt = CHILD_TO_PARENT_TYPE[newType] ?? null;
    if (pt) {
      const candidates = existingEntries.filter((e) => e.type === pt);
      setParentId(candidates.length === 1 ? candidates[0].id : null);
    } else {
      setParentId(null);
    }
  }

  async function handleSubmit() {
    const trimmed = value.trim();
    if (!trimmed) {
      toast.error("Value cannot be empty.");
      return;
    }
    try {
      await createInfo.mutateAsync({
        contactId,
        request: {
          type,
          value: trimmed,
          is_primary: isPrimary,
          ...(parentId ? { parent_id: parentId } : {}),
        },
      });
      toast.success(`Added ${contactInfoTypeLabel(type)} entry.`);
      onDone();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      toast.error(`Failed to add: ${msg}`);
    }
  }

  return (
    <div className="flex items-end gap-2 pt-2 border-t mt-2 flex-wrap">
      <div className="space-y-1">
        <FormLabel className="text-xs">Type</FormLabel>
        <Select value={type} onValueChange={handleTypeChange}>
          <SelectTrigger className="h-8 w-36 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {CONTACT_INFO_TYPES.map((t) => (
              <SelectItem key={t} value={t} className="text-xs">
                {contactInfoTypeLabel(t)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {parentType && parentCandidates.length > 1 && (
        <div className="space-y-1">
          <FormLabel className="text-xs">Account</FormLabel>
          <Select value={parentId ?? ""} onValueChange={(v) => setParentId(v || null)}>
            <SelectTrigger className="h-8 w-44 text-xs">
              <SelectValue placeholder="Select account..." />
            </SelectTrigger>
            <SelectContent>
              {parentCandidates.map((c) => (
                <SelectItem key={c.id} value={c.id} className="text-xs">
                  {c.value ?? contactInfoTypeLabel(c.type)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
      <div className="flex-1 space-y-1">
        <FormLabel className="text-xs">Value</FormLabel>
        <Input
          className="h-8 text-sm"
          type="text"
          placeholder={inputPlaceholder(type)}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          disabled={createInfo.isPending}
          autoFocus
        />
      </div>
      <label className="flex items-center gap-1 text-xs text-muted-foreground pb-0.5">
        <input
          type="checkbox"
          checked={isPrimary}
          onChange={(e) => setIsPrimary(e.target.checked)}
          className="accent-primary"
        />
        Primary
      </label>
      <Button
        size="sm"
        variant="ghost"
        className="h-8 w-8 p-0"
        onClick={handleSubmit}
        disabled={createInfo.isPending}
      >
        <Check className="h-4 w-4" />
      </Button>
      <Button
        size="sm"
        variant="ghost"
        className="h-8 w-8 p-0"
        onClick={onDone}
        disabled={createInfo.isPending}
      >
        <X className="h-4 w-4" />
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PreferredChannelSelector — COMPAT-ONLY write via patchContact
// Remove after bu-k9ylx (write-path cut-over) completes.
// ---------------------------------------------------------------------------

function PreferredChannelSelector({
  contactId,
  preferredChannel,
  contactInfo,
}: {
  contactId: string;
  preferredChannel: string | null;
  contactInfo: ContactInfoEntry[];
}) {
  // COMPAT-ONLY: patchContact for preferred_channel — contact-keyed write.
  const patchContact = usePatchContact();

  const hasTelegram = contactInfo.some((ci) => ci.type === "telegram_chat_id");
  const hasEmail = contactInfo.some((ci) => ci.type === "email");

  function handleChange(value: string) {
    const preferred = value === "none" ? null : value;
    patchContact.mutate(
      { contactId, request: { preferred_channel: preferred } },
      {
        onSuccess: () => toast.success("Preferred channel updated."),
        onError: (err) =>
          toast.error(
            `Failed to update: ${err instanceof Error ? err.message : "Unknown error"}`,
          ),
      },
    );
  }

  return (
    <div className="flex items-center gap-2 mt-1">
      <span className="text-muted-foreground text-xs w-32 shrink-0">Preferred</span>
      <Select
        value={preferredChannel ?? "none"}
        onValueChange={handleChange}
        disabled={patchContact.isPending}
      >
        <SelectTrigger className="h-6 w-32 text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="none" className="text-xs">None</SelectItem>
          <SelectItem value="telegram" className="text-xs" disabled={!hasTelegram}>
            Telegram
          </SelectItem>
          <SelectItem value="email" className="text-xs" disabled={!hasEmail}>
            Email
          </SelectItem>
        </SelectContent>
      </Select>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ContactRow — one collapsed/expanded row per linked contact
// ---------------------------------------------------------------------------

function ContactRow({
  contact,
}: {
  contact: LinkedContactSummary;
}) {
  const [expanded, setExpanded] = useState(false);
  const [addingInfo, setAddingInfo] = useState(false);

  const nonSecuredChannels = contact.contact_info.filter((ci) => !ci.secured);
  const securedChannels = contact.contact_info.filter((ci) => ci.secured);
  const hasChannels = contact.contact_info.length > 0;

  // Preferred channel chip text
  const preferredLabel = contact.preferred_channel
    ? contact.preferred_channel.charAt(0).toUpperCase() + contact.preferred_channel.slice(1)
    : null;

  return (
    <div
      data-testid={`contact-row-${contact.id}`}
      className="border-b last:border-b-0"
    >
      {/* Collapsed header row */}
      <button
        type="button"
        className="w-full flex items-center gap-3 py-2.5 px-0 text-left hover:bg-muted/30 transition-colors rounded-sm"
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
      >
        <span className="shrink-0 text-muted-foreground">
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
        </span>

        {/* Contact name */}
        <span className="font-medium text-sm min-w-0 truncate">
          {contact.full_name || "Unnamed contact"}
        </span>

        {/* Label chips */}
        {contact.labels.length > 0 && (
          <span className="flex gap-1 flex-wrap">
            {contact.labels.map((label) => (
              <Badge
                key={label.id}
                style={{
                  backgroundColor: labelStyle(label),
                  color: ENTITY_BADGE_TEXT,
                }}
                className="text-[10px] px-1.5 py-0"
              >
                {label.name}
              </Badge>
            ))}
          </span>
        )}

        {/* Preferred channel chip */}
        {preferredLabel && (
          <Badge variant="outline" className="text-[10px] px-1.5 py-0 shrink-0">
            {preferredLabel}
          </Badge>
        )}

        {/* Non-secured channel chips (collapsed view) */}
        {!expanded && nonSecuredChannels.length > 0 && (
          <span className="flex gap-1 flex-wrap min-w-0">
            {nonSecuredChannels.slice(0, 3).map((ci) => (
              <Badge
                key={ci.id}
                variant="secondary"
                className="text-[10px] px-1.5 py-0 font-normal truncate max-w-[10rem]"
              >
                {contactInfoTypeLabel(ci.type)}: {ci.value ?? "—"}
              </Badge>
            ))}
            {nonSecuredChannels.length > 3 && (
              <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
                +{nonSecuredChannels.length - 3} more
              </Badge>
            )}
          </span>
        )}

        {/* Secured channel placeholder chips (collapsed view) */}
        {!expanded && securedChannels.length > 0 && (
          <span className="flex gap-1">
            {securedChannels.map((ci) => (
              <Badge
                key={ci.id}
                variant="outline"
                className="text-[10px] px-1.5 py-0 font-mono text-muted-foreground"
              >
                {contactInfoTypeLabel(ci.type)}: ••••
              </Badge>
            ))}
          </span>
        )}
      </button>

      {/* Expanded full channel list */}
      {expanded && (
        <div className="pb-3 pl-6 pr-1 space-y-0.5">
          {hasChannels ? (
            <div className="space-y-0.5">
              {contact.contact_info.map((ci) => (
                <ExpandedContactInfoRow
                  key={ci.id}
                  entry={ci}
                  contactId={contact.id}
                />
              ))}
            </div>
          ) : (
            <p className="text-muted-foreground text-xs py-1 italic">
              No channel entries.
            </p>
          )}

          {/* Preferred channel selector */}
          <PreferredChannelSelector
            contactId={contact.id}
            preferredChannel={contact.preferred_channel}
            contactInfo={contact.contact_info}
          />

          {/* Add channel info form */}
          {addingInfo ? (
            <AddChannelInfoForm
              contactId={contact.id}
              existingEntries={contact.contact_info}
              onDone={() => setAddingInfo(false)}
            />
          ) : (
            <Button
              variant="ghost"
              size="sm"
              className="mt-1 h-7 text-xs text-muted-foreground"
              onClick={() => setAddingInfo(true)}
            >
              <Plus className="mr-1 h-3 w-3" />
              Add contact info
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ContactChannelCard — main export
// ---------------------------------------------------------------------------

/**
 * ContactChannelCard renders the entity-detail contact-channel card.
 *
 * - Fetches linked contacts via the entity-keyed endpoint
 *   GET /relationship/entities/{entityId}/linked-contacts which returns
 *   enriched LinkedContactSummary with contact_info[], labels[], and
 *   preferred_channel.
 * - For mutations, uses COMPAT-ONLY contact-keyed endpoints (see module
 *   docstring and individual component comments for migration gating info).
 * - onLinkContact: callback to open the existing link/unlink flow on the host
 *   page. The actual link/unlink UI is NOT moved into this card.
 */
export function ContactChannelCard({
  entityId,
  onLinkContact,
}: {
  entityId: string;
  onLinkContact?: () => void;
}) {
  const { data: contacts, isLoading } = useEntityLinkedContacts(entityId);

  if (isLoading) {
    return (
      <section
        data-testid="contact-channel-card"
        className="space-y-2"
      >
        <h2 className="text-lg font-semibold">Channels</h2>
        <div className="animate-pulse space-y-2">
          <div className="h-8 bg-muted rounded" />
          <div className="h-8 bg-muted rounded" />
        </div>
      </section>
    );
  }

  return (
    <section
      data-testid="contact-channel-card"
      className="space-y-2"
    >
      <h2 className="text-lg font-semibold">Channels</h2>

      {contacts && contacts.length > 0 ? (
        <div className="divide-y divide-border border-y">
          {contacts.map((contact) => (
            <ContactRow
              key={contact.id}
              contact={contact}
            />
          ))}
        </div>
      ) : (
        <div
          data-testid="contact-channel-empty-state"
          className="rounded-md border border-dashed px-4 py-6 text-center"
        >
          <p className="text-muted-foreground text-sm mb-3">
            No linked contacts. Link a contact to see their channel info here.
          </p>
          {onLinkContact && (
            <Button
              variant="outline"
              size="sm"
              onClick={onLinkContact}
              data-testid="link-contact-cta"
            >
              <Plus className="mr-1 h-3.5 w-3.5" />
              Link contact
            </Button>
          )}
        </div>
      )}
    </section>
  );
}
