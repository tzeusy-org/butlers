import { useState } from "react";
import { useNavigate } from "react-router";
import { format, formatDistanceToNow } from "date-fns";
import { Pencil, Plus, Trash2, X, Check } from "lucide-react";
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
  useDeleteContact,
  useDeleteContactInfo,
  usePatchContact,
  usePatchContactInfo,
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
// Editable contact_info row
// ---------------------------------------------------------------------------

function ContactInfoRow({
  entry,
  contactId,
}: {
  entry: ContactInfoEntry;
  contactId: string;
}) {
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(entry.value ?? "");
  const deleteInfo = useDeleteContactInfo();
  const patchInfo = usePatchContactInfo();

  function handleDelete() {
    if (!window.confirm(`Delete this ${contactInfoTypeLabel(entry.type)} entry?`)) return;
    deleteInfo.mutate(
      { contactId, infoId: entry.id },
      {
        onSuccess: () => toast.success(`Removed ${contactInfoTypeLabel(entry.type)} entry.`),
        onError: (err) =>
          toast.error(`Failed to delete: ${err instanceof Error ? err.message : "Unknown error"}`),
      },
    );
  }

  function handleSaveEdit() {
    const trimmed = editValue.trim();
    if (!trimmed) {
      toast.error("Value cannot be empty.");
      return;
    }
    if (trimmed === entry.value) {
      setEditing(false);
      return;
    }
    patchInfo.mutate(
      { contactId, infoId: entry.id, request: { value: trimmed } },
      {
        onSuccess: () => {
          toast.success(`Updated ${contactInfoTypeLabel(entry.type)} entry.`);
          setEditing(false);
        },
        onError: (err) =>
          toast.error(`Failed to update: ${err instanceof Error ? err.message : "Unknown error"}`),
      },
    );
  }

  return (
    <div className="flex gap-2 items-center group">
      <span className="text-muted-foreground text-sm w-36 shrink-0 capitalize">
        {contactInfoTypeLabel(entry.type)}
        {entry.is_primary && (
          <span className="ml-1 text-xs text-blue-500">(primary)</span>
        )}
      </span>
      {editing ? (
        <div className="flex items-center gap-1 flex-1">
          <Input
            className="h-7 text-sm flex-1"
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            disabled={patchInfo.isPending}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSaveEdit();
              if (e.key === "Escape") setEditing(false);
            }}
          />
          <Button
            variant="ghost"
            size="sm"
            className="h-7 w-7 p-0"
            onClick={handleSaveEdit}
            disabled={patchInfo.isPending}
          >
            <Check className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 w-7 p-0"
            onClick={() => {
              setEditValue(entry.value ?? "");
              setEditing(false);
            }}
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      ) : (
        <>
          <span className="flex-1">
            <SecuredInfoEntry entry={entry} contactId={contactId} />
          </span>
          <span className="flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
            {!entry.secured && (
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0"
                onClick={() => {
                  setEditValue(entry.value ?? "");
                  setEditing(true);
                }}
                title="Edit"
              >
                <Pencil className="h-3 w-3" />
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              className="h-6 w-6 p-0 text-destructive hover:text-destructive"
              onClick={handleDelete}
              disabled={deleteInfo.isPending}
              title="Delete"
            >
              <Trash2 className="h-3 w-3" />
            </Button>
          </span>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Contact Info section
// ---------------------------------------------------------------------------

function ContactInfoSection({ contact }: { contact: ContactDetail }) {
  const hasContactInfo = contact.contact_info && contact.contact_info.length > 0;
  const hasBasicInfo =
    contact.email || contact.phone || contact.address || contact.birthday;

  if (!hasContactInfo && !hasBasicInfo) return null;

  const { groups, ungrouped } = hasContactInfo
    ? groupContactInfoByAccount(contact.contact_info)
    : { groups: [], ungrouped: [] };

  return (
    <div className="space-y-1.5">
      {/* Grouped contact_info entries */}
      {hasContactInfo && (
        <>
          {groups.map(({ parent, children }) => (
            <div key={parent.id}>
              <ContactInfoRow entry={parent} contactId={contact.id} />
              {children.length > 0 && (
                <div className="ml-6 border-l pl-3 space-y-1 mt-1">
                  {children.map((child) => (
                    <ContactInfoRow key={child.id} entry={child} contactId={contact.id} />
                  ))}
                </div>
              )}
            </div>
          ))}
          {ungrouped.map((entry) => (
            <ContactInfoRow key={entry.id} entry={entry} contactId={contact.id} />
          ))}
        </>
      )}

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

const SECURED_CONTACT_INFO_TYPES = [
  "email_password",
  "telegram_api_id",
  "telegram_api_hash",
] as const;

const ALL_CONTACT_INFO_TYPES = [...CONTACT_INFO_TYPES, ...SECURED_CONTACT_INFO_TYPES] as const;

const SECURED_TYPES = new Set<string>(SECURED_CONTACT_INFO_TYPES);

/** Maps credential types to their expected parent type. */
const CHILD_TO_PARENT_TYPE: Record<string, string> = {
  email_password: "email",
  telegram: "telegram_chat_id",
  telegram_api_id: "telegram_chat_id",
  telegram_api_hash: "telegram_chat_id",
};

const CREDENTIAL_TYPES = new Set(Object.keys(CHILD_TO_PARENT_TYPE));

interface AccountGroup {
  parent: ContactInfoEntry;
  children: ContactInfoEntry[];
}

interface GroupedContactInfo {
  groups: AccountGroup[];
  ungrouped: ContactInfoEntry[];
}

/** Group contact_info entries by parent_id into account groups. */
function groupContactInfoByAccount(entries: ContactInfoEntry[]): GroupedContactInfo {
  const parentMap = new Map<string, ContactInfoEntry>();
  const childrenByParent = new Map<string, ContactInfoEntry[]>();
  const ungrouped: ContactInfoEntry[] = [];

  // First pass: index all entries by id
  const byId = new Map(entries.map((e) => [e.id, e]));

  // Second pass: classify
  for (const entry of entries) {
    if (entry.parent_id && byId.has(entry.parent_id)) {
      // This is a child with a known parent in this set
      const list = childrenByParent.get(entry.parent_id) ?? [];
      list.push(entry);
      childrenByParent.set(entry.parent_id, list);
      // Ensure parent is registered
      parentMap.set(entry.parent_id, byId.get(entry.parent_id)!);
    } else if (!CREDENTIAL_TYPES.has(entry.type)) {
      // Non-credential without parent_id — could be a standalone or a parent
      // We'll check if anything references it after
      parentMap.set(entry.id, entry);
    } else {
      // Credential type without parent_id — ungrouped
      ungrouped.push(entry);
    }
  }

  // Build groups: only include parents that have children OR are non-credential
  const groups: AccountGroup[] = [];
  const usedAsParent = new Set(childrenByParent.keys());

  for (const [id, parent] of parentMap) {
    if (usedAsParent.has(id)) {
      groups.push({ parent, children: childrenByParent.get(id) ?? [] });
    } else {
      // Standalone entry (no children reference it)
      groups.push({ parent, children: [] });
    }
  }

  return { groups, ungrouped };
}

/** Human-friendly labels for contact info types. */
function contactInfoTypeLabel(type: string): string {
  switch (type) {
    case "email": return "Email";
    case "phone": return "Phone";
    case "telegram": return "Telegram";
    case "telegram_chat_id": return "Telegram Chat ID";
    case "website": return "Website";
    case "other": return "Other";
    case "email_password": return "Email Password";
    case "telegram_api_id": return "Telegram API ID";
    case "telegram_api_hash": return "Telegram API Hash";
    default: return type;
  }
}

function inputPlaceholder(type: string): string {
  switch (type) {
    case "email": return "you@example.com";
    case "telegram": return "@handle";
    case "telegram_chat_id": return "123456789";
    case "email_password": return "••••••••";
    case "telegram_api_id": return "12345678";
    case "telegram_api_hash": return "••••••••";
    default: return "";
  }
}

function AddContactInfoForm({
  contactId,
  existingEntries,
  onDone,
}: {
  contactId: string;
  existingEntries: ContactInfoEntry[];
  onDone: () => void;
}) {
  const createInfo = useCreateContactInfo();
  const [type, setType] = useState<string>("email");
  const [value, setValue] = useState("");
  const [isPrimary, setIsPrimary] = useState(false);
  const [parentId, setParentId] = useState<string | null>(null);

  const isSecured = SECURED_TYPES.has(type);
  const parentType = CHILD_TO_PARENT_TYPE[type] ?? null;

  // Find candidate parents for the selected credential type
  const parentCandidates = parentType
    ? existingEntries.filter((e) => e.type === parentType)
    : [];

  // Auto-resolve parent_id when exactly one candidate
  function handleTypeChange(newType: string) {
    setType(newType);
    setValue("");
    const pt = CHILD_TO_PARENT_TYPE[newType] ?? null;
    if (pt) {
      const candidates = existingEntries.filter((e) => e.type === pt);
      setParentId(candidates.length === 1 ? candidates[0].id : null);
    } else {
      setParentId(null);
    }
  }

  async function handleSubmit() {
    const trimmed = value.trim();
    if (!trimmed) {
      toast.error("Value cannot be empty.");
      return;
    }
    try {
      await createInfo.mutateAsync({
        contactId,
        request: {
          type,
          value: trimmed,
          is_primary: isPrimary,
          ...(isSecured ? { secured: true } : {}),
          ...(parentId ? { parent_id: parentId } : {}),
        },
      });
      toast.success(`Added ${contactInfoTypeLabel(type)} entry.`);
      onDone();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      toast.error(`Failed to add: ${msg}`);
    }
  }

  return (
    <div className="flex items-end gap-2 pt-2 border-t mt-2 flex-wrap">
      <div className="space-y-1">
        <Label className="text-xs">Type</Label>
        <Select value={type} onValueChange={handleTypeChange}>
          <SelectTrigger className="h-8 w-36 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ALL_CONTACT_INFO_TYPES.map((t) => (
              <SelectItem key={t} value={t} className="text-xs">
                {contactInfoTypeLabel(t)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {parentType && parentCandidates.length > 1 && (
        <div className="space-y-1">
          <Label className="text-xs">Account</Label>
          <Select
            value={parentId ?? ""}
            onValueChange={(v) => setParentId(v || null)}
          >
            <SelectTrigger className="h-8 w-44 text-xs">
              <SelectValue placeholder="Select account..." />
            </SelectTrigger>
            <SelectContent>
              {parentCandidates.map((c) => (
                <SelectItem key={c.id} value={c.id} className="text-xs">
                  {c.value ?? contactInfoTypeLabel(c.type)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
      <div className="flex-1 space-y-1">
        <Label className="text-xs">Value</Label>
        <Input
          className="h-8 text-sm"
          type={isSecured ? "password" : "text"}
          placeholder={inputPlaceholder(type)}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          disabled={createInfo.isPending}
          autoFocus
        />
      </div>
      {!isSecured && (
        <label className="flex items-center gap-1 text-xs text-muted-foreground pb-0.5">
          <input
            type="checkbox"
            checked={isPrimary}
            onChange={(e) => setIsPrimary(e.target.checked)}
            className="accent-primary"
          />
          Primary
        </label>
      )}
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
// Preferred channel selector
// ---------------------------------------------------------------------------

function PreferredChannelRow({ contact }: { contact: ContactDetail }) {
  const patchContact = usePatchContact();

  const hasTelegram = contact.contact_info?.some(
    (ci) => ci.type === "telegram_chat_id",
  );
  const hasEmail = contact.contact_info?.some((ci) => ci.type === "email");

  function handleChange(value: string) {
    const preferred_channel = value === "none" ? "" : value;
    patchContact.mutate(
      { contactId: contact.id, request: { preferred_channel: preferred_channel || null } },
      {
        onSuccess: () => toast.success("Preferred channel updated."),
        onError: (err) =>
          toast.error(
            `Failed to update: ${err instanceof Error ? err.message : "Unknown error"}`,
          ),
      },
    );
  }

  return (
    <div className="flex gap-2 items-center mt-2">
      <span className="text-muted-foreground text-sm w-36 shrink-0">
        Preferred channel
      </span>
      <Select
        value={contact.preferred_channel ?? "none"}
        onValueChange={handleChange}
        disabled={patchContact.isPending}
      >
        <SelectTrigger className="h-7 w-36 text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="none" className="text-xs">
            None
          </SelectItem>
          <SelectItem
            value="telegram"
            className="text-xs"
            disabled={!hasTelegram}
          >
            Telegram
          </SelectItem>
          <SelectItem value="email" className="text-xs" disabled={!hasEmail}>
            Email
          </SelectItem>
        </SelectContent>
      </Select>
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
  const [firstName, setFirstName] = useState(contact.first_name ?? "");
  const [lastName, setLastName] = useState(contact.last_name ?? "");
  const [nickname, setNickname] = useState(contact.nickname ?? "");
  const [company, setCompany] = useState(contact.company ?? "");
  const [jobTitle, setJobTitle] = useState(contact.job_title ?? "");

  const isSaving = patchContact.isPending;

  async function handleSave() {
    const trimmedFirst = firstName.trim();
    const trimmedLast = lastName.trim();
    if (!trimmedFirst && !trimmedLast) {
      toast.error("Name cannot be empty.");
      return;
    }

    // Only send changed fields
    const request: Record<string, string | null> = {};
    if (trimmedFirst !== (contact.first_name ?? "")) request.first_name = trimmedFirst;
    if (trimmedLast !== (contact.last_name ?? "")) request.last_name = trimmedLast;
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
      <div className="grid grid-cols-3 gap-3">
        <div className="space-y-1">
          <Label htmlFor="edit-first-name" className="text-xs">
            First name
          </Label>
          <Input
            id="edit-first-name"
            value={firstName}
            onChange={(e) => setFirstName(e.target.value)}
            disabled={isSaving}
            autoFocus
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="edit-last-name" className="text-xs">
            Last name
          </Label>
          <Input
            id="edit-last-name"
            value={lastName}
            onChange={(e) => setLastName(e.target.value)}
            disabled={isSaving}
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
          <TableHead>Status</TableHead>
          <TableHead>Occasion</TableHead>
          <TableHead>Date</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {gifts.map((gift: Gift) => (
          <TableRow key={gift.id}>
            <TableCell className="font-medium">{gift.description}</TableCell>
            <TableCell>
              <Badge variant="outline" className="text-xs capitalize">
                {gift.status}
              </Badge>
            </TableCell>
            <TableCell className="text-muted-foreground text-sm">
              {gift.occasion ?? "\u2014"}
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
          <TableHead>Settled</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {loans.map((loan: Loan) => (
          <TableRow key={loan.id}>
            <TableCell className="font-medium">{loan.description ?? "\u2014"}</TableCell>
            <TableCell>
              <Badge
                variant={loan.direction === "lent" ? "default" : "outline"}
                className="text-xs"
              >
                {loan.direction}
              </Badge>
            </TableCell>
            <TableCell className="tabular-nums text-sm">
              {loan.amount.toFixed(2)}
            </TableCell>
            <TableCell>
              <Badge
                variant={loan.settled ? "secondary" : "default"}
                className="text-xs"
              >
                {loan.settled ? "settled" : "active"}
              </Badge>
            </TableCell>
            <TableCell className="text-muted-foreground text-sm">
              {formatDate(loan.created_at)}
            </TableCell>
            <TableCell className="text-muted-foreground text-sm">
              {loan.settled_at ? formatDate(loan.settled_at) : "\u2014"}
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
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [addingInfo, setAddingInfo] = useState(false);
  const deleteContactMutation = useDeleteContact();

  function handleDeleteContact() {
    if (!window.confirm(`Delete "${contact.full_name}"? This cannot be undone.`)) return;
    deleteContactMutation.mutate(contact.id, {
      onSuccess: () => {
        toast.success(`Deleted ${contact.full_name}.`);
        navigate("/contacts");
      },
      onError: (err) =>
        toast.error(`Failed to delete: ${err instanceof Error ? err.message : "Unknown error"}`),
    });
  }

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
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 w-8 p-0 text-destructive hover:text-destructive"
                  onClick={handleDeleteContact}
                  disabled={deleteContactMutation.isPending}
                  title="Delete contact"
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent>
          <ContactInfoSection contact={contact} />
          <PreferredChannelRow contact={contact} />
          {addingInfo ? (
            <AddContactInfoForm
              contactId={contact.id}
              existingEntries={contact.contact_info ?? []}
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
