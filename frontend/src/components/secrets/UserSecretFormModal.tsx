/**
 * UserSecretFormModal — modal dialog for adding or editing an entity_info
 * entry on the owner entity (User secrets tab).
 */

import { useEffect, useState } from "react";

import type { EntityInfoEntry } from "@/api/types.ts";
import { revealEntitySecret } from "@/api/index.ts";
import type { SecretDisplayRow } from "@/lib/secrets-rows";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  ENTITY_INFO_TYPES,
  entityInfoTypeLabel,
  SECURED_USER_TYPES,
} from "@/lib/user-secret-templates";
import {
  useCreateOwnerEntityInfo,
  useUpdateOwnerEntityInfo,
} from "@/hooks/use-owner-secrets";

interface FormState {
  type: string;
  value: string;
  label: string;
}

interface UserSecretFormModalProps {
  entityId: string;
  /** When provided (editing an existing entry), the type is locked. */
  editRow?: SecretDisplayRow | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function UserSecretFormModal({
  entityId,
  editRow,
  open,
  onOpenChange,
}: UserSecretFormModalProps) {
  const entry: EntityInfoEntry | null | undefined = editRow?.entityInfoEntry;
  const isEditing = !!entry;

  const [form, setForm] = useState<FormState>({ type: "", value: "", label: "" });
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [prevKey, setPrevKey] = useState("");

  const formKey = open ? `${entry?.id ?? editRow?.key ?? "__new__"}` : "__closed__";

  // Reset form when modal opens for a different entry
  if (formKey !== prevKey) {
    setPrevKey(formKey);
    setError(null);
    setSuccess(false);
    if (entry) {
      setForm({
        type: entry.type,
        value: entry.secured ? "" : (entry.value ?? ""),
        label: entry.label ?? "",
      });
    } else if (editRow) {
      // Creating from a template row (missing state)
      setForm({ type: editRow.key, value: "", label: "" });
    } else {
      setForm({ type: "", value: "", label: "" });
    }
  }

  // Pre-fill value from reveal endpoint when editing a secured entry
  useEffect(() => {
    if (open && isEditing && entry?.secured && entityId) {
      revealEntitySecret(entityId, entry.id)
        .then((resp) => {
          setForm((prev) => ({ ...prev, value: resp.value ?? "" }));
        })
        .catch(() => {
          // Silently fail — user can still type a new value
        });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, isEditing, entry?.id, entityId]);

  const createMutation = useCreateOwnerEntityInfo();
  const updateMutation = useUpdateOwnerEntityInfo();

  const isSecured = SECURED_USER_TYPES.has(form.type);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(false);

    if (!form.type.trim()) {
      setError("Type is required.");
      return;
    }
    if (!form.value.trim()) {
      setError("Value is required.");
      return;
    }

    try {
      if (isEditing && entry) {
        await updateMutation.mutateAsync({
          entityId,
          infoId: entry.id,
          request: { value: form.value.trim() },
        });
      } else {
        await createMutation.mutateAsync({
          entityId,
          request: {
            type: form.type.trim(),
            value: form.value.trim(),
            label: form.label.trim() || undefined,
            secured: isSecured,
          },
        });
      }
      setSuccess(true);
      setTimeout(() => onOpenChange(false), 800);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save credential.");
    }
  }

  const isPending = createMutation.isPending || updateMutation.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle>{isEditing ? "Edit credential" : "Add credential"}</DialogTitle>
          <DialogDescription>
            {isEditing
              ? `Update the value for ${entityInfoTypeLabel(entry?.type ?? "")}.`
              : "Add a credential to the owner entity."}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4 py-2">
          {/* Type field */}
          <div className="space-y-1.5">
            <Label htmlFor="user-secret-type">Type</Label>
            {isEditing ? (
              <Input
                id="user-secret-type"
                value={entityInfoTypeLabel(form.type)}
                disabled
                className="font-mono bg-muted"
              />
            ) : (
              <Select
                value={form.type}
                onValueChange={(v) => setForm((prev) => ({ ...prev, type: v }))}
              >
                <SelectTrigger id="user-secret-type">
                  <SelectValue placeholder="Select credential type" />
                </SelectTrigger>
                <SelectContent>
                  {ENTITY_INFO_TYPES.filter((t) => t !== "other").map((t) => (
                    <SelectItem key={t} value={t}>
                      {entityInfoTypeLabel(t)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>

          {/* Value field */}
          <div className="space-y-1.5">
            <Label htmlFor="user-secret-value">Value</Label>
            <Input
              id="user-secret-value"
              type={isSecured ? "password" : "text"}
              placeholder={isEditing ? "Enter new value..." : "Enter value..."}
              value={form.value}
              onChange={(e) => setForm((prev) => ({ ...prev, value: e.target.value }))}
              autoComplete="off"
              className="font-mono"
              required
            />
            {isSecured && (
              <p className="text-xs text-muted-foreground">
                This value will be stored as secured (masked in the UI).
              </p>
            )}
          </div>

          {/* Label field (create only) */}
          {!isEditing && (
            <div className="space-y-1.5">
              <Label htmlFor="user-secret-label">
                Label <span className="text-muted-foreground font-normal">(optional)</span>
              </Label>
              <Input
                id="user-secret-label"
                placeholder="Optional display label"
                value={form.label}
                onChange={(e) => setForm((prev) => ({ ...prev, label: e.target.value }))}
              />
            </div>
          )}

          {error && <p className="text-sm text-destructive">{error}</p>}
          {success && (
            <p className="text-sm text-green-600 dark:text-green-400">
              Credential saved successfully.
            </p>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={isPending}>
              {isPending ? "Saving..." : isEditing ? "Update" : "Add credential"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
