import { useState } from "react";
import { Link, useParams } from "react-router";
import { Check, Pencil, Plus, Trash2, X } from "lucide-react";
import { toast } from "sonner";

import type { EntityInfoEntry } from "@/api/types";
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
import {
  useCreateEntityInfo,
  useDeleteEntityInfo,
  useEntity,
  useRevealEntitySecret,
  useUpdateEntityInfo,
} from "@/hooks/use-memory";

// ---------------------------------------------------------------------------
// Entity info type helpers
// ---------------------------------------------------------------------------

const ENTITY_INFO_TYPES = [
  "api_key",
  "api_secret",
  "token",
  "password",
  "username",
  "url",
  "other",
] as const;

const SECURED_TYPES = new Set<string>(["api_key", "api_secret", "token", "password"]);

function entityInfoTypeLabel(type: string): string {
  switch (type) {
    case "api_key": return "API Key";
    case "api_secret": return "API Secret";
    case "token": return "Token";
    case "password": return "Password";
    case "username": return "Username";
    case "url": return "URL";
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
}: {
  entityId: string;
  onDone: () => void;
}) {
  const createInfo = useCreateEntityInfo();
  const [type, setType] = useState<string>("api_key");
  const [value, setValue] = useState("");
  const [label, setLabel] = useState("");
  const [isPrimary, setIsPrimary] = useState(false);

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
            {ENTITY_INFO_TYPES.map((t) => (
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
}: {
  entityId: string;
  entries: EntityInfoEntry[];
}) {
  const [addingInfo, setAddingInfo] = useState(false);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Credentials &amp; Info</CardTitle>
        </div>
      </CardHeader>
      <CardContent>
        {entries.length === 0 && !addingInfo ? (
          <p className="text-muted-foreground py-2 text-center text-sm">
            No entity info entries yet.
          </p>
        ) : (
          <div className="space-y-1.5">
            {entries.map((entry) => (
              <EntityInfoRow key={entry.id} entry={entry} entityId={entityId} />
            ))}
          </div>
        )}
        {addingInfo ? (
          <AddEntityInfoForm entityId={entityId} onDone={() => setAddingInfo(false)} />
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
// EntityDetailPage
// ---------------------------------------------------------------------------

export default function EntityDetailPage() {
  const { entityId } = useParams<{ entityId: string }>();
  const { data, isLoading, error } = useEntity(entityId);
  const entity = data?.data;

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
                <CardTitle className="text-2xl">
                  {entity.canonical_name}
                </CardTitle>
                <Badge>{entity.entity_type}</Badge>
                {entity.roles?.includes("owner") && (
                  <Badge
                    style={{ backgroundColor: "#7c3aed", color: "#fff" }}
                    className="text-xs"
                  >
                    Owner
                  </Badge>
                )}
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {/* Aliases */}
              {entity.aliases.length > 0 && (
                <div>
                  <p className="text-muted-foreground mb-1 text-sm font-medium">
                    Aliases
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {entity.aliases.map((alias) => (
                      <Badge key={alias} variant="secondary">
                        {alias}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* Metadata */}
              {Object.keys(entity.metadata).length > 0 && (
                <div>
                  <p className="text-muted-foreground mb-1 text-sm font-medium">
                    Metadata
                  </p>
                  <pre className="rounded-md bg-muted p-3 text-xs overflow-x-auto">
                    {JSON.stringify(entity.metadata, null, 2)}
                  </pre>
                </div>
              )}

              {/* Timestamps */}
              <div className="flex gap-6 text-xs text-muted-foreground">
                <span>
                  Created: {new Date(entity.created_at).toLocaleString()}
                </span>
                <span>
                  Updated: {new Date(entity.updated_at).toLocaleString()}
                </span>
              </div>
            </CardContent>
          </Card>

          {/* Entity info (credentials) */}
          <EntityInfoSection
            entityId={entity.id}
            entries={entity.entity_info ?? []}
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
                        <th className="pb-2 pr-4 font-medium">Subject</th>
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
                            {fact.subject}
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
          <Card>
            <CardHeader>
              <CardTitle>Linked Contact</CardTitle>
            </CardHeader>
            <CardContent>
              {entity.linked_contact_id ? (
                <Link
                  to={`/contacts/${entity.linked_contact_id}`}
                  className="text-primary hover:underline"
                >
                  {entity.linked_contact_name ?? entity.linked_contact_id}
                </Link>
              ) : (
                <p className="text-muted-foreground text-sm">
                  No linked contact.
                </p>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
