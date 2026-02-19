/**
 * StateBrowser — table-based browser for butler key-value state entries.
 *
 * Features:
 * - Table with key, value (JSON, expandable), updated_at columns
 * - Prefix search/filter input
 * - Expandable JSON rows (click to expand/collapse)
 * - Edit button per row (opens dialog)
 * - Delete button per row (with confirmation)
 * - Loading skeleton state
 * - Empty state message
 */

import { useMemo, useState } from "react";

import type { StateEntry } from "@/api/types.ts";
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
import { Textarea } from "@/components/ui/textarea";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface StateBrowserProps {
  entries: StateEntry[];
  isLoading: boolean;
  onEdit: (key: string, value: unknown) => void;
  onDelete: (key: string) => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Compact JSON preview (single line, truncated). */
function jsonPreview(value: unknown, maxLen = 80): string {
  const str = JSON.stringify(value);
  return str.length > maxLen ? str.slice(0, maxLen) + "..." : str;
}

/** Format an ISO timestamp to a human-readable local string. */
function formatTimestamp(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function StateBrowserSkeleton() {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead><Skeleton className="h-4 w-20" /></TableHead>
          <TableHead><Skeleton className="h-4 w-32" /></TableHead>
          <TableHead><Skeleton className="h-4 w-24" /></TableHead>
          <TableHead className="text-right"><Skeleton className="ml-auto h-4 w-20" /></TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {Array.from({ length: 5 }, (_, i) => (
          <TableRow key={i}>
            <TableCell><Skeleton className="h-4 w-28" /></TableCell>
            <TableCell><Skeleton className="h-4 w-48" /></TableCell>
            <TableCell><Skeleton className="h-4 w-32" /></TableCell>
            <TableCell className="text-right">
              <div className="flex justify-end gap-2">
                <Skeleton className="h-8 w-14" />
                <Skeleton className="h-8 w-16" />
              </div>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// ---------------------------------------------------------------------------
// Edit Dialog
// ---------------------------------------------------------------------------

function EditStateDialog({
  open,
  onOpenChange,
  editKey,
  editValue,
  onSave,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editKey: string;
  editValue: string;
  onSave: (key: string, value: string) => void;
}) {
  const [key, setKey] = useState(editKey);
  const [value, setValue] = useState(editValue);
  const [parseError, setParseError] = useState<string | null>(null);

  // Reset when dialog opens with new values
  const handleOpenChange = (nextOpen: boolean) => {
    if (nextOpen) {
      setKey(editKey);
      setValue(editValue);
      setParseError(null);
    }
    onOpenChange(nextOpen);
  };

  function handleSave() {
    try {
      JSON.parse(value);
      setParseError(null);
      onSave(key, value);
      onOpenChange(false);
    } catch {
      setParseError("Invalid JSON. Please check the value and try again.");
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{editKey ? "Edit State" : "Set Value"}</DialogTitle>
          <DialogDescription>
            {editKey
              ? `Edit the value for key "${editKey}"`
              : "Set a new key-value pair in the state store"}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <label htmlFor="state-key" className="text-sm font-medium">
              Key
            </label>
            <Input
              id="state-key"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="e.g. config.theme"
              disabled={!!editKey}
            />
          </div>
          <div className="space-y-2">
            <label htmlFor="state-value" className="text-sm font-medium">
              Value (JSON)
            </label>
            <Textarea
              id="state-value"
              value={value}
              onChange={(e) => {
                setValue(e.target.value);
                setParseError(null);
              }}
              placeholder='{"key": "value"}'
              className="min-h-32 font-mono text-sm"
            />
            {parseError && (
              <p className="text-sm text-destructive">{parseError}</p>
            )}
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={!key.trim() || !value.trim()}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Delete Confirmation Dialog
// ---------------------------------------------------------------------------

function DeleteConfirmDialog({
  open,
  onOpenChange,
  stateKey,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  stateKey: string;
  onConfirm: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Delete State Entry</DialogTitle>
          <DialogDescription>
            Are you sure you want to delete the key "{stateKey}"? This action cannot be undone.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => {
              onConfirm();
              onOpenChange(false);
            }}
          >
            Delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// StateBrowser
// ---------------------------------------------------------------------------

export default function StateBrowser({
  entries,
  isLoading,
  onEdit,
  onDelete,
}: StateBrowserProps) {
  const [filter, setFilter] = useState("");
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set());

  // Edit dialog state
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [editKey, setEditKey] = useState("");
  const [editValue, setEditValue] = useState("");

  // Delete confirmation state
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleteKey, setDeleteKey] = useState("");

  const filteredEntries = useMemo(() => {
    if (!filter.trim()) return entries;
    const lowerFilter = filter.toLowerCase();
    return entries.filter((e) => e.key.toLowerCase().startsWith(lowerFilter));
  }, [entries, filter]);

  function toggleExpand(key: string) {
    setExpandedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }

  function handleEditClick(entry: StateEntry) {
    setEditKey(entry.key);
    setEditValue(JSON.stringify(entry.value, null, 2));
    setEditDialogOpen(true);
  }

  function handleEditSave(key: string, rawValue: string) {
    const parsed = JSON.parse(rawValue) as unknown;
    onEdit(key, parsed);
  }

  function handleDeleteClick(key: string) {
    setDeleteKey(key);
    setDeleteDialogOpen(true);
  }

  function handleDeleteConfirm() {
    onDelete(deleteKey);
  }

  if (isLoading) {
    return <StateBrowserSkeleton />;
  }

  return (
    <div className="space-y-4">
      {/* Filter input */}
      <Input
        placeholder="Filter by key prefix..."
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        className="max-w-sm"
      />

      {/* Table or empty state */}
      {filteredEntries.length === 0 ? (
        <div className="flex items-center justify-center rounded-md border border-dashed py-12">
          <p className="text-sm text-muted-foreground">
            {entries.length === 0
              ? "No state entries. Use \"Set Value\" to create one."
              : "No entries match the current filter."}
          </p>
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Key</TableHead>
              <TableHead>Value</TableHead>
              <TableHead>Updated</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filteredEntries.map((entry) => {
              const isExpanded = expandedKeys.has(entry.key);
              return (
                <TableRow key={entry.key}>
                  <TableCell className="font-mono text-sm align-top">
                    {entry.key}
                  </TableCell>
                  <TableCell className="max-w-md align-top">
                    <button
                      type="button"
                      onClick={() => toggleExpand(entry.key)}
                      className="w-full cursor-pointer text-left"
                    >
                      {isExpanded ? (
                        <pre className="overflow-auto rounded-md bg-muted p-2 text-xs font-mono whitespace-pre-wrap">
                          {JSON.stringify(entry.value, null, 2)}
                        </pre>
                      ) : (
                        <code className="text-xs text-muted-foreground">
                          {jsonPreview(entry.value)}
                        </code>
                      )}
                    </button>
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground whitespace-nowrap align-top">
                    {formatTimestamp(entry.updated_at)}
                  </TableCell>
                  <TableCell className="text-right align-top">
                    <div className="flex justify-end gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleEditClick(entry)}
                      >
                        Edit
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        className="text-destructive hover:text-destructive"
                        onClick={() => handleDeleteClick(entry.key)}
                      >
                        Delete
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}

      {/* Edit dialog */}
      <EditStateDialog
        open={editDialogOpen}
        onOpenChange={setEditDialogOpen}
        editKey={editKey}
        editValue={editValue}
        onSave={handleEditSave}
      />

      {/* Delete confirmation dialog */}
      <DeleteConfirmDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        stateKey={deleteKey}
        onConfirm={handleDeleteConfirm}
      />
    </div>
  );
}
