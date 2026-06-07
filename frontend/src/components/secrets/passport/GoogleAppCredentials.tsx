// ---------------------------------------------------------------------------
// GoogleAppCredentials — editable Google OAuth app-credential panel.
//
// Surfaced on the System-credential pages for GOOGLE_OAUTH_CLIENT_ID and
// GOOGLE_OAUTH_CLIENT_SECRET (see PageSystemConnected). Replaces the former
// standalone /settings/owner page: the client_id + client_secret are written
// together via PUT /api/oauth/google/credentials (which preserves any existing
// refresh token), and the operator can (re-)authorize the Google account.
//
// Both keys live in public.butler_secrets and must be saved as a pair, so the
// same paired editor renders on either key's page.
// ---------------------------------------------------------------------------

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  getGoogleCredentialStatus,
  getGoogleOAuthStartUrl,
  upsertGoogleCredentials,
} from "@/api/index.ts";
import type { GoogleCredentialStatusResponse } from "@/api/types.ts";

import { Mono, PillBtn, BlockHead } from "./atoms.tsx";
import { secretsInventoryKeys } from "@/hooks/use-secrets-inventory.ts";

const GOOGLE_STATUS_KEY = ["secrets", "google-oauth-app"] as const;

const HEALTH_COPY: Record<GoogleCredentialStatusResponse["oauth_health"], string> = {
  connected: "connected",
  not_configured: "not configured",
  expired: "expired",
  missing_scope: "missing scope",
  redirect_uri_mismatch: "redirect uri mismatch",
  unapproved_tester: "tester not approved",
  unknown_error: "needs attention",
};

function healthColor(state: GoogleCredentialStatusResponse["oauth_health"]): string {
  if (state === "connected") return "var(--green)";
  if (state === "not_configured") return "var(--dim)";
  return "var(--red)";
}

/** Label + present/missing pill, matching the passport's dense KV rows. */
function PresenceRow({ label, present }: { label: string; present: boolean }) {
  return (
    <div
      className="flex items-center justify-between gap-3 py-1.5"
      style={{ borderBottom: "1px solid var(--border-soft)" }}
    >
      <Mono size={11} color="var(--mfg)">
        {label}
      </Mono>
      <Mono size={10} upper tracking="0.14em" color={present ? "var(--green)" : "var(--dim)"}>
        {present ? "set" : "missing"}
      </Mono>
    </div>
  );
}

/** A passport-styled text input. */
function Field({
  id,
  label,
  type = "text",
  placeholder,
  value,
  onChange,
  disabled,
  hint,
}: {
  id: string;
  label: string;
  type?: string;
  placeholder?: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  hint?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id}>
        <Mono size={9} upper tracking="0.16em" color="var(--dim)">
          {label}
        </Mono>
      </label>
      <input
        id={id}
        type={type}
        autoComplete={type === "password" ? "new-password" : "off"}
        placeholder={placeholder}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="font-mono text-[12px] px-2.5 py-1.5 outline-none disabled:opacity-40"
        style={{
          background: "var(--bg)",
          color: "var(--fg)",
          border: "1px solid var(--border-strong)",
          borderRadius: 2,
        }}
      />
      {hint && (
        <Mono size={10} color="var(--dim)">
          {hint}
        </Mono>
      )}
    </div>
  );
}

/**
 * GoogleAppCredentials — paired client_id / client_secret editor plus the
 * (re-)authorize action. Self-contained (owns its react-query state) so the
 * presentational PageSystem stays hook-free for unit tests.
 */
export function GoogleAppCredentials() {
  const queryClient = useQueryClient();
  const [clientId, setClientId] = React.useState("");
  const [clientSecret, setClientSecret] = React.useState("");

  const {
    data: status,
    isPending: statusPending,
    isError: statusError,
    refetch,
  } = useQuery({
    queryKey: GOOGLE_STATUS_KEY,
    queryFn: getGoogleCredentialStatus,
    retry: false,
  });

  const saveMutation = useMutation({
    mutationFn: () =>
      upsertGoogleCredentials({
        client_id: clientId.trim(),
        client_secret: clientSecret.trim(),
      }),
    onSuccess: (response) => {
      setClientId("");
      setClientSecret("");
      void queryClient.invalidateQueries({ queryKey: GOOGLE_STATUS_KEY });
      void queryClient.invalidateQueries({ queryKey: secretsInventoryKeys.all });
      toast.success(response.message || "Google app credentials saved.");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "Could not save Google credentials.");
    },
  });

  function handleReauthorize() {
    window.location.href = getGoogleOAuthStartUrl({
      forceConsent: true,
      selectAccount: true,
      pageOfOrigin: "secrets",
    });
  }

  const canSave =
    clientId.trim().length > 0 && clientSecret.trim().length > 0 && !saveMutation.isPending;
  const authLabel = status?.oauth_health === "connected" ? "re-authorize google" : "connect google";

  return (
    <div className="flex flex-col gap-4.5" data-google-app-credentials="true">
      <div>
        <BlockHead eyebrow="google oauth app · client id + secret" />
        <p
          className="mt-2"
          style={{
            fontFamily: "var(--font-serif, 'Source Serif 4', serif)",
            fontStyle: "italic",
            fontSize: 13,
            color: "var(--mfg)",
            maxWidth: "62ch",
          }}
        >
          The OAuth client used by Gmail, Calendar, Contacts, Drive, and other Google-backed
          modules. Client ID and secret are stored as a pair; save both when rotating. Stored
          values are write-only and never echoed back.
        </p>
      </div>

      <div className="grid gap-6" style={{ gridTemplateColumns: "1.2fr 0.8fr" }}>
        {/* Editor */}
        <form
          className="flex flex-col gap-3.5"
          onSubmit={(e) => {
            e.preventDefault();
            if (canSave) saveMutation.mutate();
          }}
        >
          <Field
            id="google-client-id"
            label="client id"
            placeholder="000000000000-example.apps.googleusercontent.com"
            value={clientId}
            onChange={setClientId}
            disabled={saveMutation.isPending}
          />
          <Field
            id="google-client-secret"
            label="client secret"
            type="password"
            placeholder="enter a new client secret"
            value={clientSecret}
            onChange={setClientSecret}
            disabled={saveMutation.isPending}
            hint="save both fields together when rotating app credentials"
          />
          <div className="flex gap-2 flex-wrap pt-0.5">
            <PillBtn variant="commit" onClick={() => saveMutation.mutate()} disabled={!canSave}>
              {saveMutation.isPending ? "saving…" : "save app credentials"}
            </PillBtn>
            <PillBtn onClick={handleReauthorize} disabled={statusPending}>
              {authLabel}
            </PillBtn>
          </div>
        </form>

        {/* Status */}
        <div className="flex flex-col gap-2.5">
          <BlockHead
            eyebrow="status"
            right={
              statusPending
                ? "…"
                : statusError || !status
                  ? "—"
                  : HEALTH_COPY[status.oauth_health]
            }
          />
          {statusError || (!statusPending && !status) ? (
            <div className="flex flex-col gap-2 pt-1">
              <Mono size={11} color="var(--dim)">
                Could not load Google status.
              </Mono>
              <PillBtn onClick={() => refetch()}>retry</PillBtn>
            </div>
          ) : statusPending ? (
            <Mono size={11} color="var(--dim)">
              loading…
            </Mono>
          ) : status ? (
            <div className="flex flex-col" style={{ borderTop: "1px solid var(--border-soft)" }}>
              <PresenceRow label="client id" present={status.client_id_configured} />
              <PresenceRow label="client secret" present={status.client_secret_configured} />
              <PresenceRow label="refresh token" present={status.refresh_token_present} />
              {status.oauth_health !== "connected" && status.oauth_health !== "not_configured" && (
                <div className="pt-2">
                  <Mono size={11} color={healthColor(status.oauth_health)}>
                    {status.oauth_health_remediation ?? HEALTH_COPY[status.oauth_health]}
                  </Mono>
                </div>
              )}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default GoogleAppCredentials;
