/**
 * WhatsAppPairModal — QR code display with auto-refresh and pairing polling.
 *
 * Flow:
 * 1. Modal opens → calls POST /pair/start → displays QR code
 * 2. Polls GET /pair/poll every 2 seconds
 * 3. On paired: shows success, calls onPaired(), closes after a brief delay
 * 4. On QR expiry: auto-requests a new QR (auto-refresh)
 * 5. After 120 seconds total timeout: shows "Pairing timed out" with Try Again
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useWhatsAppPairPoll, useWhatsAppPairStart } from "@/hooks/use-whatsapp";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type PairModalState =
  | "idle"
  | "loading"
  | "qr_ready"
  | "refreshing"
  | "paired"
  | "timeout"
  | "error";

const PAIRING_TIMEOUT_MS = 120_000; // 120 seconds total

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function WhatsAppPairModal({
  open,
  onOpenChange,
  onPaired,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onPaired: (phone: string | null) => void;
}) {
  const [modalState, setModalState] = useState<PairModalState>("idle");
  const [qrDataUri, setQrDataUri] = useState<string | null>(null);
  const [qrExpiresAt, setQrExpiresAt] = useState<Date | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const pairStartMutation = useWhatsAppPairStart();

  // Polling is active when qr_ready (waiting for scan)
  const pollEnabled = open && modalState === "qr_ready";
  const pollQuery = useWhatsAppPairPoll({ enabled: pollEnabled });

  // ---------------------------------------------------------------------------
  // Fetch a new QR code
  // ---------------------------------------------------------------------------

  const fetchQr = useCallback(async () => {
    setModalState("loading");
    setErrorMessage(null);
    try {
      const result = await pairStartMutation.mutateAsync();
      setQrDataUri(result.qr_data_uri);
      setQrExpiresAt(new Date(result.expires_at));
      setModalState("qr_ready");
    } catch (err) {
      const msg =
        err instanceof Error
          ? err.message
          : "Could not connect to WhatsApp bridge. Ensure the connector service is running.";
      setErrorMessage(msg);
      setModalState("error");
    }
  }, [pairStartMutation]);

  // ---------------------------------------------------------------------------
  // Start pairing when modal opens
  // ---------------------------------------------------------------------------

  /* eslint-disable react-hooks/set-state-in-effect -- modal state transitions are intentionally driven by open/close lifecycle. */
  useEffect(() => {
    if (!open) {
      // Reset state when modal is closed
      setModalState("idle");
      setQrDataUri(null);
      setQrExpiresAt(null);
      setErrorMessage(null);
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      return;
    }

    if (modalState === "idle") {
      // Set overall timeout
      timeoutRef.current = setTimeout(() => {
        setModalState("timeout");
      }, PAIRING_TIMEOUT_MS);
      fetchQr();
    }
  }, [open, modalState, fetchQr]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // ---------------------------------------------------------------------------
  // Handle QR expiry — auto-refresh
  // ---------------------------------------------------------------------------

  /* eslint-disable react-hooks/set-state-in-effect -- QR expiry transitions intentionally update local state machine. */
  useEffect(() => {
    if (modalState !== "qr_ready" || !qrExpiresAt) return;

    const remaining = qrExpiresAt.getTime() - Date.now();
    if (remaining <= 0) {
      // Already expired
      setModalState("refreshing");
      fetchQr();
      return;
    }

    const refreshTimer = setTimeout(() => {
      if (modalState === "qr_ready") {
        setModalState("refreshing");
        fetchQr();
      }
    }, remaining);

    return () => clearTimeout(refreshTimer);
  }, [modalState, qrExpiresAt, fetchQr]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // ---------------------------------------------------------------------------
  // Handle polling result
  // ---------------------------------------------------------------------------

  /* eslint-disable react-hooks/set-state-in-effect -- polling responses intentionally drive local modal state transitions. */
  useEffect(() => {
    if (!pollQuery.data) return;
    // Guard: once we've transitioned to "paired", ignore further poll results.
    // Without this, late-arriving poll ticks re-fire the success toast.
    if (modalState !== "qr_ready") return;

    const { status, phone } = pollQuery.data;

    if (status === "paired") {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      setModalState("paired");
      toast.success("WhatsApp paired successfully!");
      // Brief delay so user sees the success state before modal closes
      setTimeout(() => {
        onPaired(phone ?? null);
      }, 1200);
    } else if (status === "expired" && modalState === "qr_ready") {
      // Bridge reported expiry before client-side timer fired (clock skew or
      // server-side invalidation). Transition to refreshing immediately.
      setModalState("refreshing");
      fetchQr();
    }
    // 'waiting' requires no action
  }, [pollQuery.data, onPaired, modalState, fetchQr]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // ---------------------------------------------------------------------------
  // Cleanup on unmount
  // ---------------------------------------------------------------------------

  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  function renderContent() {
    switch (modalState) {
      case "idle":
      case "loading":
        return (
          <div className="flex flex-col items-center gap-4 py-8">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            <p className="text-sm text-muted-foreground">Generating QR code…</p>
          </div>
        );

      case "refreshing":
        return (
          <div className="flex flex-col items-center gap-4 py-8">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            <p className="text-sm text-muted-foreground">Refreshing QR code…</p>
          </div>
        );

      case "qr_ready":
        return (
          <div className="flex flex-col items-center gap-4">
            {qrDataUri && (
              <img
                src={qrDataUri}
                alt="WhatsApp QR code"
                className="w-64 h-64 rounded-lg border"
                style={{ imageRendering: "pixelated" }}
              />
            )}
            <ol className="text-sm text-muted-foreground space-y-1 list-decimal list-inside text-left">
              <li>Open WhatsApp on your phone</li>
              <li>Tap Settings → Linked Devices</li>
              <li>Tap Link a Device</li>
              <li>Scan this QR code</li>
            </ol>
          </div>
        );

      case "paired":
        return (
          <div className="flex flex-col items-center gap-4 py-8">
            <div className="h-12 w-12 rounded-full bg-green-100 dark:bg-green-900/30 flex items-center justify-center">
              <svg
                className="h-6 w-6 text-green-600 dark:text-green-400"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M5 13l4 4L19 7"
                />
              </svg>
            </div>
            <p className="text-sm font-medium">WhatsApp paired successfully!</p>
          </div>
        );

      case "timeout":
        return (
          <div className="flex flex-col items-center gap-4 py-8">
            <p className="text-sm text-muted-foreground">
              Pairing timed out. The QR code was not scanned within 2 minutes.
            </p>
            <Button
              size="sm"
              onClick={() => {
                timeoutRef.current = setTimeout(() => {
                  setModalState("timeout");
                }, PAIRING_TIMEOUT_MS);
                fetchQr();
              }}
            >
              Try Again
            </Button>
          </div>
        );

      case "error":
        return (
          <div className="flex flex-col items-center gap-4 py-8">
            <p className="text-sm text-destructive">
              {errorMessage ??
                "Could not connect to WhatsApp bridge. Ensure the connector service is running."}
            </p>
            <Button size="sm" variant="outline" onClick={() => fetchQr()}>
              Retry
            </Button>
          </div>
        );
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Link WhatsApp Account</DialogTitle>
          <DialogDescription>
            Scan the QR code with your phone to connect your WhatsApp account.
          </DialogDescription>
        </DialogHeader>
        <div className="py-2">{renderContent()}</div>
      </DialogContent>
    </Dialog>
  );
}
