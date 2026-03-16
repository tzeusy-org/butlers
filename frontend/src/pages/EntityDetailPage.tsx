import { useState } from "react";
import { Link, useParams } from "react-router";
import { Check, ExternalLink, Pencil, Plus, Trash2, X } from "lucide-react";
import { toast } from "sonner";

import type { ContactSummary, EntityInfoEntry } from "@/api/types";
import { OwnerSetupBanner } from "@/components/relationship/OwnerSetupBanner";
import { Badge } from "@/components/ui/badge";
import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useContacts } from "@/hooks/use-contacts";
import {
  useCreateEntityInfo,
  useDeleteEntityInfo,
  useEntity,
  usePromoteEntity,
  useRevealEntitySecret,
  useSetLinkedContact,
  useUnlinkContact,
  useUpdateEntity,
  useUpdateEntityInfo,
} from "@/hooks/use-memory";

// ---------------------------------------------------------------------------
// Entity info type helpers
// ---------------------------------------------------------------------------

const ENTITY_INFO_TYPES = [
  "telegram",
  "telegram_chat_id",
  "telegram_api_id",
  "telegram_api_hash",
  "telegram_user_session",
  "home_assistant_url",
  "home_assistant_token",
  "google_oauth_refresh",
  "other",
] as const;

const SECURED_TYPES = new Set<string>([
  "telegram_api_hash",
  "telegram_user_session",
  "home_assistant_token",
  "google_oauth_refresh",
]);

function entityInfoTypeLabel(type: string): string {
  switch (type) {
    case "telegram": return "Telegram Handle";
    case "telegram_chat_id": return "Telegram Chat ID";
    case "telegram_api_id": return "Telegram API ID";
    case "telegram_api_hash": return "Telegram API Hash";
    case "telegram_user_session": return "Telegram User Session";
    case "home_assistant_url": return "Home Assistant URL";
    case "home_assistant_token": return "Home Assistant Token";
    case "google_oauth_refresh": return "Google OAuth Refresh";
    case "other": return "Other";
    default: return type;
  }
}

// ---------------------------------------------------------------------------
// SecuredInfoEntry — masked value with click-to-reveal
// ---------------------------------------------------------------------------

function SecuredInfoEntry({
  entry,
  entityId,
}: {
  entry: EntityInfoEntry;
  entityId: string;
}) {
  const [revealed, setRevealed] = useState<string | null>(null);
  const [isRevealing, setIsRevealing] = useState(false);
  const revealMutation = useRevealEntitySecret();

  const displayValue = revealed ?? entry.value;

  async function handleReveal() {
    if (isRevealing || revealed !== null) return;
    setIsRevealing(true);
    revealMutation.mutate(
      { entityId, infoId: entry.id },
      {
        onSuccess: (data) => {
          setRevealed(data.value ?? "");
          setIsRevealing(false);
        },
        onError: () => {
          setIsRevealing(false);
        },
      },
    );
  }

  if (!entry.secured) {
    return (
      <span className="text-sm">
        {displayValue ?? <span className="text-muted-foreground italic">&mdash;</span>}
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-2">
      {displayValue !== null ? (
        <span className="text-sm font-mono">{displayValue}</span>
      ) : (
        <span className="text-muted-foreground text-sm font-mono tracking-widest">
          ••••••••
        </span>
      )}
      {revealed === null && (
        <Button
          variant="outline"
          size="sm"
          className="h-6 px-2 text-xs"
          onClick={handleReveal}
          disabled={isRevealing}
        >
          {isRevealing ? "Revealing..." : "Reveal"}
        </Button>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Editable entity_info row
// ---------------------------------------------------------------------------

function EntityInfoRow({
  entry,
  entityId,
}: {
  entry: EntityInfoEntry;
  entityId: string;
}) {
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(entry.value ?? "");
  const deleteInfo = useDeleteEntityInfo();
  const updateInfo = useUpdateEntityInfo();

  function handleDelete() {
    if (!window.confirm(`Delete this ${entityInfoTypeLabel(entry.type)} entry?`)) return;
    deleteInfo.mutate(
      { entityId, infoId: entry.id },
      {
        onSuccess: () => toast.success(`Removed ${entityInfoTypeLabel(entry.type)} entry.`),
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
    updateInfo.mutate(
      { entityId, infoId: entry.id, request: { value: trimmed } },
      {
        onSuccess: () => {
          toast.success(`Updated ${entityInfoTypeLabel(entry.type)} entry.`);
          setEditing(false);
        },
        onError: (err) =>
          toast.error(`Failed to update: ${err instanceof Error ? err.message : "Unknown error"}`),
      },
    );
  }

  return (
    <div className="flex gap-2 items-center group">
      <span className="text-muted-foreground text-sm w-36 shrink-0 capitalize">
        {entry.label ?? entityInfoTypeLabel(entry.type)}
        {entry.is_primary && (
          <span className="ml-1 text-xs text-blue-500">(primary)</span>
        )}
      </span>
      {editing ? (
        <div className="flex items-center gap-1 flex-1">
          <Input
            className="h-7 text-sm flex-1"
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            disabled={updateInfo.isPending}
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
            disabled={updateInfo.isPending}
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
            <SecuredInfoEntry entry={entry} entityId={entityId} />
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
// Add entity_info inline form
// ---------------------------------------------------------------------------

function AddEntityInfoForm({
  entityId,
  onDone,
  isOwner = false,
}: {
  entityId: string;
  onDone: () => void;
  isOwner?: boolean;
}) {
  const createInfo = useCreateEntityInfo();
  const [type, setType] = useState<string>("api_key");
  const [value, setValue] = useState("");
  const [label, setLabel] = useState("");
  const [isPrimary, setIsPrimary] = useState(false);

  // Owner entities do not store google_oauth_refresh — those live on companion entities.
  const availableTypes = isOwner
    ? ENTITY_INFO_TYPES.filter((t) => t !== "google_oauth_refresh")
    : ENTITY_INFO_TYPES;

  const isSecured = SECURED_TYPES.has(type);

  async function handleSubmit() {
    const trimmed = value.trim();
    if (!trimmed) {
      toast.error("Value cannot be empty.");
      return;
    }
    try {
      await createInfo.mutateAsync({
        entityId,
        request: {
          type,
          value: trimmed,
          label: label.trim() || undefined,
          is_primary: isPrimary,
          ...(isSecured ? { secured: true } : {}),
        },
      });
      toast.success(`Added ${entityInfoTypeLabel(type)} entry.`);
      onDone();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      toast.error(`Failed to add: ${msg}`);
    }
  }

  return (
    <div className="flex items-end gap-2 pt-2 border-t mt-2 flex-wrap">
      <div className="space-y-1">
        <Label className="text-xs">Type</Label>
        <Select value={type} onValueChange={(v) => { setType(v); setValue(""); }}>
          <SelectTrigger className="h-8 w-32 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {availableTypes.map((t) => (
              <SelectItem key={t} value={t} className="text-xs">
                {entityInfoTypeLabel(t)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-1">
        <Label className="text-xs">Label</Label>
        <Input
          className="h-8 w-28 text-sm"
          placeholder="Optional"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          disabled={createInfo.isPending}
        />
      </div>
      <div className="flex-1 space-y-1">
        <Label className="text-xs">Value</Label>
        <Input
          className="h-8 text-sm"
          type={isSecured ? "password" : "text"}
          placeholder={isSecured ? "••••••••" : ""}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          disabled={createInfo.isPending}
          autoFocus
        />
      </div>
      {!isSecured && (
        <label className="flex items-center gap-1 text-xs text-muted-foreground pb-0.5">
          <input
            type="checkbox"
            checked={isPrimary}
            onChange={(e) => setIsPrimary(e.target.checked)}
            className="accent-primary"
          />
          Primary
        </label>
      )}
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
// Entity info section
// ---------------------------------------------------------------------------

function EntityInfoSection({
  entityId,
  entries,
  isOwner = false,
}: {
  entityId: string;
  entries: EntityInfoEntry[];
  isOwner?: boolean;
}) {
  const [addingInfo, setAddingInfo] = useState(false);

  // Owner entities do not store google_oauth_refresh — those live on companion
  // entities. Filter them out so the owner entity view stays clean.
  const visibleEntries = isOwner
    ? entries.filter((e) => e.type !== "google_oauth_refresh")
    : entries;

  const hasHiddenOAuthRows = isOwner && entries.some((e) => e.type === "google_oauth_refresh");

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Credentials &amp; Info</CardTitle>
        </div>
      </CardHeader>
      <CardContent>
        {visibleEntries.length === 0 && !addingInfo ? (
          <p className="text-muted-foreground py-2 text-center text-sm">
            No entity info entries yet.
          </p>
        ) : (
          <div className="space-y-1.5">
            {visibleEntries.map((entry) => (
              <EntityInfoRow key={entry.id} entry={entry} entityId={entityId} />
            ))}
          </div>
        )}
        {isOwner && (
          <div className="mt-3 flex items-center gap-1.5 rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
            <ExternalLink className="h-3 w-3 shrink-0" />
            <span>
              Google OAuth tokens are managed on companion Google account entities.
              {hasHiddenOAuthRows
                ? " Existing token rows are hidden here — manage them at "
                : " To manage Google accounts, go to "}
              <Link to="/settings" className="text-primary hover:underline">
                Settings → Google OAuth
              </Link>
              .
            </span>
          </div>
        )}
        {addingInfo ? (
          <AddEntityInfoForm
            entityId={entityId}
            onDone={() => setAddingInfo(false)}
            isOwner={isOwner}
          />
        ) : (
          <Button
            variant="ghost"
            size="sm"
            className="mt-2 h-7 text-xs text-muted-foreground"
            onClick={() => setAddingInfo(true)}
          >
            <Plus className="mr-1 h-3 w-3" />
            Add entity info
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Linked contact section with unlink / link
// ---------------------------------------------------------------------------

function LinkedContactSection({
  entityId,
  entity,
}: {
  entityId: string;
  entity: { linked_contact_id: string | null; linked_contact_name: string | null };
}) {
  const unlinkContact = useUnlinkContact();
  const setLinkedContact = useSetLinkedContact();
  const [linking, setLinking] = useState(false);
  const [search, setSearch] = useState("");
  const { data: contactsData } = useContacts(
    linking ? { q: search || undefined, limit: 10 } : undefined,
  );
  const contacts: ContactSummary[] = contactsData?.contacts ?? [];

  function handleUnlink() {
    if (!window.confirm("Unlink this contact from the entity?")) return;
    unlinkContact.mutate(entityId, {
      onSuccess: () => toast.success("Contact unlinked."),
      onError: (err) =>
        toast.error(`Failed to unlink: ${err instanceof Error ? err.message : "Unknown"}`),
    });
  }

  function handleLink(contactId: string) {
    setLinkedContact.mutate(
      { entityId, contactId },
      {
        onSuccess: () => {
          toast.success("Contact linked.");
          setLinking(false);
          setSearch("");
        },
        onError: (err) =>
          toast.error(`Failed to link: ${err instanceof Error ? err.message : "Unknown"}`),
      },
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Linked Contact</CardTitle>
        </div>
      </CardHeader>
      <CardContent>
        {entity.linked_contact_id ? (
          <div className="flex items-center gap-3">
            <Link
              to={`/contacts/${entity.linked_contact_id}`}
              className="text-primary hover:underline"
            >
              {entity.linked_contact_name ?? entity.linked_contact_id}
            </Link>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs text-destructive hover:text-destructive"
              onClick={handleUnlink}
              disabled={unlinkContact.isPending}
            >
              <Trash2 className="mr-1 h-3 w-3" />
              Unlink
            </Button>
          </div>
        ) : linking ? (
          <div className="space-y-2">
            <Input
              placeholder="Search contacts..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              autoFocus
              className="h-8 text-sm"
            />
            {contacts.length > 0 ? (
              <div className="max-h-48 overflow-y-auto rounded border">
                {contacts.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    className="flex w-full items-center gap-2 px-3 py-1.5 text-sm
                      hover:bg-muted text-left"
                    onClick={() => handleLink(c.id)}
                    disabled={setLinkedContact.isPending}
                  >
                    <span className="font-medium">{c.full_name}</span>
                    {c.email && (
                      <span className="text-muted-foreground text-xs">{c.email}</span>
                    )}
                  </button>
                ))}
              </div>
            ) : search ? (
              <p className="text-muted-foreground text-xs py-2">No contacts found.</p>
            ) : null}
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs"
              onClick={() => { setLinking(false); setSearch(""); }}
            >
              Cancel
            </Button>
          </div>
        ) : (
          <div className="flex items-center gap-3">
            <p className="text-muted-foreground text-sm">No linked contact.</p>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs text-muted-foreground"
              onClick={() => setLinking(true)}
            >
              <Plus className="mr-1 h-3 w-3" />
              Link contact
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// EntityDetailPage
// ---------------------------------------------------------------------------

export default function EntityDetailPage() {
  const { entityId } = useParams<{ entityId: string }>();
  const { data, isLoading, error } = useEntity(entityId);
  const entity = data?.data;
  const updateEntity = useUpdateEntity();
  const promoteEntity = usePromoteEntity();

  const [editingName, setEditingName] = useState(false);
  const [draftName, setDraftName] = useState("");

  const handleStartEditName = () => {
    setDraftName(entity?.canonical_name ?? "");
    setEditingName(true);
  };

  const handleSaveName = () => {
    if (!entityId || !draftName.trim()) return;
    updateEntity.mutate(
      { entityId, request: { canonical_name: draftName.trim() } },
      {
        onSuccess: () => {
          setEditingName(false);
          toast.success("Entity name updated");
        },
        onError: (err) => toast.error(`Failed to update name: ${(err as Error).message}`),
      },
    );
  };

  const [addingAlias, setAddingAlias] = useState(false);
  const [draftAlias, setDraftAlias] = useState("");

  const handleRemoveAlias = (alias: string) => {
    if (!entityId || !entity) return;
    const updated = entity.aliases.filter((a) => a !== alias);
    updateEntity.mutate(
      { entityId, request: { aliases: updated } },
      {
        onSuccess: () => toast.success(`Removed alias "${alias}"`),
        onError: (err) => toast.error(`Failed to remove alias: ${(err as Error).message}`),
      },
    );
  };

  const handleAddAlias = () => {
    const trimmed = draftAlias.trim();
    if (!entityId || !entity || !trimmed) return;
    if (entity.aliases.includes(trimmed)) {
      toast.error("Alias already exists.");
      return;
    }
    const updated = [...entity.aliases, trimmed];
    updateEntity.mutate(
      { entityId, request: { aliases: updated } },
      {
        onSuccess: () => {
          toast.success(`Added alias "${trimmed}"`);
          setDraftAlias("");
          setAddingAlias(false);
        },
        onError: (err) => toast.error(`Failed to add alias: ${(err as Error).message}`),
      },
    );
  };

  return (
    <div className="space-y-6">
      {/* Breadcrumbs */}
      <Breadcrumbs
        items={[
          { label: "Entities", href: "/entities" },
          { label: entity?.canonical_name ?? entityId ?? "Entity" },
        ]}
      />

      {/* Loading */}
      {isLoading && (
        <div className="space-y-4">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-48 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="text-destructive py-12 text-center text-sm">
          Failed to load entity. {(error as Error).message}
        </div>
      )}

      {/* Content */}
      {entity && (
        <>
          {/* Header card */}
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-center gap-3">
                {editingName ? (
                  <div className="flex items-center gap-2">
                    <Input
                      value={draftName}
                      onChange={(e) => setDraftName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleSaveName();
                        if (e.key === "Escape") setEditingName(false);
                      }}
                      className="h-9 w-64 text-lg font-semibold"
                      autoFocus
                    />
                    <Button
                      size="icon"
                      variant="ghost"
                      onClick={handleSaveName}
                      disabled={updateEntity.isPending}
                    >
                      <Check className="h-4 w-4" />
                    </Button>
                    <Button
                      size="icon"
                      variant="ghost"
                      onClick={() => setEditingName(false)}
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <CardTitle className="text-2xl">
                      {entity.canonical_name}
                    </CardTitle>
                    <Button
                      size="icon"
                      variant="ghost"
                      className="h-7 w-7"
                      onClick={handleStartEditName}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                )}
                <Badge>{entity.entity_type}</Badge>
                {entity.roles?.includes("owner") && (
                  <Badge
                    style={{ backgroundColor: "#7c3aed", color: "#fff" }}
                    className="text-xs"
                  >
                    Owner
                  </Badge>
                )}
                {entity.unidentified && (
                  <Badge
                    style={{ backgroundColor: "#f59e0b", color: "#fff" }}
                    className="text-xs"
                  >
                    Unidentified
                  </Badge>
                )}
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {/* Aliases */}
              <div>
                <p className="text-muted-foreground mb-1 text-sm font-medium">
                  Aliases
                </p>
                <div className="flex flex-wrap gap-1.5 items-center">
                  {entity.aliases.map((alias) => (
                    <Badge key={alias} variant="secondary" className="group/alias">
                      {alias}
                      <button
                        type="button"
                        className="ml-1 opacity-0 group-hover/alias:opacity-100 transition-opacity"
                        onClick={() => handleRemoveAlias(alias)}
                        title="Remove alias"
                      >
                        <X className="h-2.5 w-2.5" />
                      </button>
                    </Badge>
                  ))}
                  {addingAlias ? (
                    <div className="flex items-center gap-1">
                      <Input
                        className="h-6 w-32 text-xs"
                        value={draftAlias}
                        onChange={(e) => setDraftAlias(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") handleAddAlias();
                          if (e.key === "Escape") setAddingAlias(false);
                        }}
                        autoFocus
                        placeholder="New alias..."
                      />
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 w-6 p-0"
                        onClick={handleAddAlias}
                      >
                        <Check className="h-3 w-3" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 w-6 p-0"
                        onClick={() => setAddingAlias(false)}
                      >
                        <X className="h-3 w-3" />
                      </Button>
                    </div>
                  ) : (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 text-xs text-muted-foreground"
                      onClick={() => setAddingAlias(true)}
                    >
                      <Plus className="mr-0.5 h-3 w-3" />
                      Add
                    </Button>
                  )}
                </div>
              </div>

              {/* Source provenance */}
              {!!(entity.metadata?.source_butler || entity.metadata?.source_scope) && (
                <div>
                  <p className="text-muted-foreground mb-1 text-sm font-medium">
                    Source Provenance
                  </p>
                  <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
                    {!!entity.metadata.source_butler && (
                      <span>
                        Butler:{" "}
                        <span className="text-foreground font-medium">
                          {String(entity.metadata.source_butler)}
                        </span>
                      </span>
                    )}
                    {!!entity.metadata.source_scope && (
                      <span>
                        Scope:{" "}
                        <span className="text-foreground font-medium">
                          {String(entity.metadata.source_scope)}
                        </span>
                      </span>
                    )}
                  </div>
                </div>
              )}

              {/* Metadata — exclude keys already shown in dedicated sections */}
              {(() => {
                const _DISPLAY_EXCLUDED = new Set([
                  "source_butler",
                  "source_scope",
                  "unidentified",
                ]);
                const displayMetadata = Object.fromEntries(
                  Object.entries(entity.metadata).filter(
                    ([k]) => !_DISPLAY_EXCLUDED.has(k),
                  ),
                );
                return Object.keys(displayMetadata).length > 0 ? (
                  <div>
                    <p className="text-muted-foreground mb-1 text-sm font-medium">
                      Metadata
                    </p>
                    <pre className="rounded-md bg-muted p-3 text-xs overflow-x-auto">
                      {JSON.stringify(displayMetadata, null, 2)}
                    </pre>
                  </div>
                ) : null;
              })()}

              {/* Timestamps */}
              <div className="flex gap-6 text-xs text-muted-foreground">
                <span>
                  Created: {new Date(entity.created_at).toLocaleString()}
                </span>
                <span>
                  Updated: {new Date(entity.updated_at).toLocaleString()}
                </span>
              </div>

              {/* Promotion action */}
              {entity.unidentified && (
                <div className="pt-1 border-t">
                  <Button
                    variant="outline"
                    size="sm"
                    className="text-xs"
                    disabled={promoteEntity.isPending}
                    onClick={() => {
                      if (!entityId) return;
                      promoteEntity.mutate(entityId, {
                        onSuccess: () => toast.success("Entity marked as confirmed."),
                        onError: (err) =>
                          toast.error(
                            `Failed to confirm: ${err instanceof Error ? err.message : "Unknown error"}`,
                          ),
                      });
                    }}
                  >
                    <Check className="mr-1 h-3.5 w-3.5" />
                    {promoteEntity.isPending ? "Confirming..." : "Mark as confirmed"}
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Owner setup banner — shown when identity not fully configured */}
          <OwnerSetupBanner entity={entity} />

          {/* Entity info (credentials) */}
          <EntityInfoSection
            entityId={entity.id}
            entries={entity.entity_info ?? []}
            isOwner={entity.roles?.includes("owner") ?? false}
          />

          {/* Facts tab */}
          <Card>
            <CardHeader>
              <CardTitle>
                Facts ({entity.fact_count})
              </CardTitle>
            </CardHeader>
            <CardContent>
              {entity.recent_facts.length === 0 ? (
                <p className="text-muted-foreground py-4 text-center text-sm">
                  No facts linked to this entity.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b text-left text-muted-foreground">
                        <th className="pb-2 pr-4 font-medium">Scope</th>
                        <th className="pb-2 pr-4 font-medium">Predicate</th>
                        <th className="pb-2 pr-4 font-medium">Content</th>
                        <th className="pb-2 pr-4 font-medium text-right">
                          Confidence
                        </th>
                        <th className="pb-2 font-medium">Created</th>
                      </tr>
                    </thead>
                    <tbody>
                      {entity.recent_facts.map((fact) => (
                        <tr
                          key={fact.id}
                          className="border-b last:border-0 hover:bg-muted/50"
                        >
                          <td className="py-2 pr-4 font-medium">
                            {fact.scope}
                          </td>
                          <td className="py-2 pr-4 text-muted-foreground">
                            {fact.predicate}
                          </td>
                          <td className="py-2 pr-4 max-w-md truncate">
                            {fact.content}
                          </td>
                          <td className="py-2 pr-4 text-right tabular-nums">
                            {(fact.confidence * 100).toFixed(0)}%
                          </td>
                          <td className="py-2 text-muted-foreground">
                            {new Date(fact.created_at).toLocaleDateString()}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Contact link */}
          <LinkedContactSection entityId={entity.id} entity={entity} />
        </>
      )}
    </div>
  );
}
