/**
 * CirclesPage — /entities/circles, the "Circles" lens on contact groups.
 *
 * Retires the standalone /groups vestige (JARVIS audit move 14) into a
 * first-class lens on the relationship surface where contacts already live.
 * A personal-scale, client-filtered list (no pagination) sorted alphabetically;
 * each row expands into a fresh single-group detail wired to the
 * previously-unused `getGroup` endpoint (client.ts:getGroup).
 *
 * Honest scope note: `GET /groups/{id}` currently returns the same fields as
 * the list row (name/description/member_count/labels/created/updated) — it
 * does NOT return a member roster, so this lens cannot deep-link individual
 * members to /entities/:entityId yet. That needs a backend change (a real
 * members array or a /groups/{id}/members endpoint) — tracked as a follow-up
 * rather than fabricated here.
 *
 * Groups themselves are read-only by spec (created via the relationship
 * butler's group_create/group_add_member tools); the only write affordances
 * on this page are label management.
 *
 * Audit: docs/redesigns/2026-07-03-jarvis-audit.md move 14; per-page dossier
 * docs/redesigns/2026-07-03-jarvis-audit-data.json (page: "groups").
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDownIcon, ChevronRightIcon, PlusIcon, XIcon } from "lucide-react";

import type { Group, Label } from "@/api/types";
import { getGroup } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Page } from "@/components/ui/page";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { categoryHueVar } from "@/components/ui/ButlerMark";
import { SubpageTabs } from "@/components/relationship/SubpageTabs";
import {
  useAssignGroupLabel,
  useCreateLabel,
  useGroups,
  useLabels,
  useRemoveGroupLabel,
} from "@/hooks/use-contacts";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

// Personal-scale dataset (a household's contact groups) — fetch the whole
// thing in one page and filter/sort client-side rather than paginating.
const FETCH_LIMIT = 500;
const BADGE_TEXT = "#fff";

function labelBg(label: Label): string {
  return label.color ?? categoryHueVar(label.name);
}

// ---------------------------------------------------------------------------
// CreateLabelDialog
// ---------------------------------------------------------------------------

function CreateLabelDialog() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [color, setColor] = useState("");
  const create = useCreateLabel();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    let formattedColor = color.trim();
    if (formattedColor && !formattedColor.startsWith("#")) {
      formattedColor = `#${formattedColor}`;
    }
    create.mutate(
      { name: name.trim(), color: formattedColor || null },
      {
        onSuccess: () => {
          setName("");
          setColor("");
          setOpen(false);
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <PlusIcon className="mr-1 h-4 w-4" />
          New label
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create label</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4 py-2">
          <div className="space-y-1">
            <label htmlFor="circle-label-name" className="text-sm font-medium">
              Name
            </label>
            <Input
              id="circle-label-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. VIP"
              required
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="circle-label-color" className="text-sm font-medium">
              Color <span className="text-muted-foreground">(optional hex)</span>
            </label>
            <Input
              id="circle-label-color"
              value={color}
              onChange={(e) => setColor(e.target.value)}
              placeholder="#e63946"
              maxLength={7}
            />
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="ghost">
                Cancel
              </Button>
            </DialogClose>
            <Button type="submit" disabled={!name.trim() || create.isPending}>
              {create.isPending ? "Creating…" : "Create"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// AssignLabelDialog / GroupLabelCell
// ---------------------------------------------------------------------------

function AssignLabelDialog({
  groupId,
  assignedIds,
}: {
  groupId: string;
  assignedIds: Set<string>;
}) {
  const [open, setOpen] = useState(false);
  const { data: allLabels = [], isPending } = useLabels();
  const assign = useAssignGroupLabel();

  const available = allLabels.filter((l) => !assignedIds.has(l.id));

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <button
          type="button"
          className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-dashed text-muted-foreground hover:border-foreground hover:text-foreground transition-colors"
          aria-label="Assign label"
          onClick={(e) => e.stopPropagation()}
        >
          <PlusIcon className="h-3 w-3" />
        </button>
      </DialogTrigger>
      <DialogContent onClick={(e) => e.stopPropagation()}>
        <DialogHeader>
          <DialogTitle>Assign label to circle</DialogTitle>
          <DialogDescription className="sr-only">
            Select a label to assign to this circle.
          </DialogDescription>
        </DialogHeader>
        {isPending ? (
          <p className="text-sm text-muted-foreground py-4">Loading labels…</p>
        ) : available.length === 0 ? (
          <p className="text-sm text-muted-foreground py-4">
            All labels are already assigned, or no labels exist yet.
          </p>
        ) : (
          <div className="flex flex-wrap gap-2 py-2">
            {available.map((label) => (
              <Badge
                key={label.id}
                className="cursor-pointer"
                style={{ backgroundColor: labelBg(label), color: BADGE_TEXT }}
                onClick={() => {
                  assign.mutate({ groupId, labelId: label.id });
                  setOpen(false);
                }}
              >
                {label.name}
              </Badge>
            ))}
          </div>
        )}
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">Close</Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function GroupLabelCell({ groupId, labels }: { groupId: string; labels: Label[] }) {
  const remove = useRemoveGroupLabel();
  const assignedIds = new Set(labels.map((l) => l.id));

  return (
    <div className="flex flex-wrap items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
      {labels.map((label) => (
        <Badge
          key={label.id}
          className="pr-1 gap-1"
          style={{ backgroundColor: labelBg(label), color: BADGE_TEXT }}
        >
          <span>{label.name}</span>
          <button
            type="button"
            aria-label={`Remove label ${label.name}`}
            className="rounded-full hover:bg-black/20 transition-colors p-0.5"
            onClick={() => remove.mutate({ groupId, labelId: label.id })}
          >
            <XIcon className="h-2.5 w-2.5" />
          </button>
        </Badge>
      ))}
      <AssignLabelDialog groupId={groupId} assignedIds={assignedIds} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// CircleDetail — expanded row body, backed by the previously-unused getGroup
// endpoint (a fresh single-record fetch rather than trusting the list cache).
// ---------------------------------------------------------------------------

function CircleDetail({ groupId }: { groupId: string }) {
  const { data, isLoading, isError, refetch } = useQuery<Group>({
    queryKey: ["group", groupId],
    queryFn: () => getGroup(groupId),
  });

  if (isLoading) {
    return <Skeleton className="h-16 w-full" />;
  }

  if (isError || !data) {
    return (
      <div className="flex items-center gap-3 py-2">
        <p className="text-sm text-destructive">Couldn't reach the relationship butler.</p>
        <Button variant="outline" size="sm" onClick={() => void refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  return (
    <div className="grid gap-1.5 py-2 text-sm">
      <p className="text-muted-foreground">
        {data.description ?? "No description."}
      </p>
      <p className="text-muted-foreground">
        {data.member_count} member{data.member_count === 1 ? "" : "s"} · created{" "}
        <Time value={data.created_at} mode="absolute" precision="day" /> · updated{" "}
        <Time value={data.updated_at} mode="absolute" precision="day" />
      </p>
      {/*
        Honest gap: the relationship API does not yet return a member roster
        from this endpoint (or any other), so we cannot deep-link individual
        members to /entities/:entityId here — only the count is real.
      */}
      <p className="text-xs italic text-muted-foreground">
        Member roster isn't available from the API yet — showing the count only.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CircleRow
// ---------------------------------------------------------------------------

function CircleRow({ group, expanded, onToggle }: { group: Group; expanded: boolean; onToggle: () => void }) {
  return (
    <div className="border-b border-border last:border-b-0">
      <div
        role="button"
        tabIndex={0}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        }}
        aria-expanded={expanded}
        className="flex w-full items-center gap-3 py-3 text-left hover:bg-accent/40 transition-colors cursor-pointer"
      >
        {expanded ? (
          <ChevronDownIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRightIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
        )}
        <span className="min-w-0 flex-1 font-medium truncate">{group.name}</span>
        <span className="shrink-0 tabular-nums text-sm text-muted-foreground">
          {group.member_count} member{group.member_count === 1 ? "" : "s"}
        </span>
        <span className="shrink-0">
          <GroupLabelCell groupId={group.id} labels={group.labels} />
        </span>
      </div>
      {expanded && (
        <div className="pl-7 pb-2">
          <CircleDetail groupId={group.id} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// CirclesPage
// ---------------------------------------------------------------------------

export default function CirclesPage() {
  const [search, setSearch] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data, isLoading, isError, error, refetch } = useGroups({ offset: 0, limit: FETCH_LIMIT });

  const groups = useMemo(() => {
    const all = data?.groups ?? [];
    const q = search.trim().toLowerCase();
    const filtered = q ? all.filter((g) => g.name.toLowerCase().includes(q)) : all;
    return [...filtered].sort((a, b) => a.name.localeCompare(b.name));
  }, [data?.groups, search]);

  const total = data?.total ?? 0;

  return (
    <Page
      archetype="overview"
      title="Circles"
      description="Contact groups maintained by the relationship butler; manage their labels here."
      breadcrumbs={[{ label: "Entities", href: "/entities" }, { label: "Circles" }]}
      actions={<CreateLabelDialog />}
      error={isError ? error : null}
      onRetry={() => void refetch()}
    >
      {/* SubpageTabs — Circles is active */}
      <SubpageTabs />

      {!isError && (
        <div className="mt-4 mb-3">
          <Input
            type="search"
            aria-label="Search circles"
            placeholder="Search circles…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="max-w-sm"
          />
        </div>
      )}

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : isError ? null : total === 0 ? (
        <EmptyState
          title="No circles yet."
          description='Ask the relationship butler to create one (e.g. "group my family") — circles appear here as the butler organizes contacts into groups.'
        />
      ) : groups.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted-foreground">
          No circles match "{search}".
        </p>
      ) : (
        <div className="rounded-md border border-border">
          {groups.map((group) => (
            <CircleRow
              key={group.id}
              group={group}
              expanded={expandedId === group.id}
              onToggle={() => setExpandedId((cur) => (cur === group.id ? null : group.id))}
            />
          ))}
        </div>
      )}
    </Page>
  );
}
