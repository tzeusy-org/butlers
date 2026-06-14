// ---------------------------------------------------------------------------
// ResearchTracker — direct add/edit/delete for health research notes [bu-wamzk]
//
// Mirrors ConditionTracker (bu-a7vw9): a list surface with an "Add research"
// toolbar affordance, per-row Edit / Delete actions, a delete confirmation
// dialog, and an add/edit dialog wrapping the shared ResearchForm. It also keeps
// the research-specific search and tag filters that previously lived on the
// page. All writes go through the /api/health/research fact-store path, so
// dashboard edits and butler edits stay in sync.
//
// Research notes are PROPERTY facts (like conditions, NOT temporal): an edit
// supersedes the prior note keyed on its `research:{title}` subject.
// ---------------------------------------------------------------------------

import { useState } from "react";
import { toast } from "sonner";

import type { HealthResearch, ResearchParams } from "@/api/types";
import { ResearchForm } from "@/components/health/ResearchForm";
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
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState as EmptyStateUI } from "@/components/ui/empty-state";
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
import { Time } from "@/components/ui/time";
import { useDeleteResearch, useResearch } from "@/hooks/use-health";

const PAGE_SIZE = 50;

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <TableRow key={i}>
          <TableCell><Skeleton className="h-4 w-48" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
        </TableRow>
      ))}
    </>
  );
}

function EmptyState() {
  return (
    <EmptyStateUI
      title="No research found."
      description="Add a research note with the button above, or save one by talking to your Health butler."
    />
  );
}

// ---------------------------------------------------------------------------
// ResearchRow — a single note with expand + edit/delete affordances
// ---------------------------------------------------------------------------

function ResearchRow({
  note,
  expanded,
  onToggleExpand,
  onEdit,
}: {
  note: HealthResearch;
  expanded: boolean;
  onToggleExpand: () => void;
  onEdit: (note: HealthResearch) => void;
}) {
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const deleteMutation = useDeleteResearch();

  async function handleDelete() {
    try {
      await deleteMutation.mutateAsync(note.id);
      toast.success("Research note deleted.");
      setConfirmingDelete(false);
    } catch {
      toast.error("Failed to delete research note.");
    }
  }

  return (
    <>
      <TableRow>
        <TableCell
          className="cursor-pointer font-medium"
          onClick={onToggleExpand}
        >
          {note.title}
        </TableCell>
        <TableCell>
          <div className="flex flex-wrap gap-1">
            {note.tags.map((tag) => (
              <Badge key={tag} variant="outline" className="text-xs">
                {tag}
              </Badge>
            ))}
            {note.tags.length === 0 && (
              <span className="text-muted-foreground text-xs">{"—"}</span>
            )}
          </div>
        </TableCell>
        <TableCell className="text-sm">
          {note.source_url ? (
            <a
              href={note.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 hover:underline dark:text-blue-400"
            >
              Link
            </a>
          ) : (
            <span className="text-muted-foreground text-xs">{"—"}</span>
          )}
        </TableCell>
        <TableCell className="text-muted-foreground text-sm">
          <Time value={note.updated_at} mode="absolute" precision="day" />
        </TableCell>
        <TableCell>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => onEdit(note)}
              aria-label={`Edit ${note.title}`}
            >
              Edit
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="text-destructive hover:text-destructive"
              onClick={() => setConfirmingDelete(true)}
              aria-label={`Delete ${note.title}`}
            >
              Delete
            </Button>
          </div>
        </TableCell>
      </TableRow>
      {expanded && (
        <TableRow>
          <TableCell colSpan={5}>
            <div className="prose dark:prose-invert max-w-none py-3 text-sm whitespace-pre-wrap">
              {note.content}
            </div>
          </TableCell>
        </TableRow>
      )}

      <AlertDialog open={confirmingDelete} onOpenChange={setConfirmingDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {note.title}?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes {note.title} from your research list. The record is
              retained for history but will no longer appear here.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                void handleDelete();
              }}
              disabled={deleteMutation.isPending}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

// ---------------------------------------------------------------------------
// ResearchTracker
// ---------------------------------------------------------------------------

export default function ResearchTracker() {
  const [search, setSearch] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  // `null` = closed; `undefined` = add mode; a HealthResearch = edit mode.
  const [formTarget, setFormTarget] = useState<HealthResearch | null | undefined>(null);

  const params: ResearchParams = {
    q: search || undefined,
    tag: tagFilter || undefined,
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };

  const { data, isLoading } = useResearch(params);

  const notes = data?.data ?? [];
  const total = data?.meta?.total ?? 0;
  const hasMore = data?.meta?.has_more ?? false;

  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = Math.min((page + 1) * PAGE_SIZE, total);

  // Collect unique tags for quick filter.
  const allTags = Array.from(new Set(notes.flatMap((n) => n.tags)));

  const dialogOpen = formTarget !== null;
  const editing = formTarget != null;

  function handleSearchChange(value: string) {
    setSearch(value);
    setPage(0);
  }

  return (
    <div className="space-y-4">
      {/* Toolbar: filters + add affordance */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <Input
            placeholder="Search research..."
            value={search}
            onChange={(e) => handleSearchChange(e.target.value)}
            className="w-64"
          />
          {allTags.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge
                variant={tagFilter === "" ? "default" : "outline"}
                className="cursor-pointer"
                onClick={() => {
                  setTagFilter("");
                  setPage(0);
                }}
              >
                All tags
              </Badge>
              {allTags.map((tag) => (
                <Badge
                  key={tag}
                  variant={tagFilter === tag ? "default" : "outline"}
                  className="cursor-pointer"
                  onClick={() => {
                    setTagFilter(tagFilter === tag ? "" : tag);
                    setPage(0);
                  }}
                >
                  {tag}
                </Badge>
              ))}
            </div>
          )}
          {(search || tagFilter) && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setSearch("");
                setTagFilter("");
                setPage(0);
              }}
            >
              Clear
            </Button>
          )}
        </div>
        <Button size="sm" onClick={() => setFormTarget(undefined)}>
          Add research
        </Button>
      </div>

      {!isLoading && notes.length === 0 ? (
        <EmptyState />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Title</TableHead>
              <TableHead>Tags</TableHead>
              <TableHead>Source</TableHead>
              <TableHead>Updated</TableHead>
              <TableHead>Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <SkeletonRows />
            ) : (
              notes.map((note) => (
                <ResearchRow
                  key={note.id}
                  note={note}
                  expanded={expandedId === note.id}
                  onToggleExpand={() =>
                    setExpandedId(expandedId === note.id ? null : note.id)
                  }
                  onEdit={setFormTarget}
                />
              ))
            )}
          </TableBody>
        </Table>
      )}

      {/* Pagination */}
      {total > 0 && (
        <div className="flex items-center justify-between">
          <p className="text-muted-foreground text-sm">
            Showing {rangeStart}–{rangeEnd} of {total.toLocaleString()}
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!hasMore}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </div>
        </div>
      )}

      {/* Add / edit dialog */}
      <Dialog open={dialogOpen} onOpenChange={(open) => !open && setFormTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? "Edit research" : "Add research"}</DialogTitle>
            <DialogDescription>
              {editing
                ? "Update this research note's details."
                : "Add a research note to your record. It appears immediately."}
            </DialogDescription>
          </DialogHeader>
          <ResearchForm
            research={editing ? formTarget : undefined}
            onDone={() => setFormTarget(null)}
            onCancel={() => setFormTarget(null)}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}
