/**
 * SecretFormModal — modal dialog for adding or editing a butler secret.
 *
 * Used for both create (no initial data) and edit (pre-filled from existing
 * SecretEntry). Values are write-only — when editing, the value field starts
 * empty and must be re-entered if you want to change it.
 */

import { useState } from "react";

import type { SecretEntry } from "@/api/types.ts";
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
import { useUpsertSecret } from "@/hooks/use-secrets";

// ---------------------------------------------------------------------------
// Known secret templates for suggestions
// ---------------------------------------------------------------------------

export interface SecretTemplate {
  key: string;
  description: string;
  category: string;
}

const SECRET_TEMPLATES: SecretTemplate[] = [
  { key: "ANTHROPIC_API_KEY", description: "Anthropic Claude API key", category: "core" },
  { key: "BUTLER_TELEGRAM_TOKEN", description: "Telegram bot token", category: "telegram" },
  { key: "BUTLER_TELEGRAM_CHAT_ID", description: "Telegram chat ID", category: "telegram" },
  { key: "BUTLER_EMAIL_ADDRESS", description: "Butler email address", category: "email" },
  { key: "BUTLER_EMAIL_PASSWORD", description: "Email account password or app password", category: "email" },
  { key: "BUTLER_EMAIL_SMTP_HOST", description: "SMTP server hostname", category: "email" },
  { key: "BUTLER_EMAIL_IMAP_HOST", description: "IMAP server hostname", category: "email" },
  { key: "GOOGLE_CLIENT_ID", description: "Google OAuth client ID", category: "google" },
  { key: "GOOGLE_CLIENT_SECRET", description: "Google OAuth client secret", category: "google" },
  { key: "GEMINI_API_KEY", description: "Google Gemini API key", category: "gemini" },
];

const CATEGORIES = ["core", "telegram", "email", "google", "gemini", "general"] as const;

function categoryFromKey(key: string): string {
  const upper = key.toUpperCase();
  if (upper.includes("TELEGRAM")) return "telegram";
  if (upper.includes("EMAIL") || upper.includes("SMTP") || upper.includes("IMAP")) return "email";
  if (upper.includes("GOOGLE") || upper.includes("GOOGLE_CLIENT")) return "google";
  if (upper.includes("GEMINI")) return "gemini";
  if (upper.includes("ANTHROPIC") || upper.includes("DATABASE_URL") || upper.includes("SECRET_KEY")) return "core";
  return "general";
}

// ---------------------------------------------------------------------------
// Internal form state type
// ---------------------------------------------------------------------------

interface FormState {
  key: string;
  value: string;
  category: string;
  description: string;
}

function makeInitialState(editSecret: SecretEntry | null | undefined): FormState {
  if (editSecret) {
    return {
      key: editSecret.key,
      value: "",
      category: editSecret.category ?? "general",
      description: editSecret.description ?? "",
    };
  }
  return { key: "", value: "", category: "general", description: "" };
}

// ---------------------------------------------------------------------------
// Modal component
// ---------------------------------------------------------------------------

interface SecretFormModalProps {
  butlerName: string;
  /** When provided, the modal is in edit mode (key is locked). */
  editSecret?: SecretEntry | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function SecretFormModal({
  butlerName,
  editSecret,
  open,
  onOpenChange,
}: SecretFormModalProps) {
  const isEditing = !!editSecret;

  // Reset form state each time the modal opens by keying on `open + editSecret.key`
  const formKey = open ? (editSecret?.key ?? "__new__") : "__closed__";

  const [form, setForm] = useState<FormState>(() => makeInitialState(editSecret));
  const [prevFormKey, setPrevFormKey] = useState(formKey);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  // Reset state when modal opens for a different secret (avoid useEffect/setState cascade)
  if (formKey !== prevFormKey) {
    setPrevFormKey(formKey);
    setForm(makeInitialState(editSecret));
    setError(null);
    setSuccess(false);
  }

  const upsertMutation = useUpsertSecret(butlerName);

  // Auto-suggest category and description from template when typing key (create mode only)
  function handleKeyChange(newKey: string) {
    const template = SECRET_TEMPLATES.find((t) => t.key === newKey.toUpperCase());
    if (template) {
      setForm((prev) => ({
        ...prev,
        key: newKey,
        category: template.category,
        description: prev.description || template.description,
      }));
    } else {
      setForm((prev) => ({
        ...prev,
        key: newKey,
        category: categoryFromKey(newKey),
      }));
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(false);

    if (!form.key.trim()) {
      setError("Key is required.");
      return;
    }
    if (!form.value.trim()) {
      setError(
        isEditing
          ? "Enter a new value to update this secret. (Values are write-only and cannot be pre-filled.)"
          : "Value is required when creating a new secret.",
      );
      return;
    }

    try {
      await upsertMutation.mutateAsync({
        key: form.key.trim().toUpperCase(),
        request: {
          value: form.value.trim(),
          category: form.category || undefined,
          description: form.description.trim() || undefined,
        },
      });
      setSuccess(true);
      setTimeout(() => {
        onOpenChange(false);
      }, 800);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save secret.");
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle>{isEditing ? "Edit Secret" : "Add Secret"}</DialogTitle>
          <DialogDescription>
            {isEditing
              ? `Update the value or metadata for ${editSecret?.key}. The current value cannot be displayed.`
              : "Store a new secret in the database. Values are encrypted at rest and never echoed back."}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4 py-2">
          {/* Key field */}
          <div className="space-y-1.5">
            <Label htmlFor="secret-key">Key</Label>
            {isEditing ? (
              <Input
                id="secret-key"
                value={form.key}
                disabled
                className="font-mono bg-muted"
              />
            ) : (
              <>
                <Input
                  id="secret-key"
                  list="secret-key-suggestions"
                  placeholder="ANTHROPIC_API_KEY"
                  value={form.key}
                  onChange={(e) => handleKeyChange(e.target.value)}
                  autoComplete="off"
                  className="font-mono"
                  required
                />
                <datalist id="secret-key-suggestions">
                  {SECRET_TEMPLATES.map((t) => (
                    <option key={t.key} value={t.key} />
                  ))}
                </datalist>
                <p className="text-xs text-muted-foreground">
                  Key will be uppercased automatically.
                </p>
              </>
            )}
          </div>

          {/* Value field */}
          <div className="space-y-1.5">
            <Label htmlFor="secret-value">
              Value
            </Label>
            <Input
              id="secret-value"
              type="password"
              placeholder={isEditing ? "Enter new value to update..." : "Enter secret value..."}
              value={form.value}
              onChange={(e) => setForm((prev) => ({ ...prev, value: e.target.value }))}
              autoComplete="new-password"
              required
            />
            <p className="text-xs text-muted-foreground">
              Write-only — values are stored securely and never displayed.
            </p>
          </div>

          {/* Category */}
          <div className="space-y-1.5">
            <Label htmlFor="secret-category">Category</Label>
            <Select
              value={form.category}
              onValueChange={(v) => setForm((prev) => ({ ...prev, category: v }))}
            >
              <SelectTrigger id="secret-category">
                <SelectValue placeholder="Select category" />
              </SelectTrigger>
              <SelectContent>
                {CATEGORIES.map((cat) => (
                  <SelectItem key={cat} value={cat}>
                    {cat.charAt(0).toUpperCase() + cat.slice(1)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Description */}
          <div className="space-y-1.5">
            <Label htmlFor="secret-description">
              Description <span className="text-muted-foreground font-normal">(optional)</span>
            </Label>
            <Input
              id="secret-description"
              placeholder="Human-readable label for this secret"
              value={form.description}
              onChange={(e) => setForm((prev) => ({ ...prev, description: e.target.value }))}
            />
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}
          {success && (
            <p className="text-sm text-green-600 dark:text-green-400">
              Secret saved successfully.
            </p>
          )}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={upsertMutation.isPending}>
              {upsertMutation.isPending
                ? "Saving..."
                : isEditing
                  ? "Update secret"
                  : "Add secret"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
