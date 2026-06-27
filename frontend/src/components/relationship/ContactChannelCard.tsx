/**
 * ContactChannelCard — entity detail contact-channel card.
 *
 * Renders one collapsed row per linked contact when an entity has linked
 * contacts. Expand-on-click shows full channel list with edit/delete
 * affordances for entity-facts-sourced entries.
 *
 * Data source:
 *   Primary:  GET /relationship/entities/{entityId}/linked-contacts
 *             Returns enriched LinkedContactSummary with contact_info[],
 *             labels[], and preferred_channel. No N+1 getContact() fanout
 *             is needed for the collapsed view.
 *
 * Migration status: contacts-to-triples migration (bu-uhjxr) COMPLETE.
 * public.contact_info dropped (bu-e2ja9). The live API (list_entity_linked_contacts)
 * serves only source="entity_facts" entries. The component retains read-only rendering
 * for source=null entries for compat display of any legacy fixtures; no such entries
 * are returned by the current API.
 *
 *   preferred_channel — entity-keyed via the `prefers-channel` fact
 *     (entity-keyed-preferred-channel). Set/clear route to
 *     PUT/DELETE /entities/{entityId}/preferred-channel
 *     (useSetPreferredChannel / useClearPreferredChannel). The control offers
 *     only the entity's `reachable_channels` (channels it has a contact fact
 *     for). The legacy contact-keyed contacts.preferred_channel write path is
 *     no longer used here.
 *
 * See: contact-detail parity inventory (point-in-time report, retired)
 */

import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, ChevronRight, Pencil, Plus, ShieldCheck, Trash2, X } from "lucide-react";
import { toast } from "sonner";

import type { ContactInfoEntry, Label, LinkedContactSummary } from "@/api/types";
import type { AddEntityContactRequest, UpdateEntityContactRequest } from "@/api/types";
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
import { useEntityLinkedContacts, useAddEntityContact, useDeleteEntityContact, useMarkEntityContactVerified, useUpdateEntityContact, useRevealEntityContactSecret, useSetPreferredChannel, useClearPreferredChannel } from "@/hooks/use-entities";
import { sortChannelsPrimaryFirst } from "./contact-channel-utils";

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

// Contact_info types available in the add-channel form. Only types with a
// triple predicate mapping are included (telegram_chat_id has no predicate
// and is excluded; home_assistant_url likewise has none).
const CONTACT_INFO_TYPES = [
  "email",
  "phone",
  "telegram",
  "website",
  "other",
] as const;

type ContactInfoType = typeof CONTACT_INFO_TYPES[number];

// Map contact_info type → entity_facts predicate (must-start-with "has-").
// Mirrors the server-side _CI_TYPE_TO_PREDICATE in relationship_assert_fact.py.
// Types not in this map have no triple predicate and are not addable via
// the entity-keyed path.
const CONTACT_TYPE_TO_PREDICATE: Record<ContactInfoType, string> = {
  email: "has-email",
  phone: "has-phone",
  telegram: "has-handle",
  website: "has-website",
  other: "has-handle",
};

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
    case "website": return "https://example.com";
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
//
// All secured entries surfaced by list_entity_linked_contacts carry
// source="entity_facts" (public.contact_info was dropped in bu-e2ja9).
// Reveal routes exclusively to the entity-keyed endpoint via
// useRevealEntityContactSecret (GET /relationship/entities/{entityId}/secrets/{infoId}).
// ---------------------------------------------------------------------------

function SecuredChannelEntry({
  entry,
  entityId,
}: {
  entry: ContactInfoEntry;
  entityId: string;
}) {
  const [revealed, setRevealed] = useState<string | null>(null);
  const [isRevealing, setIsRevealing] = useState(false);
  const autoHideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const revealEntityMutation = useRevealEntityContactSecret();

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
    const onSuccess = (data: { value?: string | null }) => {
      // IMPORTANT: never log the secret value
      setRevealed(data.value ?? "");
      setIsRevealing(false);
      // Auto-hide is managed by the useEffect above.
    };
    const onError = () => {
      setIsRevealing(false);
      toast.error("Failed to reveal secret.");
    };

    revealEntityMutation.mutate(
      { entityId, infoId: entry.id },
      { onSuccess, onError },
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
// ExpandedContactInfoRow — channel row with entity-keyed edit and delete.
//
// Mutation routing:
//   source="entity_facts"  → Edit via useUpdateEntityContact (entity-keyed PUT).
//                            Delete via useDeleteEntityContact (entity-keyed).
//   source=null/absent     → Legacy public.contact_info row. Read-only.
//                            Write-blocked (409) since PR #2021. Shown with a
//                            tooltip so users understand why no edit/delete
//                            affordance is present.
// ---------------------------------------------------------------------------

export function ExpandedContactInfoRow({
  entry,
  entityId,
}: {
  entry: ContactInfoEntry;
  entityId: string;
}) {
  const deleteEntityContact = useDeleteEntityContact();
  const markVerified = useMarkEntityContactVerified();
  const updateEntityContact = useUpdateEntityContact();
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState("");

  const isEntityFacts = entry.source === "entity_facts";
  const canEdit = isEntityFacts && entry.predicate != null && entry.value_hash != null && !entry.secured;
  const canDelete = isEntityFacts && entry.predicate != null && entry.value_hash != null;
  const canVerify = isEntityFacts && entry.predicate != null && entry.value_hash != null && !entry.verified;

  function handleMarkVerified() {
    if (!canVerify || markVerified.isPending) return;
    markVerified.mutate(
      {
        entityId,
        predicate: entry.predicate!,
        valueHash: entry.value_hash!,
      },
      {
        onSuccess: () => {
          toast.success(`Marked ${contactInfoTypeLabel(entry.type)} as verified.`);
        },
        onError: (err) => {
          toast.error(
            `Failed to verify: ${err instanceof Error ? err.message : "Unknown error"}`,
          );
        },
      },
    );
  }

  function handleEditStart() {
    if (!canEdit) return;
    setEditValue(entry.value ?? "");
    setEditing(true);
  }

  function handleEditCancel() {
    setEditing(false);
    setEditValue("");
  }

  async function handleEditSave() {
    const trimmed = editValue.trim();
    if (!trimmed) {
      toast.error("Value cannot be empty.");
      return;
    }
    const request: UpdateEntityContactRequest = { new_value: trimmed, primary: entry.is_primary };
    try {
      await updateEntityContact.mutateAsync({
        entityId,
        predicate: entry.predicate!,
        valueHash: entry.value_hash!,
        request,
      });
      toast.success(`Updated ${contactInfoTypeLabel(entry.type)} entry.`);
      setEditing(false);
      setEditValue("");
    } catch (err) {
      toast.error(
        `Failed to update: ${err instanceof Error ? err.message : "Unknown error"}`,
      );
    }
  }

  function handleDelete() {
    if (!canDelete || deleteEntityContact.isPending) return;
    deleteEntityContact.mutate(
      {
        entityId,
        predicate: entry.predicate!,
        valueHash: entry.value_hash!,
      },
      {
        onSuccess: () => {
          toast.success(`Deleted ${contactInfoTypeLabel(entry.type)} entry.`);
        },
        onError: (err) => {
          toast.error(
            `Failed to delete: ${err instanceof Error ? err.message : "Unknown error"}`,
          );
        },
      },
    );
  }

  // Inline edit form shown when editing is active.
  if (editing) {
    return (
      <div className="flex items-center gap-2 py-1">
        <span className="text-muted-foreground text-xs w-32 shrink-0">
          {contactInfoTypeLabel(entry.type)}
          {entry.is_primary && (
            <span className="ml-1 text-blue-500">(primary)</span>
          )}
        </span>
        <Input
          className="h-6 text-sm flex-1"
          type="text"
          value={editValue}
          onChange={(e) => setEditValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              void handleEditSave();
            } else if (e.key === "Escape") {
              handleEditCancel();
            }
          }}
          disabled={updateEntityContact.isPending}
          autoFocus
          placeholder={inputPlaceholder(entry.type)}
          data-testid="edit-contact-input"
        />
        <Button
          variant="ghost"
          size="sm"
          className="h-6 w-6 p-0 shrink-0"
          title="Save"
          onClick={handleEditSave}
          disabled={updateEntityContact.isPending}
          data-testid="edit-contact-save"
        >
          <Check className="h-3 w-3" />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="h-6 w-6 p-0 shrink-0"
          title="Cancel"
          onClick={handleEditCancel}
          disabled={updateEntityContact.isPending}
          data-testid="edit-contact-cancel"
        >
          <X className="h-3 w-3" />
        </Button>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2 py-1">
      <span className="text-muted-foreground text-xs w-32 shrink-0 flex items-center gap-1">
        {contactInfoTypeLabel(entry.type)}
        {entry.is_primary && (
          <span className="ml-1 text-blue-500">(primary)</span>
        )}
        {/* Amber dot: unverified entity_facts channel */}
        {isEntityFacts && !entry.verified && (
          <span
            data-testid="unverified-dot"
            title="Unverified: owner has not confirmed this channel"
            style={{
              display: "inline-block",
              width: "6px",
              height: "6px",
              borderRadius: "50%",
              backgroundColor: "var(--amber)",
              flexShrink: 0,
            }}
          />
        )}
      </span>
      <span className="flex-1">
        {entry.secured ? (
          <SecuredChannelEntry entry={entry} entityId={entityId} />
        ) : (
          <ChannelValue entry={entry} />
        )}
      </span>
      {/* Edit/Delete/Verify affordances — entity_facts rows only (non-secured) */}
      {isEntityFacts && (
        <span className="flex items-center gap-1 shrink-0">
          {/* Mark verified button — shown only when not yet verified */}
          {canVerify && (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 w-6 p-0 text-muted-foreground"
              style={{ color: "var(--amber)" }}
              title="Mark as verified"
              onClick={handleMarkVerified}
              disabled={markVerified.isPending}
              data-testid="mark-verified-btn"
            >
              <ShieldCheck className="h-3 w-3" />
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0 text-muted-foreground"
            title={canEdit ? "Edit" : "Edit (secured values cannot be edited inline)"}
            disabled={!canEdit || deleteEntityContact.isPending}
            onClick={handleEditStart}
            data-testid="edit-contact-btn"
          >
            <Pencil className="h-3 w-3" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0 text-destructive hover:text-destructive"
            title="Delete"
            onClick={handleDelete}
            disabled={!canDelete || deleteEntityContact.isPending}
          >
            <Trash2 className="h-3 w-3" />
          </Button>
        </span>
      )}
      {/* Legacy contact_info row: read-only, write-blocked since PR #2021. */}
      {!isEntityFacts && (
        <span
          className="text-xs text-muted-foreground shrink-0"
          title="Legacy channel: no entity-keyed write path available (read-only)"
        >
          (legacy)
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AddChannelInfoForm — inline add form for a linked contact.
//
// Routes through useAddEntityContact (entity-keyed POST /entities/{id}/contacts).
// Maps contact_info type → has-* predicate via CONTACT_TYPE_TO_PREDICATE.
// Types with no predicate mapping (telegram_chat_id, home_assistant_url) are
// excluded from the form's type selector.
// ---------------------------------------------------------------------------

function AddChannelInfoForm({
  entityId,
  onDone,
}: {
  entityId: string;
  onDone: () => void;
}) {
  const addEntityContact = useAddEntityContact();
  const [type, setType] = useState<ContactInfoType>("email");
  const [value, setValue] = useState("");
  const [isPrimary, setIsPrimary] = useState(false);

  function handleTypeChange(newType: string) {
    setType(newType as ContactInfoType);
    setValue("");
  }

  async function handleSubmit() {
    const trimmed = value.trim();
    if (!trimmed) {
      toast.error("Value cannot be empty.");
      return;
    }
    const predicate = CONTACT_TYPE_TO_PREDICATE[type];
    const request: AddEntityContactRequest = {
      predicate,
      value: trimmed,
      primary: isPrimary || null,
      // Pass the channel type so the backend can normalise telegram handles to
      // the canonical "telegram:<bare>" storage form (the has-* predicate alone
      // can't distinguish telegram from other handles).
      channel_type: type,
    };
    try {
      await addEntityContact.mutateAsync({ entityId, request });
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
      <div className="flex-1 space-y-1">
        <FormLabel className="text-xs">Value</FormLabel>
        <Input
          className="h-8 text-sm"
          type="text"
          placeholder={inputPlaceholder(type)}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          disabled={addEntityContact.isPending}
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
        disabled={addEntityContact.isPending}
      >
        <Check className="h-4 w-4" />
      </Button>
      <Button
        size="sm"
        variant="ghost"
        className="h-8 w-8 p-0"
        onClick={onDone}
        disabled={addEntityContact.isPending}
      >
        <X className="h-4 w-4" />
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PreferredChannelSelector — entity-keyed write via the prefers-channel fact
//
// Reads/writes the preferred channel through the entity-keyed `prefers-channel`
// fact (entity-keyed-preferred-channel), NOT the orphaned
// contacts.preferred_channel CRM column. Set routes to
// useSetPreferredChannel (PUT /entities/{id}/preferred-channel); clear routes to
// useClearPreferredChannel (DELETE). Only the entity's `reachableChannels`
// (channels it has a contact fact for) are offered.
// ---------------------------------------------------------------------------

// Deliverable channels the control can offer, with their display labels. A
// channel is selectable only when present in the entity's reachableChannels set.
const PREFERRED_CHANNEL_OPTIONS = [
  { value: "telegram", label: "Telegram" },
  { value: "email", label: "Email" },
] as const;

function PreferredChannelSelector({
  entityId,
  preferredChannel,
  reachableChannels,
}: {
  entityId: string;
  preferredChannel: string | null;
  reachableChannels: string[];
}) {
  const setPreferredChannel = useSetPreferredChannel();
  const clearPreferredChannel = useClearPreferredChannel();
  const isPending = setPreferredChannel.isPending || clearPreferredChannel.isPending;

  const reachable = new Set(reachableChannels);

  function handleChange(value: string) {
    if (value === "none") {
      clearPreferredChannel.mutate(
        { entityId },
        {
          onSuccess: () => toast.success("Preferred channel cleared."),
          onError: (err) =>
            toast.error(
              `Failed to update: ${err instanceof Error ? err.message : "Unknown error"}`,
            ),
        },
      );
      return;
    }
    setPreferredChannel.mutate(
      { entityId, channel: value },
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
        disabled={isPending}
      >
        <SelectTrigger className="h-6 w-32 text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="none" className="text-xs">None</SelectItem>
          {PREFERRED_CHANNEL_OPTIONS.map((opt) => (
            <SelectItem
              key={opt.value}
              value={opt.value}
              className="text-xs"
              disabled={!reachable.has(opt.value)}
            >
              {opt.label}
            </SelectItem>
          ))}
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
  entityId,
}: {
  contact: LinkedContactSummary;
  entityId: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [addingInfo, setAddingInfo] = useState(false);

  // Primary-first ordering: is_primary=true entries come before is_primary=false
  // entries; stable within each group (preserves server-side insertion order).
  const sortedChannels = sortChannelsPrimaryFirst(contact.contact_info);
  const nonSecuredChannels = sortedChannels.filter((ci) => !ci.secured);
  const securedChannels = sortedChannels.filter((ci) => ci.secured);
  const hasChannels = contact.contact_info.length > 0;

  // Preferred channel chip text — use contactInfoTypeLabel for consistent
  // labelling between the collapsed badge and the expanded channel list.
  const preferredLabel = contact.preferred_channel
    ? contactInfoTypeLabel(contact.preferred_channel)
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
              {sortedChannels.map((ci) => (
                <ExpandedContactInfoRow
                  key={ci.id}
                  entry={ci}
                  entityId={entityId}
                />
              ))}
            </div>
          ) : (
            <p className="text-muted-foreground text-xs py-1 italic">
              No channel entries.
            </p>
          )}

          {/* Preferred channel selector — entity-keyed (prefers-channel fact).
              preferred_channel + reachable_channels are entity-level and the
              backend attaches them to the first linked contact only, so the
              control renders once. We gate on reachable_channels being present
              (the first contact) so non-first contacts don't show a duplicate. */}
          {contact.reachable_channels.length > 0 || contact.preferred_channel ? (
            <PreferredChannelSelector
              entityId={entityId}
              preferredChannel={contact.preferred_channel}
              reachableChannels={contact.reachable_channels}
            />
          ) : null}

          {/* Add channel info form */}
          {addingInfo ? (
            <AddChannelInfoForm
              entityId={entityId}
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
 * - Mutations are entity-keyed:
 *   - Add: useAddEntityContact (POST /entities/{id}/contacts)
 *   - Edit of entity_facts rows: useUpdateEntityContact
 *     (PUT /entities/{id}/contacts/{predicate}/{value_hash})
 *   - Delete of entity_facts rows: useDeleteEntityContact
 *     (DELETE /entities/{id}/contacts/{predicate}/{value_hash})
 *   - Reveal secured entries: useRevealEntityContactSecret
 *     (GET /relationship/entities/{entityId}/secrets/{infoId})
 *   - Legacy contact_info rows: read-only (write-blocked since PR #2021)
 *   - preferred_channel: entity-keyed via useSetPreferredChannel /
 *     useClearPreferredChannel (PUT/DELETE /entities/{id}/preferred-channel),
 *     backed by the single-valued `prefers-channel` fact. The orphaned
 *     contacts.preferred_channel CRM column is no longer written.
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
              entityId={entityId}
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
