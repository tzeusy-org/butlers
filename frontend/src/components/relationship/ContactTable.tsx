import { useState, useEffect, useCallback } from "react";
import { EmptyState as EmptyStateUI } from "@/components/ui/empty-state";
import { formatDistanceToNow } from "date-fns";
import { useNavigate } from "react-router";
import { EditIcon, GitMergeIcon, TrashIcon } from "lucide-react";
import { toast } from "sonner";

import { getContacts } from "@/api/client";
import type { ContactSummary, Label } from "@/api/types";
import { useDeleteContact, useMergeContact } from "@/hooks/use-contacts";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
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
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ContactTableProps {
  contacts: ContactSummary[];
  isLoading: boolean;
  /** Search query, controlled externally. */
  search: string;
  onSearchChange: (value: string) => void;
  /** All available labels for the filter. */
  allLabels: Label[];
  /** Currently selected label filter (name), or empty for no filter. */
  activeLabel: string;
  onLabelFilter: (label: string) => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Deterministic badge color from label color or name hash. */
function labelStyle(label: Label): string {
  if (label.color) {
    return label.color;
  }
  const colors = [
    "#3b82f6", "#8b5cf6", "#f59e0b", "#14b8a6",
    "#f43f5e", "#6366f1", "#06b6d4", "#f97316",
  ];
  let hash = 0;
  for (let i = 0; i < label.name.length; i++) {
    hash = (hash * 31 + label.name.charCodeAt(i)) | 0;
  }
  return colors[Math.abs(hash) % colors.length];
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <TableRow key={i}>
          <TableCell><Skeleton className="h-4 w-36" /></TableCell>
          <TableCell><Skeleton className="h-4 w-40" /></TableCell>
          <TableCell><Skeleton className="h-4 w-28" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
          <TableCell><Skeleton className="h-4 w-20" /></TableCell>
          <TableCell><Skeleton className="h-4 w-16" /></TableCell>
        </TableRow>
      ))}
    </>
  );
}

function EmptyState() {
  return (
    <EmptyStateUI
      title="No contacts found"
      description="Contacts will appear here as they are added through the Relationship butler."
    />
  );
}

// ---------------------------------------------------------------------------
// MergeDialog
// ---------------------------------------------------------------------------

function MergeDialog({
  open,
  onOpenChange,
  sourceContact,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  sourceContact: ContactSummary;
}) {
  const [mergeSearch, setMergeSearch] = useState("");
  const [results, setResults] = useState<ContactSummary[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [selectedTarget, setSelectedTarget] = useState<ContactSummary | null>(null);
  const mergeMutation = useMergeContact();

  // Reset state when dialog opens/closes
  useEffect(() => {
    if (!open) {
      setMergeSearch("");
      setResults([]);
      setSelectedTarget(null);
    }
  }, [open]);

  // Debounced search
  useEffect(() => {
    if (!mergeSearch.trim()) {
      setResults([]);
      return;
    }

    const timeout = setTimeout(async () => {
      setIsSearching(true);
      try {
        const data = await getContacts({ q: mergeSearch, limit: 10 });
        // Exclude the source contact from results
        setResults(data.contacts.filter((c) => c.id !== sourceContact.id));
      } catch {
        setResults([]);
      } finally {
        setIsSearching(false);
      }
    }, 300);

    return () => clearTimeout(timeout);
  }, [mergeSearch, sourceContact.id]);

  async function handleMerge() {
    if (!selectedTarget) return;
    await mergeMutation.mutateAsync({
      contactId: selectedTarget.id,
      request: { source_contact_id: sourceContact.id },
    });
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Merge Contact</DialogTitle>
          <DialogDescription>
            Merge <strong>{sourceContact.full_name}</strong> into another contact.
            Search by name or paste a contact ID.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 py-2">
          <Input
            placeholder="Search by name or ID..."
            value={mergeSearch}
            onChange={(e) => {
              setMergeSearch(e.target.value);
              setSelectedTarget(null);
            }}
            autoFocus
          />

          {isSearching && (
            <p className="text-muted-foreground text-sm">Searching...</p>
          )}

          {!isSearching && mergeSearch.trim() && results.length === 0 && (
            <p className="text-muted-foreground text-sm">No matching contacts found.</p>
          )}

          {results.length > 0 && (
            <div className="max-h-48 space-y-1 overflow-y-auto">
              {results.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  className={`w-full rounded-md border px-3 py-2 text-left text-sm transition-colors ${
                    selectedTarget?.id === c.id
                      ? "border-primary bg-accent"
                      : "hover:bg-accent/50"
                  }`}
                  onClick={() => setSelectedTarget(c)}
                >
                  <span className="font-medium">{c.full_name}</span>
                  {c.email && (
                    <span className="text-muted-foreground ml-2 text-xs">{c.email}</span>
                  )}
                  <span className="text-muted-foreground ml-2 font-mono text-xs">{c.id}</span>
                </button>
              ))}
            </div>
          )}

          {selectedTarget && (
            <p className="text-sm">
              Will merge into: <strong>{selectedTarget.full_name}</strong>
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={handleMerge}
            disabled={!selectedTarget || mergeMutation.isPending}
          >
            {mergeMutation.isPending ? "Merging..." : "Merge"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// ContactTable
// ---------------------------------------------------------------------------

export default function ContactTable({
  contacts,
  isLoading,
  search,
  onSearchChange,
  allLabels,
  activeLabel,
  onLabelFilter,
}: ContactTableProps) {
  const navigate = useNavigate();
  const deleteMutation = useDeleteContact();

  const [mergeTarget, setMergeTarget] = useState<ContactSummary | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ContactSummary | null>(null);

  const stopPropagation = useCallback((e: React.MouseEvent) => e.stopPropagation(), []);

  return (
    <div className="space-y-4">
      {/* Search + label filter */}
      <div className="flex flex-wrap items-center gap-3">
        <Input
          placeholder="Search contacts..."
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          className="w-64"
        />
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge
            variant={activeLabel === "" ? "default" : "outline"}
            className="cursor-pointer"
            onClick={() => onLabelFilter("")}
          >
            All
          </Badge>
          {allLabels.map((label) => (
            <Badge
              key={label.id}
              variant={activeLabel === label.name ? "default" : "outline"}
              className="cursor-pointer"
              style={
                activeLabel === label.name
                  ? { backgroundColor: labelStyle(label), color: "#fff" }
                  : {}
              }
              onClick={() =>
                onLabelFilter(activeLabel === label.name ? "" : label.name)
              }
            >
              {label.name}
            </Badge>
          ))}
        </div>
      </div>

      {/* Table */}
      {!isLoading && contacts.length === 0 ? (
        <EmptyState />
      ) : (
        <TooltipProvider>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Email</TableHead>
                <TableHead>Phone</TableHead>
                <TableHead>Labels</TableHead>
                <TableHead>Last Interaction</TableHead>
                <TableHead className="w-[100px]">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading ? (
                <SkeletonRows />
              ) : (
                contacts.map((contact) => (
                  <TableRow
                    key={contact.id}
                    className="cursor-pointer"
                    onClick={() => navigate(`/contacts/${contact.id}`)}
                  >
                    <TableCell className="font-medium">
                      {contact.full_name}
                      {contact.nickname && (
                        <span className="text-muted-foreground ml-1 text-xs">
                          ({contact.nickname})
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {contact.email ?? "\u2014"}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {contact.phone ?? "\u2014"}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {contact.labels.map((label) => (
                          <Badge
                            key={label.id}
                            variant="outline"
                            style={{
                              borderColor: labelStyle(label),
                              color: labelStyle(label),
                            }}
                            className="text-xs"
                          >
                            {label.name}
                          </Badge>
                        ))}
                        {contact.labels.length === 0 && (
                          <span className="text-muted-foreground text-xs">{"\u2014"}</span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {contact.last_interaction_at
                        ? formatDistanceToNow(new Date(contact.last_interaction_at), {
                            addSuffix: true,
                          })
                        : "\u2014"}
                    </TableCell>
                    <TableCell onClick={stopPropagation}>
                      <div className="flex items-center gap-1">
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon-xs"
                              onClick={() => navigate(`/contacts/${contact.id}`)}
                            >
                              <EditIcon />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>Edit</TooltipContent>
                        </Tooltip>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon-xs"
                              onClick={() => setMergeTarget(contact)}
                            >
                              <GitMergeIcon />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>Merge</TooltipContent>
                        </Tooltip>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon-xs"
                              onClick={() => setDeleteTarget(contact)}
                            >
                              <TrashIcon />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>Delete</TooltipContent>
                        </Tooltip>
                      </div>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </TooltipProvider>
      )}

      {/* Merge dialog */}
      {mergeTarget && (
        <MergeDialog
          open={!!mergeTarget}
          onOpenChange={(open) => { if (!open) setMergeTarget(null); }}
          sourceContact={mergeTarget}
        />
      )}

      {/* Delete confirmation */}
      <AlertDialog
        open={!!deleteTarget}
        onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete contact?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete{" "}
              <strong>{deleteTarget?.full_name}</strong> and all associated
              contact info. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deleteMutation.isPending}
              onClick={async () => {
                if (!deleteTarget) return;
                try {
                  await deleteMutation.mutateAsync(deleteTarget.id);
                  toast.success(`Deleted ${deleteTarget.full_name}`);
                } catch (err) {
                  toast.error(
                    `Delete failed: ${err instanceof Error ? err.message : "Unknown error"}`,
                  );
                }
                setDeleteTarget(null);
              }}
            >
              {deleteMutation.isPending ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
