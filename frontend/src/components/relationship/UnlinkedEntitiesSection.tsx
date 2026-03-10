/**
 * UnlinkedEntitiesSection
 *
 * Displays contacts that have no entity_id linked to the memory entity graph.
 * Shows ranked entity suggestions and allows linking or creating new entities.
 *
 * Shown above the contacts table on ContactsPage.
 */

import { useState } from "react";

import { useDebounce } from "@/hooks/use-debounce";

import type { EntityLinkSuggestion, UnlinkedContactSummary } from "@/api/types";
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
  useCreateAndLinkEntity,
  useEntitySuggestions,
  useLinkEntity,
  useUnlinkedContacts,
} from "@/hooks/use-contacts";

const PAGE_SIZE = 20;

// ---------------------------------------------------------------------------
// EntitySearchDialog
// ---------------------------------------------------------------------------

interface EntitySearchDialogProps {
  contact: UnlinkedContactSummary;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function EntitySearchDialog({ contact, open, onOpenChange }: EntitySearchDialogProps) {
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<EntityLinkSuggestion | null>(null);
  const linkMutation = useLinkEntity();

  // Use search override when user types, otherwise show pre-computed suggestions
  const { data: searchResults, isLoading: isSearching } = useEntitySuggestions(
    search.length >= 2 ? contact.id : undefined,
    search.length >= 2 ? search : undefined,
  );

  const suggestions =
    search.length >= 2
      ? searchResults ?? []
      : contact.suggestions;

  function handleConfirmLink() {
    if (!selected) return;
    linkMutation.mutate(
      { contactId: contact.id, request: { entity_id: selected.entity_id } },
      {
        onSuccess: () => {
          onOpenChange(false);
          setSearch("");
          setSelected(null);
        },
      },
    );
  }

  function handleClose() {
    onOpenChange(false);
    setSearch("");
    setSelected(null);
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Link Entity</DialogTitle>
          <DialogDescription>
            Link <strong>{contact.full_name}</strong> to an existing memory entity.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <label className="text-sm font-medium">Search entities</label>
            <Input
              className="mt-1"
              placeholder="Type a name to search..."
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setSelected(null);
              }}
            />
          </div>

          {isSearching && <Skeleton className="h-20 w-full" />}

          {!isSearching && suggestions.length > 0 && (
            <div className="border rounded-md divide-y max-h-48 overflow-y-auto">
              {suggestions.map((s) => (
                <button
                  key={s.entity_id}
                  type="button"
                  className={`w-full text-left px-3 py-2 text-sm hover:bg-muted transition-colors ${
                    selected?.entity_id === s.entity_id ? "bg-muted font-medium" : ""
                  }`}
                  onClick={() => setSelected(s)}
                >
                  <span className="font-medium">{s.canonical_name}</span>
                  <span className="text-muted-foreground ml-2 text-xs">
                    {s.entity_type} &middot; score {Math.round(s.score)}
                  </span>
                  {s.aliases.length > 0 && (
                    <div className="text-xs text-muted-foreground">
                      aka {s.aliases.slice(0, 3).join(", ")}
                    </div>
                  )}
                </button>
              ))}
            </div>
          )}

          {!isSearching && suggestions.length === 0 && (
            <p className="text-sm text-muted-foreground">No matching entities found.</p>
          )}

          {selected && (
            <div className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm dark:border-blue-800 dark:bg-blue-950">
              <span className="font-medium">Selected: </span>
              {selected.canonical_name}
              <span className="text-muted-foreground ml-1">
                ({selected.entity_type})
              </span>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={handleClose}>
            Cancel
          </Button>
          <Button
            onClick={handleConfirmLink}
            disabled={!selected || linkMutation.isPending}
          >
            {linkMutation.isPending ? "Linking..." : "Link"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// UnlinkedEntitiesSection
// ---------------------------------------------------------------------------

export function UnlinkedEntitiesSection() {
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search, 300);
  const { data, isLoading } = useUnlinkedContacts({
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
    q: debouncedSearch || undefined,
  });
  const createMutation = useCreateAndLinkEntity();
  const [linkTarget, setLinkTarget] = useState<UnlinkedContactSummary | null>(null);

  const contacts = data?.contacts ?? [];
  const total = data?.total ?? 0;
  const hasMore = contacts.length === PAGE_SIZE && (page + 1) * PAGE_SIZE < total;

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Contacts Needing Entity Link</CardTitle>
          <CardDescription>Contacts not yet linked to the memory entity graph</CardDescription>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-16 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (total === 0 && !search) return null;

  function handleCreateNew(contact: UnlinkedContactSummary) {
    createMutation.mutate({
      contactId: contact.id,
      request: {},
    });
  }

  return (
    <>
      <Card className="border-blue-200 dark:border-blue-800">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            Contacts Needing Entity Link
            <Badge variant="outline" className="border-blue-500 text-blue-600">
              {total}
            </Badge>
          </CardTitle>
          <CardDescription>
            These contacts are not linked to a memory entity. Link to an existing entity or create
            a new one.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="mb-4">
            <Input
              placeholder="Filter by name..."
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(0);
              }}
              className="max-w-sm"
            />
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Best Match</TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {contacts.length === 0 && (
                <TableRow>
                  <TableCell colSpan={3} className="text-center text-muted-foreground py-6">
                    No contacts match &ldquo;{search}&rdquo;
                  </TableCell>
                </TableRow>
              )}
              {contacts.map((contact) => {
                const bestMatch = contact.suggestions[0] ?? null;
                return (
                  <TableRow key={contact.id}>
                    <TableCell className="font-medium">
                      {contact.full_name}
                    </TableCell>
                    <TableCell>
                      {bestMatch ? (
                        <div className="text-sm">
                          <span className="font-medium">{bestMatch.canonical_name}</span>
                          <span className="text-muted-foreground ml-1">
                            ({Math.round(bestMatch.score)})
                          </span>
                        </div>
                      ) : (
                        <span className="text-muted-foreground text-sm">&mdash;</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => setLinkTarget(contact)}
                        >
                          Link
                        </Button>
                        <Button
                          size="sm"
                          variant="default"
                          disabled={createMutation.isPending}
                          onClick={() => handleCreateNew(contact)}
                        >
                          Create New
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>

          {/* Pagination */}
          {total > PAGE_SIZE && (
            <div className="flex items-center justify-between mt-4">
              <p className="text-muted-foreground text-sm">
                Showing {page * PAGE_SIZE + 1}&ndash;
                {Math.min((page + 1) * PAGE_SIZE, total)} of {total}
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
        </CardContent>
      </Card>

      {linkTarget && (
        <EntitySearchDialog
          contact={linkTarget}
          open={linkTarget !== null}
          onOpenChange={(open) => {
            if (!open) setLinkTarget(null);
          }}
        />
      )}
    </>
  );
}
