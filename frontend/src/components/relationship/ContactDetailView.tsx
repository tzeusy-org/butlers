import { useState } from "react";
import { format, formatDistanceToNow } from "date-fns";

import type {
  ActivityFeedItem,
  ContactDetail,
  ContactInfoEntry,
  Gift,
  Interaction,
  Loan,
  Note,
} from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
  useContactFeed,
  useContactGifts,
  useContactInteractions,
  useContactLoans,
  useContactNotes,
  useRevealContactSecret,
} from "@/hooks/use-contacts";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ContactDetailViewProps {
  contact: ContactDetail;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function labelStyle(label: { color: string | null; name: string }): string {
  if (label.color) return label.color;
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

function formatDate(iso: string): string {
  return format(new Date(iso), "MMM d, yyyy");
}

function formatRelative(iso: string): string {
  return formatDistanceToNow(new Date(iso), { addSuffix: true });
}

/** Return a Tailwind-friendly color class for a role badge. */
function roleBadgeStyle(role: string): React.CSSProperties {
  switch (role.toLowerCase()) {
    case "owner":
      return { backgroundColor: "#7c3aed", color: "#fff" }; // violet-700
    case "admin":
      return { backgroundColor: "#b45309", color: "#fff" }; // amber-700
    default:
      return { backgroundColor: "#0369a1", color: "#fff" }; // sky-700
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
// Secured contact_info entry with click-to-reveal
// ---------------------------------------------------------------------------

function SecuredInfoEntry({
  entry,
  contactId,
}: {
  entry: ContactInfoEntry;
  contactId: string;
}) {
  const [revealed, setRevealed] = useState<string | null>(null);
  const [isRevealing, setIsRevealing] = useState(false);
  const revealMutation = useRevealContactSecret();

  const displayValue = revealed ?? entry.value;

  async function handleReveal() {
    if (isRevealing || revealed !== null) return;
    setIsRevealing(true);
    revealMutation.mutate(
      { contactId, infoId: entry.id },
      {
        onSuccess: (data) => {
          setRevealed(data.value ?? "");
          setIsRevealing(false);
        },
        onError: () => {
          setIsRevealing(false);
        },
      },
    );
  }

  if (!entry.secured) {
    return (
      <span className="text-sm">
        {displayValue ?? <span className="text-muted-foreground italic">—</span>}
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-2">
      {displayValue !== null ? (
        <span className="text-sm font-mono">{displayValue}</span>
      ) : (
        <span className="text-muted-foreground text-sm font-mono tracking-widest">••••••••</span>
      )}
      {revealed === null && (
        <Button
          variant="outline"
          size="sm"
          className="h-6 px-2 text-xs"
          onClick={handleReveal}
          disabled={isRevealing}
        >
          {isRevealing ? "Revealing..." : "Reveal"}
        </Button>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Contact Info section (new identity-aware structured entries)
// ---------------------------------------------------------------------------

function ContactInfoSection({
  contact,
}: {
  contact: ContactDetail;
}) {
  const hasContactInfo = contact.contact_info && contact.contact_info.length > 0;
  const hasBasicInfo =
    contact.email || contact.phone || contact.address || contact.birthday;

  if (!hasContactInfo && !hasBasicInfo) return null;

  return (
    <div className="space-y-1.5">
      {/* Structured contact_info entries */}
      {hasContactInfo &&
        contact.contact_info.map((entry) => (
          <div key={entry.id} className="flex gap-2 items-start">
            <span className="text-muted-foreground text-sm w-28 shrink-0 capitalize">
              {entry.type}
              {entry.is_primary && (
                <span className="ml-1 text-xs text-blue-500">(primary)</span>
              )}
            </span>
            <SecuredInfoEntry entry={entry} contactId={contact.id} />
          </div>
        ))}

      {/* Legacy flat fields (shown if no contact_info entries cover them) */}
      {!hasContactInfo && (
        <>
          {contact.email && (
            <div className="flex gap-2">
              <span className="text-muted-foreground text-sm w-24 shrink-0">Email</span>
              <span className="text-sm">{contact.email}</span>
            </div>
          )}
          {contact.phone && (
            <div className="flex gap-2">
              <span className="text-muted-foreground text-sm w-24 shrink-0">Phone</span>
              <span className="text-sm">{contact.phone}</span>
            </div>
          )}
          {contact.address && (
            <div className="flex gap-2">
              <span className="text-muted-foreground text-sm w-24 shrink-0">Address</span>
              <span className="text-sm">{contact.address}</span>
            </div>
          )}
          {contact.birthday && (
            <div className="flex gap-2">
              <span className="text-muted-foreground text-sm w-24 shrink-0">Birthday</span>
              <span className="text-sm">{formatDate(contact.birthday)}</span>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Notes tab
// ---------------------------------------------------------------------------

function NotesTab({ contactId }: { contactId: string }) {
  const { data: notes, isLoading } = useContactNotes(contactId);

  if (isLoading) return <TabSkeleton />;
  if (!notes || notes.length === 0) return <EmptyTab message="No notes yet." />;

  return (
    <div className="space-y-3 py-4">
      {notes.map((note: Note) => (
        <Card key={note.id}>
          <CardContent className="py-3">
            <p className="text-sm whitespace-pre-wrap">{note.content}</p>
            <p className="text-muted-foreground mt-2 text-xs">
              {formatRelative(note.created_at)}
            </p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Interactions tab
// ---------------------------------------------------------------------------

function InteractionsTab({ contactId }: { contactId: string }) {
  const { data: interactions, isLoading } = useContactInteractions(contactId);

  if (isLoading) return <TabSkeleton />;
  if (!interactions || interactions.length === 0)
    return <EmptyTab message="No interactions recorded." />;

  return (
    <div className="space-y-3 py-4">
      {interactions.map((item: Interaction) => (
        <div
          key={item.id}
          className="flex items-start gap-3 border-l-2 border-border pl-4 py-2"
        >
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="text-xs">
                {item.type}
              </Badge>
              <span className="text-muted-foreground text-xs">
                {formatDate(item.occurred_at)}
              </span>
            </div>
            <p className="mt-1 text-sm font-medium">{item.summary}</p>
            {item.details && (
              <p className="text-muted-foreground mt-1 text-xs">{item.details}</p>
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

function GiftsTab({ contactId }: { contactId: string }) {
  const { data: gifts, isLoading } = useContactGifts(contactId);

  if (isLoading) return <TabSkeleton />;
  if (!gifts || gifts.length === 0) return <EmptyTab message="No gifts recorded." />;

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Description</TableHead>
          <TableHead>Direction</TableHead>
          <TableHead>Occasion</TableHead>
          <TableHead>Date</TableHead>
          <TableHead className="text-right">Value</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {gifts.map((gift: Gift) => (
          <TableRow key={gift.id}>
            <TableCell className="font-medium">{gift.description}</TableCell>
            <TableCell>
              <Badge
                variant={gift.direction === "given" ? "default" : "outline"}
                className="text-xs"
              >
                {gift.direction}
              </Badge>
            </TableCell>
            <TableCell className="text-muted-foreground text-sm">
              {gift.occasion ?? "\u2014"}
            </TableCell>
            <TableCell className="text-muted-foreground text-sm">
              {formatDate(gift.date)}
            </TableCell>
            <TableCell className="text-right tabular-nums text-sm">
              {gift.value != null ? `$${gift.value.toFixed(2)}` : "\u2014"}
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

function statusVariant(
  status: string,
): "default" | "destructive" | "outline" | "secondary" {
  switch (status.toLowerCase()) {
    case "active":
      return "default";
    case "repaid":
      return "secondary";
    case "forgiven":
      return "outline";
    default:
      return "outline";
  }
}

function LoansTab({ contactId }: { contactId: string }) {
  const { data: loans, isLoading } = useContactLoans(contactId);

  if (isLoading) return <TabSkeleton />;
  if (!loans || loans.length === 0) return <EmptyTab message="No loans recorded." />;

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Description</TableHead>
          <TableHead>Direction</TableHead>
          <TableHead>Amount</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Date</TableHead>
          <TableHead>Due Date</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {loans.map((loan: Loan) => (
          <TableRow key={loan.id}>
            <TableCell className="font-medium">{loan.description}</TableCell>
            <TableCell>
              <Badge
                variant={loan.direction === "lent" ? "default" : "outline"}
                className="text-xs"
              >
                {loan.direction}
              </Badge>
            </TableCell>
            <TableCell className="tabular-nums text-sm">
              {loan.amount.toFixed(2)} {loan.currency}
            </TableCell>
            <TableCell>
              <Badge variant={statusVariant(loan.status)} className="text-xs">
                {loan.status}
              </Badge>
            </TableCell>
            <TableCell className="text-muted-foreground text-sm">
              {formatDate(loan.date)}
            </TableCell>
            <TableCell className="text-muted-foreground text-sm">
              {loan.due_date ? formatDate(loan.due_date) : "\u2014"}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// ---------------------------------------------------------------------------
// Activity tab
// ---------------------------------------------------------------------------

function ActivityTab({ contactId }: { contactId: string }) {
  const { data: feed, isLoading } = useContactFeed(contactId);

  if (isLoading) return <TabSkeleton />;
  if (!feed || feed.length === 0)
    return <EmptyTab message="No activity yet." />;

  return (
    <div className="space-y-2 py-4">
      {feed.map((item: ActivityFeedItem) => (
        <div
          key={item.id}
          className="flex items-center gap-3 rounded-md border px-3 py-2"
        >
          <Badge variant="outline" className="text-xs shrink-0">
            {item.action}
          </Badge>
          <span className="text-sm flex-1">
            {Object.keys(item.details).length > 0
              ? JSON.stringify(item.details)
              : "No additional details"}
          </span>
          <span className="text-muted-foreground text-xs shrink-0">
            {formatRelative(item.created_at)}
          </span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ContactDetailView
// ---------------------------------------------------------------------------

export default function ContactDetailView({ contact }: ContactDetailViewProps) {
  return (
    <div className="space-y-6">
      {/* Header */}
      <Card>
        <CardHeader>
          <div className="flex items-start justify-between">
            <div>
              <CardTitle className="text-xl flex items-center gap-2 flex-wrap">
                {contact.full_name}
                {contact.nickname && (
                  <span className="text-muted-foreground text-base font-normal">
                    ({contact.nickname})
                  </span>
                )}
                {/* Role badges */}
                {contact.roles && contact.roles.length > 0 && (
                  <span className="flex gap-1 flex-wrap">
                    {contact.roles.map((role) => (
                      <Badge
                        key={role}
                        style={roleBadgeStyle(role)}
                        className="text-xs capitalize"
                      >
                        {role}
                      </Badge>
                    ))}
                  </span>
                )}
              </CardTitle>
              {(contact.company || contact.job_title) && (
                <CardDescription className="mt-1">
                  {[contact.job_title, contact.company].filter(Boolean).join(" at ")}
                </CardDescription>
              )}
            </div>
            <div className="flex gap-1.5 flex-wrap justify-end">
              {contact.labels.map((label) => (
                <Badge
                  key={label.id}
                  style={{
                    backgroundColor: labelStyle(label),
                    color: "#fff",
                  }}
                >
                  {label.name}
                </Badge>
              ))}
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <ContactInfoSection contact={contact} />
        </CardContent>
      </Card>

      {/* Tabs for sub-resources */}
      <Tabs defaultValue="notes">
        <TabsList>
          <TabsTrigger value="notes">Notes</TabsTrigger>
          <TabsTrigger value="interactions">Interactions</TabsTrigger>
          <TabsTrigger value="gifts">Gifts</TabsTrigger>
          <TabsTrigger value="loans">Loans</TabsTrigger>
          <TabsTrigger value="activity">Activity</TabsTrigger>
        </TabsList>

        <TabsContent value="notes">
          <NotesTab contactId={contact.id} />
        </TabsContent>
        <TabsContent value="interactions">
          <InteractionsTab contactId={contact.id} />
        </TabsContent>
        <TabsContent value="gifts">
          <GiftsTab contactId={contact.id} />
        </TabsContent>
        <TabsContent value="loans">
          <LoansTab contactId={contact.id} />
        </TabsContent>
        <TabsContent value="activity">
          <ActivityTab contactId={contact.id} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
