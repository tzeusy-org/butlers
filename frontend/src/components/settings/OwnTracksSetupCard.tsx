/**
 * OwnTracksSetupCard — webhook setup and connection status card for the settings page.
 *
 * Displays the OwnTracks connector state with a color-coded health badge.
 * States:
 * - active (green badge): last event timestamp, events today, webhook URL, token copy
 * - idle (amber/secondary badge): token configured but no recent events; hint shown
 * - not_configured (outline badge): token generation flow
 *
 * Key UX flows:
 * 1. Token generation: one-click generate, copy-to-clipboard reveal panel
 * 2. Token regeneration: confirmation dialog before overwriting existing token
 * 3. Webhook URL: always visible once configured (copy-to-clipboard)
 * 4. Setup instructions: inline iOS/Android differentiated guide
 * 5. No-events hint: shown after setup when no events received (idle state)
 */

import { useState } from "react";

import { Check, Copy, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import type { OwnTracksState } from "@/api/index.ts";
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
import { Skeleton } from "@/components/ui/skeleton";
import {
  useOwnTracksConfig,
  useOwnTracksGenerateToken,
  useOwnTracksStatus,
} from "@/hooks/use-owntracks";

// ---------------------------------------------------------------------------
// Badge helpers
// ---------------------------------------------------------------------------

function stateBadgeVariant(
  state: OwnTracksState,
): "default" | "secondary" | "destructive" | "outline" {
  switch (state) {
    case "active":
      return "default";
    case "idle":
      return "secondary";
    case "not_configured":
      return "outline";
    default:
      return "outline";
  }
}

function stateBadgeLabel(state: OwnTracksState): string {
  switch (state) {
    case "active":
      return "Active";
    case "idle":
      return "Idle";
    case "not_configured":
      return "Not configured";
    default:
      return state;
  }
}

function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ---------------------------------------------------------------------------
// CopyButton — icon button that copies text and shows a transient check
// ---------------------------------------------------------------------------

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error(`Failed to copy ${label}`);
    }
  }

  return (
    <Button
      variant="ghost"
      size="icon"
      className="h-7 w-7 shrink-0"
      onClick={handleCopy}
      title={`Copy ${label}`}
      aria-label={`Copy ${label}`}
    >
      {copied ? <Check className="h-3.5 w-3.5 text-green-600" /> : <Copy className="h-3.5 w-3.5" />}
    </Button>
  );
}

// ---------------------------------------------------------------------------
// WebhookURLRow — read-only copyable webhook URL
// ---------------------------------------------------------------------------

function WebhookURLRow({ url }: { url: string }) {
  return (
    <div className="space-y-1">
      <span className="text-xs font-medium text-muted-foreground">Webhook URL</span>
      <div className="flex items-center gap-1 rounded-md border bg-muted/40 px-2 py-1.5">
        <span className="min-w-0 flex-1 truncate font-mono text-xs">{url}</span>
        <CopyButton value={url} label="webhook URL" />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// TokenRevealPanel — shows a freshly generated token with copy affordance
// ---------------------------------------------------------------------------

function TokenRevealPanel({ token, onDismiss }: { token: string; onDismiss: () => void }) {
  return (
    <div className="rounded-md border border-amber-300 bg-amber-50 p-3 dark:border-amber-700 dark:bg-amber-950/30">
      <p className="mb-2 text-xs font-medium text-amber-800 dark:text-amber-200">
        Copy this token now — it {"won't"} be shown again.
      </p>
      <div className="flex items-center gap-1 rounded-md border bg-background px-2 py-1.5">
        <span className="min-w-0 flex-1 truncate font-mono text-xs">{token}</span>
        <CopyButton value={token} label="bearer token" />
      </div>
      <Button
        variant="ghost"
        size="sm"
        className="mt-2 h-7 text-xs text-muted-foreground"
        onClick={onDismiss}
      >
        {"I've copied it"}
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// RegenerateDialog — confirmation before overwriting an existing token
// ---------------------------------------------------------------------------

function RegenerateDialog({
  open,
  onOpenChange,
  onConfirm,
  isPending,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  isPending: boolean;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Regenerate OwnTracks token?</DialogTitle>
          <DialogDescription>
            The existing token will be invalidated immediately. You{"'"}ll need to update the
            bearer token in the OwnTracks app on every device using this endpoint.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isPending}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={onConfirm} disabled={isPending}>
            {isPending ? "Regenerating..." : "Regenerate token"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// SetupInstructions — iOS/Android app configuration guide
// ---------------------------------------------------------------------------

function SetupInstructions({ webhookUrl, token }: { webhookUrl: string; token?: string }) {
  const [platform, setPlatform] = useState<"ios" | "android">("ios");

  return (
    <div className="rounded-md border bg-muted/30 p-3 text-sm">
      <div className="mb-3 flex items-center gap-2">
        <span className="text-xs font-medium text-muted-foreground">Platform</span>
        <div className="flex rounded-md border" role="tablist" aria-label="Platform">
          <button
            type="button"
            role="tab"
            aria-selected={platform === "ios"}
            onClick={() => setPlatform("ios")}
            className={`px-3 py-1 text-xs rounded-l-md transition-colors ${
              platform === "ios"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            iOS
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={platform === "android"}
            onClick={() => setPlatform("android")}
            className={`px-3 py-1 text-xs rounded-r-md border-l transition-colors ${
              platform === "android"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            Android
          </button>
        </div>
      </div>

      <ol className="space-y-1.5 text-xs text-muted-foreground">
        {platform === "ios" ? (
          <>
            <li>
              <span className="font-medium text-foreground">1.</span> Open OwnTracks → tap the{" "}
              <span className="font-medium text-foreground">i</span> (info) button → Settings.
            </li>
            <li>
              <span className="font-medium text-foreground">2.</span> Set{" "}
              <span className="font-mono text-foreground">Mode</span> to{" "}
              <span className="font-medium text-foreground">HTTP</span>.
            </li>
            <li>
              <span className="font-medium text-foreground">3.</span> Paste the webhook URL into{" "}
              <span className="font-mono text-foreground">URL</span>:{" "}
              <span className="font-mono text-foreground break-all">{webhookUrl}</span>
            </li>
            <li>
              <span className="font-medium text-foreground">4.</span> Tap{" "}
              <span className="font-mono text-foreground">Authentication</span> → set{" "}
              <span className="font-mono text-foreground">Username</span> to anything and{" "}
              <span className="font-mono text-foreground">Password</span> to the bearer token{" "}
              {token ? (
                <span className="font-mono text-foreground">({token.slice(0, 8)}…)</span>
              ) : (
                "generated above"
              )}
              .
            </li>
            <li>
              <span className="font-medium text-foreground">5.</span> Tap the location arrow to
              send a manual update and confirm events appear below.
            </li>
          </>
        ) : (
          <>
            <li>
              <span className="font-medium text-foreground">1.</span> Open OwnTracks → hamburger
              menu → Preferences → Connection.
            </li>
            <li>
              <span className="font-medium text-foreground">2.</span> Set{" "}
              <span className="font-mono text-foreground">Mode</span> to{" "}
              <span className="font-medium text-foreground">HTTP private</span>.
            </li>
            <li>
              <span className="font-medium text-foreground">3.</span> Set{" "}
              <span className="font-mono text-foreground">Host</span> to the webhook URL:{" "}
              <span className="font-mono text-foreground break-all">{webhookUrl}</span>
            </li>
            <li>
              <span className="font-medium text-foreground">4.</span> Under{" "}
              <span className="font-mono text-foreground">Identification</span>, set{" "}
              <span className="font-mono text-foreground">Password</span> to the bearer token{" "}
              {token ? (
                <span className="font-mono text-foreground">({token.slice(0, 8)}…)</span>
              ) : (
                "generated above"
              )}
              . Username can be anything.
            </li>
            <li>
              <span className="font-medium text-foreground">5.</span> Return to the map and tap
              the send icon to trigger a manual update.
            </li>
          </>
        )}
      </ol>
    </div>
  );
}

// ---------------------------------------------------------------------------
// NoEventsHint — troubleshooting guidance for idle state
// ---------------------------------------------------------------------------

function NoEventsHint() {
  return (
    <div className="rounded-md border border-amber-200 bg-amber-50/50 p-3 dark:border-amber-800 dark:bg-amber-950/20">
      <p className="text-xs font-medium text-amber-800 dark:text-amber-200">
        No location events received yet
      </p>
      <ul className="mt-1.5 space-y-1 text-xs text-amber-700 dark:text-amber-300">
        <li>• Confirm the webhook URL and bearer token match the app settings.</li>
        <li>• Ensure the app is not in low-power/background-restricted mode.</li>
        <li>• Tap the send icon in the app to trigger a manual update.</li>
        <li>• Check that your device can reach this server{"'"}s hostname.</li>
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// OwnTracksSection — embeddable variant (no Card wrapper)
// ---------------------------------------------------------------------------

export function OwnTracksSection() {
  const statusQuery = useOwnTracksStatus();
  const configQuery = useOwnTracksConfig();
  const generateTokenMutation = useOwnTracksGenerateToken();

  const [revealedToken, setRevealedToken] = useState<string | null>(null);
  const [regenerateDialogOpen, setRegenerateDialogOpen] = useState(false);
  const [showInstructions, setShowInstructions] = useState(false);

  const status = statusQuery.data;
  const config = configQuery.data;
  const displayState: OwnTracksState = status?.state ?? "not_configured";
  const tokenConfigured = status?.token_configured ?? false;

  async function handleGenerateToken() {
    try {
      const result = await generateTokenMutation.mutateAsync();
      setRevealedToken(result.token);
      toast.success("Bearer token generated");
    } catch {
      toast.error("Failed to generate token");
    }
  }

  async function handleRegenerateConfirm() {
    try {
      const result = await generateTokenMutation.mutateAsync();
      setRevealedToken(result.token);
      setRegenerateDialogOpen(false);
      toast.success("Bearer token regenerated");
    } catch {
      toast.error("Failed to regenerate token");
    }
  }

  if (statusQuery.isLoading) {
    return (
      <div>
        <h3 className="leading-none font-semibold">OwnTracks</h3>
        <Skeleton className="mt-3 h-12 w-full" />
      </div>
    );
  }

  if (statusQuery.isError) {
    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <h3 className="leading-none font-semibold">OwnTracks</h3>
          <Badge variant="destructive">Error</Badge>
        </div>
        <p className="text-sm text-muted-foreground">
          Could not load OwnTracks connector status. Please check your connection and try
          again.
        </p>
        <Button size="sm" variant="outline" onClick={() => statusQuery.refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  return (
    <>
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="leading-none font-semibold">OwnTracks</h3>
            <p className="text-muted-foreground text-sm mt-2">
              Receive location events from the OwnTracks mobile app via a private webhook.
              Raw GPS coordinates are not stored at rest (metadata tier by default).
            </p>
          </div>
          <Badge variant={stateBadgeVariant(displayState)}>
            {stateBadgeLabel(displayState)}
          </Badge>
        </div>

        {displayState === "active" && status && (
          <div className="space-y-1 text-sm">
            {status.last_event_at && (
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">Last event</span>
                <span>{formatDateTime(status.last_event_at)}</span>
              </div>
            )}
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground">Events today</span>
              <span>{status.events_today}</span>
            </div>
          </div>
        )}

        {displayState === "idle" && <NoEventsHint />}

        {displayState === "not_configured" && !tokenConfigured && (
          <p className="text-sm text-muted-foreground">
            Generate a bearer token to activate the OwnTracks webhook endpoint, then configure
            the app with the URL and token shown below.
          </p>
        )}

        {config && tokenConfigured && (
          <WebhookURLRow url={config.webhook_url} />
        )}

        {revealedToken && (
          <TokenRevealPanel
            token={revealedToken}
            onDismiss={() => setRevealedToken(null)}
          />
        )}

        <div className="flex flex-wrap items-center gap-2">
          {!tokenConfigured && (
            <Button
              size="sm"
              onClick={handleGenerateToken}
              disabled={generateTokenMutation.isPending}
            >
              {generateTokenMutation.isPending ? "Generating..." : "Generate token"}
            </Button>
          )}
          {tokenConfigured && (
            <Button
              variant="outline"
              size="sm"
              className="gap-1.5"
              onClick={() => setRegenerateDialogOpen(true)}
              disabled={generateTokenMutation.isPending}
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Regenerate token
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground"
            onClick={() => setShowInstructions((v) => !v)}
          >
            {showInstructions ? "Hide setup guide" : "Show setup guide"}
          </Button>
        </div>

        {showInstructions && config && (
          <SetupInstructions
            webhookUrl={config.webhook_url}
            token={revealedToken ?? undefined}
          />
        )}
      </div>

      <RegenerateDialog
        open={regenerateDialogOpen}
        onOpenChange={setRegenerateDialogOpen}
        onConfirm={handleRegenerateConfirm}
        isPending={generateTokenMutation.isPending}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// OwnTracksSetupCard — main component
// ---------------------------------------------------------------------------

export function OwnTracksSetupCard() {
  const statusQuery = useOwnTracksStatus();
  const configQuery = useOwnTracksConfig();
  const generateTokenMutation = useOwnTracksGenerateToken();

  const [revealedToken, setRevealedToken] = useState<string | null>(null);
  const [regenerateDialogOpen, setRegenerateDialogOpen] = useState(false);
  const [showInstructions, setShowInstructions] = useState(false);

  const status = statusQuery.data;
  const config = configQuery.data;
  const displayState: OwnTracksState = status?.state ?? "not_configured";
  const tokenConfigured = status?.token_configured ?? false;

  async function handleGenerateToken() {
    try {
      const result = await generateTokenMutation.mutateAsync();
      setRevealedToken(result.token);
      toast.success("Bearer token generated");
    } catch {
      toast.error("Failed to generate token");
    }
  }

  async function handleRegenerateConfirm() {
    try {
      const result = await generateTokenMutation.mutateAsync();
      setRevealedToken(result.token);
      setRegenerateDialogOpen(false);
      toast.success("Bearer token regenerated");
    } catch {
      toast.error("Failed to regenerate token");
    }
  }

  if (statusQuery.isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>OwnTracks</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-16 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (statusQuery.isError) {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>OwnTracks</CardTitle>
              <CardDescription className="mt-1">
                Could not load OwnTracks connector status. Please check your connection and try
                again.
              </CardDescription>
            </div>
            <Badge variant="destructive">Error</Badge>
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Connector actions are disabled until status can be fetched.
          </p>
          <Button
            className="mt-3"
            size="sm"
            variant="outline"
            onClick={() => statusQuery.refetch()}
          >
            Retry
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <>
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>OwnTracks</CardTitle>
              <CardDescription className="mt-1">
                Receive location events from the OwnTracks mobile app via a private webhook.
                Raw GPS coordinates are not stored at rest (metadata tier by default).
              </CardDescription>
            </div>
            <Badge variant={stateBadgeVariant(displayState)}>
              {stateBadgeLabel(displayState)}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Active state: show event stats */}
          {displayState === "active" && status && (
            <div className="space-y-1 text-sm">
              {status.last_event_at && (
                <div className="flex items-center gap-2">
                  <span className="text-muted-foreground">Last event</span>
                  <span>{formatDateTime(status.last_event_at)}</span>
                </div>
              )}
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">Events today</span>
                <span>{status.events_today}</span>
              </div>
            </div>
          )}

          {/* Idle state: no-events hint */}
          {displayState === "idle" && <NoEventsHint />}

          {/* not_configured description */}
          {displayState === "not_configured" && !tokenConfigured && (
            <p className="text-sm text-muted-foreground">
              Generate a bearer token to activate the OwnTracks webhook endpoint, then configure
              the app with the URL and token shown below.
            </p>
          )}

          {/* Webhook URL — shown whenever a token is configured */}
          {config && tokenConfigured && (
            <WebhookURLRow url={config.webhook_url} />
          )}

          {/* Freshly generated token reveal panel */}
          {revealedToken && (
            <TokenRevealPanel
              token={revealedToken}
              onDismiss={() => setRevealedToken(null)}
            />
          )}

          {/* Action buttons */}
          <div className="flex flex-wrap items-center gap-2">
            {!tokenConfigured && (
              <Button
                size="sm"
                onClick={handleGenerateToken}
                disabled={generateTokenMutation.isPending}
              >
                {generateTokenMutation.isPending ? "Generating..." : "Generate token"}
              </Button>
            )}
            {tokenConfigured && (
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5"
                onClick={() => setRegenerateDialogOpen(true)}
                disabled={generateTokenMutation.isPending}
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Regenerate token
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              className="text-muted-foreground"
              onClick={() => setShowInstructions((v) => !v)}
            >
              {showInstructions ? "Hide setup guide" : "Show setup guide"}
            </Button>
          </div>

          {/* Setup instructions panel */}
          {showInstructions && config && (
            <SetupInstructions
              webhookUrl={config.webhook_url}
              token={revealedToken ?? undefined}
            />
          )}
        </CardContent>
      </Card>

      <RegenerateDialog
        open={regenerateDialogOpen}
        onOpenChange={setRegenerateDialogOpen}
        onConfirm={handleRegenerateConfirm}
        isPending={generateTokenMutation.isPending}
      />
    </>
  );
}
