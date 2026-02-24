/**
 * PendingIdentitiesSection
 *
 * Displays temp contacts that require owner attention to resolve identity:
 * - Merge into an existing contact
 * - Confirm as a new known contact
 * - Archive (not yet implemented in backend, handled via patch)
 *
 * Shown above the contacts table on ContactsPage.
 */

import { useState } from "react";
import { format } from "date-fns";
import { toast } from "sonner";

import type { ContactDetail, ContactSummary } from "@/api/types";
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
  useConfirmContact,
  useContacts,
  useMergeContact,
  usePendingContacts,
} from "@/hooks/use-contacts";

// ---------------------------------------------------------------------------
// MergeDialog
// ---------------------------------------------------------------------------

interface MergeDialogProps {
  pendingContact: ContactDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function MergeDialog({ pendingContact, open, onOpenChange }: MergeDialogProps) {
  const [search, setSearch] = useState("");
  const [selectedContact, setSelectedContact] = useState<ContactSummary | null>(null);
  const mergeMutation = useMergeContact();

  // Fetch contacts matching search for selection
  const { data: searchResults, isLoading: isSearching } = useContacts(
    search.length >= 2 ? { q: search, limit: 10 } : { limit: 10 },
  );

  const candidates = (searchResults?.contacts ?? []).filter(
    (c) => c.id !== pendingContact.id,
  );

  function handleConfirmMerge() {
    if (!selectedContact) return;
    // The pending contact is the source; selected is the target
    // We merge INTO the selected contact (target), so call merge on selectedContact
    mergeMutation.mutate(
      {
        contactId: selectedContact.id,
        request: { source_contact_id: pendingContact.id },
      },
      {
        onSuccess: () => {
          onOpenChange(false);
          setSearch("");
          setSelectedContact(null);
        },
      },
    );
  }

  function handleClose() {
    onOpenChange(false);
    setSearch("");
    setSelectedContact(null);
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Merge Contact</DialogTitle>
          <DialogDescription>
            Merge <strong>{pendingContact.full_name}</strong> into an existing contact.
            The pending contact will be removed and its data moved to the target.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <label className="text-sm font-medium">Search for target contact</label>
            <Input
              className="mt-1"
              placeholder="Type a name or email..."
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setSelectedContact(null);
              }}
            />
          </div>

          {isSearching && <Skeleton className="h-20 w-full" />}

          {!isSearching && candidates.length > 0 && (
            <div className="border rounded-md divide-y max-h-48 overflow-y-auto">
              {candidates.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  className={`w-full text-left px-3 py-2 text-sm hover:bg-muted transition-colors ${
                    selectedContact?.id === c.id ? "bg-muted font-medium" : ""
                  }`}
                  onClick={() => setSelectedContact(c)}
                >
                  <span className="font-medium">{c.full_name}</span>
                  {c.email && (
                    <span className="text-muted-foreground ml-2">{c.email}</span>
                  )}
                </button>
              ))}
            </div>
          )}

          {!isSearching && search.length >= 2 && candidates.length === 0 && (
            <p className="text-sm text-muted-foreground">No contacts found.</p>
          )}

          {selectedContact && (
            <div className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm dark:border-blue-800 dark:bg-blue-950">
              <span className="font-medium">Selected: </span>
              {selectedContact.full_name}
              {selectedContact.email && (
                <span className="text-muted-foreground ml-1">({selectedContact.email})</span>
              )}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={handleClose}>
            Cancel
          </Button>
          <Button
            onClick={handleConfirmMerge}
            disabled={!selectedContact || mergeMutation.isPending}
          >
            {mergeMutation.isPending ? "Merging..." : "Merge"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// PendingIdentitiesSection
// ---------------------------------------------------------------------------

export function PendingIdentitiesSection() {
  const { data: pending, isLoading } = usePendingContacts();
  const confirmMutation = useConfirmContact();
  const [mergeTarget, setMergeTarget] = useState<ContactDetail | null>(null);

  const pendingContacts = pending ?? [];

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Pending Identities</CardTitle>
          <CardDescription>Contacts awaiting identity resolution</CardDescription>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-16 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (pendingContacts.length === 0) return null;

  function handleConfirm(contactId: string) {
    confirmMutation.mutate(contactId);
  }

  function handleArchive(contact: ContactDetail) {
    // Archive: patch contact to set archived (not yet a full backend action)
    // For now, show a toast to indicate this is not yet supported
    toast.info(`Archive not yet implemented for ${contact.full_name}`);
  }

  return (
    <>
      <Card className="border-amber-200 dark:border-amber-800">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            Pending Identities
            <Badge variant="outline" className="border-amber-500 text-amber-600">
              {pendingContacts.length}
            </Badge>
          </CardTitle>
          <CardDescription>
            New contacts detected that need your review. Merge into an existing contact or confirm
            as new.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Source</TableHead>
                <TableHead>Detected</TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {pendingContacts.map((contact) => {
                const source =
                  typeof contact.metadata?.source === "string"
                    ? contact.metadata.source
                    : null;
                return (
                  <TableRow key={contact.id}>
                    <TableCell className="font-medium">
                      {contact.full_name}
                      {contact.email && (
                        <div className="text-xs text-muted-foreground">{contact.email}</div>
                      )}
                    </TableCell>
                    <TableCell>
                      {source ? (
                        <Badge variant="outline" className="text-xs">
                          {source}
                        </Badge>
                      ) : (
                        <span className="text-muted-foreground text-sm">&mdash;</span>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {format(new Date(contact.created_at), "MMM d, yyyy")}
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => setMergeTarget(contact)}
                        >
                          Merge
                        </Button>
                        <Button
                          size="sm"
                          variant="default"
                          disabled={confirmMutation.isPending}
                          onClick={() => handleConfirm(contact.id)}
                        >
                          Confirm as New
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => handleArchive(contact)}
                        >
                          Archive
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {mergeTarget && (
        <MergeDialog
          pendingContact={mergeTarget}
          open={mergeTarget !== null}
          onOpenChange={(open) => {
            if (!open) setMergeTarget(null);
          }}
        />
      )}
    </>
  );
}
