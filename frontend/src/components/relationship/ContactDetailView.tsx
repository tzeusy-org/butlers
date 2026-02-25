import { useState } from "react";
import { format, formatDistanceToNow } from "date-fns";
import { Pencil, Plus, X, Check } from "lucide-react";
import { toast } from "sonner";

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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
  useCreateContactInfo,
  usePatchContact,
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
        {displayValue ?? <span className="text-muted-foreground italic">&mdash;</span>}
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-2">
      {displayValue !== null ? (
        <span className="text-sm font-mono">{displayValue}</span>
      ) : (
        <span className="text-muted-foreground text-sm font-mono tracking-widest">
          ••••••••
        </span>
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
// Contact Info section (read-only)
// ---------------------------------------------------------------------------

function ContactInfoSection({ contact }: { contact: ContactDetail }) {
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
              <span className="text-muted-foreground text-sm w-24 shrink-0">
                Birthday
              </span>
              <span className="text-sm">{formatDate(contact.birthday)}</span>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Add contact_info inline form
// ---------------------------------------------------------------------------

const CONTACT_INFO_TYPES = [
  "email",
  "phone",
  "telegram",
  "telegram_chat_id",
  "website",
  "other",
] as const;

function AddContactInfoForm({
  contactId,
  onDone,
}: {
  contactId: string;
  onDone: () => void;
}) {
  const createInfo = useCreateContactInfo();
  const [type, setType] = useState<string>("email");
  const [value, setValue] = useState("");
  const [isPrimary, setIsPrimary] = useState(false);

  async function handleSubmit() {
    const trimmed = value.trim();
    if (!trimmed) {
      toast.error("Value cannot be empty.");
      return;
    }
    try {
      await createInfo.mutateAsync({
        contactId,
        request: { type, value: trimmed, is_primary: isPrimary },
      });
      toast.success(`Added ${type} entry.`);
      onDone();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      toast.error(`Failed to add: ${msg}`);
    }
  }

  return (
    <div className="flex items-end gap-2 pt-2 border-t mt-2">
      <div className="space-y-1">
        <Label className="text-xs">Type</Label>
        <Select value={type} onValueChange={setType}>
          <SelectTrigger className="h-8 w-28 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {CONTACT_INFO_TYPES.map((t) => (
              <SelectItem key={t} value={t} className="text-xs capitalize">
                {t}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="flex-1 space-y-1">
        <Label className="text-xs">Value</Label>
        <Input
          className="h-8 text-sm"
          placeholder={type === "email" ? "you@example.com" : type === "telegram" ? "@handle" : ""}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          disabled={createInfo.isPending}
          autoFocus
        />
      </div>
      <label className="flex items-center gap-1 text-xs text-muted-foreground pb-0.5">
        <input
          type="checkbox"
          checked={isPrimary}
          onChange={(e) => setIsPrimary(e.target.checked)}
          className="accent-primary"
        />
        Primary
      </label>
      <Button
        size="sm"
        variant="ghost"
        className="h-8 w-8 p-0"
        onClick={handleSubmit}
        disabled={createInfo.isPending}
      >
        <Check className="h-4 w-4" />
      </Button>
      <Button
        size="sm"
        variant="ghost"
        className="h-8 w-8 p-0"
        onClick={onDone}
        disabled={createInfo.isPending}
      >
        <X className="h-4 w-4" />
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Edit header form (inline within the card)
// ---------------------------------------------------------------------------

interface EditHeaderFormProps {
  contact: ContactDetail;
  onDone: () => void;
}

function EditHeaderForm({ contact, onDone }: EditHeaderFormProps) {
  const patchContact = usePatchContact();
  const [fullName, setFullName] = useState(contact.full_name);
  const [nickname, setNickname] = useState(contact.nickname ?? "");
  const [company, setCompany] = useState(contact.company ?? "");
  const [jobTitle, setJobTitle] = useState(contact.job_title ?? "");

  const isSaving = patchContact.isPending;

  async function handleSave() {
    const trimmedName = fullName.trim();
    if (!trimmedName) {
      toast.error("Name cannot be empty.");
      return;
    }

    // Only send changed fields
    const request: Record<string, string | null> = {};
    if (trimmedName !== contact.full_name) request.full_name = trimmedName;
    const nick = nickname.trim() || null;
    if (nick !== (contact.nickname ?? null)) request.nickname = nick ?? "";
    const comp = company.trim() || null;
    if (comp !== (contact.company ?? null)) request.company = comp ?? "";
    const jt = jobTitle.trim() || null;
    if (jt !== (contact.job_title ?? null)) request.job_title = jt ?? "";

    if (Object.keys(request).length === 0) {
      onDone();
      return;
    }

    try {
      await patchContact.mutateAsync({
        contactId: contact.id,
        request,
      });
      toast.success("Contact updated.");
      onDone();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      toast.error(`Failed to update: ${msg}`);
    }
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <Label htmlFor="edit-name" className="text-xs">
            Full name
          </Label>
          <Input
            id="edit-name"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            disabled={isSaving}
            autoFocus
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="edit-nickname" className="text-xs">
            Nickname
          </Label>
          <Input
            id="edit-nickname"
            value={nickname}
            onChange={(e) => setNickname(e.target.value)}
            disabled={isSaving}
            placeholder="Optional"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="edit-company" className="text-xs">
            Company
          </Label>
          <Input
            id="edit-company"
            value={company}
            onChange={(e) => setCompany(e.target.value)}
            disabled={isSaving}
            placeholder="Optional"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="edit-jobtitle" className="text-xs">
            Job title
          </Label>
          <Input
            id="edit-jobtitle"
            value={jobTitle}
            onChange={(e) => setJobTitle(e.target.value)}
            disabled={isSaving}
            placeholder="Optional"
          />
        </div>
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onDone} disabled={isSaving}>
          Cancel
        </Button>
        <Button size="sm" onClick={handleSave} disabled={isSaving}>
          {isSaving ? "Saving..." : "Save"}
        </Button>
      </div>
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
  const [editing, setEditing] = useState(false);
  const [addingInfo, setAddingInfo] = useState(false);

  return (
    <div className="space-y-6">
      {/* Header */}
      <Card>
        <CardHeader>
          <div className="flex items-start justify-between">
            <div className="flex-1">
              {editing ? (
                <EditHeaderForm contact={contact} onDone={() => setEditing(false)} />
              ) : (
                <>
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
                      {[contact.job_title, contact.company]
                        .filter(Boolean)
                        .join(" at ")}
                    </CardDescription>
                  )}
                </>
              )}
            </div>
            {!editing && (
              <div className="flex items-center gap-2">
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
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 w-8 p-0"
                  onClick={() => setEditing(true)}
                  title="Edit contact"
                >
                  <Pencil className="h-4 w-4" />
                </Button>
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent>
          <ContactInfoSection contact={contact} />
          {addingInfo ? (
            <AddContactInfoForm
              contactId={contact.id}
              onDone={() => setAddingInfo(false)}
            />
          ) : (
            <Button
              variant="ghost"
              size="sm"
              className="mt-2 h-7 text-xs text-muted-foreground"
              onClick={() => setAddingInfo(true)}
            >
              <Plus className="mr-1 h-3 w-3" />
              Add contact info
            </Button>
          )}
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
