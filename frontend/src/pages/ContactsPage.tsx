import { useState } from "react";
import { toast } from "sonner";

import { triggerContactsSync } from "@/api/index.ts";
import type { ContactParams } from "@/api/types";
import ContactTable from "@/components/relationship/ContactTable";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useContacts, useLabels } from "@/hooks/use-contacts";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 50;

// ---------------------------------------------------------------------------
// ContactsPage
// ---------------------------------------------------------------------------

export default function ContactsPage() {
  const [search, setSearch] = useState("");
  const [activeLabel, setActiveLabel] = useState("");
  const [page, setPage] = useState(0);
  const [isSyncing, setIsSyncing] = useState(false);

  const params: ContactParams = {
    q: search || undefined,
    label: activeLabel || undefined,
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };

  const { data, isLoading, refetch } = useContacts(params);
  const { data: labels } = useLabels();

  const contacts = data?.contacts ?? [];
  const total = data?.total ?? 0;
  const hasMore = contacts.length === PAGE_SIZE && (page + 1) * PAGE_SIZE < total;

  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = Math.min((page + 1) * PAGE_SIZE, total);

  function handleSearchChange(value: string) {
    setSearch(value);
    setPage(0);
  }

  function handleLabelFilter(label: string) {
    setActiveLabel(label);
    setPage(0);
  }

  async function handleSyncFromGoogle() {
    if (isSyncing) return;
    setIsSyncing(true);
    try {
      const result = await triggerContactsSync("incremental");
      await refetch();
      const stats = [
        result.created != null ? `${result.created} created` : null,
        result.updated != null ? `${result.updated} updated` : null,
        result.skipped != null ? `${result.skipped} skipped` : null,
        result.errors != null ? `${result.errors} errors` : null,
      ]
        .filter(Boolean)
        .join(", ");

      toast.success(
        result.message ?? (stats ? `Google sync complete: ${stats}` : "Google sync complete"),
      );
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Unknown error";
      toast.error(`Google sync failed: ${message}`);
    } finally {
      setIsSyncing(false);
    }
  }

  return (
    <div className="space-y-6">
      {/* Page heading */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Contacts</h1>
          <p className="text-muted-foreground mt-1">
            Manage your personal and professional contacts.
          </p>
        </div>
        <Button
          onClick={handleSyncFromGoogle}
          disabled={isSyncing}
          aria-label="Sync From Google"
        >
          {isSyncing ? "Syncing..." : "Sync From Google"}
        </Button>
      </div>

      {/* Contact table */}
      <Card>
        <CardHeader>
          <CardTitle>All Contacts</CardTitle>
          <CardDescription>
            {total > 0 ? `${total.toLocaleString()} contact${total !== 1 ? "s" : ""}` : ""}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ContactTable
            contacts={contacts}
            isLoading={isLoading}
            search={search}
            onSearchChange={handleSearchChange}
            allLabels={labels ?? []}
            activeLabel={activeLabel}
            onLabelFilter={handleLabelFilter}
          />
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
    </div>
  );
}
