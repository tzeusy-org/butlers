import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import {
  ArrowDown,
  ArrowUp,
  ChevronsUpDown,
  Check,
  Layers,
  Loader2,
  Pencil,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { Time } from "@/components/ui/time";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getEntityGloss, DUNBAR_TIER_VALUES, ENTITY_TYPE_VALUES, CURATION_RAIL_GLOSSES } from "@/lib/entity-glosses";
import type { DunbarTier, EntityState, EntityType, CurationRailAction } from "@/lib/entity-glosses";

import type {
  ContactSummary,
  EntityFact,
  EntityFactStalenessBand,
  EntityFactsValidity,
  EntityInfoEntry,
  EntityTimelineItem,
  Fact,
  MessageThreadSummary,
  NeighbourEntry,
} from "@/api/types";
import {
  getTelegramSessionStatus,
  telegramSendCode,
  telegramVerifyCode,
} from "@/api/index";
import { ActivitySparkline } from "@/components/relationship/ActivitySparkline";
import { ContactChannelCard } from "@/components/relationship/ContactChannelCard";
import { CoreDatesBlock } from "@/components/relationship/CoreDatesBlock";
import { DeltaSinceLastVisitBanner } from "@/components/relationship/DeltaSinceLastVisitBanner";
import { LatestInteractionsBlock } from "@/components/relationship/LatestInteractionsBlock";
import { OwnerSetupBanner } from "@/components/relationship/OwnerSetupBanner";
import { PracticalDrawer } from "@/components/relationship/PracticalDrawer";
import { PulseStrip } from "@/components/relationship/PulseStrip";
import { EntityMark } from "@/components/ui/EntityMark";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Row } from "@/components/ui/Row";
import { Voice } from "@/components/ui/Voice";
import { ProvenanceMarks, StalenessBand } from "@/components/ui/Provenance";
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
import { TierBadge } from "@/components/ui/TierBadge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Page } from "@/components/ui/page";
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
  useArchiveRelationshipEntity,
  useEntityActivityBins,
  useEntityDeltaFacts,
  useEntityFacts,
  useEntityGifts,
  useEntityLoans,
  useEntityMessageThreads,
  useEntityNeighbours,
  useEntityTimeline,
  useRelationshipEntities,
  useRelationshipEntitiesByIds,
  useRelationshipEntityQueue,
  useUpdateEntityDunbarTier,
} from "@/hooks/use-entities";
import { MergeCompareDialog } from "@/components/relationship/MergeCompareDialog";
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
      <section className="space-y-3">
        <Eyebrow as="div">Telegram user session</Eyebrow>
        <Skeleton className="h-8 w-48" />
      </section>
    );
  }

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <Eyebrow as="div">Telegram user session</Eyebrow>
        {status?.ready && (
          <Badge variant="outline" className="text-green-600 border-green-600">Connected</Badge>
        )}
      </div>
      <div className="space-y-3">
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
      </div>
    </section>
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
    <section className="space-y-3">
      <Eyebrow as="div">Linked contact</Eyebrow>
      <div>
        {entity.linked_contact_id ? (
          <div className="flex items-center gap-3">
            <span className="text-sm">
              {entity.linked_contact_name ?? entity.linked_contact_id}
            </span>
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
      </div>
    </section>
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

function _firstActiveFact(
  facts: Fact[],
  predicates: Set<string>,
  entityId: string,
): Fact | null {
  for (const f of facts) {
    if (f.validity !== "active") continue;
    if (!predicates.has(f.predicate)) continue;
    // Place/work/role are directional, subject-owned properties. The detail
    // API returns facts where this entity is the subject OR the object
    // (entity_id = $1 OR object_entity_id = $1), so an object-side fact —
    // e.g. a doctor's "role" fact that references the owner as its object —
    // would otherwise leak into the owner's Profile. Only honor facts where
    // the viewed entity is the SUBJECT.
    const isSubject = f.entity_id === entityId || f.subject === entityId;
    if (!isSubject) continue;
    return f;
  }
  return null;
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
  // Date-kind facts (birthday, anniversaries, upcoming dates) are no longer
  // matched client-side here — they are server-extracted by GET
  // /entities/{id}/core-dates and rendered by <CoreDatesBlock> (bu-xzh76). This
  // section keeps only the non-date profile rows derived from the entity's
  // recent_facts.
  const placeFact = useMemo(
    () => _firstActiveFact(facts, _PLACE_PREDICATES, entityId),
    [facts, entityId],
  );
  const workFact = useMemo(
    () => _firstActiveFact(facts, _WORK_PREDICATES, entityId),
    [facts, entityId],
  );
  const roleFact = useMemo(
    () => _firstActiveFact(facts, _ROLE_PREDICATES, entityId),
    [facts, entityId],
  );
  const family = useMemo(() => _familyFromFacts(facts, entityId), [facts, entityId]);

  const hasPlace = !!placeFact;
  const hasWork = !!workFact || !!roleFact;
  const hasFamily =
    family.outgoing.length > 0 || family.incoming.length > 0;

  if (!hasPlace && !hasWork && !hasFamily) {
    return null;
  }

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">Profile</h2>
      <dl className="divide-y divide-border border-y">
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

/**
 * Index the per-fact provenance (the facts-drill `EntityFact` rows) by
 * predicate, so an editorial fact row can reveal its `src` / `verified` /
 * staleness on demand. The editorial list itself renders from `recent_facts`
 * (which carry no provenance); this is the spec's "facts drill endpoint is the
 * canonical fact-level read for ... Editorial provenance reveals".
 */
function _indexProvenanceByPredicate(
  facts: EntityFact[],
): Map<string, EntityFact[]> {
  const map = new Map<string, EntityFact[]>();
  for (const fact of facts) {
    const list = map.get(fact.predicate) ?? [];
    list.push(fact);
    map.set(fact.predicate, list);
  }
  return map;
}

/**
 * Resolve the provenance row for an editorial fact: match by predicate, then —
 * when a predicate has several rows — narrow by object/content equality. Returns
 * null when no drill row corresponds (no invented provenance).
 */
function _provenanceForFact(
  fact: Fact,
  index: Map<string, EntityFact[]>,
): EntityFact | null {
  const candidates = index.get(fact.predicate);
  if (!candidates || candidates.length === 0) return null;
  if (candidates.length === 1) return candidates[0];
  const target = (fact.content ?? "").trim();
  return (
    candidates.find((c) => (c.object ?? "").trim() === target) ?? candidates[0]
  );
}

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
  // One drill read over both stores feeds every row's on-demand provenance
  // reveal — no per-row fetch.
  const { data: provenanceData } = useEntityFacts(entityId, {
    store: "all",
    limit: 200,
  });
  const provenanceIndex = useMemo(
    () => _indexProvenanceByPredicate(provenanceData?.items ?? []),
    [provenanceData],
  );

  // Delta-since-last-visit highlight (spec: "a highlight treatment on the delta
  // rows"). Shares the banner's cached delta-facts query (same queryKey), so no
  // extra request. The set is empty on a first visit (no mark) — no highlight.
  const { data: deltaData } = useEntityDeltaFacts(entityId);
  const deltaFactIds = useMemo(
    () => new Set((deltaData?.items ?? []).map((d) => d.id)),
    [deltaData],
  );

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
                  <FactRow
                    key={fact.id}
                    fact={fact}
                    entityId={entityId}
                    provenance={_provenanceForFact(fact, provenanceIndex)}
                    isDelta={deltaFactIds.has(fact.id)}
                  />
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

function FactRow({
  fact,
  entityId,
  provenance,
  isDelta = false,
}: {
  fact: Fact;
  entityId: string;
  provenance: EntityFact | null;
  /** True when this fact changed since the owner's last visit (delta highlight). */
  isDelta?: boolean;
}) {
  const isIncoming =
    fact.object_entity_id === entityId && fact.entity_id !== entityId;
  const created = new Date(fact.created_at);
  // On-demand provenance reveal: default row chrome stays clean (spec:
  // "Editorial — on-demand ... the row chrome itself stays clean"). The
  // affordance only shows when there is provenance to reveal.
  const [revealed, setRevealed] = useState(false);
  const hasProvenance = provenance != null;

  return (
    <li
      data-delta={isDelta || undefined}
      data-testid={isDelta ? "delta-fact-row" : undefined}
      // Delta highlight: a 2px left border marks facts changed since the last
      // visit (spec "Delta-since-last-visit": "a highlight treatment on the
      // delta rows"). Matches the design-language focus treatment (left border,
      // no glow); the transparent border keeps unchanged rows from reflowing.
      className={`border-l-2 py-2 pl-2 text-sm ${
        isDelta ? "border-l-[var(--amber)]" : "border-l-transparent"
      }`}
    >
      <div className="grid grid-cols-[auto_1fr_auto] items-baseline gap-3">
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
        <span className="text-muted-foreground flex shrink-0 items-center gap-2 text-xs tabular-nums">
          <Time value={created} mode="absolute" precision="day" />
          {fact.session_id && (
            <Link
              to={sessionDetailHref(fact.session_id, fact.source_butler)}
              className="text-primary hover:underline"
              title={fact.session_id}
            >
              session
            </Link>
          )}
          {hasProvenance && (
            <button
              type="button"
              data-testid={`fact-provenance-toggle-${fact.id}`}
              aria-expanded={revealed}
              aria-label={
                revealed ? "Hide provenance" : "Reveal provenance"
              }
              onClick={() => setRevealed((v) => !v)}
              className="rounded px-1 font-mono text-[10px] leading-none text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              {revealed ? "−" : "i"}
            </button>
          )}
        </span>
      </div>
      {hasProvenance && revealed && (
        <div
          data-testid={`fact-provenance-reveal-${fact.id}`}
          className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1.5 pl-0 text-xs"
        >
          <StalenessBand band={provenance.staleness_band} />
          <ProvenanceMarks src={provenance.src} verified={provenance.verified} />
        </div>
      )}
    </li>
  );
}

// ---------------------------------------------------------------------------
// ProvenanceGrid — Workbench mode dense sortable per-fact grid
//
// Per §6b Amendment 7: Workbench = dense sortable provenance grid with
// real per-fact provenance fields from relationship.entity_facts (bu-mg4dk):
//   - weight       — relational aggregation weight (INT, nullable)
//   - last_observed_at — most-recent observation timestamp (nullable)
//   - object_kind  — 'literal' | 'entity'
//   - src          — authoring butler slug
//
// Note: source_event_id is not yet a column in relationship.entity_facts and
// is tracked as a separate schema addition. Use src for source attribution.
// ---------------------------------------------------------------------------

type ProvenanceSortKey = "predicate" | "weight" | "last_observed_at";
type SortDir = "asc" | "desc";

interface ProvenanceSortState {
  key: ProvenanceSortKey;
  dir: SortDir;
}

const DEFAULT_SORT_DIRECTIONS: Record<ProvenanceSortKey, SortDir> = {
  predicate: "asc",
  weight: "desc",
  last_observed_at: "desc",
};

function _sortEntityFacts(facts: EntityFact[], sort: ProvenanceSortState): EntityFact[] {
  return [...facts].sort((a, b) => {
    let cmp = 0;
    if (sort.key === "predicate") {
      cmp = a.predicate.localeCompare(b.predicate);
    } else if (sort.key === "weight") {
      cmp = (a.weight ?? 0) - (b.weight ?? 0);
    } else if (sort.key === "last_observed_at") {
      const aTs = a.last_observed_at ?? a.created_at;
      const bTs = b.last_observed_at ?? b.created_at;
      cmp = aTs.localeCompare(bTs);
    }
    return sort.dir === "asc" ? cmp : -cmp;
  });
}

function SortHeaderButton({
  label,
  column,
  sort,
  onSort,
}: {
  label: string;
  column: ProvenanceSortKey;
  sort: ProvenanceSortState;
  onSort: (key: ProvenanceSortKey) => void;
}) {
  const active = sort.key === column;
  return (
    <button
      type="button"
      className="inline-flex items-center gap-1 hover:text-foreground transition-colors"
      onClick={() => onSort(column)}
      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
    >
      {label}
      {active ? (
        sort.dir === "asc" ? (
          <ArrowUp className="h-3 w-3" aria-hidden />
        ) : (
          <ArrowDown className="h-3 w-3" aria-hidden />
        )
      ) : (
        <ChevronsUpDown className="h-3 w-3 opacity-40" aria-hidden />
      )}
    </button>
  );
}

const PROVENANCE_FACTS_PAGE_SIZE = 20;

const STALENESS_BADGE_VARIANT: Record<EntityFactStalenessBand, "default" | "secondary" | "outline"> =
  {
    fresh: "default",
    aging: "secondary",
    stale: "outline",
  };

function ProvenanceGrid({
  entityId,
  defaultStoreAll = false,
}: {
  entityId: string;
  /**
   * When true the grid opens showing BOTH stores (identity + narrative). The
   * Workbench three-rail layout passes this so the spec's "dense grid over both
   * stores" renders without an extra click; the Editorial drill defaults to
   * identity-only.
   */
  defaultStoreAll?: boolean;
}) {
  const [sort, setSort] = useState<ProvenanceSortState>({
    key: "last_observed_at",
    dir: "desc",
  });
  // History view: active (default) vs. superseded facts.
  const [validity, setValidity] = useState<EntityFactsValidity>("active");
  // Narrative layering: identity-only (default) vs. all stores.
  const [storeAll, setStoreAll] = useState(defaultStoreAll);
  // Keyset cursor stack: accumulated cursors driving the current visible window.
  // We re-derive the merged page from each loaded cursor (React Query caches per key).
  const [cursors, setCursors] = useState<string[]>([]);

  const store = storeAll ? "all" : "identity";

  // The active page request: the last cursor in the stack (undefined → first page).
  const activeCursor = cursors[cursors.length - 1];
  const { data, isFetching, error } = useEntityFacts(entityId, {
    validity,
    store,
    limit: PROVENANCE_FACTS_PAGE_SIZE,
    cursor: activeCursor,
  });

  // Pages loaded BEFORE the active cursor. The active page's items are merged
  // on top via useMemo so the first render (SSR/tests) shows facts without
  // waiting for an effect to flush.
  const [priorPages, setPriorPages] = useState<EntityFact[]>([]);

  // Reset the cursor stack + accumulation when a filter changes (validity /
  // store / entity). Done during render via the React-recommended "store the
  // previous value in state and adjust on change" pattern — this avoids a
  // cascading effect and re-renders immediately with the reset state.
  const filterKey = `${entityId}|${validity}|${store}`;
  const [prevFilterKey, setPrevFilterKey] = useState(filterKey);
  if (prevFilterKey !== filterKey) {
    setPrevFilterKey(filterKey);
    if (cursors.length > 0) setCursors([]);
    if (priorPages.length > 0) setPriorPages([]);
  }

  const pageItems = useMemo(() => data?.items ?? [], [data?.items]);

  const facts = useMemo(() => {
    if (priorPages.length === 0) return pageItems;
    const seen = new Set(priorPages.map((f) => `${f.store}:${f.id}`));
    return [...priorPages, ...pageItems.filter((f) => !seen.has(`${f.store}:${f.id}`))];
  }, [priorPages, pageItems]);

  const hasMore = data?.has_more ?? false;
  const nextCursor = data?.next_cursor ?? null;

  function handleLoadMore() {
    if (nextCursor == null) return;
    // Fold the currently-visible page into priorPages, then advance the cursor
    // so the next page appends to (rather than replaces) what is on screen.
    setPriorPages(facts);
    setCursors((prev) => [...prev, nextCursor]);
  }

  function handleSort(key: ProvenanceSortKey) {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { key, dir: DEFAULT_SORT_DIRECTIONS[key] },
    );
  }

  const sorted = useMemo(() => _sortEntityFacts(facts, sort), [facts, sort]);

  if (error) {
    return (
      <section className="space-y-3" data-testid="provenance-grid">
        <h2 className="text-lg font-semibold">Provenance</h2>
        <p role="alert" className="text-destructive text-sm py-4">
          {error instanceof Error ? error.message : "Failed to load provenance facts."}
        </p>
      </section>
    );
  }

  return (
    <section className="space-y-3" data-testid="provenance-grid">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-lg font-semibold">Provenance</h2>
        <span className="text-muted-foreground text-xs" data-testid="provenance-count">
          {facts.length} {facts.length === 1 ? "fact" : "facts"} loaded
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-md border border-border" role="group" aria-label="Fact validity">
          <Button
            type="button"
            variant={validity === "active" ? "secondary" : "ghost"}
            size="sm"
            aria-pressed={validity === "active"}
            data-testid="provenance-validity-active"
            onClick={() => setValidity("active")}
          >
            Active
          </Button>
          <Button
            type="button"
            variant={validity === "superseded" ? "secondary" : "ghost"}
            size="sm"
            aria-pressed={validity === "superseded"}
            data-testid="provenance-validity-superseded"
            onClick={() => setValidity("superseded")}
          >
            History
          </Button>
        </div>
        <Button
          type="button"
          variant={storeAll ? "secondary" : "ghost"}
          size="sm"
          aria-pressed={storeAll}
          data-testid="provenance-store-all"
          onClick={() => setStoreAll((prev) => !prev)}
        >
          {storeAll ? "All stores" : "Identity only"}
        </Button>
      </div>

      {facts.length === 0 && !isFetching ? (
        <p className="text-muted-foreground py-6 text-center text-sm">
          No facts linked to this entity.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="text-muted-foreground text-xs">
                <SortHeaderButton
                  label="Predicate"
                  column="predicate"
                  sort={sort}
                  onSort={handleSort}
                />
              </TableHead>
              <TableHead className="text-muted-foreground text-xs">Object</TableHead>
              <TableHead className="text-muted-foreground text-xs">Kind</TableHead>
              <TableHead className="text-muted-foreground text-xs">Store</TableHead>
              <TableHead className="text-muted-foreground text-xs">Freshness</TableHead>
              <TableHead className="text-muted-foreground text-xs">
                <SortHeaderButton
                  label="Weight"
                  column="weight"
                  sort={sort}
                  onSort={handleSort}
                />
              </TableHead>
              <TableHead className="text-muted-foreground text-xs">Source</TableHead>
              <TableHead className="text-muted-foreground text-xs">
                <SortHeaderButton
                  label="Last Observed"
                  column="last_observed_at"
                  sort={sort}
                  onSort={handleSort}
                />
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((fact) => (
              <ProvenanceRow key={`${fact.store}:${fact.id}`} fact={fact} />
            ))}
          </TableBody>
        </Table>
      )}

      {hasMore && (
        <div className="flex justify-center pt-1">
          <Button
            variant="outline"
            size="sm"
            data-testid="provenance-load-more"
            onClick={handleLoadMore}
            disabled={isFetching || nextCursor == null}
          >
            {isFetching ? "Loading..." : "Load more facts"}
          </Button>
        </div>
      )}
    </section>
  );
}

function ProvenanceRow({ fact }: { fact: EntityFact }) {
  const isEntityRef = fact.object_kind === "entity";
  const lastObserved = fact.last_observed_at
    ? new Date(fact.last_observed_at)
    : new Date(fact.created_at);

  const objectCell = isEntityRef ? (
    <Link
      to={`/entities/${fact.object}`}
      className="text-primary hover:underline"
    >
      {fact.object}
    </Link>
  ) : (
    <span className="truncate max-w-[16rem] inline-block" title={fact.object}>
      {fact.object}
    </span>
  );

  return (
    <TableRow data-testid={`provenance-row-${fact.store}`}>
      <TableCell className="text-xs capitalize font-medium">
        {fact.predicate.replaceAll("-", " ").replaceAll("_", " ")}
      </TableCell>
      <TableCell className="text-xs">{objectCell}</TableCell>
      <TableCell className="text-xs text-muted-foreground">
        {fact.object_kind}
      </TableCell>
      <TableCell className="text-xs">
        <Badge variant={fact.store === "narrative" ? "outline" : "secondary"} className="capitalize">
          {fact.store}
        </Badge>
      </TableCell>
      <TableCell className="text-xs">
        <Badge variant={STALENESS_BADGE_VARIANT[fact.staleness_band]} className="capitalize">
          {fact.staleness_band}
        </Badge>
      </TableCell>
      <TableCell className="text-xs tabular-nums">
        {fact.weight != null ? fact.weight : <span className="text-muted-foreground">—</span>}
      </TableCell>
      <TableCell className="text-xs text-muted-foreground">
        {fact.src}
      </TableCell>
      <TableCell className="text-xs text-muted-foreground tabular-nums">
        <Time value={lastObserved} mode="absolute" precision="day" />
      </TableCell>
    </TableRow>
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
      title={`${mode === "editorial" ? "Editorial" : "Workbench"} mode: click to switch to ${nextMode}`}
      className="inline-flex items-center gap-1.5 rounded border border-border px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:border-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
    >
      <Layers className="h-3.5 w-3.5" aria-hidden />
      {mode === "editorial" ? "Editorial" : "Workbench"}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Workbench three-rail layout (entity v3, dashboard-relationship
// "Workbench three-rail layout"). Rendered inside archetype="overview".
//
// Layout: left context rail (~240px) · middle column (1fr) · right action
// rail (~280px). The 44px Display headline is FORBIDDEN here — the identity
// hero (already rendered above the rails) carries the name at text-2xl; the
// rails are a dense curation workspace, not a re-skin of Editorial.
// ---------------------------------------------------------------------------

/** Format a raw predicate slug into a human label (deterministic, no prose). */
function formatPredicateLabel(predicate: string): string {
  return predicate.replaceAll("-", " ").replaceAll("_", " ");
}

/**
 * A single top-relation row in the left rail: entity mark, name + predicate
 * label, and the relational weight. Navigates to the neighbour's detail page.
 */
function WorkbenchRelationRow({
  predicate,
  neighbour,
}: {
  predicate: string;
  neighbour: NeighbourEntry;
}) {
  return (
    <Row
      mark={<EntityMark name={neighbour.canonical_name} entityType="person" size={16} />}
      meta={
        <span className="font-mono text-[10px] tabular-nums text-[var(--mfg)]">
          ×{neighbour.weight ?? 1}
        </span>
      }
      density="scan"
      interactive
      className="px-1"
      data-testid="workbench-relation-row"
    >
      <Link to={`/entities/${neighbour.entity_id}`} className="block min-w-0">
        <div className="truncate text-xs font-medium">{neighbour.canonical_name}</div>
        <div className="font-mono text-[9px] uppercase leading-none tracking-[0.08em] text-[var(--dim)]">
          {formatPredicateLabel(predicate)}
        </div>
      </Link>
    </Row>
  );
}

/**
 * Left context rail: top relations by weight, the canned "introduced via"
 * serif gloss, and the "shares identifiers with" amber-mono hint that opens
 * the compare view for the duplicate pair.
 */
function WorkbenchContextRail({
  entityId,
  duplicatePeers,
  onOpenMergeReviewWith,
}: {
  entityId: string;
  /** Every peer this entity shares an identifier with (id + resolved name). */
  duplicatePeers: Array<{ id: string; name: string | null }>;
  /** Open the compare view for this entity and the given peer. */
  onOpenMergeReviewWith: (peerId: string) => void;
}) {
  const { data: neighboursData } = useEntityNeighbours(entityId, {
    rank: "weight",
    per_predicate: 6,
  });

  // Flatten the per-predicate neighbour groups into a single weight-ranked list
  // of the top relations. Deterministic: weight DESC, then name ASC.
  const topRelations = useMemo(() => {
    const groups = neighboursData?.neighbours ?? {};
    const flat: Array<{ predicate: string; neighbour: NeighbourEntry }> = [];
    for (const [predicate, entries] of Object.entries(groups)) {
      for (const neighbour of entries) flat.push({ predicate, neighbour });
    }
    flat.sort((a, b) => {
      const w = (b.neighbour.weight ?? 0) - (a.neighbour.weight ?? 0);
      if (w !== 0) return w;
      return a.neighbour.canonical_name.localeCompare(b.neighbour.canonical_name);
    });
    return flat.slice(0, 8);
  }, [neighboursData]);

  // "Introduced via" is canned and deterministic — it names the strongest
  // relation, not a generated narrative. Omitted when there are no relations.
  const introduced = topRelations[0];

  return (
    <aside
      className="space-y-5 lg:border-r lg:border-border lg:pr-5"
      data-testid="workbench-context-rail"
      aria-label="Context"
    >
      <section className="space-y-2">
        <Eyebrow>top relations</Eyebrow>
        {topRelations.length === 0 ? (
          <p className="text-xs text-muted-foreground">No relations yet.</p>
        ) : (
          <div>
            {topRelations.map(({ predicate, neighbour }) => (
              <WorkbenchRelationRow
                key={`${predicate}:${neighbour.entity_id}`}
                predicate={predicate}
                neighbour={neighbour}
              />
            ))}
          </div>
        )}
      </section>

      {introduced && (
        <section className="space-y-2">
          <Eyebrow>introduced via</Eyebrow>
          <Voice variant="italic" className="text-xs text-[var(--mfg)]" data-testid="workbench-introduced-via">
            {formatPredicateLabel(introduced.predicate)} · {introduced.neighbour.canonical_name}
          </Voice>
        </section>
      )}

      {duplicatePeers.length > 0 && (
        <section className="space-y-2">
          <Eyebrow>shares identifiers with</Eyebrow>
          <div className="space-y-1">
            {duplicatePeers.map((peer) => (
              <button
                key={peer.id}
                type="button"
                data-testid="workbench-shares-identifiers"
                onClick={() => onOpenMergeReviewWith(peer.id)}
                className="block w-full text-left font-mono text-[10px] uppercase leading-relaxed tracking-[0.08em] text-[var(--amber)] hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              >
                {peer.name ?? "another entity"}, likely the same →
              </button>
            ))}
          </div>
        </section>
      )}
    </aside>
  );
}

/** One cell of the four-cell KPI strip: mono eyebrow + tabular mega-number. */
function WorkbenchKpiCell({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="px-3 py-2 first:pl-0" data-testid="workbench-kpi-cell">
      <div className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 text-3xl font-medium tabular-nums leading-none tracking-[-0.03em]">
        {value}
      </div>
    </div>
  );
}

/**
 * Middle-column KPI strip: relations / touches 90d / sources / channels.
 * All numerals tabular. No card chrome — a hairline-divided four-cell grid.
 *
 * Sources and channels are derived from the first page of facts (max 200).
 * When the entity has more than 200 facts (has_more=true), these counts are
 * lower bounds — displayed with a "+" suffix to signal incompleteness rather
 * than showing a silently wrong exact number.
 */
function WorkbenchKpiStrip({ entityId }: { entityId: string }) {
  const { data: neighboursData } = useEntityNeighbours(entityId);
  const { data: binsData } = useEntityActivityBins(entityId, { window: "90d" });
  // Pull the max page of facts (200) to count distinct sources and channel
  // predicates. has_more=true means >200 facts exist; in that case we surface
  // the observed counts as lower bounds (e.g. "3+") rather than exact totals.
  const { data: factsData } = useEntityFacts(entityId, { store: "all", limit: 200 });

  const relations = useMemo(() => {
    const groups = neighboursData?.neighbours ?? {};
    return Object.values(groups).reduce((sum, entries) => sum + entries.length, 0);
  }, [neighboursData]);

  const touches90d = useMemo(() => {
    const bins = binsData?.bins ?? [];
    return bins.reduce((sum, bin) => sum + (bin.count ?? 0), 0);
  }, [binsData]);

  const facts = useMemo(() => factsData?.items ?? [], [factsData]);
  // has_more=true means the 200-fact window is incomplete; counts are lower bounds.
  const truncated = factsData?.has_more ?? false;
  const sourcesCount = useMemo(() => new Set(facts.map((f) => f.src)).size, [facts]);
  const channelsCount = useMemo(
    () => facts.filter((f) => f.predicate.startsWith("has-")).length,
    [facts],
  );
  // Display exact count when complete, or "${n}+" as an honest lower bound when
  // the facts window was truncated.
  const sources: string | number = truncated ? `${sourcesCount}+` : sourcesCount;
  const channels: string | number = truncated ? `${channelsCount}+` : channelsCount;

  return (
    <div
      className="grid grid-cols-4 divide-x divide-border border-y border-border"
      data-testid="workbench-kpi-strip"
    >
      <WorkbenchKpiCell label="relations" value={relations} />
      <WorkbenchKpiCell label="touches 90d" value={touches90d} />
      <WorkbenchKpiCell label="sources" value={sources} />
      <WorkbenchKpiCell label="channels" value={channels} />
    </div>
  );
}

/** A single curation action — a quiet pill button, never colored (except red). */
function CurationAction({
  label,
  onClick,
  disabled = false,
  destructive = false,
  testId,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  destructive?: boolean;
  testId: string;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      disabled={disabled}
      className={
        destructive
          ? "block w-full rounded border border-border px-2.5 py-1.5 text-left font-mono text-[11px] uppercase tracking-[0.04em] text-destructive transition-colors hover:border-destructive hover:bg-destructive/10 disabled:opacity-40 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          : "block w-full rounded border border-border px-2.5 py-1.5 text-left font-mono text-[11px] uppercase tracking-[0.04em] text-muted-foreground transition-colors hover:border-foreground hover:text-foreground disabled:opacity-40 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      }
    >
      {label}
    </button>
  );
}

/**
 * One cell in the editorial curation 3×2 grid.
 *
 * Label sits above a short serif voice gloss (Source Serif 4, 12px, italic).
 * Forget is red per the design language; all others are quiet muted text with
 * border-hover. Wired to the SAME backend mutations as WorkbenchActionRail.
 */
function EditorialCurationCell({
  action,
  onClick,
  disabled = false,
}: {
  action: CurationRailAction;
  onClick: () => void;
  disabled?: boolean;
}) {
  const gloss = CURATION_RAIL_GLOSSES[action];
  const isDestructive = action === "forget";
  const label =
    action === "edit-aliases"
      ? "Edit aliases"
      : action.charAt(0).toUpperCase() + action.slice(1);

  return (
    <button
      type="button"
      data-testid={`editorial-curation-${action}`}
      onClick={onClick}
      disabled={disabled}
      className={[
        "group flex flex-col gap-1 rounded border px-3 py-2.5 text-left transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-40",
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
        isDestructive
          ? "border-border text-destructive hover:border-destructive hover:bg-destructive/5"
          : "border-border text-foreground hover:border-foreground/40 hover:bg-muted/20",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <span className="text-[11px] font-medium leading-none tracking-wide">
        {label}
      </span>
      <span
        className={[
          "font-serif text-[11px] italic leading-snug",
          isDestructive ? "text-destructive/70" : "text-[var(--mfg)]",
        ].join(" ")}
      >
        {gloss}
      </span>
    </button>
  );
}

/**
 * Editorial-mode curation rail — 3×2 serif-gloss grid.
 *
 * Brief §1 (L80): "curation rail 3×2 (merge/promote/demote/archive/forget(red)/
 * edit-aliases) with serif gloss" on the EDITORIAL page.
 *
 * "promote" = step Dunbar tier inward (closer), same as WorkbenchActionRail's
 * "Promote tier" button.
 * "demote"  = step Dunbar tier outward, same as "Demote tier".
 * merge is gated: disabled when no duplicate peer exists.
 *
 * All six actions delegate to the SAME handlers that power WorkbenchActionRail.
 * No dead buttons — every onClick traces to a real mutation or state transition.
 */
function EditorialCurationRail({
  duplicatePeerId,
  onOpenMergeReview,
  onPromoteTier,
  onDemoteTier,
  onEditAliases,
  onArchive,
  onForget,
}: {
  duplicatePeerId: string | null;
  onOpenMergeReview: () => void;
  onPromoteTier: () => void;
  onDemoteTier: () => void;
  onEditAliases: () => void;
  onArchive: () => void;
  onForget: () => void;
}) {
  return (
    <section data-testid="editorial-curation-rail" aria-label="Curation">
      <Eyebrow className="mb-3">curation</Eyebrow>
      {/* 3×2 grid: merge/promote/demote on row 1, archive/forget/edit-aliases on row 2 */}
      <div className="grid grid-cols-3 gap-2">
        <EditorialCurationCell
          action="merge"
          onClick={onOpenMergeReview}
          disabled={!duplicatePeerId}
        />
        <EditorialCurationCell
          action="promote"
          onClick={onPromoteTier}
        />
        <EditorialCurationCell
          action="demote"
          onClick={onDemoteTier}
        />
        <EditorialCurationCell
          action="archive"
          onClick={onArchive}
        />
        <EditorialCurationCell
          action="forget"
          onClick={onForget}
        />
        <EditorialCurationCell
          action="edit-aliases"
          onClick={onEditAliases}
        />
      </div>
    </section>
  );
}

/**
 * Per-fact staleness inspector row. Renders the staleness band (dim when stale)
 * plus source/verified marks.
 */
function WorkbenchInspectorRow({ fact }: { fact: EntityFact }) {
  return (
    <Row
      density="scan"
      className="px-0"
      meta={<StalenessBand band={fact.staleness_band} />}
      data-testid="workbench-inspector-row"
    >
      <div className="min-w-0">
        <div className="truncate text-[11px] font-medium">
          {formatPredicateLabel(fact.predicate)}
        </div>
        <ProvenanceMarks src={fact.src} verified={fact.verified} className="mt-0.5" />
      </div>
    </Row>
  );
}

/**
 * Right action rail: the duplicate warning panel (when duplicate-candidate),
 * the curation action list, and the per-fact confidence/staleness inspector.
 */
function WorkbenchActionRail({
  entityId,
  isUnidentified,
  duplicatePeerId,
  onOpenMergeReview,
  onPromote,
  onPromoteTier,
  onDemoteTier,
  onEditAliases,
  onEditContacts,
  onArchive,
  onForget,
  duplicateEvidence,
}: {
  entityId: string;
  isUnidentified: boolean;
  duplicatePeerId: string | null;
  onOpenMergeReview: () => void;
  onPromote: () => void;
  onPromoteTier: () => void;
  onDemoteTier: () => void;
  onEditAliases: () => void;
  onEditContacts: () => void;
  onArchive: () => void;
  onForget: () => void;
  duplicateEvidence: string | null;
}) {
  const { data: factsData } = useEntityFacts(entityId, { store: "all", limit: 20 });
  const facts = useMemo(() => factsData?.items ?? [], [factsData]);

  return (
    <aside
      className="space-y-5 lg:border-l lg:border-border lg:pl-5"
      data-testid="workbench-action-rail"
      aria-label="Curation"
    >
      {/* Duplicate warning panel — amber 1px border, atop the right rail. */}
      {duplicatePeerId && (
        <section
          className="space-y-2 rounded-md border border-[var(--amber)] p-3"
          data-testid="workbench-duplicate-panel"
        >
          <Eyebrow>duplicate candidate</Eyebrow>
          {duplicateEvidence && (
            <p className="font-mono text-[11px] leading-relaxed text-[var(--mfg)]">
              {duplicateEvidence}
            </p>
          )}
          <button
            type="button"
            data-testid="workbench-duplicate-commit"
            onClick={onOpenMergeReview}
            className="inline-flex items-center gap-1.5 rounded border border-[var(--amber)] px-2.5 py-1 font-mono text-[11px] uppercase tracking-[0.04em] text-[var(--amber)] transition-colors hover:bg-[var(--amber)]/10 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            <Layers className="h-3.5 w-3.5" aria-hidden />
            Review &amp; merge →
          </button>
        </section>
      )}

      <section className="space-y-2">
        <Eyebrow>curation</Eyebrow>
        <div className="space-y-1.5">
          <CurationAction
            label="Merge"
            testId="workbench-action-merge"
            onClick={onOpenMergeReview}
            disabled={!duplicatePeerId}
          />
          {isUnidentified && (
            <CurationAction label="Promote" testId="workbench-action-promote" onClick={onPromote} />
          )}
          <CurationAction
            label="Promote tier"
            testId="workbench-action-promote-tier"
            onClick={onPromoteTier}
          />
          <CurationAction
            label="Demote tier"
            testId="workbench-action-demote-tier"
            onClick={onDemoteTier}
          />
          <CurationAction
            label="Edit names"
            testId="workbench-action-edit-names"
            onClick={onEditAliases}
          />
          <CurationAction
            label="Edit contacts"
            testId="workbench-action-edit-contacts"
            onClick={onEditContacts}
          />
          <CurationAction label="Archive" testId="workbench-action-archive" onClick={onArchive} />
          <CurationAction
            label="Forget"
            testId="workbench-action-forget"
            onClick={onForget}
            destructive
          />
        </div>
      </section>

      <section className="space-y-2">
        <Eyebrow>staleness</Eyebrow>
        {facts.length === 0 ? (
          <p className="text-xs text-muted-foreground">No facts to inspect.</p>
        ) : (
          <div data-testid="workbench-inspector">
            {facts.map((fact) => (
              <WorkbenchInspectorRow key={`${fact.store}:${fact.id}`} fact={fact} />
            ))}
          </div>
        )}
      </section>
    </aside>
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
  const archiveEntity = useArchiveRelationshipEntity();
  const updateDunbarTier = useUpdateEntityDunbarTier();

  // Ref used by the ContactChannelCard "Link contact" CTA to scroll to the
  // practical drawer where the existing link/unlink flow lives.
  const practicalDrawerRef = useRef<HTMLDivElement>(null);
  // View-local keyboard-map root. The Detail map (m / j / k / Esc) binds to this
  // focusable container via onKeyDown, never to window.
  const detailRootRef = useRef<HTMLDivElement>(null);

  const [forgetDialogOpen, setForgetDialogOpen] = useState(false);
  const [forgetError, setForgetError] = useState<string | null>(null);

  // Name-edit state declared here so handleDetailKeyDown (below) can reference
  // handleStartEditName without forward-reference lint issues.
  const [editingName, setEditingName] = useState(false);
  const [draftName, setDraftName] = useState("");

  const handleStartEditName = useCallback(() => {
    setDraftName(entity?.canonical_name ?? "");
    setEditingName(true);
  }, [entity?.canonical_name]);

  // Merge-review entry point: the detail page's `m` key opens the compare view
  // when duplicate evidence exists for this entity (relationship-merge-review
  // "Single-pair review UX"). Duplicate evidence + the peer entity come from the
  // curation queue's duplicate-candidate bucket.
  const { data: queueData } = useRelationshipEntityQueue({ limit: 100 });
  // The duplicate-candidate queue entry for this entity (if any). Drives both
  // the duplicate-warning panel and the Workbench right-rail panel/evidence.
  const duplicateEntry = useMemo(() => {
    if (!entityId) return null;
    return (
      queueData?.items.find(
        (item) => item.entity_id === entityId && item.bucket === "duplicate-candidate",
      ) ?? null
    );
  }, [queueData, entityId]);
  // ALL peer entities this entity shares an identifier with. An entity can
  // collide with more than one peer; each is comparable independently, so we
  // resolve every ``peer_entity_ids`` entry (no longer ``[0]`` only) and pair it
  // with a display name resolved off the queue's own items.
  const duplicatePeers = useMemo<Array<{ id: string; name: string | null }>>(() => {
    const peers = duplicateEntry?.evidence?.["peer_entity_ids"];
    if (!Array.isArray(peers)) return [];
    return peers
      .filter((p): p is string => typeof p === "string")
      .map((id) => ({
        id,
        name: queueData?.items.find((item) => item.entity_id === id)?.canonical_name ?? null,
      }));
  }, [duplicateEntry, queueData]);
  // The primary peer drives the `m` key and the panel's default review action.
  const duplicatePeerId = duplicatePeers[0]?.id ?? null;
  // Triggering shared evidence (predicate + value), handed to the compare view
  // so the matching shared row pre-highlights. Null when flagged by metadata.
  const duplicateTrigger = useMemo<{ predicate: string; object: string } | null>(() => {
    const ev = duplicateEntry?.evidence;
    if (!ev) return null;
    const predicate = typeof ev["predicate"] === "string" ? (ev["predicate"] as string) : null;
    const sharedValue =
      typeof ev["shared_value"] === "string" ? (ev["shared_value"] as string) : null;
    return predicate && sharedValue ? { predicate, object: sharedValue } : null;
  }, [duplicateEntry]);
  // Deterministic evidence string for the Workbench duplicate panel — assembled
  // from the queue entry's structured evidence, never generated prose.
  const duplicateEvidence = useMemo<string | null>(() => {
    if (!duplicateEntry) return null;
    if (duplicateTrigger) {
      return `Shares ${duplicateTrigger.predicate.replaceAll("-", " ").replaceAll("_", " ")} ${duplicateTrigger.object}`;
    }
    return "Shares an identifier with another entity";
  }, [duplicateEntry, duplicateTrigger]);
  const [comparePair, setComparePair] = useState<{
    entityA: string;
    entityB: string;
    highlight?: { predicate: string; object: string } | null;
  } | null>(null);

  const openMergeReviewWith = useCallback(
    (peerId: string) => {
      if (!entityId) return;
      setComparePair({ entityA: entityId, entityB: peerId, highlight: duplicateTrigger });
    },
    [entityId, duplicateTrigger],
  );
  const openMergeReview = useCallback(() => {
    if (!duplicatePeerId) return;
    openMergeReviewWith(duplicatePeerId);
  }, [duplicatePeerId, openMergeReviewWith]);

  // `m` opens the merge-review compare view for the primary peer. The handler
  // binds to a VIEW-LOCAL focusable container (see detailRootRef / onKeyDown
  // below), never to window, so it is active only while this page holds focus
  // and never shadows app-wide shortcuts or other routes' keyboard maps.

  // Sibling navigation for the Detail keyboard map: `k`/`j` step to the
  // previous/next entity in Index order (default scope), `Esc` returns to the
  // Index. View-local handlers only — they never shadow the app-wide ⌘K/`/`.
  // The sibling set is the relationship Index list (the most recent list scope).
  const { data: siblingsData } = useRelationshipEntities({ limit: 200 });
  const siblingIds = useMemo<string[]>(
    () => siblingsData?.items.map((item) => item.id) ?? [],
    [siblingsData],
  );
  const stepSibling = useCallback(
    (delta: number) => {
      if (!entityId || siblingIds.length === 0) return;
      const idx = siblingIds.indexOf(entityId);
      if (idx === -1) return;
      const next = siblingIds[idx + delta];
      if (next) void navigate(`/entities/${next}`);
    },
    [entityId, siblingIds, navigate],
  );

  // Fetch first_seen / last_seen for the current entity from the relationship
  // entity list endpoint (the only surface that exposes these computed fields).
  const { data: entityRelData } = useRelationshipEntitiesByIds(
    entityId ? { ids: [entityId] } : { ids: [] },
  );
  const entityRelItem = entityRelData?.items.find((item) => item.id === entityId) ?? null;
  const entityFirstSeen: string | null = entityRelItem?.first_seen ?? null;
  const entityLastSeen: string | null = entityRelItem?.last_seen ?? null;

  // The view-local Detail keyboard map. Bound to the page root via onKeyDown
  // (detailRootRef + tabIndex below) — NEVER to window — so `m`/`j`/`k`/`Esc`
  // are active only while this page holds keyboard focus and never shadow the
  // app-wide ⌘K / "/" shortcuts or another route's bindings.
  //   m                open the merge-review compare view (when a duplicate peer exists)
  //   k / j            step to the previous / next entity in Index order
  //   Esc              return to the Index
  //   e                start editing the entity name
  //   Shift+Backspace  open the forget confirmation dialog (no unconfirmed deletion)
  const handleDetailKeyDown = useCallback(
    (e: ReactKeyboardEvent<HTMLDivElement>) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable) return;
      if (e.key === "m") {
        if (!duplicatePeerId) return;
        e.preventDefault();
        openMergeReview();
      } else if (e.key === "k") {
        e.preventDefault();
        stepSibling(-1);
      } else if (e.key === "j") {
        e.preventDefault();
        stepSibling(1);
      } else if (e.key === "Escape") {
        e.preventDefault();
        void navigate("/entities");
      } else if (e.key === "e") {
        e.preventDefault();
        handleStartEditName();
      } else if (e.shiftKey && e.key === "Backspace") {
        e.preventDefault();
        setForgetDialogOpen(true);
      }
    },
    [duplicatePeerId, openMergeReview, stepSibling, navigate, handleStartEditName, setForgetDialogOpen],
  );

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

  // Workbench right-rail curation handlers. Tier promote/demote step along the
  // canonical Dunbar ramp (5 = innermost … 1500 = outermost); promote moves
  // closer (smaller tier), demote moves outward (larger tier).
  const handlePromoteEntity = useCallback(() => {
    if (!entityId) return;
    promoteEntity.mutate(entityId, {
      onSuccess: () => toast.success("Entity marked as confirmed."),
      onError: (err) =>
        toast.error(`Failed to confirm: ${err instanceof Error ? err.message : "Unknown error"}`),
    });
  }, [entityId, promoteEntity]);

  const stepDunbarTier = useCallback(
    (direction: "promote" | "demote") => {
      if (!entityId || !entity) return;
      const ramp = DUNBAR_TIER_VALUES;
      const current = entity.dunbar_tier ?? ramp[ramp.length - 1];
      const idx = ramp.indexOf(current as (typeof ramp)[number]);
      const baseIdx = idx === -1 ? ramp.length - 1 : idx;
      const nextIdx = direction === "promote" ? baseIdx - 1 : baseIdx + 1;
      if (nextIdx < 0 || nextIdx >= ramp.length) {
        toast.error(direction === "promote" ? "Already at the innermost tier." : "Already at the outermost tier.");
        return;
      }
      const nextTier = ramp[nextIdx];
      updateDunbarTier.mutate(
        { entityId, tier: nextTier },
        {
          onSuccess: () => toast.success(`Tier set to ${nextTier}`),
          onError: (err) => toast.error(`Failed to set tier: ${(err as Error).message}`),
        },
      );
    },
    [entityId, entity, updateDunbarTier],
  );

  const handleArchiveEntity = useCallback(() => {
    if (!entityId) return;
    archiveEntity.mutate(entityId, {
      onSuccess: () => {
        toast.success(`Archived ${entity?.canonical_name ?? "entity"}`);
        void navigate("/entities");
      },
      onError: (err) => toast.error(`Failed to archive: ${(err as Error).message}`),
    });
  }, [entityId, entity, archiveEntity, navigate]);

  const handleEditAliases = useCallback(() => {
    setAddingAlias(true);
  }, []);

  const handleEditContacts = useCallback(() => {
    practicalDrawerRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  const isOwner = entity?.roles?.includes("owner") ?? false;
  const ownerNeedsSetup = isOwner && entity ? !entity.linked_contact_id : false;

  // Practical drawer — collapsed by default, owner setup forces it open.
  // Defined once and rendered near the top of whichever view mode is active
  // (editorial: below first/last seen; workbench: above the three-rail). The
  // two modes are mutually exclusive, so the single ref is never duplicated.
  const practicalDrawer =
    entity && entityId ? (
      <div ref={practicalDrawerRef}>
        <PracticalDrawer entity={entity} forceOpen={ownerNeedsSetup}>
          <OwnerSetupBanner entity={entity} />

          {/* Full contact channels (email, phone, telegram, …) — the primary
              contact details, expounded inline now that contacts are entities. */}
          <ContactChannelCard entityId={entity.id} />

          {/* Link/unlink management control for the underlying contact record. */}
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
      </div>
    ) : null;
  const dunbarPinned =
    entity?.recent_facts?.some(
      (f) => f.predicate === "dunbar_tier_override" && f.validity === "active",
    ) ?? false;

  // Build breadcrumbs based on origin page.
  // The optional `?from=` query param signals which entities sub-page the user
  // arrived from (hop | columns | concentration). Links in those pages that
  // navigate to an entity detail page may include e.g. `?from=hop` so that
  // the crumb trail reflects the real navigation path.
  // Direct URL access (no ?from=) shows only: Index → Entity name.
  const originFrom = searchParams.get("from");
  const breadcrumbs = useMemo(() => {
    const ORIGIN_CRUMBS: Record<string, { label: string; href: string }> = {
      hop:           { label: "Hop",           href: "/entities/hop" },
      columns:       { label: "Columns",       href: "/entities/columns" },
      concentration: { label: "Concentration", href: "/entities/concentration" },
    };
    const originCrumb =
      originFrom && Object.prototype.hasOwnProperty.call(ORIGIN_CRUMBS, originFrom)
        ? ORIGIN_CRUMBS[originFrom]
        : null;
    return [
      { label: "Index", href: "/entities" },
      ...(originCrumb ? [originCrumb] : []),
      { label: entity?.canonical_name ?? entityId ?? "Entity" },
    ];
  }, [entity?.canonical_name, entityId, originFrom]);

  // Editorial mode uses archetype="editorial" for the Display 44px headline
  // (Brief §6b Amendment 7). Workbench uses archetype="overview" (interim,
  // per entity-brief.md R3; workspace archetype gap deferred to Phase 2).
  const pageArchetype = mode === "editorial" ? "editorial" : "overview";
  const pageActions = (
    <div className="flex items-center gap-2">
      <button
        type="button"
        data-testid="forget-entity-button"
        aria-label="Forget this entity"
        title="Forget this entity: irreversible hard delete"
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
        <div
          ref={detailRootRef}
          tabIndex={0}
          onKeyDown={handleDetailKeyDown}
          data-testid="entity-detail-root"
          className="outline-none focus-visible:ring-1 focus-visible:ring-ring focus-visible:ring-offset-2"
        >
          {/* Duplicate-warning panel — "shares identifiers with" hint. Its merge
              action opens the compare view; `m` is the keyboard equivalent. */}
          {duplicatePeerId && (
            <div
              className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-[var(--amber)]/40 bg-[var(--amber)]/5 px-3 py-2 text-sm"
              data-testid="duplicate-warning-panel"
            >
              <span className="text-foreground">
                Shares identifiers with another entity. This may be a duplicate.
              </span>
              <Button
                type="button"
                size="sm"
                variant="outline"
                data-testid="duplicate-warning-review"
                onClick={openMergeReview}
              >
                <Layers className="h-3.5 w-3.5" />
                Review merge <span className="ml-1 text-xs text-muted-foreground">(m)</span>
              </Button>
            </div>
          )}

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
              {entity.dunbar_tier != null && (
                <TierBadge tier={entity.dunbar_tier} data-testid="hero-tier-badge" />
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

            {/* 90-day activity sparkline — editorial-only quick-refresh affordance (entity v3, bu-3rj2j).
                Brief §1 (L80): sparkline in Detail-Editorial hero; workbench does not surface it. */}
            {mode === "editorial" && <ActivitySparkline entityId={entityId} />}

            {/* Delta-since-last-visit banner — reads delta, then advances the
                view mark (entity v3). Renders nothing on a first visit. */}
            <DeltaSinceLastVisitBanner entityId={entityId} />
          </section>

          {mode === "editorial" ? (
            <>
              {/* Entity gloss — canned voice string for this entity's (tier, state, category) */}
              <EntityGlossBlock
                dunbarTier={entity.dunbar_tier}
                unidentified={entity.unidentified}
                entityType={entity.entity_type}
              />

              {/* First / last seen — from the relationship entity index (entity v3). */}
              <dl className="grid grid-cols-[8rem_1fr] items-baseline gap-x-3 gap-y-0 border-t border-border pt-4 text-sm">
                <dt className="text-muted-foreground text-xs uppercase tracking-wide">
                  First seen
                </dt>
                <dd className="text-sm">
                  {entityFirstSeen ? (
                    <Time value={entityFirstSeen} mode="absolute" precision="day" />
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </dd>
                <dt className="text-muted-foreground text-xs uppercase tracking-wide">
                  Last seen
                </dt>
                <dd className="text-sm">
                  {entityLastSeen ? (
                    <Time value={entityLastSeen} mode="absolute" precision="day" />
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </dd>
              </dl>

              {/* Practical details — first data element below first/last seen. */}
              {practicalDrawer}

              {/* Core dates — server-extracted date-kind facts with next
                  occurrence (entity v3; replaces client-side date matching). */}
              <CoreDatesBlock entityId={entityId} />

              {/* Latest interactions — most-recent touch per channel, read
                  through the existing thread + timeline endpoints (entity v3). */}
              <LatestInteractionsBlock entityId={entityId} />

              {/* Profile snapshot — place, work, family (date-kind facts moved
                  to <CoreDatesBlock> above). */}
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

              {/* Editorial curation rail — 3×2 serif-gloss grid.
                  Brief §1 (L80): merge/promote/demote/archive/forget(red)/edit-aliases.
                  Every action wired to the real mutation; no dead buttons. */}
              <EditorialCurationRail
                duplicatePeerId={duplicatePeerId}
                onOpenMergeReview={openMergeReview}
                onPromoteTier={() => stepDunbarTier("promote")}
                onDemoteTier={() => stepDunbarTier("demote")}
                onEditAliases={handleEditAliases}
                onArchive={handleArchiveEntity}
                onForget={() => {
                  setForgetError(null);
                  setForgetDialogOpen(true);
                }}
              />
            </>
          ) : (
            <>
              {/* Core dates — first-class section in both modes (entity v3). */}
              <CoreDatesBlock entityId={entityId} />

              {/* Latest interactions — first-class section in both modes
                  (entity v3); most-recent touch per channel. */}
              <LatestInteractionsBlock entityId={entityId} />

              {/* Practical details — kept near the top in workbench mode too. */}
              {practicalDrawer}

              {/* Workbench three-rail layout: context · workbench · curation.
                  No 44px Display here (the identity hero above carries the name). */}
              <div
                className="grid grid-cols-1 gap-6 lg:grid-cols-[240px_minmax(0,1fr)_280px]"
                data-testid="workbench-three-rail"
              >
                <WorkbenchContextRail
                  entityId={entity.id}
                  duplicatePeers={duplicatePeers}
                  onOpenMergeReviewWith={openMergeReviewWith}
                />

                <div className="min-w-0 space-y-5">
                  <WorkbenchKpiStrip entityId={entity.id} />
                  {/* Dense sortable provenance grid over BOTH stores. */}
                  <ProvenanceGrid entityId={entity.id} defaultStoreAll />
                </div>

                <WorkbenchActionRail
                  entityId={entity.id}
                  isUnidentified={entity.unidentified}
                  duplicatePeerId={duplicatePeerId}
                  duplicateEvidence={duplicateEvidence}
                  onOpenMergeReview={openMergeReview}
                  onPromote={handlePromoteEntity}
                  onPromoteTier={() => stepDunbarTier("promote")}
                  onDemoteTier={() => stepDunbarTier("demote")}
                  onEditAliases={handleEditAliases}
                  onEditContacts={handleEditContacts}
                  onArchive={handleArchiveEntity}
                  onForget={() => {
                    setForgetError(null);
                    setForgetDialogOpen(true);
                  }}
                />
              </div>
            </>
          )}
        </div>
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

    {/* Merge-review compare view (opened by `m` or the duplicate-warning panel) */}
    <MergeCompareDialog
      pair={comparePair}
      highlightFact={comparePair?.highlight ?? null}
      onOpenChange={(open) => {
        if (!open) setComparePair(null);
      }}
    />
    </>
  );
}
