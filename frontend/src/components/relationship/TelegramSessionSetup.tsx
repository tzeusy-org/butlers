import * as React from "react";
import { Check, Loader2 } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  getTelegramSessionStatus,
  telegramSendCode,
  telegramVerifyCode,
} from "@/api/index";
import type { EntityInfoEntry } from "@/api/types";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useRevealEntitySecret } from "@/hooks/use-memory";

/**
 * Primary poll interval for Telegram CLI-auth session polling queries (bu-ep4ks.15).
 * No fleet-bus event type covers this domain (see
 * event-cache-registry.ts's EVENT_CACHE_REGISTRY) -- this cadence IS
 * the update path, not a reconciliation sweep.
 */
const TELEGRAM_SESSION_POLL_MS = 30_000;

type TelegramStep =
  | "idle"
  | "loading_creds"
  | "credentials"
  | "phone"
  | "code"
  | "two_fa"
  | "success";

function AccountWideScopeConsent({
  checked,
  disabled,
  inputId,
  onCheckedChange,
}: {
  checked: boolean;
  disabled: boolean;
  inputId: string;
  onCheckedChange: (checked: boolean) => void;
}) {
  const disclosureId = `${inputId}-disclosure`;
  return (
    <fieldset className="space-y-2 rounded-md border border-border p-3">
      <legend className="px-1 text-sm font-medium">Account-wide ingestion scope</legend>
      <p id={disclosureId} className="text-sm text-muted-foreground">
        Butlers will read and ingest new messages from every Telegram direct chat, group,
        supergroup, and channel visible to this account. The current connector has no per-chat
        or per-sender controls, and messages enter the normal routing pipeline. Historical
        messages are read only when optional backfill is configured.
      </p>
      <div className="flex items-start gap-2">
        <Checkbox
          id={inputId}
          checked={checked}
          disabled={disabled}
          aria-describedby={disclosureId}
          onCheckedChange={(value) => onCheckedChange(value === true)}
        />
        <Label htmlFor={inputId} className="leading-snug">
          I acknowledge the account-wide Telegram ingestion scope described above.
        </Label>
      </div>
    </fieldset>
  );
}

/**
 * Guided Telegram user-session bootstrap shared by the entity detail view and
 * Secrets Passport. The API hash is only sent to the session-auth endpoint;
 * that endpoint persists the credential trio after successful verification.
 */
export function TelegramSessionSetup({
  entityId,
  entries,
  startImmediately = false,
}: {
  entityId: string;
  entries: EntityInfoEntry[];
  /** Passport starts a new session; entity detail begins with status. */
  startImmediately?: boolean;
}) {
  const queryClient = useQueryClient();
  const { data: status, isPending: isStatusPending, isError, refetch } = useQuery({
    queryKey: ["telegram-session-status"],
    queryFn: getTelegramSessionStatus,
    refetchInterval: TELEGRAM_SESSION_POLL_MS,
  });

  const apiIdEntry = entries.find((entry) => entry.type === "telegram_api_id");
  const apiHashEntry = entries.find((entry) => entry.type === "telegram_api_hash");
  const revealMutation = useRevealEntitySecret();
  const visibleApiId = apiIdEntry?.value ?? "";
  const fieldId = React.useId();

  const [step, setStep] = React.useState<TelegramStep>(
    startImmediately ? "credentials" : "idle",
  );
  const [apiId, setApiId] = React.useState(visibleApiId);
  const [apiHash, setApiHash] = React.useState("");
  const [phone, setPhone] = React.useState("");
  const [code, setCode] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [scopeConsent, setScopeConsent] = React.useState(false);
  const [sessionToken, setSessionToken] = React.useState("");
  const [userName, setUserName] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  async function handleStart() {
    if (!apiIdEntry || !apiHashEntry) {
      setStep("credentials");
      return;
    }

    setStep("loading_creds");
    setError(null);

    try {
      let resolvedApiId = apiIdEntry.value;
      if (!resolvedApiId && apiIdEntry.secured) {
        resolvedApiId = await new Promise<string>((resolve, reject) => {
          revealMutation.mutate(
            { entityId, infoId: apiIdEntry.id },
            { onSuccess: (data) => resolve(data.value ?? ""), onError: reject },
          );
        });
      }
      const resolvedApiHash = await new Promise<string>((resolve, reject) => {
        revealMutation.mutate(
          { entityId, infoId: apiHashEntry.id },
          { onSuccess: (data) => resolve(data.value ?? ""), onError: reject },
        );
      });
      setApiId(resolvedApiId ?? "");
      setApiHash(resolvedApiHash);
      setStep("phone");
    } catch {
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
    onError: (cause) => {
      setError(cause instanceof Error ? cause.message : "Failed to send code");
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
    onError: (cause) => {
      setError(cause instanceof Error ? cause.message : "Verification failed");
    },
  });

  function handleSendCode() {
    setError(null);
    const numericApiId = Number.parseInt(apiId.trim(), 10);
    if (Number.isNaN(numericApiId)) {
      setError("API ID must be a number");
      return;
    }
    if (!apiHash.trim()) {
      setError("API hash is required");
      return;
    }
    if (!phone.trim()) {
      setError("Phone number is required");
      return;
    }
    if (!scopeConsent) {
      setError("Acknowledge the account-wide Telegram ingestion scope before continuing.");
      return;
    }
    sendCodeMutation.mutate({
      api_id: numericApiId,
      api_hash: apiHash.trim(),
      phone: phone.trim(),
      scope_consent: true,
    });
  }

  function handleVerifyCode() {
    setError(null);
    if (!code.trim()) {
      setError("Please enter the verification code");
      return;
    }
    verifyMutation.mutate({ session_token: sessionToken, code: code.trim() });
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
    setScopeConsent(false);
    setSessionToken("");
    setUserName(null);
    setError(null);
  }

  const isPending = sendCodeMutation.isPending || verifyMutation.isPending;
  const apiIdInputId = `${fieldId}-api-id`;
  const apiHashInputId = `${fieldId}-api-hash`;
  const phoneInputId = `${fieldId}-phone`;
  const codeInputId = `${fieldId}-code`;
  const passwordInputId = `${fieldId}-password`;
  const scopeConsentInputId = `${fieldId}-account-wide-scope-consent`;

  if (isError) {
    return (
      <section className="space-y-3" aria-live="polite">
        <Eyebrow as="div">Telegram user session</Eyebrow>
        <p className="text-sm text-muted-foreground" role="status">
          Session status unavailable.
        </p>
        <Button variant="outline" size="sm" onClick={() => void refetch()}>Retry status</Button>
      </section>
    );
  }

  if (isStatusPending || status == null) {
    return (
      <section className="space-y-3" aria-live="polite">
        <Eyebrow as="div">Telegram user session</Eyebrow>
        <Skeleton className="h-8 w-48" />
      </section>
    );
  }

  return (
    <section className="space-y-3" data-telegram-session-setup="true">
      <div className="flex items-center justify-between">
        <Eyebrow as="div">Telegram user session</Eyebrow>
        {status?.ready && status.has_scope_consent && (
          <span
            aria-label="Telegram session ready"
            className="h-2 w-2 rounded-full bg-[var(--green)]"
            role="img"
          />
        )}
      </div>

      {status && step === "idle" && (
        <div className="flex flex-col gap-1.5 text-sm" aria-label="Telegram credential status">
          <span>{status.has_api_id ? "+" : "−"} API ID</span>
          <span>{status.has_api_hash ? "+" : "−"} API hash</span>
          <span>{status.has_session ? "+" : "−"} session</span>
          <span>{status.has_scope_consent ? "+" : "−"} account-wide ingestion consent</span>
        </div>
      )}

      {status?.ready && !status.has_scope_consent && step === "idle" && (
        <p className="text-sm text-muted-foreground" role="status">
          Account-wide ingestion is disabled until you review and acknowledge its scope.
        </p>
      )}

      {step === "idle" && (
        <Button
          variant={status?.ready ? "outline" : "default"}
          size="sm"
          onClick={handleStart}
        >
          {status?.ready
            ? status.has_scope_consent
              ? "Regenerate session"
              : "Review scope and enable"
            : "Set up Telegram session"}
        </Button>
      )}

      {step === "loading_creds" && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground" aria-live="polite">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading existing credentials...
        </div>
      )}

      {step === "credentials" && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Create an API application at{" "}
            <a
              href="https://my.telegram.org/apps"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline"
            >
              my.telegram.org/apps
            </a>{" "}
            and enter its credentials to send a verification code.
          </p>
          <div className="grid gap-2">
            <div className="space-y-1">
              <Label className="text-xs" htmlFor={apiIdInputId}>Telegram API ID</Label>
              <Input
                id={apiIdInputId}
                className="h-8 text-sm"
                inputMode="numeric"
                placeholder="12345678"
                value={apiId}
                onChange={(event) => setApiId(event.target.value)}
                disabled={isPending}
                autoFocus
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs" htmlFor={apiHashInputId}>Telegram API hash</Label>
              <Input
                id={apiHashInputId}
                className="h-8 text-sm"
                type="password"
                autoComplete="off"
                placeholder="a1b2c3d4e5f6..."
                value={apiHash}
                onChange={(event) => setApiHash(event.target.value)}
                disabled={isPending}
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs" htmlFor={phoneInputId}>Telegram phone number</Label>
              <Input
                id={phoneInputId}
                className="h-8 text-sm"
                type="tel"
                autoComplete="tel"
                placeholder="+1234567890"
                value={phone}
                onChange={(event) => setPhone(event.target.value)}
                disabled={isPending}
                onKeyDown={(event) => { if (event.key === "Enter") handleSendCode(); }}
              />
            </div>
          </div>
          <AccountWideScopeConsent
            checked={scopeConsent}
            disabled={isPending}
            inputId={scopeConsentInputId}
            onCheckedChange={setScopeConsent}
          />
          <div className="flex gap-2">
            <Button size="sm" onClick={handleSendCode} disabled={isPending || !scopeConsent}>
              {sendCodeMutation.isPending && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
              Send code
            </Button>
            <Button variant="ghost" size="sm" onClick={handleReset} disabled={isPending}>Cancel</Button>
          </div>
        </div>
      )}

      {step === "phone" && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Using existing API credentials. Enter your phone number to receive a verification code.
          </p>
          <div className="space-y-1">
            <Label className="text-xs" htmlFor={phoneInputId}>Telegram phone number</Label>
            <Input
              id={phoneInputId}
              className="h-8 w-56 text-sm"
              type="tel"
              autoComplete="tel"
              placeholder="+1234567890"
              value={phone}
              onChange={(event) => setPhone(event.target.value)}
              disabled={isPending}
              autoFocus
              onKeyDown={(event) => { if (event.key === "Enter") handleSendCode(); }}
            />
          </div>
          <AccountWideScopeConsent
            checked={scopeConsent}
            disabled={isPending}
            inputId={scopeConsentInputId}
            onCheckedChange={setScopeConsent}
          />
          <div className="flex gap-2">
            <Button size="sm" onClick={handleSendCode} disabled={isPending || !scopeConsent}>
              {sendCodeMutation.isPending && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
              Send code
            </Button>
            <Button variant="ghost" size="sm" onClick={handleReset} disabled={isPending}>Cancel</Button>
          </div>
        </div>
      )}

      {step === "code" && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            A verification code was sent to your Telegram app. Enter it below.
          </p>
          <div className="space-y-1">
            <Label className="text-xs" htmlFor={codeInputId}>Verification code</Label>
            <Input
              id={codeInputId}
              className="h-8 w-48 text-sm font-mono tracking-widest"
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="12345"
              value={code}
              onChange={(event) => setCode(event.target.value)}
              disabled={isPending}
              autoFocus
              onKeyDown={(event) => { if (event.key === "Enter") handleVerifyCode(); }}
            />
          </div>
          <div className="flex gap-2">
            <Button size="sm" onClick={handleVerifyCode} disabled={isPending}>
              {verifyMutation.isPending && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
              Verify
            </Button>
            <Button variant="ghost" size="sm" onClick={handleReset} disabled={isPending}>Cancel</Button>
          </div>
        </div>
      )}

      {step === "two_fa" && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">Two-factor authentication is enabled. Enter your 2FA password.</p>
          <div className="space-y-1">
            <Label className="text-xs" htmlFor={passwordInputId}>Two-factor password</Label>
            <Input
              id={passwordInputId}
              className="h-8 w-64 text-sm"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={isPending}
              autoFocus
              onKeyDown={(event) => { if (event.key === "Enter") handleSubmit2FA(); }}
            />
          </div>
          <div className="flex gap-2">
            <Button size="sm" onClick={handleSubmit2FA} disabled={isPending}>
              {verifyMutation.isPending && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
              Submit
            </Button>
            <Button variant="ghost" size="sm" onClick={handleReset} disabled={isPending}>Cancel</Button>
          </div>
        </div>
      )}

      {step === "success" && (
        <div className="space-y-2" aria-live="polite">
          <div className="flex items-center gap-2 text-sm text-[var(--green)]">
            <Check className="h-4 w-4" />
            <span>Session created{userName ? ` for ${userName}` : ""}. Your API credentials and session are stored.</span>
          </div>
          <Button variant="ghost" size="sm" onClick={handleReset}>Done</Button>
        </div>
      )}

      {error && <p className="text-sm text-destructive" role="alert">{error}</p>}
    </section>
  );
}
