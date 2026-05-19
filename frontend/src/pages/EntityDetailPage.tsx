import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import {
  Check,
  Layers,
  Loader2,
  Pencil,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { format, formatDistanceToNow } from "date-fns";
import { Time } from "@/components/ui/time";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getEntityGloss, DUNBAR_TIER_VALUES, ENTITY_TYPE_VALUES } from "@/lib/entity-glosses";
import type { DunbarTier, EntityState, EntityType } from "@/lib/entity-glosses";

import type {
  ContactSummary,
  EntityImportantDate,
  EntityInfoEntry,
  EntityTimelineItem,
  Fact,
  LinkedContactSummary,
  MessageThreadSummary,
} from "@/api/types";
import {
  getTelegramSessionStatus,
  telegramSendCode,
  telegramVerifyCode,
} from "@/api/index";
import { OwnerSetupBanner } from "@/components/relationship/OwnerSetupBanner";
import { PracticalDrawer } from "@/components/relationship/PracticalDrawer";
import { PulseStrip } from "@/components/relationship/PulseStrip";
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
import { Page } from "@/components/ui/page";
import {
  Card,
  CardContent,
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
import { useContacts } from "@/hooks/use-contacts";
import {
  useEntityDates,
  useEntityGifts,
  useEntityLinkedContacts,
  useEntityLoans,
  useEntityMessageThreads,
  useEntityTimeline,
} from "@/hooks/use-entities";
import {
  useEntity,
  useForgetRelationshipEntity,
  usePromoteEntity,
  useRevealEntitySecret,
  useSetLinkedContact,
  useUnlinkContact,
  useUpdateEntity,
} from "@/hooks/use-memory";

// ---------------------------------------------------------------------------
// Editorial / Workbench mode
// ---------------------------------------------------------------------------

/** The two display modes for the entity detail page. */
type EntityDetailMode = "editorial" | "workbench";

/** localStorage key for persisting the entity detail mode. */
export const ENTITY_MODE_STORAGE_KEY = "entities.detail.mode";

/** URL search-param name for per-link mode override. */
const ENTITY_MODE_PARAM = "mode";

/**
 * Reads the persisted mode from localStorage, defaulting to "editorial".
 * Falls back gracefully when localStorage is unavailable.
 */
function readPersistedEntityMode(): EntityDetailMode {
  try {
    const stored = localStorage.getItem(ENTITY_MODE_STORAGE_KEY);
    if (stored === "editorial" || stored === "workbench") return stored;
  } catch {
    // localStorage not available (e.g. SSR or private browsing restrictions)
  }
  return "editorial";
}

/**
 * Writes the mode to localStorage.
 * Ignores write failures (e.g. storage quota exceeded).
 */
function persistEntityMode(mode: EntityDetailMode): void {
  try {
    localStorage.setItem(ENTITY_MODE_STORAGE_KEY, mode);
  } catch {
    // Ignore write failures
  }
}

// ---------------------------------------------------------------------------

const FACTS_PAGE_SIZE = 20;
// Profile snapshot pulls predicates from recent_facts; profile-relevant facts
// (birthday, lives_in, works_at, family) are often old and would fall outside
// a 20-row window. Load a wider initial slice so the snapshot has data to work
// with; the user can still page further via "Load more facts".
const FACTS_INITIAL_LIMIT = 200;

function sessionDetailHref(sessionId: string, butler: string | null): string {
  const query = butler ? `?butler=${encodeURIComponent(butler)}` : "";
  return `/sessions/${encodeURIComponent(sessionId)}${query}`;
}

// ---------------------------------------------------------------------------
// Telegram session setup — interactive auth flow
// ---------------------------------------------------------------------------

type TelegramStep = "idle" | "loading_creds" | "credentials" | "phone" | "code" | "two_fa" | "success";

function TelegramSessionSetup({
  entityId,
  entries,
}: {
  entityId: string;
  entries: EntityInfoEntry[];
}) {
  const queryClient = useQueryClient();
  const { data: status, isLoading } = useQuery({
    queryKey: ["telegram-session-status"],
    queryFn: getTelegramSessionStatus,
    refetchInterval: 30_000,
  });

  // Check for existing entity_info entries (entries, not values — secured values are null)
  const apiIdEntry = entries.find((e) => e.type === "telegram_api_id");
  const apiHashEntry = entries.find((e) => e.type === "telegram_api_hash");

  const revealMutation = useRevealEntitySecret();
  const hasExistingCreds = !!apiIdEntry && !!apiHashEntry;

  // Use the value directly if visible, otherwise empty (will be revealed)
  const visibleApiId = apiIdEntry?.value ?? "";

  const [step, setStep] = useState<TelegramStep>("idle");
  const [apiId, setApiId] = useState(visibleApiId);
  const [apiHash, setApiHash] = useState("");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [sessionToken, setSessionToken] = useState("");
  const [userName, setUserName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleStart() {
    if (!hasExistingCreds) {
      setStep("credentials");
      return;
    }

    // Both entries exist — reveal their actual values, then skip to phone step
    setStep("loading_creds");
    setError(null);

    try {
      // Reveal API ID if secured (value is null), otherwise use visible value
      let resolvedApiId = apiIdEntry.value;
      if (!resolvedApiId && apiIdEntry.secured) {
        const revealed = await new Promise<string>((resolve, reject) => {
          revealMutation.mutate(
            { entityId, infoId: apiIdEntry.id },
            {
              onSuccess: (data) => resolve(data.value ?? ""),
              onError: reject,
            },
          );
        });
        resolvedApiId = revealed;
      }

      // Reveal API Hash (always secured)
      const resolvedApiHash = await new Promise<string>((resolve, reject) => {
        revealMutation.mutate(
          { entityId, infoId: apiHashEntry.id },
          {
            onSuccess: (data) => resolve(data.value ?? ""),
            onError: reject,
          },
        );
      });

      setApiId(resolvedApiId ?? "");
      setApiHash(resolvedApiHash);
      setStep("phone");
    } catch {
      // Fall back to manual entry if reveals fail
      setApiId(visibleApiId);
      setStep("credentials");
      setError("Could not load existing credentials. Please re-enter them.");
    }
  }

  const sendCodeMutation = useMutation({
    mutationFn: telegramSendCode,
    onSuccess: (data) => {
      setSessionToken(data.session_token);
      setStep("code");
      setError(null);
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : "Failed to send code");
    },
  });

  const verifyMutation = useMutation({
    mutationFn: telegramVerifyCode,
    onSuccess: (data) => {
      if (data.success) {
        setUserName(data.user_name);
        setStep("success");
        setError(null);
        void queryClient.invalidateQueries({ queryKey: ["telegram-session-status"] });
        void queryClient.invalidateQueries({ queryKey: ["memory-entity", entityId] });
        toast.success("Telegram session created successfully!");
      } else if (data.message.includes("2FA") || data.message.includes("Two-factor")) {
        setStep("two_fa");
        setError(null);
      } else {
        setError(data.message);
      }
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : "Verification failed");
    },
  });

  function handleSendCode() {
    setError(null);
    const id = parseInt(apiId.trim(), 10);
    if (isNaN(id)) {
      setError("API ID must be a number");
      return;
    }
    if (!apiHash.trim()) {
      setError("API Hash is required");
      return;
    }
    if (!phone.trim()) {
      setError("Phone number is required");
      return;
    }
    sendCodeMutation.mutate({
      api_id: id,
      api_hash: apiHash.trim(),
      phone: phone.trim(),
    });
  }

  function handleVerifyCode() {
    setError(null);
    if (!code.trim()) {
      setError("Please enter the verification code");
      return;
    }
    verifyMutation.mutate({
      session_token: sessionToken,
      code: code.trim(),
    });
  }

  function handleSubmit2FA() {
    setError(null);
    if (!password.trim()) {
      setError("Please enter your 2FA password");
      return;
    }
    verifyMutation.mutate({
      session_token: sessionToken,
      code: code.trim(),
      password: password.trim(),
    });
  }

  function handleReset() {
    setStep("idle");
    setApiId(visibleApiId);
    setApiHash("");
    setPhone("");
    setCode("");
    setPassword("");
    setSessionToken("");
    setUserName(null);
    setError(null);
  }

  const isPending = sendCodeMutation.isPending || verifyMutation.isPending;

  if (isLoading) {
    return (
      <Card>
        <CardHeader><CardTitle>Telegram User Session</CardTitle></CardHeader>
        <CardContent><Skeleton className="h-8 w-48" /></CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Telegram User Session</CardTitle>
          {status?.ready && (
            <Badge variant="outline" className="text-green-600 border-green-600">Connected</Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Status summary */}
        {status && step === "idle" && (
          <div className="flex flex-col gap-1.5 text-sm">
            <div className="flex items-center gap-2">
              <span className={status.has_api_id ? "text-green-600" : "text-muted-foreground"}>
                {status.has_api_id ? "+" : "-"}
              </span>
              <span>API ID</span>
            </div>
            <div className="flex items-center gap-2">
              <span className={status.has_api_hash ? "text-green-600" : "text-muted-foreground"}>
                {status.has_api_hash ? "+" : "-"}
              </span>
              <span>API Hash</span>
            </div>
            <div className="flex items-center gap-2">
              <span className={status.has_session ? "text-green-600" : "text-muted-foreground"}>
                {status.has_session ? "+" : "-"}
              </span>
              <span>Session String</span>
            </div>
          </div>
        )}

        {/* Step: idle — show setup button */}
        {step === "idle" && (
          <Button
            variant={status?.ready ? "outline" : "default"}
            size="sm"
            onClick={handleStart}
          >
            {status?.ready ? "Re-generate Session" : "Set Up Telegram Session"}
          </Button>
        )}

        {/* Step: loading_creds — revealing existing API hash */}
        {step === "loading_creds" && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading existing credentials...
          </div>
        )}

        {/* Step: credentials — enter API ID + Hash + Phone */}
        {step === "credentials" && (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              Enter your Telegram API credentials from{" "}
              <a
                href="https://my.telegram.org/apps"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline"
              >
                my.telegram.org/apps
              </a>
              .
            </p>
            <div className="grid gap-2">
              <div className="space-y-1">
                <Label className="text-xs">API ID</Label>
                <Input
                  className="h-8 text-sm"
                  placeholder="12345678"
                  value={apiId}
                  onChange={(e) => setApiId(e.target.value)}
                  disabled={isPending}
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">API Hash</Label>
                <Input
                  className="h-8 text-sm"
                  type="password"
                  placeholder="a1b2c3d4e5f6..."
                  value={apiHash}
                  onChange={(e) => setApiHash(e.target.value)}
                  disabled={isPending}
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Phone Number</Label>
                <Input
                  className="h-8 text-sm"
                  placeholder="+1234567890"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  disabled={isPending}
                  onKeyDown={(e) => { if (e.key === "Enter") handleSendCode(); }}
                />
              </div>
            </div>
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={handleSendCode}
                disabled={isPending}
              >
                {sendCodeMutation.isPending && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
                Send Code
              </Button>
              <Button variant="ghost" size="sm" onClick={handleReset} disabled={isPending}>
                Cancel
              </Button>
            </div>
          </div>
        )}

        {/* Step: phone — API creds pre-filled, just need phone */}
        {step === "phone" && (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              Using existing API credentials. Enter your phone number to receive a verification code.
            </p>
            <div className="space-y-1">
              <Label className="text-xs">Phone Number</Label>
              <Input
                className="h-8 w-56 text-sm"
                placeholder="+1234567890"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                disabled={isPending}
                autoFocus
                onKeyDown={(e) => { if (e.key === "Enter") handleSendCode(); }}
              />
            </div>
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={handleSendCode}
                disabled={isPending}
              >
                {sendCodeMutation.isPending && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
                Send Code
              </Button>
              <Button variant="ghost" size="sm" onClick={handleReset} disabled={isPending}>
                Cancel
              </Button>
            </div>
          </div>
        )}

        {/* Step: code — enter OTP */}
        {step === "code" && (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              A verification code has been sent to your Telegram app. Enter it below.
            </p>
            <div className="space-y-1">
              <Label className="text-xs">Verification Code</Label>
              <Input
                className="h-8 w-48 text-sm font-mono tracking-widest"
                placeholder="12345"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                disabled={isPending}
                autoFocus
                onKeyDown={(e) => { if (e.key === "Enter") handleVerifyCode(); }}
              />
            </div>
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={handleVerifyCode}
                disabled={isPending}
              >
                {verifyMutation.isPending && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
                Verify
              </Button>
              <Button variant="ghost" size="sm" onClick={handleReset} disabled={isPending}>
                Cancel
              </Button>
            </div>
          </div>
        )}

        {/* Step: 2FA password */}
        {step === "two_fa" && (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              Two-factor authentication is enabled. Enter your 2FA password.
            </p>
            <div className="space-y-1">
              <Label className="text-xs">2FA Password</Label>
              <Input
                className="h-8 w-64 text-sm"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={isPending}
                autoFocus
                onKeyDown={(e) => { if (e.key === "Enter") handleSubmit2FA(); }}
              />
            </div>
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={handleSubmit2FA}
                disabled={isPending}
              >
                {verifyMutation.isPending && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
                Submit
              </Button>
              <Button variant="ghost" size="sm" onClick={handleReset} disabled={isPending}>
                Cancel
              </Button>
            </div>
          </div>
        )}

        {/* Step: success */}
        {step === "success" && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-sm text-green-600">
              <Check className="h-4 w-4" />
              <span>
                Session created{userName ? ` for ${userName}` : ""}.
                All three credentials are now stored on this entity.
              </span>
            </div>
            <Button variant="ghost" size="sm" onClick={handleReset}>
              Done
            </Button>
          </div>
        )}

        {/* Error display */}
        {error && (
          <p className="text-sm text-destructive">{error}</p>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Linked contact section with unlink / link
// ---------------------------------------------------------------------------

function LinkedContactSection({
  entityId,
  entity,
}: {
  entityId: string;
  entity: { linked_contact_id: string | null; linked_contact_name: string | null };
}) {
  const unlinkContact = useUnlinkContact();
  const setLinkedContact = useSetLinkedContact();
  const [linking, setLinking] = useState(false);
  const [search, setSearch] = useState("");
  const { data: contactsData } = useContacts(
    linking ? { q: search || undefined, limit: 10 } : undefined,
  );
  const contacts: ContactSummary[] = contactsData?.contacts ?? [];

  function handleUnlink() {
    if (!window.confirm("Unlink this contact from the entity?")) return;
    unlinkContact.mutate(entityId, {
      onSuccess: () => toast.success("Contact unlinked."),
      onError: (err) =>
        toast.error(`Failed to unlink: ${err instanceof Error ? err.message : "Unknown"}`),
    });
  }

  function handleLink(contactId: string) {
    setLinkedContact.mutate(
      { entityId, contactId },
      {
        onSuccess: () => {
          toast.success("Contact linked.");
          setLinking(false);
          setSearch("");
        },
        onError: (err) =>
          toast.error(`Failed to link: ${err instanceof Error ? err.message : "Unknown"}`),
      },
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Linked Contact</CardTitle>
        </div>
      </CardHeader>
      <CardContent>
        {entity.linked_contact_id ? (
          <div className="flex items-center gap-3">
            <Link
              to={`/contacts/${entity.linked_contact_id}`}
              className="text-primary hover:underline"
            >
              {entity.linked_contact_name ?? entity.linked_contact_id}
            </Link>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs text-destructive hover:text-destructive"
              onClick={handleUnlink}
              disabled={unlinkContact.isPending}
            >
              <Trash2 className="mr-1 h-3 w-3" />
              Unlink
            </Button>
          </div>
        ) : linking ? (
          <div className="space-y-2">
            <Input
              placeholder="Search contacts..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              autoFocus
              className="h-8 text-sm"
            />
            {contacts.length > 0 ? (
              <div className="max-h-48 overflow-y-auto rounded border">
                {contacts.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    className="flex w-full items-center gap-2 px-3 py-1.5 text-sm
                      hover:bg-muted text-left"
                    onClick={() => handleLink(c.id)}
                    disabled={setLinkedContact.isPending}
                  >
                    <span className="font-medium">{c.full_name}</span>
                    {c.email && (
                      <span className="text-muted-foreground text-xs">{c.email}</span>
                    )}
                  </button>
                ))}
              </div>
            ) : search ? (
              <p className="text-muted-foreground text-xs py-2">No contacts found.</p>
            ) : null}
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs"
              onClick={() => { setLinking(false); setSearch(""); }}
            >
              Cancel
            </Button>
          </div>
        ) : (
          <div className="flex items-center gap-3">
            <p className="text-muted-foreground text-sm">No linked contact.</p>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs text-muted-foreground"
              onClick={() => setLinking(true)}
            >
              <Plus className="mr-1 h-3 w-3" />
              Link contact
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Profile snapshot — birthday, place, work, family, upcoming dates
// ---------------------------------------------------------------------------

const _PLACE_PREDICATES = new Set([
  "lives_in",
  "home_address",
  "address",
  "from",
  "born_in",
]);

const _WORK_PREDICATES = new Set(["works_at", "employer"]);

const _ROLE_PREDICATES = new Set(["role", "title", "job_title"]);

const _FAMILY_PREDICATES = new Set([
  "married_to",
  "partner",
  "spouse",
  "parent_of",
  "child_of",
  "sibling_of",
]);

interface FamilyEntry {
  label: string;
  name: string;
  entityId: string | null;
}

function _firstActiveFact(facts: Fact[], predicates: Set<string>): Fact | null {
  for (const f of facts) {
    if (f.validity === "active" && predicates.has(f.predicate)) return f;
  }
  return null;
}

function _ageOnNextBirthday(birthYear: number, occurrence: Date): number {
  return occurrence.getFullYear() - birthYear;
}

function _formatMonthDay(month: number, day: number): string {
  // Use a stable date so locale-formatting is consistent regardless of year.
  const sample = new Date(2000, month - 1, day);
  return format(sample, "MMM d");
}

function _formatCountdown(target: Date, now: Date): string {
  const diffMs = target.getTime() - now.getTime();
  const days = Math.round(diffMs / (24 * 60 * 60 * 1000));
  if (days <= 0) return "today";
  if (days === 1) return "tomorrow";
  if (days < 30) return `in ${days} days`;
  return `in ${formatDistanceToNow(target)}`;
}

function _familyFromFacts(
  facts: Fact[],
  entityId: string,
): { incoming: FamilyEntry[]; outgoing: FamilyEntry[] } {
  const incoming: FamilyEntry[] = [];
  const outgoing: FamilyEntry[] = [];
  for (const f of facts) {
    if (f.validity !== "active") continue;
    if (!_FAMILY_PREDICATES.has(f.predicate)) continue;
    const isIncoming =
      f.object_entity_id === entityId && f.entity_id !== entityId;
    if (isIncoming) {
      // This entity is the object — invert the predicate label conceptually
      // for display.  Don't try to invert parent/child here; just label as
      // "<name> <predicate> this".
      const inversion: Record<string, string> = {
        parent_of: "Child of",
        child_of: "Parent of",
        married_to: "Married to",
        partner: "Partner of",
        spouse: "Spouse of",
        sibling_of: "Sibling of",
      };
      incoming.push({
        label: inversion[f.predicate] ?? f.predicate.replaceAll("_", " "),
        name: f.entity_name ?? f.subject,
        entityId: f.entity_id,
      });
    } else {
      const labelMap: Record<string, string> = {
        parent_of: "Parent of",
        child_of: "Child of",
        married_to: "Married to",
        partner: "Partner",
        spouse: "Spouse",
        sibling_of: "Sibling of",
      };
      outgoing.push({
        label: labelMap[f.predicate] ?? f.predicate.replaceAll("_", " "),
        name: f.object_entity_name ?? f.content,
        entityId: f.object_entity_id,
      });
    }
  }
  return { incoming, outgoing };
}

function ProfileSnapshot({
  entityId,
  facts,
}: {
  entityId: string;
  facts: Fact[];
}) {
  const { data: dates, isLoading: datesLoading } = useEntityDates(entityId);
  const [today] = useState(() => {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d;
  });

  const birthday = useMemo(
    () => (dates ?? []).find((d) => d.label.toLowerCase() === "birthday") ?? null,
    [dates],
  );

  const otherUpcoming = useMemo(() => {
    const list = (dates ?? []).filter(
      (d) => d.label.toLowerCase() !== "birthday",
    );
    // Soft cap to the next two non-birthday dates.
    return list.slice(0, 2);
  }, [dates]);

  const placeFact = useMemo(() => _firstActiveFact(facts, _PLACE_PREDICATES), [facts]);
  const workFact = useMemo(() => _firstActiveFact(facts, _WORK_PREDICATES), [facts]);
  const roleFact = useMemo(() => _firstActiveFact(facts, _ROLE_PREDICATES), [facts]);
  const family = useMemo(() => _familyFromFacts(facts, entityId), [facts, entityId]);

  const hasBirthday = !!birthday;
  const hasPlace = !!placeFact;
  const hasWork = !!workFact || !!roleFact;
  const hasFamily =
    family.outgoing.length > 0 || family.incoming.length > 0;
  const hasUpcoming = otherUpcoming.length > 0;

  if (
    !hasBirthday &&
    !hasPlace &&
    !hasWork &&
    !hasFamily &&
    !hasUpcoming &&
    !datesLoading
  ) {
    return null;
  }

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">Profile</h2>
      <dl className="divide-y divide-border border-y">
        {hasBirthday && (
          <ProfileRow label="Birthday">
            <BirthdayValue date={birthday} today={today} />
          </ProfileRow>
        )}
        {hasPlace && placeFact && (
          <ProfileRow label="Lives in">
            <span>{placeFact.content}</span>
          </ProfileRow>
        )}
        {hasWork && (
          <ProfileRow label="Works at">
            <WorkValue work={workFact} role={roleFact} />
          </ProfileRow>
        )}
        {hasFamily && (
          <ProfileRow label="Family">
            <FamilyValue family={family} />
          </ProfileRow>
        )}
        {hasUpcoming && (
          <ProfileRow label="Upcoming">
            <UpcomingValue dates={otherUpcoming} today={today} />
          </ProfileRow>
        )}
      </dl>
    </section>
  );
}

function ProfileRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[8rem_1fr] items-baseline gap-3 py-2.5">
      <dt className="text-muted-foreground text-xs uppercase tracking-wide">
        {label}
      </dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

function BirthdayValue({
  date,
  today,
}: {
  date: EntityImportantDate;
  today: Date;
}) {
  const occurrence = new Date(date.upcoming_date);
  const monthDay = _formatMonthDay(date.month, date.day);
  const countdown = _formatCountdown(occurrence, today);
  if (date.year) {
    const age = _ageOnNextBirthday(date.year, occurrence);
    return (
      <span>
        {monthDay}
        <span className="text-muted-foreground"> · turns {age} {countdown}</span>
      </span>
    );
  }
  return (
    <span>
      {monthDay}
      <span className="text-muted-foreground"> · {countdown}</span>
    </span>
  );
}

function WorkValue({
  work,
  role,
}: {
  work: Fact | null;
  role: Fact | null;
}) {
  // Prefer metadata.role on the works_at fact when present; fall back to a
  // standalone role/title fact.
  const inlineRole =
    work && typeof work.metadata?.role === "string"
      ? (work.metadata.role as string)
      : null;
  const fallbackRole = role?.content ?? null;
  const roleText = inlineRole ?? fallbackRole;

  if (!work) {
    return <span>{roleText ?? "—"}</span>;
  }

  const employerNode = work.object_entity_id ? (
    <Link
      to={`/entities/${work.object_entity_id}`}
      className="text-primary hover:underline"
    >
      {work.object_entity_name ?? work.content}
    </Link>
  ) : (
    <span>{work.content}</span>
  );

  return (
    <span>
      {employerNode}
      {roleText && (
        <span className="text-muted-foreground">, {roleText}</span>
      )}
    </span>
  );
}

function FamilyValue({
  family,
}: {
  family: { incoming: FamilyEntry[]; outgoing: FamilyEntry[] };
}) {
  // Group by relation label so multiple parents/children collapse into a
  // single row with comma-separated names.
  const groups = new Map<string, FamilyEntry[]>();
  for (const e of [...family.outgoing, ...family.incoming]) {
    const list = groups.get(e.label) ?? [];
    list.push(e);
    groups.set(e.label, list);
  }
  return (
    <ul className="space-y-1">
      {Array.from(groups.entries()).map(([label, entries]) => (
        <li key={label}>
          <span className="text-muted-foreground text-xs">{label}:</span>{" "}
          {entries.map((e, i) => (
            <span key={`${label}-${i}`}>
              {i > 0 && ", "}
              {e.entityId ? (
                <Link
                  to={`/entities/${e.entityId}`}
                  className="text-primary hover:underline"
                >
                  {e.name}
                </Link>
              ) : (
                <span>{e.name}</span>
              )}
            </span>
          ))}
        </li>
      ))}
    </ul>
  );
}

function UpcomingValue({
  dates,
  today,
}: {
  dates: EntityImportantDate[];
  today: Date;
}) {
  return (
    <ul className="space-y-1">
      {dates.map((d) => {
        const occurrence = new Date(d.upcoming_date);
        return (
          <li key={`${d.contact_id}-${d.label}-${d.month}-${d.day}`}>
            <span className="capitalize">{d.label}</span>
            <span className="text-muted-foreground">
              {" "}
              · {_formatMonthDay(d.month, d.day)} ({_formatCountdown(occurrence, today)})
            </span>
          </li>
        );
      })}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Activity timeline — single feed with filter pills, replaces the tabbed view
// ---------------------------------------------------------------------------

type TimelineFilter = "all" | "interaction" | "note" | "gift" | "loan" | "life_event";

const _TIMELINE_FILTERS: { id: TimelineFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "interaction", label: "Interactions" },
  { id: "note", label: "Notes" },
  { id: "gift", label: "Gifts" },
  { id: "loan", label: "Loans" },
  { id: "life_event", label: "Life events" },
];

function timelineKindGlyph(kind: string): string {
  switch (kind) {
    case "interaction":
      return "·";
    case "note":
      return "✎";
    case "gift":
      return "◇";
    case "loan":
      return "↻";
    case "life_event":
      return "★";
    case "dunbar_tier_override":
      return "○";
    default:
      return "•";
  }
}

function ActivityTimeline({ entityId }: { entityId: string }) {
  const { data: items, isLoading } = useEntityTimeline(entityId);
  const [filter, setFilter] = useState<TimelineFilter>("all");

  const counts = useMemo(() => {
    const acc: Record<TimelineFilter, number> = {
      all: items?.length ?? 0,
      interaction: 0,
      note: 0,
      gift: 0,
      loan: 0,
      life_event: 0,
    };
    for (const it of items ?? []) {
      if (it.kind in acc) {
        acc[it.kind as TimelineFilter] += 1;
      }
    }
    return acc;
  }, [items]);

  const filtered = useMemo(() => {
    if (!items) return [];
    if (filter === "all") return items;
    return items.filter((it) => it.kind === filter);
  }, [items, filter]);

  return (
    <section className="space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-lg font-semibold">Activity</h2>
        <span className="text-muted-foreground text-xs">
          {items ? `${items.length} entries` : ""}
        </span>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {_TIMELINE_FILTERS.map((f) => {
          const active = f.id === filter;
          const count = counts[f.id];
          const disabled = count === 0 && f.id !== "all";
          return (
            <button
              key={f.id}
              type="button"
              onClick={() => setFilter(f.id)}
              disabled={disabled}
              className={
                "rounded-full border px-3 py-1 text-xs transition-colors " +
                (active
                  ? "border-foreground bg-foreground text-background"
                  : disabled
                    ? "border-border text-muted-foreground/60 cursor-not-allowed"
                    : "border-border text-muted-foreground hover:text-foreground hover:border-foreground")
              }
            >
              {f.label}
              {count > 0 && (
                <span className={"ml-1.5 tabular-nums " + (active ? "" : "text-muted-foreground")}>
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {isLoading ? (
        <div className="space-y-2 py-2">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <p className="text-muted-foreground py-8 text-center text-sm">
          {filter === "all"
            ? "No activity recorded yet."
            : `No ${_TIMELINE_FILTERS.find((f) => f.id === filter)?.label.toLowerCase()} yet.`}
        </p>
      ) : (
        <ul className="divide-y divide-border border-y">
          {filtered.map((item) => (
            <TimelineRow key={item.id} item={item} />
          ))}
        </ul>
      )}
    </section>
  );
}

function TimelineRow({ item }: { item: EntityTimelineItem }) {
  const date = item.valid_at ? new Date(item.valid_at) : null;
  const subtitle = item.predicate.startsWith("interaction_")
    ? item.predicate.slice("interaction_".length).replaceAll("_", " ")
    : item.predicate.replaceAll("_", " ");

  return (
    <li className="flex items-start gap-3 py-2.5">
      <span
        aria-hidden
        className="text-muted-foreground mt-0.5 w-4 shrink-0 text-center text-sm"
        title={item.kind}
      >
        {timelineKindGlyph(item.kind)}
      </span>
      <div className="min-w-0 flex-1">
        {item.content && (
          <p className="text-sm leading-snug">{item.content}</p>
        )}
        <p className="text-muted-foreground mt-0.5 text-xs capitalize">
          {subtitle}
        </p>
      </div>
      <span className="text-muted-foreground shrink-0 text-xs tabular-nums">
        {date ? <Time value={date} mode="relative" /> : "—"}
      </span>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Gifts and loans — structured panels, only render when non-empty
// ---------------------------------------------------------------------------

function GiftsPanel({ entityId }: { entityId: string }) {
  const { data: gifts, isLoading } = useEntityGifts(entityId);
  if (isLoading || !gifts || gifts.length === 0) return null;

  return (
    <section className="space-y-2">
      <div className="flex items-baseline gap-3">
        <h3 className="text-sm font-semibold uppercase tracking-wide">
          Gifts
        </h3>
        <span className="text-muted-foreground text-xs">{gifts.length}</span>
      </div>
      <ul className="space-y-1.5">
        {gifts.map((gift) => (
          <li
            key={gift.id}
            className="flex items-baseline gap-3 text-sm"
          >
            <span className="flex-1 truncate">{gift.description ?? "Unnamed gift"}</span>
            {gift.occasion && (
              <span className="text-muted-foreground text-xs">{gift.occasion}</span>
            )}
            {gift.status && (
              <Badge variant="outline" className="text-[10px] capitalize">
                {gift.status}
              </Badge>
            )}
            {gift.created_at && (
              <span className="text-muted-foreground text-xs tabular-nums">
                <Time value={gift.created_at} mode="absolute" precision="day" />
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function LoansPanel({ entityId }: { entityId: string }) {
  const { data: loans, isLoading } = useEntityLoans(entityId);
  if (isLoading || !loans || loans.length === 0) return null;

  return (
    <section className="space-y-2">
      <div className="flex items-baseline gap-3">
        <h3 className="text-sm font-semibold uppercase tracking-wide">
          Loans
        </h3>
        <span className="text-muted-foreground text-xs">{loans.length}</span>
      </div>
      <ul className="space-y-1.5">
        {loans.map((loan) => {
          const settled = loan.settled === "true";
          return (
            <li
              key={loan.id}
              className="flex items-baseline gap-3 text-sm"
            >
              <span className="flex-1 truncate">
                {loan.description ?? "Unspecified loan"}
              </span>
              {loan.direction && (
                <span className="text-muted-foreground text-xs capitalize">
                  {loan.direction}
                </span>
              )}
              {loan.amount_cents && (
                <span className="tabular-nums text-xs">
                  {loan.currency ?? ""} {loan.amount_cents}
                </span>
              )}
              <Badge
                variant={settled ? "outline" : "secondary"}
                className="text-[10px]"
              >
                {settled ? "settled" : "active"}
              </Badge>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Message threads — grouped by channel + thread
// ---------------------------------------------------------------------------

function channelLabel(channel: string | null): string {
  if (!channel) return "Unknown channel";
  return channel.charAt(0).toUpperCase() + channel.slice(1);
}

function MessageThreadsSection({ entityId }: { entityId: string }) {
  const { data: threads, isLoading } = useEntityMessageThreads(entityId);
  if (isLoading) return null;
  if (!threads || threads.length === 0) return null;

  return (
    <section className="space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-lg font-semibold">Message threads</h2>
        <span className="text-muted-foreground text-xs">
          {threads.length}
        </span>
      </div>
      <ul className="divide-y divide-border border-y">
        {threads.map((t) => (
          <MessageThreadRow key={`${t.source_channel}:${t.thread_identity}`} thread={t} />
        ))}
      </ul>
    </section>
  );
}

function MessageThreadRow({ thread }: { thread: MessageThreadSummary }) {
  const date = thread.last_received_at
    ? new Date(thread.last_received_at)
    : null;
  return (
    <li className="flex items-start gap-3 py-2.5">
      <Badge variant="outline" className="mt-0.5 shrink-0 text-[10px]">
        {channelLabel(thread.source_channel)}
      </Badge>
      <div className="min-w-0 flex-1">
        {thread.last_snippet && (
          <p className="text-sm leading-snug line-clamp-2">
            {thread.last_snippet}
          </p>
        )}
        <p className="text-muted-foreground mt-0.5 text-xs">
          {thread.message_count} message
          {thread.message_count === 1 ? "" : "s"}
          {thread.last_direction && ` · last ${thread.last_direction}`}
          {thread.thread_identity && ` · thread ${thread.thread_identity}`}
        </p>
      </div>
      <span className="text-muted-foreground shrink-0 text-xs tabular-nums">
        {date ? <Time value={date} mode="relative" /> : ""}
      </span>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Linked contacts (relationship butler) — surfaces other contacts on this entity
// ---------------------------------------------------------------------------

function LinkedContactsList({ entityId }: { entityId: string }) {
  const { data: contacts, isLoading } = useEntityLinkedContacts(entityId);
  if (isLoading || !contacts || contacts.length === 0) return null;

  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold uppercase tracking-wide">
        Linked contacts
      </h3>
      <ul className="space-y-1.5">
        {contacts.map((contact: LinkedContactSummary) => (
          <li key={contact.id} className="flex items-baseline gap-3 text-sm">
            <Link
              to={`/contacts/${contact.id}`}
              className="text-primary truncate font-medium hover:underline"
            >
              {contact.full_name}
            </Link>
            <span className="text-muted-foreground flex flex-1 gap-3 text-xs">
              {contact.email && <span className="truncate">{contact.email}</span>}
              {contact.phone && <span className="truncate">{contact.phone}</span>}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Facts section — grouped, lighter than the previous full-table layout
// ---------------------------------------------------------------------------

function _factGroupForPredicate(predicate: string): string {
  if (predicate.startsWith("interaction_")) return "Interactions";
  if (predicate.startsWith("preference_") || predicate === "preference") {
    return "Preferences";
  }
  if (
    predicate === "works_at" ||
    predicate === "role" ||
    predicate === "title" ||
    predicate.startsWith("work_")
  ) {
    return "Work";
  }
  if (
    predicate === "lives_in" ||
    predicate === "born_in" ||
    predicate === "from"
  ) {
    return "Place";
  }
  if (
    predicate === "married_to" ||
    predicate === "parent_of" ||
    predicate === "child_of" ||
    predicate === "sibling_of" ||
    predicate === "friend_of" ||
    predicate.startsWith("relationship_")
  ) {
    return "Relationships";
  }
  if (predicate === "contact_note" || predicate === "note") return "Notes";
  if (predicate === "life_event") return "Life events";
  if (predicate === "gift") return "Gifts";
  if (predicate === "loan") return "Loans";
  return "Other";
}

const _FACT_GROUP_ORDER = [
  "Relationships",
  "Work",
  "Place",
  "Preferences",
  "Life events",
  "Notes",
  "Interactions",
  "Gifts",
  "Loans",
  "Other",
];

function FactsSection({
  entityId,
  facts,
  total,
  hasMore,
  isFetching,
  onLoadMore,
}: {
  entityId: string;
  facts: Fact[];
  total: number;
  hasMore: boolean;
  isFetching: boolean;
  onLoadMore: () => void;
}) {
  const grouped = useMemo(() => {
    const map = new Map<string, Fact[]>();
    for (const fact of facts) {
      const group = _factGroupForPredicate(fact.predicate);
      const list = map.get(group) ?? [];
      list.push(fact);
      map.set(group, list);
    }
    return _FACT_GROUP_ORDER
      .map((g) => [g, map.get(g) ?? []] as const)
      .filter(([, list]) => list.length > 0);
  }, [facts]);

  return (
    <section className="space-y-4">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-lg font-semibold">Facts</h2>
        <span className="text-muted-foreground text-xs">
          {facts.length} of {total}
        </span>
      </div>

      {facts.length === 0 ? (
        <p className="text-muted-foreground py-6 text-center text-sm">
          No facts linked to this entity.
        </p>
      ) : (
        <div className="space-y-5">
          {grouped.map(([group, list]) => (
            <div key={group} className="space-y-1.5">
              <h3 className="text-muted-foreground text-xs font-medium uppercase tracking-wide">
                {group}
              </h3>
              <ul className="divide-y divide-border border-y">
                {list.map((fact) => (
                  <FactRow key={fact.id} fact={fact} entityId={entityId} />
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      {hasMore && (
        <div className="flex justify-center pt-1">
          <Button
            variant="outline"
            size="sm"
            onClick={onLoadMore}
            disabled={isFetching}
          >
            {isFetching ? "Loading..." : "Load more facts"}
          </Button>
        </div>
      )}
    </section>
  );
}

function FactRow({ fact, entityId }: { fact: Fact; entityId: string }) {
  const isIncoming =
    fact.object_entity_id === entityId && fact.entity_id !== entityId;
  const created = new Date(fact.created_at);

  return (
    <li className="grid grid-cols-[auto_1fr_auto] items-baseline gap-3 py-2 text-sm">
      <span className="text-muted-foreground text-xs capitalize">
        {fact.predicate.replaceAll("_", " ")}
      </span>
      <span className="min-w-0 truncate">
        {isIncoming ? (
          <>
            <Link
              to={`/entities/${fact.entity_id}`}
              className="text-primary hover:underline"
            >
              {fact.entity_name ?? fact.subject}
            </Link>
            <span className="text-muted-foreground"> → this entity</span>
          </>
        ) : fact.object_entity_id ? (
          <Link
            to={`/entities/${fact.object_entity_id}`}
            className="text-primary hover:underline"
          >
            {fact.object_entity_name ?? fact.content}
          </Link>
        ) : (
          fact.content
        )}
      </span>
      <span className="text-muted-foreground shrink-0 text-xs tabular-nums">
        <Time value={created} mode="absolute" precision="day" />
        {fact.session_id && (
          <Link
            to={sessionDetailHref(fact.session_id, fact.source_butler)}
            className="text-primary ml-2 hover:underline"
            title={fact.session_id}
          >
            session
          </Link>
        )}
      </span>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Entity gloss — canned voice gloss for the Editorial layout
//
// Derives (tier, state, category) from the entity data and renders the
// appropriate gloss string as a prose paragraph.
//
// Mapping:
//   tier     → entity.dunbar_tier (must be a valid DunbarTier literal)
//   state    → "unidentified" when entity.unidentified is true, else "healthy"
//              (EntityDetail does not expose stale/duplicate-candidate state —
//              those come from the relationship butler's curation queue)
//   category → entity.entity_type cast to EntityType when it matches a known value
//
// If any dimension cannot be resolved to a valid enum member, the gloss is
// silently skipped (returns null). No crash, no placeholder text.
// ---------------------------------------------------------------------------

const _VALID_DUNBAR_TIERS = new Set<number>(DUNBAR_TIER_VALUES);
const _VALID_ENTITY_TYPES = new Set<string>(ENTITY_TYPE_VALUES);

interface GlossTuple {
  tier: DunbarTier;
  state: EntityState;
  category: EntityType;
}

/**
 * Derive the (tier, state, category) tuple from EntityDetail fields.
 * Returns null if any dimension cannot be mapped to a valid enum value.
 */
function _deriveGlossTuple(
  dunbarTier: number | null,
  unidentified: boolean,
  entityType: string,
): GlossTuple | null {
  if (dunbarTier == null || !_VALID_DUNBAR_TIERS.has(dunbarTier)) return null;
  if (!_VALID_ENTITY_TYPES.has(entityType)) return null;
  return {
    tier: dunbarTier as DunbarTier,
    state: unidentified ? "unidentified" : "healthy",
    category: entityType as EntityType,
  };
}

/**
 * Renders the canned voice gloss for the Editorial layout.
 * Returns null when the gloss cannot be derived (no tier, unknown type, etc.).
 *
 * Brief §4 anti-temptation: this is a CANNED STRING lookup, not an LLM call.
 */
function EntityGlossBlock({
  dunbarTier,
  unidentified,
  entityType,
}: {
  dunbarTier: number | null;
  unidentified: boolean;
  entityType: string;
}) {
  const tuple = _deriveGlossTuple(dunbarTier, unidentified, entityType);
  if (!tuple) return null;
  const gloss = getEntityGloss(tuple);
  return (
    <p
      data-testid="entity-gloss"
      className="text-muted-foreground text-sm italic leading-relaxed"
    >
      {gloss}
    </p>
  );
}

// ---------------------------------------------------------------------------
// EntityDetailModeToggle — icon button in the Page actions slot
// ---------------------------------------------------------------------------

function EntityDetailModeToggle({
  mode,
  onModeChange,
}: {
  mode: EntityDetailMode;
  onModeChange: (mode: EntityDetailMode) => void;
}) {
  const nextMode: EntityDetailMode = mode === "editorial" ? "workbench" : "editorial";
  return (
    <button
      type="button"
      role="switch"
      aria-checked={mode === "workbench"}
      aria-label={`Switch to ${nextMode} mode`}
      data-testid="entity-mode-toggle"
      onClick={() => onModeChange(nextMode)}
      title={`${mode === "editorial" ? "Editorial" : "Workbench"} mode — click to switch to ${nextMode}`}
      className="inline-flex items-center gap-1.5 rounded border border-border px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:border-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
    >
      <Layers className="h-3.5 w-3.5" aria-hidden />
      {mode === "editorial" ? "Editorial" : "Workbench"}
    </button>
  );
}

// ---------------------------------------------------------------------------
// EntityDetailPage
// ---------------------------------------------------------------------------

export default function EntityDetailPage() {
  const { entityId } = useParams<{ entityId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();

  // ---------------------------------------------------------------------------
  // Mode — editorial vs workbench
  // Initialised from ?mode= URL param (per-link override) or localStorage.
  // Defaults to "editorial" when unset or invalid.
  // ---------------------------------------------------------------------------
  const [mode, setModeState] = useState<EntityDetailMode>(() => {
    const urlMode = searchParams.get(ENTITY_MODE_PARAM);
    if (urlMode === "editorial" || urlMode === "workbench") return urlMode;
    return readPersistedEntityMode();
  });

  const setMode = useCallback(
    (next: EntityDetailMode) => {
      setModeState(next);
      persistEntityMode(next);
      // Update the URL param so the current view is deep-linkable.
      setSearchParams(
        (prev) => {
          const updated = new URLSearchParams(prev);
          updated.set(ENTITY_MODE_PARAM, next);
          return updated;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const [factsLimit, setFactsLimit] = useState(FACTS_INITIAL_LIMIT);
  const { data, isLoading, isFetching, error } = useEntity(entityId, {
    facts_limit: factsLimit,
  });
  const entity = data?.data;
  const updateEntity = useUpdateEntity();
  const promoteEntity = usePromoteEntity();
  const forgetEntity = useForgetRelationshipEntity();

  const [forgetDialogOpen, setForgetDialogOpen] = useState(false);
  const [forgetError, setForgetError] = useState<string | null>(null);

  const handleForgetConfirm = async () => {
    if (!entityId) return;
    setForgetError(null);
    try {
      await forgetEntity.mutateAsync(entityId);
      toast.success(`Forgot ${entity?.canonical_name ?? "entity"}`);
      setForgetDialogOpen(false);
      void navigate("/entities");
    } catch (err) {
      setForgetError(err instanceof Error ? err.message : "Failed to forget entity");
    }
  };

  const [editingName, setEditingName] = useState(false);
  const [draftName, setDraftName] = useState("");

  const handleStartEditName = () => {
    setDraftName(entity?.canonical_name ?? "");
    setEditingName(true);
  };

  const handleSaveName = () => {
    if (!entityId || !draftName.trim()) return;
    updateEntity.mutate(
      { entityId, request: { canonical_name: draftName.trim() } },
      {
        onSuccess: () => {
          setEditingName(false);
          toast.success("Entity name updated");
        },
        onError: (err) => toast.error(`Failed to update name: ${(err as Error).message}`),
      },
    );
  };

  const [addingAlias, setAddingAlias] = useState(false);
  const [draftAlias, setDraftAlias] = useState("");
  const [addingRole, setAddingRole] = useState(false);
  const [draftRole, setDraftRole] = useState("");

  const handleRemoveAlias = (alias: string) => {
    if (!entityId || !entity) return;
    const updated = entity.aliases.filter((a) => a !== alias);
    updateEntity.mutate(
      { entityId, request: { aliases: updated } },
      {
        onSuccess: () => toast.success(`Removed alias "${alias}"`),
        onError: (err) => toast.error(`Failed to remove alias: ${(err as Error).message}`),
      },
    );
  };

  const handleAddAlias = () => {
    const trimmed = draftAlias.trim();
    if (!entityId || !entity || !trimmed) return;
    if (entity.aliases.includes(trimmed)) {
      toast.error("Alias already exists.");
      return;
    }
    const updated = [...entity.aliases, trimmed];
    updateEntity.mutate(
      { entityId, request: { aliases: updated } },
      {
        onSuccess: () => {
          toast.success(`Added alias "${trimmed}"`);
          setDraftAlias("");
          setAddingAlias(false);
        },
        onError: (err) => toast.error(`Failed to add alias: ${(err as Error).message}`),
      },
    );
  };

  const handleRemoveRole = (role: string) => {
    if (!entityId || !entity) return;
    const updated = (entity.roles ?? []).filter((r) => r !== role);
    updateEntity.mutate(
      { entityId, request: { roles: updated } },
      {
        onSuccess: () => toast.success(`Removed role "${role}"`),
        onError: (err) => toast.error(`Failed to remove role: ${(err as Error).message}`),
      },
    );
  };

  const handleAddRole = () => {
    const trimmed = draftRole.trim().toLowerCase();
    if (!entityId || !entity || !trimmed) return;
    if ((entity.roles ?? []).includes(trimmed)) {
      toast.error("Role already exists.");
      return;
    }
    const updated = [...(entity.roles ?? []), trimmed];
    updateEntity.mutate(
      { entityId, request: { roles: updated } },
      {
        onSuccess: () => {
          toast.success(`Added role "${trimmed}"`);
          setDraftRole("");
          setAddingRole(false);
        },
        onError: (err) => toast.error(`Failed to add role: ${(err as Error).message}`),
      },
    );
  };

  const isOwner = entity?.roles?.includes("owner") ?? false;
  const ownerNeedsSetup = isOwner && entity ? !entity.linked_contact_id : false;
  const dunbarPinned =
    entity?.recent_facts?.some(
      (f) => f.predicate === "dunbar_tier_override" && f.validity === "active",
    ) ?? false;

  const breadcrumbs = useMemo(
    () => [
      { label: "Home", href: "/" },
      { label: "Entities", href: "/entities" },
      { label: entity?.canonical_name ?? entityId ?? "Entity" },
    ],
    [entity?.canonical_name, entityId],
  );

  const pageArchetype = mode === "editorial" ? "detail" : "overview";
  const pageActions = (
    <div className="flex items-center gap-2">
      <button
        type="button"
        data-testid="forget-entity-button"
        aria-label="Forget this entity"
        title="Forget this entity — irreversible hard delete"
        onClick={() => {
          setForgetError(null);
          setForgetDialogOpen(true);
        }}
        className="inline-flex items-center gap-1.5 rounded border border-border px-2.5 py-1 text-xs font-medium text-destructive transition-colors hover:border-destructive hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      >
        <Trash2 className="h-3.5 w-3.5" aria-hidden />
        Forget
      </button>
      <EntityDetailModeToggle mode={mode} onModeChange={setMode} />
    </div>
  );

  return (
    <>
    <Page
      archetype={pageArchetype}
      title={entity?.canonical_name ?? entityId ?? "Entity"}
      loading={isLoading}
      error={error ?? null}
      breadcrumbs={breadcrumbs}
      actions={pageActions}
    >
      {entity && entityId && (
        <>
          {/* Identity hero — name, type, badges, aliases, roles */}
          <section className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              {editingName ? (
                <div className="flex items-center gap-2">
                  <Input
                    value={draftName}
                    onChange={(e) => setDraftName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleSaveName();
                      if (e.key === "Escape") setEditingName(false);
                    }}
                    className="h-10 w-72 text-2xl font-semibold"
                    autoFocus
                  />
                  <Button
                    size="icon"
                    variant="ghost"
                    onClick={handleSaveName}
                    disabled={updateEntity.isPending}
                  >
                    <Check className="h-4 w-4" />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    onClick={() => setEditingName(false)}
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  <h1 className="text-2xl font-semibold leading-tight">
                    {entity.canonical_name}
                  </h1>
                  <Button
                    size="icon"
                    variant="ghost"
                    className="h-7 w-7"
                    onClick={handleStartEditName}
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </Button>
                </div>
              )}
              <Select
                value={entity.entity_type}
                onValueChange={(val) => {
                  if (!entityId || val === entity.entity_type) return;
                  updateEntity.mutate(
                    { entityId, request: { entity_type: val } },
                    {
                      onSuccess: () => toast.success(`Type changed to ${val}`),
                      onError: (err) =>
                        toast.error(`Failed: ${(err as Error).message}`),
                    },
                  );
                }}
              >
                <SelectTrigger className="h-7 w-auto gap-1 rounded-full border-none bg-secondary px-2.5 py-0.5 text-xs font-medium">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {["person", "organization", "place", "other"].map((t) => (
                    <SelectItem key={t} value={t} className="text-xs capitalize">
                      {t}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {isOwner && (
                <Badge variant="outline" className="text-xs">
                  Owner
                </Badge>
              )}
              {entity.unidentified && (
                <Badge variant="outline" className="text-xs">
                  Unidentified
                </Badge>
              )}
              {entity.unidentified && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 text-xs"
                  disabled={promoteEntity.isPending}
                  onClick={() => {
                    if (!entityId) return;
                    promoteEntity.mutate(entityId, {
                      onSuccess: () => toast.success("Entity marked as confirmed."),
                      onError: (err) =>
                        toast.error(
                          `Failed to confirm: ${err instanceof Error ? err.message : "Unknown error"}`,
                        ),
                    });
                  }}
                >
                  <Check className="mr-1 h-3.5 w-3.5" />
                  {promoteEntity.isPending ? "Confirming..." : "Mark confirmed"}
                </Button>
              )}
            </div>

            {/* Aliases + roles in one tight row */}
            <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
              <div className="flex items-baseline gap-2">
                <span className="text-muted-foreground text-xs uppercase tracking-wide">
                  Aliases
                </span>
                <div className="flex flex-wrap items-center gap-1.5">
                  {entity.aliases.length === 0 && !addingAlias && (
                    <span className="text-muted-foreground text-xs italic">none</span>
                  )}
                  {entity.aliases.map((alias) => (
                    <Badge key={alias} variant="secondary" className="group/alias">
                      {alias}
                      <button
                        type="button"
                        className="ml-1 opacity-0 transition-opacity group-hover/alias:opacity-100"
                        onClick={() => handleRemoveAlias(alias)}
                        title="Remove alias"
                      >
                        <X className="h-2.5 w-2.5" />
                      </button>
                    </Badge>
                  ))}
                  {addingAlias ? (
                    <div className="flex items-center gap-1">
                      <Input
                        className="h-6 w-32 text-xs"
                        value={draftAlias}
                        onChange={(e) => setDraftAlias(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") handleAddAlias();
                          if (e.key === "Escape") setAddingAlias(false);
                        }}
                        autoFocus
                        placeholder="New alias..."
                      />
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 w-6 p-0"
                        onClick={handleAddAlias}
                      >
                        <Check className="h-3 w-3" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 w-6 p-0"
                        onClick={() => setAddingAlias(false)}
                      >
                        <X className="h-3 w-3" />
                      </Button>
                    </div>
                  ) : (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-muted-foreground h-6 text-xs"
                      onClick={() => setAddingAlias(true)}
                    >
                      <Plus className="mr-0.5 h-3 w-3" />
                      Add
                    </Button>
                  )}
                </div>
              </div>

              <div className="flex items-baseline gap-2">
                <span className="text-muted-foreground text-xs uppercase tracking-wide">
                  Roles
                </span>
                <div className="flex flex-wrap items-center gap-1.5">
                  {(entity.roles ?? []).length === 0 && !addingRole && (
                    <span className="text-muted-foreground text-xs italic">none</span>
                  )}
                  {(entity.roles ?? []).map((role) => (
                    <Badge key={role} variant="outline" className="group/role">
                      {role}
                      <button
                        type="button"
                        className="ml-1 opacity-0 transition-opacity group-hover/role:opacity-100"
                        onClick={() => handleRemoveRole(role)}
                        title="Remove role"
                      >
                        <X className="h-2.5 w-2.5" />
                      </button>
                    </Badge>
                  ))}
                  {addingRole ? (
                    <div className="flex items-center gap-1">
                      <Input
                        className="h-6 w-32 text-xs"
                        value={draftRole}
                        onChange={(e) => setDraftRole(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") handleAddRole();
                          if (e.key === "Escape") setAddingRole(false);
                        }}
                        autoFocus
                        placeholder="New role..."
                      />
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 w-6 p-0"
                        onClick={handleAddRole}
                      >
                        <Check className="h-3 w-3" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 w-6 p-0"
                        onClick={() => setAddingRole(false)}
                      >
                        <X className="h-3 w-3" />
                      </Button>
                    </div>
                  ) : (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-muted-foreground h-6 text-xs"
                      onClick={() => setAddingRole(true)}
                    >
                      <Plus className="mr-0.5 h-3 w-3" />
                      Add
                    </Button>
                  )}
                </div>
              </div>
            </div>

            {/* Pulse strip — closeness + open loops at-a-glance */}
            <PulseStrip
              entityId={entityId}
              dunbarTier={entity.dunbar_tier ?? null}
              isPinned={dunbarPinned}
            />
          </section>

          {/* Entity gloss — Editorial only; canned voice string for this entity's (tier, state, category) */}
          {mode === "editorial" && (
            <EntityGlossBlock
              dunbarTier={entity.dunbar_tier}
              unidentified={entity.unidentified}
              entityType={entity.entity_type}
            />
          )}

          {/* Profile snapshot — birthday, place, work, family, upcoming */}
          <ProfileSnapshot entityId={entityId} facts={entity.recent_facts} />

          {/* Activity timeline — primary content */}
          <ActivityTimeline entityId={entityId} />

          {/* Gifts and loans — structured panels, hidden when empty */}
          <div className="grid gap-6 sm:grid-cols-2">
            <GiftsPanel entityId={entityId} />
            <LoansPanel entityId={entityId} />
          </div>

          {/* Message threads — only when matches exist */}
          <MessageThreadsSection entityId={entityId} />

          {/* Linked contacts — only when contacts exist */}
          <LinkedContactsList entityId={entityId} />

          {/* Facts — grouped by predicate family */}
          <FactsSection
            entityId={entity.id}
            facts={entity.recent_facts}
            total={entity.recent_facts_total}
            hasMore={entity.recent_facts_has_more}
            isFetching={isFetching}
            onLoadMore={() =>
              setFactsLimit((current) => current + FACTS_PAGE_SIZE)
            }
          />

          {/* Practical drawer — collapsed by default, owner setup forces it open */}
          <PracticalDrawer entity={entity} forceOpen={ownerNeedsSetup}>
            <OwnerSetupBanner entity={entity} />
            <LinkedContactSection entityId={entity.id} entity={entity} />

            {/* Credentials moved to the User tab of /secrets — link only. */}
            <div className="rounded-md border bg-muted/30 px-3 py-2 text-sm">
              <span className="text-muted-foreground">
                Credentials and identity-bound secrets are managed in{" "}
              </span>
              <Link to="/secrets" className="text-primary font-medium hover:underline">
                Secrets → User
              </Link>
              <span className="text-muted-foreground">
                . Switch identity there to view this entity's credentials.
              </span>
            </div>

            {isOwner && (
              <TelegramSessionSetup
                entityId={entity.id}
                entries={entity.entity_info ?? []}
              />
            )}
          </PracticalDrawer>
        </>
      )}
    </Page>

    {/* Forget entity confirmation dialog */}
    <AlertDialog
      open={forgetDialogOpen}
      onOpenChange={(open) => {
        if (!open) {
          setForgetDialogOpen(false);
          setForgetError(null);
        }
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Forget this entity?</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to forget{" "}
            <strong>{entity?.canonical_name ?? "this entity"}</strong>? This
            will retract all associated facts and permanently remove the entity
            from your memory graph. This action cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {forgetError && (
          <p className="px-1 text-sm text-destructive" role="alert">
            {forgetError}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={forgetEntity.isPending}>
            Cancel
          </AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={forgetEntity.isPending}
            onClick={(event) => {
              event.preventDefault();
              void handleForgetConfirm();
            }}
          >
            {forgetEntity.isPending ? "Forgetting…" : "Forget this entity"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
    </>
  );
}
