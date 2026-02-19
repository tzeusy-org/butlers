/**
 * SecretsTable — generic table for listing and managing butler secrets.
 *
 * Groups secrets by category and renders them in a table with edit/delete
 * actions. Values are masked by default with a reveal toggle per row.
 */

import { useState } from "react";

import type { SecretEntry } from "@/api/types.ts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useDeleteSecret } from "@/hooks/use-secrets";

// ---------------------------------------------------------------------------
// Category ordering and display names
// ---------------------------------------------------------------------------

const CATEGORY_ORDER = ["core", "telegram", "email", "google", "gemini", "general"];

function getCategoryLabel(category: string): string {
  const labels: Record<string, string> = {
    core: "Core",
    telegram: "Telegram",
    email: "Email",
    google: "Google",
    gemini: "Gemini",
    general: "General",
  };
  return labels[category] ?? category.charAt(0).toUpperCase() + category.slice(1);
}

function groupByCategory(secrets: SecretEntry[]): [string, SecretEntry[]][] {
  const groups: Record<string, SecretEntry[]> = {};
  for (const secret of secrets) {
    const cat = secret.category ?? "general";
    if (!groups[cat]) groups[cat] = [];
    groups[cat].push(secret);
  }
  // Sort categories by known order, then alphabetically for unknowns
  const sortedKeys = Object.keys(groups).sort((a, b) => {
    const ai = CATEGORY_ORDER.indexOf(a);
    const bi = CATEGORY_ORDER.indexOf(b);
    if (ai !== -1 && bi !== -1) return ai - bi;
    if (ai !== -1) return -1;
    if (bi !== -1) return 1;
    return a.localeCompare(b);
  });
  return sortedKeys.map((k) => [k, groups[k]]);
}

// ---------------------------------------------------------------------------
// Source badge
// ---------------------------------------------------------------------------

function SourceBadge({ source }: { source: string }) {
  return (
    <Badge variant={source === "database" ? "default" : "secondary"} className="text-xs">
      {source}
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------

function StatusBadge({ isSet }: { isSet: boolean }) {
  return (
    <Badge variant={isSet ? "default" : "outline"} className="text-xs">
      {isSet ? "Configured" : "Missing"}
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// Masked value cell with reveal toggle
// ---------------------------------------------------------------------------

function MaskedValue({ isSet }: { isSet: boolean }) {
  const [revealed, setRevealed] = useState(false);

  if (!isSet) {
    return <span className="text-muted-foreground text-xs italic">not set</span>;
  }

  return (
    <span className="flex items-center gap-1.5">
      <span className="font-mono text-sm">
        {revealed ? "(value hidden — write-only)" : "••••••••"}
      </span>
      <button
        type="button"
        onClick={() => setRevealed((v) => !v)}
        className="text-muted-foreground hover:text-foreground transition-colors"
        title={revealed ? "Hide" : "Reveal"}
        aria-label={revealed ? "Hide value" : "Reveal value indicator"}
      >
        {revealed ? (
          // Eye-off icon
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
            <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
            <line x1="1" y1="1" x2="23" y2="23" />
          </svg>
        ) : (
          // Eye icon
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
            <circle cx="12" cy="12" r="3" />
          </svg>
        )}
      </button>
    </span>
  );
}

// ---------------------------------------------------------------------------
// Delete confirmation dialog
// ---------------------------------------------------------------------------

function DeleteSecretDialog({
  butlerName,
  secretKey,
  open,
  onOpenChange,
}: {
  butlerName: string;
  secretKey: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const deleteMutation = useDeleteSecret(butlerName);
  const [error, setError] = useState<string | null>(null);

  async function handleDelete() {
    setError(null);
    try {
      await deleteMutation.mutateAsync(secretKey);
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete secret.");
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete secret?</DialogTitle>
          <DialogDescription>
            This will permanently remove the secret <code className="font-mono">{secretKey}</code>{" "}
            from the database. This action cannot be undone.
          </DialogDescription>
        </DialogHeader>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={handleDelete}
            disabled={deleteMutation.isPending}
          >
            {deleteMutation.isPending ? "Deleting..." : "Delete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Row action icons
// ---------------------------------------------------------------------------

function EditIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
      <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
      <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Secret row
// ---------------------------------------------------------------------------

function SecretRow({
  secret,
  butlerName,
  onEdit,
}: {
  secret: SecretEntry;
  butlerName: string;
  onEdit: (secret: SecretEntry) => void;
}) {
  const [deleteOpen, setDeleteOpen] = useState(false);

  const updatedAt = new Date(secret.updated_at).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });

  return (
    <>
      <TableRow>
        <TableCell className="font-mono text-sm">{secret.key}</TableCell>
        <TableCell className="text-sm text-muted-foreground max-w-[200px] truncate">
          {secret.description ?? <span className="italic">No description</span>}
        </TableCell>
        <TableCell>
          <StatusBadge isSet={secret.is_set} />
        </TableCell>
        <TableCell>
          <SourceBadge source={secret.source} />
        </TableCell>
        <TableCell className="text-sm text-muted-foreground">{updatedAt}</TableCell>
        <TableCell>
          <MaskedValue isSet={secret.is_set} />
        </TableCell>
        <TableCell>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0"
              onClick={() => onEdit(secret)}
              title="Edit secret"
            >
              <EditIcon />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0 text-destructive hover:text-destructive"
              onClick={() => setDeleteOpen(true)}
              title="Delete secret"
            >
              <TrashIcon />
            </Button>
          </div>
        </TableCell>
      </TableRow>
      <DeleteSecretDialog
        butlerName={butlerName}
        secretKey={secret.key}
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// Category group row
// ---------------------------------------------------------------------------

function CategoryGroupRows({
  category,
  secrets,
  butlerName,
  onEdit,
}: {
  category: string;
  secrets: SecretEntry[];
  butlerName: string;
  onEdit: (secret: SecretEntry) => void;
}) {
  return (
    <>
      <TableRow className="bg-muted/30 hover:bg-muted/30">
        <TableCell colSpan={7} className="py-1.5 px-4">
          <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {getCategoryLabel(category)}
          </span>
        </TableCell>
      </TableRow>
      {secrets.map((secret) => (
        <SecretRow
          key={secret.key}
          secret={secret}
          butlerName={butlerName}
          onEdit={onEdit}
        />
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

interface SecretsTableProps {
  butlerName: string;
  secrets: SecretEntry[];
  isLoading: boolean;
  isError: boolean;
  onEdit: (secret: SecretEntry) => void;
}

export function SecretsTable({
  butlerName,
  secrets,
  isLoading,
  isError,
  onEdit,
}: SecretsTableProps) {
  if (isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
      </div>
    );
  }

  if (isError) {
    return (
      <p className="text-sm text-destructive">
        Failed to load secrets. Ensure the dashboard API is running.
      </p>
    );
  }

  if (secrets.length === 0) {
    return (
      <EmptyState
        title="No secrets configured"
        description="Add your first secret using the button above. Secrets are stored securely in the database and never echoed back."
        icon={
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="40"
            height="40"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
            <path d="M7 11V7a5 5 0 0 1 10 0v4" />
          </svg>
        }
      />
    );
  }

  const groups = groupByCategory(secrets);

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-[200px]">Key</TableHead>
          <TableHead>Description</TableHead>
          <TableHead className="w-[110px]">Status</TableHead>
          <TableHead className="w-[110px]">Source</TableHead>
          <TableHead className="w-[120px]">Last Updated</TableHead>
          <TableHead className="w-[140px]">Value</TableHead>
          <TableHead className="w-[80px]">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {groups.map(([category, categorySecrets]) => (
          <CategoryGroupRows
            key={category}
            category={category}
            secrets={categorySecrets}
            butlerName={butlerName}
            onEdit={onEdit}
          />
        ))}
      </TableBody>
    </Table>
  );
}
