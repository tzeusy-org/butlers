import { useMemo, useState } from "react";
import { Link, useParams } from "react-router";
import {
  Check,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Loader2,
  Pencil,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { format, formatDistanceToNow } from "date-fns";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import type {
  ContactSummary,
  EntityGift,
  EntityInfoEntry,
  EntityLoan,
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
import { Badge } from "@/components/ui/badge";
import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import { Button } from "@/components/ui/button";
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
  useEntityGifts,
  useEntityLinkedContacts,
  useEntityLoans,
  useEntityMessageThreads,
  useEntityTimeline,
} from "@/hooks/use-entities";
import {
  useCreateEntityInfo,
  useDeleteEntityInfo,
  useEntity,
  usePromoteEntity,
  useRevealEntitySecret,
  useSetLinkedContact,
  useUnlinkContact,
  useUpdateEntity,
  useUpdateEntityInfo,
} from "@/hooks/use-memory";

// ---------------------------------------------------------------------------
// Entity info type helpers
// ---------------------------------------------------------------------------

import {
  ENTITY_INFO_TYPES,
  SECURED_USER_TYPES as SECURED_TYPES,
  entityInfoTypeLabel,
} from "@/lib/user-secret-templates";

const FACTS_PAGE_SIZE = 20;

function sessionDetailHref(sessionId: string, butler: string | null): string {
  const query = butler ? `?butler=${encodeURIComponent(butler)}` : "";
  return `/sessions/${encodeURIComponent(sessionId)}${query}`;
}

// ---------------------------------------------------------------------------
// SecuredInfoEntry — masked value with click-to-reveal
// ---------------------------------------------------------------------------

function SecuredInfoEntry({
  entry,
  entityId,
}: {
  entry: EntityInfoEntry;
  entityId: string;
}) {
  const [revealed, setRevealed] = useState<string | null>(null);
  const [isRevealing, setIsRevealing] = useState(false);
  const revealMutation = useRevealEntitySecret();

  const displayValue = revealed ?? entry.value;

  async function handleReveal() {
    if (isRevealing || revealed !== null) return;
    setIsRevealing(true);
    revealMutation.mutate(
      { entityId, infoId: entry.id },
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
// Editable entity_info row
// ---------------------------------------------------------------------------

function EntityInfoRow({
  entry,
  entityId,
}: {
  entry: EntityInfoEntry;
  entityId: string;
}) {
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(entry.value ?? "");
  const deleteInfo = useDeleteEntityInfo();
  const updateInfo = useUpdateEntityInfo();

  function handleDelete() {
    if (!window.confirm(`Delete this ${entityInfoTypeLabel(entry.type)} entry?`)) return;
    deleteInfo.mutate(
      { entityId, infoId: entry.id },
      {
        onSuccess: () => toast.success(`Removed ${entityInfoTypeLabel(entry.type)} entry.`),
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
    updateInfo.mutate(
      { entityId, infoId: entry.id, request: { value: trimmed } },
      {
        onSuccess: () => {
          toast.success(`Updated ${entityInfoTypeLabel(entry.type)} entry.`);
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
        {entry.label ?? entityInfoTypeLabel(entry.type)}
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
            disabled={updateInfo.isPending}
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
            disabled={updateInfo.isPending}
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
            <SecuredInfoEntry entry={entry} entityId={entityId} />
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
// Add entity_info inline form
// ---------------------------------------------------------------------------

function AddEntityInfoForm({
  entityId,
  onDone,
  isOwner = false,
}: {
  entityId: string;
  onDone: () => void;
  isOwner?: boolean;
}) {
  const createInfo = useCreateEntityInfo();
  const [type, setType] = useState<string>("api_key");
  const [value, setValue] = useState("");
  const [label, setLabel] = useState("");
  const [isPrimary, setIsPrimary] = useState(false);

  // Owner entities do not store google_oauth_refresh — those live on companion entities.
  const availableTypes = isOwner
    ? ENTITY_INFO_TYPES.filter((t) => t !== "google_oauth_refresh")
    : ENTITY_INFO_TYPES;

  const isSecured = SECURED_TYPES.has(type);

  async function handleSubmit() {
    const trimmed = value.trim();
    if (!trimmed) {
      toast.error("Value cannot be empty.");
      return;
    }
    try {
      await createInfo.mutateAsync({
        entityId,
        request: {
          type,
          value: trimmed,
          label: label.trim() || undefined,
          is_primary: isPrimary,
          ...(isSecured ? { secured: true } : {}),
        },
      });
      toast.success(`Added ${entityInfoTypeLabel(type)} entry.`);
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
        <Select value={type} onValueChange={(v) => { setType(v); setValue(""); }}>
          <SelectTrigger className="h-8 w-32 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {availableTypes.map((t) => (
              <SelectItem key={t} value={t} className="text-xs">
                {entityInfoTypeLabel(t)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-1">
        <Label className="text-xs">Label</Label>
        <Input
          className="h-8 w-28 text-sm"
          placeholder="Optional"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          disabled={createInfo.isPending}
        />
      </div>
      <div className="flex-1 space-y-1">
        <Label className="text-xs">Value</Label>
        <Input
          className="h-8 text-sm"
          type={isSecured ? "password" : "text"}
          placeholder={isSecured ? "••••••••" : ""}
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
// Entity info section
// ---------------------------------------------------------------------------

function EntityInfoSection({
  entityId,
  entries,
  isOwner = false,
}: {
  entityId: string;
  entries: EntityInfoEntry[];
  isOwner?: boolean;
}) {
  const [addingInfo, setAddingInfo] = useState(false);

  // Owner entities do not store google_oauth_refresh — those live on companion
  // entities. Filter them out so the owner entity view stays clean.
  const visibleEntries = isOwner
    ? entries.filter((e) => e.type !== "google_oauth_refresh")
    : entries;

  const hasHiddenOAuthRows = isOwner && entries.some((e) => e.type === "google_oauth_refresh");

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Credentials &amp; Info</CardTitle>
        </div>
      </CardHeader>
      <CardContent>
        {visibleEntries.length === 0 && !addingInfo ? (
          <p className="text-muted-foreground py-2 text-center text-sm">
            No entity info entries yet.
          </p>
        ) : (
          <div className="space-y-1.5">
            {visibleEntries.map((entry) => (
              <EntityInfoRow key={entry.id} entry={entry} entityId={entityId} />
            ))}
          </div>
        )}
        {isOwner && (
          <div className="mt-3 flex items-center gap-1.5 rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
            <ExternalLink className="h-3 w-3 shrink-0" />
            <span>
              Google OAuth tokens are managed on companion Google account entities.
              {hasHiddenOAuthRows
                ? " Existing token rows are hidden here — manage them at "
                : " To manage Google accounts, go to "}
              <Link to="/settings" className="text-primary hover:underline">
                Settings → Google OAuth
              </Link>
              .
            </span>
          </div>
        )}
        {addingInfo ? (
          <AddEntityInfoForm
            entityId={entityId}
            onDone={() => setAddingInfo(false)}
            isOwner={isOwner}
          />
        ) : (
          <Button
            variant="ghost"
            size="sm"
            className="mt-2 h-7 text-xs text-muted-foreground"
            onClick={() => setAddingInfo(true)}
          >
            <Plus className="mr-1 h-3 w-3" />
            Add entity info
          </Button>
        )}
      </CardContent>
    </Card>
  );
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
// Pulse strip — three derived stat tiles surfaced at the top of the page
// ---------------------------------------------------------------------------

const _DUNBAR_LABEL: Record<number, string> = {
  1: "Inner 5",
  2: "Close 15",
  3: "Sympathy 50",
  4: "Active 150",
  5: "Acquaintance",
};

function dunbarLabel(tier: number | null | undefined): string | null {
  if (tier == null) return null;
  return _DUNBAR_LABEL[tier] ?? `Tier ${tier}`;
}

function PulseStrip({
  entityId,
  dunbarTier,
}: {
  entityId: string;
  dunbarTier: number | null;
}) {
  const { data: timelineItems, isLoading: timelineLoading } =
    useEntityTimeline(entityId);
  const { data: gifts } = useEntityGifts(entityId);
  const { data: loans } = useEntityLoans(entityId);

  const lastInteraction = useMemo(() => {
    if (!timelineItems) return null;
    return timelineItems.find(
      (it: EntityTimelineItem) => it.kind === "interaction" && it.valid_at,
    );
  }, [timelineItems]);

  // `now` is captured once at mount via lazy state init — Date.now() is impure
  // and would trip react-hooks/purity inside useMemo. The cadence window only
  // needs to be approximate, so a per-mount snapshot is fine.
  const [mountedAt] = useState(() => Date.now());
  const cadence30d = useMemo(() => {
    if (!timelineItems) return null;
    const cutoff = mountedAt - 30 * 24 * 60 * 60 * 1000;
    return timelineItems.filter(
      (it: EntityTimelineItem) =>
        it.kind === "interaction" &&
        it.valid_at &&
        new Date(it.valid_at).getTime() >= cutoff,
    ).length;
  }, [timelineItems, mountedAt]);

  const openLoops = useMemo(() => {
    const giftOpen = (gifts ?? []).filter(
      (g: EntityGift) =>
        g.status && g.status !== "given" && g.status !== "thanked",
    ).length;
    const loanOpen = (loans ?? []).filter(
      (l: EntityLoan) => l.settled !== "true",
    ).length;
    return giftOpen + loanOpen;
  }, [gifts, loans]);

  const tierLabel = dunbarLabel(dunbarTier);
  const isLoading = timelineLoading;

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <PulseTile
        label="Dunbar tier"
        value={tierLabel ?? "Unranked"}
        muted={tierLabel === null}
      />
      <PulseTile
        label="Last interaction"
        value={
          isLoading
            ? "..."
            : lastInteraction?.valid_at
              ? formatDistanceToNow(new Date(lastInteraction.valid_at), {
                  addSuffix: true,
                })
              : "None recorded"
        }
        muted={!lastInteraction}
      />
      <PulseTile
        label="Last 30 days"
        value={
          isLoading
            ? "..."
            : cadence30d === null || cadence30d === 0
              ? "Quiet"
              : `${cadence30d} interaction${cadence30d === 1 ? "" : "s"}`
        }
        muted={cadence30d === 0}
      />
      <PulseTile
        label="Open loops"
        value={
          isLoading
            ? "..."
            : openLoops === 0
              ? "None"
              : `${openLoops} unresolved`
        }
        muted={openLoops === 0}
        emphasis={openLoops > 0}
      />
    </div>
  );
}

function PulseTile({
  label,
  value,
  muted = false,
  emphasis = false,
}: {
  label: string;
  value: string;
  muted?: boolean;
  emphasis?: boolean;
}) {
  return (
    <div
      className={
        "rounded-md border px-3 py-2.5 " +
        (emphasis ? "bg-accent" : "bg-card")
      }
    >
      <p className="text-muted-foreground text-[11px] uppercase tracking-wide">
        {label}
      </p>
      <p
        className={
          "mt-1 text-sm font-medium leading-tight " +
          (muted ? "text-muted-foreground" : "text-foreground")
        }
      >
        {value}
      </p>
    </div>
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
      <span
        className="text-muted-foreground shrink-0 text-xs tabular-nums"
        title={date?.toLocaleString() ?? undefined}
      >
        {date ? formatDistanceToNow(date, { addSuffix: true }) : "—"}
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
                {format(new Date(gift.created_at), "MMM d, yyyy")}
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
      <span
        className="text-muted-foreground shrink-0 text-xs tabular-nums"
        title={date?.toLocaleString() ?? undefined}
      >
        {date ? formatDistanceToNow(date, { addSuffix: true }) : ""}
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
      <span
        className="text-muted-foreground shrink-0 text-xs tabular-nums"
        title={created.toLocaleString()}
      >
        {format(created, "MMM d, yyyy")}
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
// Practical drawer — collapsed by default, holds admin-only sections
// ---------------------------------------------------------------------------

function PracticalDrawer({
  entity,
  forceOpen,
  children,
}: {
  entity: { metadata: Record<string, unknown>; created_at: string; updated_at: string };
  forceOpen: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(forceOpen);

  return (
    <section className="rounded-md border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="hover:bg-muted/40 flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition-colors"
      >
        <span className="text-sm font-medium">
          Practical details
          {forceOpen && (
            <span className="text-muted-foreground ml-2 text-xs">
              (action needed)
            </span>
          )}
        </span>
        {open ? (
          <ChevronDown className="text-muted-foreground h-4 w-4" />
        ) : (
          <ChevronRight className="text-muted-foreground h-4 w-4" />
        )}
      </button>
      {open && (
        <div className="space-y-4 border-t px-4 py-4">
          {children}
          <ProvenanceFooter entity={entity} />
        </div>
      )}
    </section>
  );
}

function ProvenanceFooter({
  entity,
}: {
  entity: { metadata: Record<string, unknown>; created_at: string; updated_at: string };
}) {
  const sourceButler = entity.metadata?.source_butler;
  const sourceScope = entity.metadata?.source_scope;
  const _DISPLAY_EXCLUDED = new Set([
    "source_butler",
    "source_scope",
    "unidentified",
  ]);
  const extraMetadata = Object.fromEntries(
    Object.entries(entity.metadata).filter(([k]) => !_DISPLAY_EXCLUDED.has(k)),
  );
  const hasExtra = Object.keys(extraMetadata).length > 0;

  return (
    <div className="text-muted-foreground space-y-2 border-t pt-3 text-xs">
      <div className="flex flex-wrap gap-x-6 gap-y-1">
        {!!sourceButler && (
          <span>
            Source butler:{" "}
            <span className="text-foreground font-medium">{String(sourceButler)}</span>
          </span>
        )}
        {!!sourceScope && (
          <span>
            Scope:{" "}
            <span className="text-foreground font-medium">{String(sourceScope)}</span>
          </span>
        )}
        <span>Created {format(new Date(entity.created_at), "MMM d, yyyy")}</span>
        <span>Updated {format(new Date(entity.updated_at), "MMM d, yyyy")}</span>
      </div>
      {hasExtra && (
        <details>
          <summary className="cursor-pointer text-xs hover:text-foreground">
            Raw metadata
          </summary>
          <pre className="bg-muted mt-2 overflow-x-auto rounded p-3 text-[11px]">
            {JSON.stringify(extraMetadata, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// EntityDetailPage
// ---------------------------------------------------------------------------

export default function EntityDetailPage() {
  const { entityId } = useParams<{ entityId: string }>();
  const [factsLimit, setFactsLimit] = useState(FACTS_PAGE_SIZE);
  const { data, isLoading, isFetching, error } = useEntity(entityId, {
    facts_limit: factsLimit,
  });
  const entity = data?.data;
  const updateEntity = useUpdateEntity();
  const promoteEntity = usePromoteEntity();

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

  return (
    <div className="space-y-8">
      <Breadcrumbs
        items={[
          { label: "Entities", href: "/entities" },
          { label: entity?.canonical_name ?? entityId ?? "Entity" },
        ]}
      />

      {isLoading && (
        <div className="space-y-4">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      )}

      {error && (
        <div className="text-destructive py-12 text-center text-sm">
          Failed to load entity. {(error as Error).message}
        </div>
      )}

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
            <PulseStrip entityId={entityId} dunbarTier={entity.dunbar_tier ?? null} />
          </section>

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
            <EntityInfoSection
              entityId={entity.id}
              entries={entity.entity_info ?? []}
              isOwner={isOwner}
            />
            {isOwner && (
              <TelegramSessionSetup
                entityId={entity.id}
                entries={entity.entity_info ?? []}
              />
            )}
          </PracticalDrawer>
        </>
      )}
    </div>
  );
}
