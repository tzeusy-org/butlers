// ---------------------------------------------------------------------------
// ResearchTracker — direct add/edit/delete for health research notes [bu-wamzk]
//
// Dispatch reframe [bu-w7b18.4]: research notes render as a Dispatch rule-list
// (time · topic + source-tag · excerpt · expand-arrow), not a Card-wrapped data
// table. The topic line is a button: clicking it expands the full note in place
// with a rotating chevron. Source URLs open in a new tab (noopener noreferrer).
// Per-row Edit / Delete actions, the delete confirmation dialog, and the search
// + tag filters are preserved. Research notes are PROPERTY facts (like
// conditions, NOT temporal): an edit supersedes the prior note keyed on its
// `research:{title}` subject. All writes go through the /api/health/research
// fact-store path, so dashboard edits and butler edits stay in sync.
// ---------------------------------------------------------------------------

import { ChevronRight } from "lucide-react";
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
import { Input } from "@/components/ui/input";
import { Time } from "@/components/ui/time";
import { useDeleteResearch, useResearch } from "@/hooks/use-health";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 50;

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <div className="divide-y divide-border/60 border-y border-border/60">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="grid grid-cols-[1fr_auto] items-start gap-3 py-3">
          <div className="space-y-1.5">
            <div className="bg-muted h-3.5 w-48 animate-pulse rounded" />
            <div className="bg-muted h-2.5 w-32 animate-pulse rounded" />
            <div className="bg-muted h-3 w-full max-w-md animate-pulse rounded" />
          </div>
          <div className="bg-muted h-7 w-24 animate-pulse rounded" />
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ResearchRow — a single note rule-list row with in-place expand + edit/delete
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
    <div className="py-3">
      <div className="grid grid-cols-[1fr_auto] items-start gap-3">
        {/* Topic + source-tag + excerpt — click to expand the full note. */}
        <button
          type="button"
          onClick={onToggleExpand}
          aria-expanded={expanded}
          className="group focus-visible:ring-ring w-full min-w-0 text-left focus-visible:outline-none focus-visible:ring-1"
        >
          <span className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <ChevronRight
              className={cn(
                "text-muted-foreground group-hover:text-foreground duration-fast mt-0.5 h-3.5 w-3.5 shrink-0 self-center transition-transform",
                expanded && "rotate-90",
              )}
              aria-hidden="true"
            />
            <span className="text-foreground text-sm font-medium">{note.title}</span>
            {note.tags.map((tag) => (
              <span
                key={tag}
                className="text-muted-foreground font-mono text-[10px] uppercase tracking-[0.1em]"
              >
                {tag}
              </span>
            ))}
          </span>
          <span className="text-muted-foreground mt-0.5 block pl-[22px] font-mono text-[10px] tabular-nums">
            updated <Time value={note.updated_at} mode="absolute" precision="day" />
          </span>
          {!expanded && (
            <span className="text-muted-foreground mt-0.5 block truncate pl-[22px] text-[13px]">
              {note.content}
            </span>
          )}
        </button>

        <div className="flex shrink-0 items-center gap-2">
          {note.source_url && (
            <a
              href={note.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-muted-foreground hover:text-foreground duration-fast font-mono text-[10px] uppercase tracking-[0.1em] transition-colors"
            >
              Source ↗
            </a>
          )}
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
      </div>

      {expanded && (
        <div className="text-foreground mt-2 pl-[22px] text-sm leading-relaxed whitespace-pre-wrap">
          {note.content}
          {note.source_url && (
            <div className="mt-3">
              <a
                href={note.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-muted-foreground hover:text-foreground duration-fast font-mono text-[10px] uppercase tracking-[0.1em] transition-colors"
              >
                Open source ↗
              </a>
            </div>
          )}
        </div>
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
    </div>
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

      {isLoading ? (
        <SkeletonRows />
      ) : notes.length === 0 ? (
        <p className="text-muted-foreground font-serif text-[15px] italic">
          Nothing saved yet. Add a research note above, or tell your Health butler.
        </p>
      ) : (
        <div className="divide-y divide-border/60 border-y border-border/60">
          {notes.map((note) => (
            <ResearchRow
              key={note.id}
              note={note}
              expanded={expandedId === note.id}
              onToggleExpand={() => setExpandedId(expandedId === note.id ? null : note.id)}
              onEdit={setFormTarget}
            />
          ))}
        </div>
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
