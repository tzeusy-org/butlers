import { Link } from "react-router";
import { format, formatDistanceToNow } from "date-fns";

import type {
  EntityGift,
  EntityInteraction,
  EntityLoan,
  EntityNote,
  EntityTimelineItem,
  LinkedContactSummary,
  RelationshipEntityDetail,
} from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  useEntityGifts,
  useEntityInteractions,
  useEntityLinkedContacts,
  useEntityLoans,
  useEntityNotes,
  useEntityTimeline,
} from "@/hooks/use-entities";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface EntityDetailViewProps {
  entity: RelationshipEntityDetail;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return format(new Date(iso), "MMM d, yyyy");
}

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  return formatDistanceToNow(new Date(iso), { addSuffix: true });
}

function roleBadgeStyle(role: string): React.CSSProperties {
  switch (role.toLowerCase()) {
    case "owner":
      return { backgroundColor: "#7c3aed", color: "#fff" };
    case "admin":
      return { backgroundColor: "#b45309", color: "#fff" };
    default:
      return { backgroundColor: "#0369a1", color: "#fff" };
  }
}

// ---------------------------------------------------------------------------
// Loading skeleton for tab content
// ---------------------------------------------------------------------------

function TabSkeleton() {
  return (
    <div className="space-y-3 py-4">
      {Array.from({ length: 3 }, (_, i) => (
        <Skeleton key={i} className="h-12 w-full" />
      ))}
    </div>
  );
}

function EmptyTab({ message }: { message: string }) {
  return (
    <div className="text-muted-foreground flex items-center justify-center py-12 text-sm">
      {message}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Linked contacts section
// ---------------------------------------------------------------------------

function LinkedContactsSection({ entityId }: { entityId: string }) {
  const { data: contacts, isLoading } = useEntityLinkedContacts(entityId);

  if (isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    );
  }

  if (!contacts || contacts.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">No contacts linked to this entity.</p>
    );
  }

  return (
    <div className="space-y-2">
      {contacts.map((contact: LinkedContactSummary) => (
        <div
          key={contact.id}
          className="flex items-center justify-between rounded-md border px-3 py-2"
        >
          <div className="flex flex-col gap-0.5">
            <Link
              to={`/contacts/${contact.id}`}
              className="text-primary text-sm font-medium hover:underline"
            >
              {contact.full_name}
            </Link>
            <div className="text-muted-foreground flex gap-3 text-xs">
              {contact.email && <span>{contact.email}</span>}
              {contact.phone && <span>{contact.phone}</span>}
            </div>
          </div>
          <Link
            to={`/contacts/${contact.id}`}
            className="text-muted-foreground text-xs hover:underline"
          >
            View contact
          </Link>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Notes tab
// ---------------------------------------------------------------------------

function NotesTab({ entityId }: { entityId: string }) {
  const { data: notes, isLoading } = useEntityNotes(entityId);

  if (isLoading) return <TabSkeleton />;
  if (!notes || notes.length === 0)
    return <EmptyTab message="No notes for this entity yet." />;

  return (
    <div className="space-y-3 py-4">
      {notes.map((note: EntityNote) => (
        <Card key={note.id}>
          <CardContent className="py-3">
            <p className="text-sm whitespace-pre-wrap">{note.content}</p>
            <div className="text-muted-foreground mt-2 flex items-center gap-3 text-xs">
              <span>{formatRelative(note.created_at)}</span>
              {note.emotion && (
                <Badge variant="outline" className="text-xs">
                  {note.emotion}
                </Badge>
              )}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Interactions tab
// ---------------------------------------------------------------------------

function InteractionsTab({ entityId }: { entityId: string }) {
  const { data: interactions, isLoading } = useEntityInteractions(entityId);

  if (isLoading) return <TabSkeleton />;
  if (!interactions || interactions.length === 0)
    return <EmptyTab message="No interactions recorded for this entity." />;

  return (
    <div className="space-y-3 py-4">
      {interactions.map((item: EntityInteraction) => (
        <div
          key={item.id}
          className="flex items-start gap-3 border-l-2 border-border pl-4 py-2"
        >
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="text-xs">
                {item.type}
              </Badge>
              {item.direction && (
                <Badge variant="secondary" className="text-xs">
                  {item.direction}
                </Badge>
              )}
              <span className="text-muted-foreground text-xs">
                {formatDate(item.occurred_at)}
              </span>
            </div>
            {item.summary && (
              <p className="mt-1 text-sm">{item.summary}</p>
            )}
            {item.group_size && (
              <p className="text-muted-foreground mt-0.5 text-xs">
                Group size: {item.group_size}
              </p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Gifts tab
// ---------------------------------------------------------------------------

function GiftsTab({ entityId }: { entityId: string }) {
  const { data: gifts, isLoading } = useEntityGifts(entityId);

  if (isLoading) return <TabSkeleton />;
  if (!gifts || gifts.length === 0)
    return <EmptyTab message="No gifts recorded for this entity." />;

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Description</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Occasion</TableHead>
          <TableHead>Date</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {gifts.map((gift: EntityGift) => (
          <TableRow key={gift.id}>
            <TableCell className="font-medium">{gift.description ?? "—"}</TableCell>
            <TableCell>
              {gift.status ? (
                <Badge variant="outline" className="text-xs capitalize">
                  {gift.status}
                </Badge>
              ) : (
                "—"
              )}
            </TableCell>
            <TableCell className="text-muted-foreground text-sm">
              {gift.occasion ?? "—"}
            </TableCell>
            <TableCell className="text-muted-foreground text-sm">
              {formatDate(gift.created_at)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// ---------------------------------------------------------------------------
// Loans tab
// ---------------------------------------------------------------------------

function LoansTab({ entityId }: { entityId: string }) {
  const { data: loans, isLoading } = useEntityLoans(entityId);

  if (isLoading) return <TabSkeleton />;
  if (!loans || loans.length === 0)
    return <EmptyTab message="No loans recorded for this entity." />;

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Description</TableHead>
          <TableHead>Direction</TableHead>
          <TableHead>Amount</TableHead>
          <TableHead>Currency</TableHead>
          <TableHead>Settled</TableHead>
          <TableHead>Date</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {loans.map((loan: EntityLoan) => (
          <TableRow key={loan.id}>
            <TableCell className="font-medium">{loan.description ?? "—"}</TableCell>
            <TableCell>
              {loan.direction ? (
                <Badge
                  variant={loan.direction === "lent" ? "default" : "outline"}
                  className="text-xs"
                >
                  {loan.direction}
                </Badge>
              ) : (
                "—"
              )}
            </TableCell>
            <TableCell className="tabular-nums text-sm">
              {loan.amount_cents ?? "—"}
            </TableCell>
            <TableCell className="text-sm">{loan.currency ?? "—"}</TableCell>
            <TableCell>
              {loan.settled != null ? (
                <Badge
                  variant={loan.settled === "true" ? "secondary" : "default"}
                  className="text-xs"
                >
                  {loan.settled === "true" ? "settled" : "active"}
                </Badge>
              ) : (
                "—"
              )}
            </TableCell>
            <TableCell className="text-muted-foreground text-sm">
              {formatDate(loan.created_at)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// ---------------------------------------------------------------------------
// Timeline tab
// ---------------------------------------------------------------------------

function TimelineTab({ entityId }: { entityId: string }) {
  const { data: items, isLoading } = useEntityTimeline(entityId);

  if (isLoading) return <TabSkeleton />;
  if (!items || items.length === 0)
    return <EmptyTab message="No timeline events for this entity yet." />;

  return (
    <div className="space-y-2 py-4">
      {items.map((item: EntityTimelineItem) => (
        <div
          key={item.id}
          className="flex items-start gap-3 rounded-md border px-3 py-2"
        >
          <Badge variant="outline" className="text-xs shrink-0 capitalize">
            {item.kind}
          </Badge>
          <div className="flex-1 min-w-0">
            {item.content && (
              <p className="text-sm truncate">{item.content}</p>
            )}
            <p className="text-muted-foreground text-xs">{item.predicate}</p>
          </div>
          <span className="text-muted-foreground text-xs shrink-0">
            {formatRelative(item.valid_at)}
          </span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// EntityDetailView
// ---------------------------------------------------------------------------

export default function EntityDetailView({ entity }: EntityDetailViewProps) {
  const isUnidentified = entity.metadata?.["unidentified"] === "true";

  return (
    <div className="space-y-6">
      {/* Header card */}
      <Card>
        <CardHeader>
          <div className="flex items-start justify-between flex-wrap gap-2">
            <div className="flex-1">
              <CardTitle className="text-xl flex items-center gap-2 flex-wrap">
                {entity.canonical_name}
                {isUnidentified && (
                  <Badge
                    variant="outline"
                    className="text-xs border-yellow-500 text-yellow-600"
                  >
                    Unidentified
                  </Badge>
                )}
              </CardTitle>
              <p className="text-muted-foreground mt-1 text-sm capitalize">
                {entity.entity_type}
              </p>

              {/* Role badges */}
              {entity.roles.length > 0 && (
                <div className="mt-2 flex gap-1 flex-wrap">
                  {entity.roles.map((role) => (
                    <Badge
                      key={role}
                      style={roleBadgeStyle(role)}
                      className="text-xs capitalize"
                    >
                      {role}
                    </Badge>
                  ))}
                </div>
              )}

              {/* Alias chips */}
              {entity.aliases.length > 0 && (
                <div className="mt-2 flex gap-1 flex-wrap">
                  {entity.aliases.map((alias) => (
                    <Badge key={alias} variant="secondary" className="text-xs">
                      {alias}
                    </Badge>
                  ))}
                </div>
              )}
            </div>

            {/* "View identity →" link */}
            <div className="flex items-center">
              <Link
                to={`/entities/${entity.id}`}
                className="text-primary text-sm font-medium hover:underline"
              >
                View identity →
              </Link>
            </div>
          </div>
        </CardHeader>
      </Card>

      {/* Linked contacts */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Linked Contacts</CardTitle>
        </CardHeader>
        <CardContent>
          <LinkedContactsSection entityId={entity.id} />
        </CardContent>
      </Card>

      {/* Activity tabs */}
      <Tabs defaultValue="notes">
        <TabsList>
          <TabsTrigger value="notes">Notes</TabsTrigger>
          <TabsTrigger value="interactions">Interactions</TabsTrigger>
          <TabsTrigger value="gifts">Gifts</TabsTrigger>
          <TabsTrigger value="loans">Loans</TabsTrigger>
          <TabsTrigger value="timeline">Timeline</TabsTrigger>
        </TabsList>

        <TabsContent value="notes">
          <NotesTab entityId={entity.id} />
        </TabsContent>
        <TabsContent value="interactions">
          <InteractionsTab entityId={entity.id} />
        </TabsContent>
        <TabsContent value="gifts">
          <GiftsTab entityId={entity.id} />
        </TabsContent>
        <TabsContent value="loans">
          <LoansTab entityId={entity.id} />
        </TabsContent>
        <TabsContent value="timeline">
          <TimelineTab entityId={entity.id} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
