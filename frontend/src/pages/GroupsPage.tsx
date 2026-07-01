import { useState } from "react";
import { PlusIcon, XIcon } from "lucide-react";
import { Time } from "@/components/ui/time";

import type { GroupParams } from "@/api/types";
import type { Label } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
import { categoryHueVar } from "@/components/ui/ButlerMark";
import { Page } from "@/components/ui/page";
import {
  useGroups,
  useLabels,
  useCreateLabel,
  useAssignGroupLabel,
  useRemoveGroupLabel,
} from "@/hooks/use-contacts";
import { EmptyState as EmptyStateUI } from "@/components/ui/empty-state";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 50;
const BADGE_TEXT = "#fff";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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
            <label htmlFor="label-name" className="text-sm font-medium">
              Name
            </label>
            <Input
              id="label-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. VIP"
              required
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="label-color" className="text-sm font-medium">
              Color <span className="text-muted-foreground">(optional hex)</span>
            </label>
            <Input
              id="label-color"
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
// AssignLabelDialog
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
        >
          <PlusIcon className="h-3 w-3" />
        </button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Assign label to group</DialogTitle>
          <DialogDescription className="sr-only">
            Select a label to assign to this group.
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

// ---------------------------------------------------------------------------
// GroupLabelCell
// ---------------------------------------------------------------------------

function GroupLabelCell({
  groupId,
  labels,
}: {
  groupId: string;
  labels: Label[];
}) {
  const remove = useRemoveGroupLabel();
  const assignedIds = new Set(labels.map((l) => l.id));

  return (
    <div className="flex flex-wrap items-center gap-1.5">
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
// Sub-components
// ---------------------------------------------------------------------------

function SkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <TableRow key={i}>
          <TableCell><Skeleton className="h-4 w-32" /></TableCell>
          <TableCell><Skeleton className="h-4 w-48" /></TableCell>
          <TableCell><Skeleton className="h-4 w-16" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
          <TableCell><Skeleton className="h-4 w-36" /></TableCell>
        </TableRow>
      ))}
    </>
  );
}

function EmptyState() {
  return (
    <EmptyStateUI
      title="No groups found."
      description="Groups appear as you organize contacts into categories."
    />
  );
}

// ---------------------------------------------------------------------------
// GroupsPage
// ---------------------------------------------------------------------------

export default function GroupsPage() {
  const [page, setPage] = useState(0);

  const params: GroupParams = {
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };

  const { data, isLoading } = useGroups(params);

  const groups = data?.groups ?? [];
  const total = data?.total ?? 0;
  const hasMore = groups.length === PAGE_SIZE && (page + 1) * PAGE_SIZE < total;

  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = Math.min((page + 1) * PAGE_SIZE, total);

  return (
    <Page
      archetype="list"
      title="Groups"
      description="Organize contacts into groups."
      actions={<CreateLabelDialog />}
    >
      {/* Groups table */}
      <Card>
        <CardHeader>
          <CardTitle>All Groups</CardTitle>
          <CardDescription>
            {total > 0 ? `${total.toLocaleString()} group${total !== 1 ? "s" : ""}` : ""}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!isLoading && groups.length === 0 ? (
            <EmptyState />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead>Members</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead>Labels</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading ? (
                  <SkeletonRows />
                ) : (
                  groups.map((group) => (
                    <TableRow key={group.id}>
                      <TableCell className="font-medium">{group.name}</TableCell>
                      <TableCell className="text-muted-foreground text-sm max-w-xs truncate">
                        {group.description ?? "—"}
                      </TableCell>
                      <TableCell className="tabular-nums text-sm">
                        {group.member_count}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        <Time value={group.created_at} mode="absolute" precision="day" />
                      </TableCell>
                      <TableCell>
                        <GroupLabelCell groupId={group.id} labels={group.labels} />
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

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
    </Page>
  );
}
