/**
 * HomeAssistantSetupCard — connection setup card for the settings page.
 *
 * Displays the Home Assistant connection state with a color-coded health badge.
 * States:
 * - connected (green badge): masked URL, Disconnect button, Re-configure option
 * - not_configured (outline badge): URL + token inputs with reveal toggle
 * - disconnected (amber/secondary badge): Re-configure form
 *
 * Key UX flows:
 * 1. Configure: URL input + token input (password with reveal toggle) + Save
 * 2. Delete: confirmation dialog before removing credentials
 * 3. Re-configure: same form pre-filled with existing masked URL
 * 4. Specific error messages for unreachable / auth failure / unexpected errors
 */

import { useEffect, useState } from "react";

import { Eye, EyeOff } from "lucide-react";
import { toast } from "sonner";

import type { HomeAssistantState } from "@/api/index.ts";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useConfigureHomeAssistant,
  useDeleteHomeAssistantConfig,
  useHomeAssistantStatus,
} from "@/hooks/use-home-assistant";

// ---------------------------------------------------------------------------
// Badge helpers
// ---------------------------------------------------------------------------

function stateBadgeVariant(
  state: HomeAssistantState,
): "default" | "secondary" | "destructive" | "outline" {
  switch (state) {
    case "connected":
      return "default";
    case "disconnected":
      return "secondary";
    case "not_configured":
      return "outline";
    default:
      return "outline";
  }
}

function stateBadgeLabel(state: HomeAssistantState): string {
  switch (state) {
    case "connected":
      return "Connected";
    case "disconnected":
      return "Disconnected";
    case "not_configured":
      return "Not configured";
    default:
      return state;
  }
}

// ---------------------------------------------------------------------------
// HAConfigForm — URL + token input form
// ---------------------------------------------------------------------------

interface HAConfigFormProps {
  initialUrl?: string;
  onCancel?: () => void;
  isEdit?: boolean;
}

function HAConfigForm({ initialUrl = "", onCancel, isEdit = false }: HAConfigFormProps) {
  const [url, setUrl] = useState(initialUrl);
  const [token, setToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const configureMutation = useConfigureHomeAssistant();

  // Derive a user-friendly error message from the raw API error detail.
  function extractErrorMessage(err: unknown): string {
    if (err instanceof Error) {
      const msg = err.message;
      // Surface actionable messages from the backend
      if (msg.toLowerCase().includes("authentication failed") || msg.toLowerCase().includes("401") || msg.toLowerCase().includes("403")) {
        return "Authentication failed — check that the long-lived access token is valid.";
      }
      if (msg.toLowerCase().includes("timed out") || msg.toLowerCase().includes("timeout") || msg.toLowerCase().includes("unreachable") || msg.toLowerCase().includes("could not reach")) {
        return "Could not reach Home Assistant — check the URL and that the server is accessible.";
      }
      if (msg.toLowerCase().includes("unexpected response")) {
        return "Home Assistant returned an unexpected response — check the server status.";
      }
      return msg;
    }
    return "Failed to save Home Assistant configuration.";
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim() || !token.trim()) return;
    try {
      const result = await configureMutation.mutateAsync({
        url: url.trim(),
        token: token.trim(),
      });
      toast.success(result.message || "Home Assistant configured successfully");
    } catch (err) {
      toast.error(extractErrorMessage(err));
    }
  }

  const isValid = url.trim().length > 0 && token.trim().length > 0;

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="ha-url">Home Assistant URL</Label>
        <Input
          id="ha-url"
          type="url"
          placeholder="http://homeassistant.local:8123"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          disabled={configureMutation.isPending}
          autoComplete="off"
        />
        <p className="text-xs text-muted-foreground">
          The base URL of your Home Assistant instance (e.g. http://homeassistant.local:8123).
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="ha-token">Long-lived access token</Label>
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Input
              id="ha-token"
              type={showToken ? "text" : "password"}
              placeholder="Enter your long-lived access token"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              disabled={configureMutation.isPending}
              autoComplete="new-password"
              className="pr-9"
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="absolute right-1 top-1/2 -translate-y-1/2 h-7 w-7 text-muted-foreground"
              onClick={() => setShowToken((v) => !v)}
              aria-label={showToken ? "Hide token" : "Show token"}
              tabIndex={-1}
            >
              {showToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </Button>
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          Create a long-lived access token in Home Assistant under{" "}
          <span className="font-medium">Profile → Security → Long-lived access tokens</span>.
        </p>
      </div>

      {/* Inline error feedback */}
      {configureMutation.isError && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3">
          <p className="text-sm text-destructive">
            {extractErrorMessage(configureMutation.error)}
          </p>
        </div>
      )}

      <div className="flex items-center gap-2">
        <Button
          type="submit"
          size="sm"
          disabled={!isValid || configureMutation.isPending}
        >
          {configureMutation.isPending
            ? "Validating..."
            : isEdit
              ? "Update"
              : "Save"}
        </Button>
        {onCancel && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={onCancel}
            disabled={configureMutation.isPending}
          >
            Cancel
          </Button>
        )}
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// DeleteDialog — confirmation before removing credentials
// ---------------------------------------------------------------------------

function DeleteDialog({
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
          <DialogTitle>Disconnect Home Assistant?</DialogTitle>
          <DialogDescription>
            Your Home Assistant credentials will be removed. You{"'"}ll need to re-enter your URL
            and access token to reconnect.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isPending}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={onConfirm} disabled={isPending}>
            {isPending ? "Disconnecting..." : "Disconnect"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// HomeAssistantSection — embeddable variant (no Card wrapper)
// ---------------------------------------------------------------------------

export function HomeAssistantSection() {
  const statusQuery = useHomeAssistantStatus();
  const deleteMutation = useDeleteHomeAssistantConfig();

  const [showConfigForm, setShowConfigForm] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  const status = statusQuery.data;
  const displayState: HomeAssistantState = status?.state ?? "not_configured";

  // Reset form visibility when status transitions to connected
  useEffect(() => {
    if (displayState === "connected") {
      setShowConfigForm(false);
    }
  }, [displayState]);

  async function handleDelete() {
    try {
      await deleteMutation.mutateAsync();
      toast.success("Home Assistant disconnected");
      setDeleteDialogOpen(false);
    } catch {
      toast.error("Failed to disconnect Home Assistant");
    }
  }

  if (statusQuery.isLoading) {
    return (
      <div>
        <h3 className="leading-none font-semibold">Home Assistant</h3>
        <Skeleton className="mt-3 h-12 w-full" />
      </div>
    );
  }

  if (statusQuery.isError) {
    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <h3 className="leading-none font-semibold">Home Assistant</h3>
          <Badge variant="destructive">Error</Badge>
        </div>
        <p className="text-sm text-destructive">
          Failed to fetch Home Assistant status. Please refresh to try again.
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="leading-none font-semibold">Home Assistant</h3>
            <p className="text-muted-foreground text-sm mt-2">
              Connect your Home Assistant instance to let butlers observe and act on your
              smart home state.
            </p>
          </div>
          <Badge variant={stateBadgeVariant(displayState)}>
            {stateBadgeLabel(displayState)}
          </Badge>
        </div>

        {/* Connected: show masked URL */}
        {displayState === "connected" && status?.masked_url && !showConfigForm && (
          <div className="space-y-1 text-sm">
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground">URL</span>
              <span className="font-mono">{status.masked_url}</span>
            </div>
          </div>
        )}

        {/* Configuration form */}
        {(displayState === "not_configured" || displayState === "disconnected" || showConfigForm) && (
          <HAConfigForm
            initialUrl={displayState === "connected" ? (status?.masked_url ?? "") : ""}
            onCancel={showConfigForm && displayState === "connected" ? () => setShowConfigForm(false) : undefined}
            isEdit={displayState === "connected" || displayState === "disconnected"}
          />
        )}

        {/* Action buttons when connected */}
        {displayState === "connected" && !showConfigForm && (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setShowConfigForm(true)}
            >
              Re-configure
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="text-destructive hover:bg-destructive/10"
              onClick={() => setDeleteDialogOpen(true)}
            >
              Disconnect
            </Button>
          </div>
        )}
      </div>

      <DeleteDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        onConfirm={handleDelete}
        isPending={deleteMutation.isPending}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// HomeAssistantSetupCard — standalone Card variant
// ---------------------------------------------------------------------------

export function HomeAssistantSetupCard() {
  const statusQuery = useHomeAssistantStatus();
  const deleteMutation = useDeleteHomeAssistantConfig();

  const [showConfigForm, setShowConfigForm] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  const status = statusQuery.data;
  const displayState: HomeAssistantState = status?.state ?? "not_configured";

  // Reset form visibility when status transitions to connected
  useEffect(() => {
    if (displayState === "connected") {
      setShowConfigForm(false);
    }
  }, [displayState]);

  async function handleDelete() {
    try {
      await deleteMutation.mutateAsync();
      toast.success("Home Assistant disconnected");
      setDeleteDialogOpen(false);
    } catch {
      toast.error("Failed to disconnect Home Assistant");
    }
  }

  if (statusQuery.isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Home Assistant</CardTitle>
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
              <CardTitle>Home Assistant</CardTitle>
            </div>
            <Badge variant="destructive">Error</Badge>
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-destructive">
            Failed to fetch Home Assistant status. Please refresh to try again.
          </p>
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
              <CardTitle>Home Assistant</CardTitle>
              <CardDescription className="mt-1">
                Connect your Home Assistant instance to let butlers observe and act on your
                smart home state.
              </CardDescription>
            </div>
            <Badge variant={stateBadgeVariant(displayState)}>
              {stateBadgeLabel(displayState)}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Connected: show masked URL */}
          {displayState === "connected" && status?.masked_url && !showConfigForm && (
            <div className="space-y-1 text-sm">
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">URL</span>
                <span className="font-mono">{status.masked_url}</span>
              </div>
            </div>
          )}

          {/* Configuration form */}
          {(displayState === "not_configured" || displayState === "disconnected" || showConfigForm) && (
            <HAConfigForm
              initialUrl={displayState === "connected" ? (status?.masked_url ?? "") : ""}
              onCancel={showConfigForm && displayState === "connected" ? () => setShowConfigForm(false) : undefined}
              isEdit={displayState === "connected" || displayState === "disconnected"}
            />
          )}

          {/* Action buttons when connected */}
          {displayState === "connected" && !showConfigForm && (
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowConfigForm(true)}
              >
                Re-configure
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="text-destructive hover:bg-destructive/10"
                onClick={() => setDeleteDialogOpen(true)}
              >
                Disconnect
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      <DeleteDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        onConfirm={handleDelete}
        isPending={deleteMutation.isPending}
      />
    </>
  );
}
