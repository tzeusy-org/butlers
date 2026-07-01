// ---------------------------------------------------------------------------
// Page components: PageUser, PageSystem, PageCli [bu-qu8v8]
//
// Spec: butler-secrets §One Row Template Across All Three Families
//       §Passport-Book Information Architecture
//       §Evidence-Over-Value Affordance Contract
//
// All three pages share identical structure:
//   HeadingBand → KV band → body (WhatBreaks | ProbeResult | StampRow) → footer
//
// No LLM narration. All text is stored-prose, templated, verbatim, or literal.
// ---------------------------------------------------------------------------

import * as React from "react";

import type {
  UserCredential,
  SystemCredential,
  CliCredential,
  ProviderInfo,
} from "./types.ts";
import { STATE_CATALOG } from "./constants.ts";
import {
  Eyebrow,
  Mono,
  Voice,
  ProviderMark,
  FingerprintRow,
  StampRow,
  BlockHead,
  WhatBreaks,
  ProbeResult,
  VisaRow,
  ScopeBalance,
  PillBtn,
  KV,
  toneColor,
  IdentityChip,
} from "./atoms.tsx";
import type { Identity } from "./types.ts";
import { reauthorizeUserCredential, ApiError } from "@/api/client.ts";
import {
  SECRET_TEMPLATES,
  SECRET_CATEGORIES,
  categoryFromKey,
  type SecretCategory,
} from "@/lib/secret-templates.ts";
import {
  USER_SECRET_TEMPLATES,
  ENTITY_INFO_TYPES,
  entityInfoTypeLabel,
} from "@/lib/user-secret-templates.ts";
import {
  useCliDeviceAuth,
  useSaveCLIAuthApiKey,
  useDeleteCLIAuthApiKey,
  useTestCLIAuthApiKey,
  cliAuthProviderName,
  type CliDeviceAuthState,
} from "@/hooks/use-cli-auth.ts";
import {
  useProbeUserSecret,
  useRotateUserSecret,
  useDisconnectUserSecret,
  useSetSystemSecret,
  useProbeSystemSecret,
  useDeleteSystemSecret,
  useRotateCliRuntime,
  useRevokeCliRuntime,
  useCreateUserSecret,
} from "@/hooks/use-secrets-mutations.ts";
import { useButlers } from "@/hooks/use-butlers";
import {
  useGoogleAccounts,
  useSetPrimaryAccount,
  useDisconnectAccount,
} from "@/hooks/use-secrets.ts";
import { useDisconnectGoogleHealth, useGoogleHealthStatus } from "@/hooks/use-google-health.ts";
import {
  getGoogleOAuthStartUrl,
  GOOGLE_HEALTH_SCOPES,
} from "@/api/client.ts";
import type { GoogleAccount, GoogleHealthStatusResponse } from "@/api/types.ts";
import {
  HomeAssistantDrawer,
  OwnTracksDrawer,
  SteamDrawer,
  SpotifyDrawer,
  WhatsAppDrawer,
} from "./ProviderConfigDrawer.tsx";
import { GoogleAppCredentials } from "./GoogleAppCredentials.tsx";
import { computeTestModeBannerVariant } from "@/lib/google-health-test-mode.ts";

// ── Shared-credential-store helpers ───────────────────────────────────────────

/** butler_secrets keys holding the shared Google OAuth *app* credentials. */
const GOOGLE_APP_KEYS = new Set(["GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"]);

/** True when a system credential is one of the editable Google app keys. */
function isGoogleAppCredential(credential: SystemCredential): boolean {
  return GOOGLE_APP_KEYS.has(credential.key);
}

// ── Shared layout atoms ──────────────────────────────────────────────────────

/** HeadingBand: credential title + state plaque. Used by all three pages. */
function HeadingBand({
  eyebrowLeft,
  eyebrowSub,
  title,
  titleMono = false,
  subtitle,
  mark,
  stateColor,
  stateLabel,
  stateLines = [],
}: {
  eyebrowLeft: string;
  eyebrowSub?: string;
  title: string;
  titleMono?: boolean;
  subtitle?: string;
  mark?: React.ReactNode;
  stateColor: string;
  stateLabel: string;
  stateLines?: string[];
}) {
  return (
    <div
      className="flex justify-between items-start gap-6"
      data-heading-band="true"
    >
      <div className="min-w-0">
        <Eyebrow sub={eyebrowSub}>{eyebrowLeft}</Eyebrow>
        <div className="flex items-center gap-3.5 mt-2 min-w-0">
          {mark}
          <div className="min-w-0">
            <h1
              className="m-0"
              style={{
                fontFamily: titleMono
                  ? "var(--font-mono, monospace)"
                  : "var(--font-sans, 'Inter Tight', sans-serif)",
                fontSize: titleMono ? 24 : 30,
                fontWeight: 500,
                letterSpacing: titleMono ? "0.005em" : "-0.025em",
                lineHeight: 1.05,
                color: "var(--fg)",
              }}
            >
              {title}
            </h1>
            {subtitle && (
              <Mono size={11} color="var(--mfg)">
                {subtitle}
              </Mono>
            )}
          </div>
        </div>
      </div>

      {/* State plaque */}
      <div
        className="flex flex-col gap-0.5 items-end shrink-0 p-2"
        style={{
          border: `1.5px solid ${stateColor}`,
          color: stateColor,
        }}
        data-state-plaque="true"
      >
        <Mono size={12} upper tracking="0.18em" color={stateColor} weight={500}>
          {stateLabel}
        </Mono>
        {stateLines.map((line, i) => (
          <Mono key={i} size={9} color={stateColor}>
            {line}
          </Mono>
        ))}
      </div>
    </div>
  );
}

/** CrossRefFooter: linked resources. */
function CrossRefFooter({
  refs,
}: {
  refs: Array<{ eyebrow: string; children: React.ReactNode }>;
}) {
  return (
    <div
      className="grid pt-3.5 pb-0"
      style={{
        gridTemplateColumns: `repeat(${refs.length}, 1fr)`,
        columnGap: 36,
        borderTop: "1px solid var(--border)",
      }}
    >
      {refs.map((r, i) => (
        <div key={i}>
          <Mono size={9} upper tracking="0.14em" color="var(--dim)">
            {r.eyebrow}
          </Mono>
          <div className="flex flex-col gap-1.5 mt-2">{r.children}</div>
        </div>
      ))}
    </div>
  );
}

/** CommitFooter: primary action buttons. At most one commit button per surface. */
function CommitFooter({
  left,
  right,
}: {
  left: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <div
      className="flex justify-between items-center pt-3.5 flex-wrap gap-2"
      style={{ borderTop: "1px solid var(--border)" }}
    >
      <div className="flex gap-2 flex-wrap">{left}</div>
      {right && <div className="flex gap-2 flex-wrap">{right}</div>}
    </div>
  );
}

/** Action arrow: underlined text ending in →. */
function ActionArrow({
  children,
  href,
}: {
  children: React.ReactNode;
  href?: string;
}) {
  return (
    <a
      href={href ?? "#"}
      className="text-[13px] whitespace-nowrap"
      style={{
        color: "var(--fg)",
        textDecoration: "underline",
        textUnderlineOffset: 4,
        textDecorationColor: "var(--border-strong)",
      }}
    >
      {children} →
    </a>
  );
}

// ── PageGoogleAccounts — Google-specific multi-account drawer ────────────────

/**
 * Account state rendered as a 6px dot. State is NEVER a word inline with the
 * account row — per design-language rule §Evidence-Over-Value Affordance Contract.
 */
function GoogleAccountDot({ status }: { status: GoogleAccount["status"] }) {
  const color =
    status === "active"
      ? "var(--green)"
      : status === "expired"
        ? "var(--amber)"
        : "var(--red)"; // revoked
  const label = status === "active" ? "active" : status === "expired" ? "expired" : "revoked";
  return (
    <span
      role="img"
      aria-label={label}
      data-google-account-state={status}
      className="inline-block shrink-0 rounded-full"
      style={{ width: 6, height: 6, backgroundColor: color }}
    />
  );
}

/**
 * Scope-set picker: grant Calendar / Drive / Google Health.
 * Health revoke is handled via DELETE /api/connectors/google-health/disconnect
 * (selective scope-strip, preserves Calendar/Drive).
 *
 * The three scope sets map to backend GOOGLE_SCOPE_SETS names.
 */
const SCOPE_SETS: Array<{ id: string; label: string; description: string }> = [
  { id: "calendar", label: "Calendar",   description: "Read and manage calendar events" },
  { id: "drive",    label: "Drive",      description: "Read Drive files and metadata"   },
  { id: "health",   label: "Health",     description: "Google Health sleep, activity, and metrics" },
];

function hasHealthScopes(grantedScopes: string[]): boolean {
  return GOOGLE_HEALTH_SCOPES.some((s) => grantedScopes.includes(s));
}

/**
 * Per-account row: email + state dot + primary badge + actions.
 * Inline panels for: set-primary confirm, hard-delete confirm.
 */
function GoogleAccountRow({
  account,
  totalAccounts,
}: {
  account: GoogleAccount;
  totalAccounts: number;
}) {
  const setPrimaryMutation = useSetPrimaryAccount();
  const disconnectMutation = useDisconnectAccount();
  // Per-account health revoke [bu-kma08]: each account row can revoke Health
  // scopes independently via the account_email-scoped backend endpoint.
  const revokeHealthMutation = useDisconnectGoogleHealth({
    accountEmail: account.email ?? undefined,
  });

  const [disconnectOpen, setDisconnectOpen] = React.useState(false);
  const [hardDelete, setHardDelete] = React.useState(false);
  const [revokeHealthOpen, setRevokeHealthOpen] = React.useState(false);

  // Re-authorize this account: uses account_hint (pre-selects the email in
  // Google's account chooser) + forceConsent (always shows the consent screen
  // so new/changed scopes are re-granted) + selectAccount (forces chooser
  // even if only one Google session is active).
  function handleReauthorize() {
    const url = getGoogleOAuthStartUrl({
      accountHint: account.email ?? undefined,
      forceConsent: true,
      pageOfOrigin: "secrets",
    });
    window.location.assign(url);
  }

  function handleSetPrimary() {
    if (setPrimaryMutation.isPending) return;
    setPrimaryMutation.mutate(account.id);
  }

  function handleDisconnectConfirm() {
    if (disconnectMutation.isPending) return;
    disconnectMutation.mutate({ accountId: account.id, hardDelete });
  }

  function handleDisconnectCancel() {
    setDisconnectOpen(false);
    setHardDelete(false);
    disconnectMutation.reset();
  }

  // Per-account Google Health grant [bu-kg2nl]. Each account carries its own
  // granted_scopes, so health state is derived — and granted — per account,
  // not just for the primary. account_hint targets the OAuth dance at THIS
  // account; the backend widens scopes from the hinted account's own grants.
  const healthGranted = hasHealthScopes(account.granted_scopes ?? []);

  function handleGrantHealth() {
    const url = getGoogleOAuthStartUrl({
      scopeSet: "health",
      forceConsent: true,
      pageOfOrigin: "secrets",
      accountHint: account.email ?? undefined,
    });
    window.location.assign(url);
  }

  const email = account.email ?? account.id;
  const isPrimary = account.is_primary;
  const isOnlyAccount = totalAccounts === 1;

  return (
    <div
      className="flex flex-col gap-2 py-2.5"
      style={{ borderTop: "1px solid var(--border)" }}
      data-google-account-row={account.id}
    >
      {/* Account identity row */}
      <div className="flex items-center gap-2.5 min-w-0">
        <GoogleAccountDot status={account.status} />
        <Mono size={12} className="flex-1 min-w-0 truncate">{email}</Mono>
        {isPrimary && (
          <span
            className="font-mono text-[9px] uppercase tracking-[0.12em] px-1 py-0.5 shrink-0"
            style={{
              border: "1px solid var(--border-strong)",
              color: "var(--mfg)",
            }}
            data-primary-badge="true"
          >
            primary
          </span>
        )}
      </div>

      {/* Action row — commit-pill pattern */}
      <div className="flex gap-2 flex-wrap pl-[14px]">
        <PillBtn
          variant={account.status !== "active" ? "commit" : "pill"}
          onClick={handleReauthorize}
        >
          re-authorize
        </PillBtn>
        {!isPrimary && (
          <PillBtn
            onClick={handleSetPrimary}
            disabled={setPrimaryMutation.isPending}
          >
            {setPrimaryMutation.isPending ? "setting…" : "set primary"}
          </PillBtn>
        )}
        {healthGranted ? (
          <span
            className="inline-flex items-center gap-1.5"
            data-account-health={account.id}
            data-account-health-state="granted"
          >
            <span
              className="inline-block rounded-full shrink-0"
              style={{ width: 6, height: 6, backgroundColor: "var(--green)" }}
              aria-hidden="true"
            />
            <Mono size={9} color="var(--dim)">health</Mono>
            <PillBtn
              variant="danger"
              onClick={() => setRevokeHealthOpen(true)}
              disabled={revokeHealthMutation.isPending || revokeHealthOpen}
              data-revoke-health={account.id}
            >
              {revokeHealthMutation.isPending ? "revoking…" : "revoke"}
            </PillBtn>
          </span>
        ) : (
          <span
            data-account-health={account.id}
            data-account-health-state="absent"
          >
            <PillBtn onClick={handleGrantHealth}>grant health</PillBtn>
          </span>
        )}
        {revokeHealthMutation.error && (
          <Mono size={11} color="var(--red)">
            {revokeHealthMutation.error instanceof Error
              ? revokeHealthMutation.error.message
              : "Health revoke failed."}
          </Mono>
        )}
        {!isOnlyAccount && (
          <PillBtn
            variant="danger"
            onClick={() => { setDisconnectOpen(true); setHardDelete(false); }}
            disabled={disconnectOpen}
          >
            disconnect
          </PillBtn>
        )}
      </div>

      {/* Disconnect inline confirm — danger confirm pattern from .3/.4 */}
      {disconnectOpen && (
        <div
          className="flex flex-col gap-2.5 p-3.5 ml-[14px]"
          style={{ border: "1px solid var(--red)", background: "var(--bg-elev)" }}
          data-google-disconnect-confirm={account.id}
        >
          <Mono size={11} color="var(--red)">
            Disconnect {email}? Removes saved tokens.
          </Mono>
          {/* Hard-delete toggle */}
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={hardDelete}
              onChange={(e) => setHardDelete(e.target.checked)}
              className="shrink-0"
              data-hard-delete-checkbox="true"
            />
            <Mono size={10} color="var(--red)">also delete account record (hard delete)</Mono>
          </label>
          {disconnectMutation.error && (
            <Mono size={11} color="var(--red)">
              {disconnectMutation.error instanceof Error
                ? disconnectMutation.error.message
                : "Disconnect failed."}
            </Mono>
          )}
          <div className="flex gap-2">
            <PillBtn
              variant="danger"
              onClick={handleDisconnectConfirm}
              disabled={disconnectMutation.isPending}
            >
              {disconnectMutation.isPending ? "disconnecting…" : "yes, disconnect"}
            </PillBtn>
            <PillBtn onClick={handleDisconnectCancel} disabled={disconnectMutation.isPending}>
              cancel
            </PillBtn>
          </div>
        </div>
      )}

      {/* Health revoke inline confirm — spec dashboard-google-accounts §Revoking a scope set */}
      {revokeHealthOpen && (
        <div
          className="flex flex-col gap-2.5 p-3.5 ml-[14px]"
          style={{ border: "1px solid var(--red)", background: "var(--bg-elev)" }}
          data-revoke-health-confirm={account.id}
        >
          <Mono size={11} color="var(--red)">
            This revokes Google Health access only. Calendar and Drive remain connected.
          </Mono>
          {revokeHealthMutation.error && (
            <Mono size={11} color="var(--red)">
              {revokeHealthMutation.error instanceof Error
                ? revokeHealthMutation.error.message
                : "Health revoke failed."}
            </Mono>
          )}
          <div className="flex gap-2">
            <PillBtn
              variant="danger"
              onClick={() => {
                revokeHealthMutation.mutate(undefined, {
                  onSuccess: () => setRevokeHealthOpen(false),
                });
              }}
              disabled={revokeHealthMutation.isPending}
            >
              {revokeHealthMutation.isPending ? "revoking…" : "yes, revoke"}
            </PillBtn>
            <PillBtn
              onClick={() => { revokeHealthMutation.reset(); setRevokeHealthOpen(false); }}
              disabled={revokeHealthMutation.isPending}
            >
              cancel
            </PillBtn>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * ScopeSetPicker — grant Calendar / Drive via scope_set OAuth re-dance
 * (targeted at the primary account), or selectively revoke Health via
 * DELETE /api/connectors/google-health/disconnect (primary-only backend).
 *
 * Health GRANT moved per-account [bu-kg2nl]: each account row carries its own
 * "grant health" control, so the picker's health row only shows granted state
 * (primary) + revoke, or a hint pointing at the per-account controls above.
 *
 * Rendered once per account list (calendar/drive grants apply to the primary
 * account; health revoke applies to the primary account).
 */
function ScopeSetPicker({
  grantedScopes,
  primaryAccountEmail,
}: {
  grantedScopes: string[];
  /** Primary account email for account_hint — pre-selects the account in Google's
   *  consent flow so the scope grant lands on the right account.
   *  Best-effort: omitted when no primary account is available (empty state);
   *  Google's account chooser handles selection in that case.
   *  [bu-3gekd] */
  primaryAccountEmail?: string;
}) {
  // Revoke in the picker targets the primary account explicitly so the backend
  // uses the account_email-scoped path even when called from the summary row.
  // [bu-kma08]: per-account revoke is now also on the individual account rows.
  const disconnectHealthMutation = useDisconnectGoogleHealth({
    accountEmail: primaryAccountEmail,
  });
  const [grantPending, setGrantPending] = React.useState<string | null>(null);
  const [grantError, setGrantError] = React.useState<string | null>(null);
  const [revokeHealthOpen, setRevokeHealthOpen] = React.useState(false);

  const healthGranted = hasHealthScopes(grantedScopes);

  function handleGrant(scopeSetId: string) {
    if (grantPending) return;
    setGrantPending(scopeSetId);
    setGrantError(null);
    // Navigate to OAuth start with scope_set → requests incremental consent.
    // account_hint pre-selects the primary Google account so the scope grant
    // lands on the correct account (calendar / drive only — health grant is
    // per-account on the rows above [bu-kg2nl]).
    const url = getGoogleOAuthStartUrl({
      scopeSet: scopeSetId,
      forceConsent: true,
      pageOfOrigin: "secrets",
      accountHint: primaryAccountEmail,
    });
    window.location.assign(url);
  }

  return (
    <div className="flex flex-col gap-3" data-scope-set-picker="true">
      <Mono size={9} upper tracking="0.14em" color="var(--dim)">
        scope grants · calendar / drive / health
      </Mono>
      <div className="flex flex-col gap-2">
        {SCOPE_SETS.map(({ id, label, description }) => {
          const isHealth = id === "health";
          const isGranted = isHealth
            ? healthGranted
            : grantedScopes.some((s) => s.toLowerCase().includes(id));

          return (
            <div
              key={id}
              className="flex items-center gap-3"
              style={{ borderTop: "1px solid var(--border-soft)", paddingTop: 8 }}
            >
              <div className="flex-1 min-w-0">
                <Mono size={11}>{label}</Mono>
                <Mono size={9} color="var(--dim)" className="block mt-0.5">{description}</Mono>
              </div>
              {isGranted ? (
                <div className="flex items-center gap-1.5 shrink-0">
                  <span
                    className="inline-block rounded-full shrink-0"
                    style={{ width: 6, height: 6, backgroundColor: "var(--green)" }}
                    aria-hidden="true"
                  />
                  {isHealth && (
                    <>
                      <Mono size={9} color="var(--dim)">primary</Mono>
                      <PillBtn
                        variant="danger"
                        onClick={() => setRevokeHealthOpen(true)}
                        disabled={disconnectHealthMutation.isPending || revokeHealthOpen}
                        data-scope-revoke-health="true"
                      >
                        {disconnectHealthMutation.isPending ? "revoking…" : "revoke"}
                      </PillBtn>
                    </>
                  )}
                </div>
              ) : isHealth ? (
                /* Health grant lives on the account rows [bu-kg2nl] — each
                 * connected account has its own "grant health" control. */
                <Mono size={9} color="var(--dim)" className="shrink-0">
                  grant per account above
                </Mono>
              ) : (
                <PillBtn
                  variant="commit"
                  onClick={() => handleGrant(id)}
                  disabled={!!grantPending}
                >
                  {grantPending === id ? "redirecting…" : "grant"}
                </PillBtn>
              )}
            </div>
          );
        })}
      </div>
      {grantError && (
        <Mono size={11} color="var(--red)">{grantError}</Mono>
      )}

      {/* Health revoke inline confirm — spec dashboard-google-accounts §Revoking a scope set */}
      {revokeHealthOpen && (
        <div
          className="flex flex-col gap-2.5 p-3.5"
          style={{ border: "1px solid var(--red)", background: "var(--bg-elev)" }}
          data-revoke-health-confirm="primary"
        >
          <Mono size={11} color="var(--red)">
            This revokes Google Health access only. Calendar and Drive remain connected.
          </Mono>
          {disconnectHealthMutation.error && (
            <Mono size={11} color="var(--red)">
              {disconnectHealthMutation.error instanceof Error
                ? disconnectHealthMutation.error.message
                : "Health revoke failed."}
            </Mono>
          )}
          <div className="flex gap-2">
            <PillBtn
              variant="danger"
              onClick={() => {
                disconnectHealthMutation.mutate(undefined, {
                  onSuccess: () => setRevokeHealthOpen(false),
                });
              }}
              disabled={disconnectHealthMutation.isPending}
            >
              {disconnectHealthMutation.isPending ? "revoking…" : "yes, revoke"}
            </PillBtn>
            <PillBtn
              onClick={() => { disconnectHealthMutation.reset(); setRevokeHealthOpen(false); }}
              disabled={disconnectHealthMutation.isPending}
            >
              cancel
            </PillBtn>
          </div>
        </div>
      )}

      {!revokeHealthOpen && disconnectHealthMutation.error && (
        <Mono size={11} color="var(--red)">
          {disconnectHealthMutation.error instanceof Error
            ? disconnectHealthMutation.error.message
            : "Health revoke failed."}
        </Mono>
      )}
    </div>
  );
}

// ── GoogleHealthPassportStatusCard — health connector status in passport ──────

/**
 * Module-level helper: compute banner visibility flags from status snapshot.
 * Called once per render; Date.now() is not called inside the render body
 * (required by the react-hooks/purity ESLint rule).
 */
function computeHealthBannerFlags(status: GoogleHealthStatusResponse): {
  showBanner: boolean;
  isExpiring: boolean;
} {
  // Spec dashboard-google-accounts §Test-Mode Pre-Verification Warning:
  //   - The orange banner is PERSISTENT — it shows whenever test_mode=true,
  //     regardless of token age (test-mode consent expires every 7 days).
  //   - It elevates to the RED variant once last_token_refresh_at is older than
  //     5 days 6 hours (computeTestModeBannerVariant's threshold).
  const now = new Date(Date.now());
  const showBanner = status.test_mode;
  const isExpiring =
    status.test_mode &&
    computeTestModeBannerVariant(status.last_token_refresh_at, now) === "red";
  return { showBanner, isExpiring };
}

/**
 * TestModeExpiryBanner — PERSISTENT warning shown whenever the primary account
 * is in Google Health test mode. Test-mode consent expires every 7 days until
 * production-mode verification completes.
 *
 * Variants (spec dashboard-google-accounts §Test-Mode Pre-Verification Warning):
 *   - orange (default): consent expires every 7 days; informational.
 *   - red (`isExpiring`): last_token_refresh_at is older than 5d6h — consent is
 *     about to expire.
 * Both variants surface a re-consent link to the scope_set=health OAuth flow so
 * the owner can re-grant before access lapses.
 *
 * Derives entirely from existing status endpoint signals: test_mode +
 * last_token_refresh_at. Does NOT require new backend persistence.
 *
 * Accepts pre-computed `isExpiring` boolean from the parent so the component
 * stays pure (no impure Date.now() in render).
 *
 * [bu-hh875][bu-bxu50]
 */
function TestModeExpiryBanner({
  isExpiring,
  primaryAccountEmail,
}: {
  isExpiring: boolean;
  primaryAccountEmail: string | null;
}) {
  const tone = isExpiring ? "var(--red)" : "var(--amber)";
  const label = isExpiring
    ? "test-mode consent about to expire, re-consent to keep Google Health connected"
    : "test mode: consent expires every 7 days until production verification completes";

  // Re-consent link → OAuth start scoped to Google Health, forcing the consent
  // screen and pre-selecting the primary account. Spec requires the (red)
  // banner to link directly to the scope_set=health re-consent flow.
  const reconsentUrl = getGoogleOAuthStartUrl({
    scopeSet: "health",
    forceConsent: true,
    accountHint: primaryAccountEmail ?? undefined,
    pageOfOrigin: "secrets",
  });

  return (
    <div
      className="flex items-start gap-2 p-2.5 rounded-sm"
      style={{
        border: `1px solid ${tone}`,
        background: isExpiring
          ? "color-mix(in oklch, var(--red) 8%, transparent)"
          : "color-mix(in oklch, var(--amber) 8%, transparent)",
      }}
      data-testid="test-mode-expiry-banner"
      data-expired={isExpiring ? "true" : "false"}
      data-variant={isExpiring ? "red" : "orange"}
    >
      <span
        className="inline-block shrink-0 rounded-full mt-1"
        style={{ width: 6, height: 6, backgroundColor: tone }}
        aria-hidden="true"
      />
      <div className="flex flex-col gap-1 min-w-0">
        <Mono size={10} color={tone}>{label}</Mono>
        <a
          href={reconsentUrl}
          data-testid="test-mode-reconsent-link"
          className="underline underline-offset-2 w-fit"
        >
          <Mono size={9} color={tone}>re-consent (Google Health)</Mono>
        </a>
      </div>
    </div>
  );
}

/**
 * GoogleHealthPassportStatusCard — inline connector status card for the
 * Google credential page in the Secrets passport.
 *
 * Acceptance rules:
 * - HIDDEN when the primary account has no health scopes granted.
 * - Shows: state + last ingest + 7d sleep/daily counts.
 * - Shows TestModeExpiryBanner persistently when test_mode=true (red past 5d6h).
 *
 * Only the PRIMARY-account view is shown (per dashboard-google-accounts spec).
 * Polls every 30 s via useGoogleHealthStatus (same cadence as butler-detail tab).
 *
 * [bu-hh875]
 */
function GoogleHealthPassportStatusCard({ status }: { status: GoogleHealthStatusResponse }) {
  const stateColor =
    status.state === "healthy"
      ? "var(--green)"
      : status.state === "error"
        ? "var(--red)"
        : "var(--amber)";

  // Compute banner flags via a module-level helper so Date.now() is not called
  // directly during render (required by the react-hooks/purity ESLint rule).
  const { showBanner, isExpiring } = computeHealthBannerFlags(status);

  return (
    <div
      className="flex flex-col gap-2"
      data-testid="health-passport-status-card"
    >
      <Mono size={9} upper tracking="0.14em" color="var(--dim)">
        health connector
      </Mono>

      {showBanner && (
        <TestModeExpiryBanner
          isExpiring={isExpiring}
          primaryAccountEmail={status.primary_account_email}
        />
      )}

      <div className="flex flex-col gap-1.5">
        {/* State row */}
        <div className="flex items-center gap-2">
          <span
            className="inline-block shrink-0 rounded-full"
            style={{ width: 6, height: 6, backgroundColor: stateColor }}
            aria-hidden="true"
          />
          <Mono size={10} color={stateColor}>{status.state}</Mono>
        </div>

        {/* KV rows */}
        <div
          className="flex flex-col gap-0.5 pt-1.5"
          style={{ borderTop: "1px solid var(--border-soft)" }}
        >
          <div className="flex justify-between gap-3">
            <Mono size={9} color="var(--dim)">last ingest</Mono>
            <Mono size={9}>
              {status.last_ingest_at && !Number.isNaN(new Date(status.last_ingest_at).getTime())
                ? new Date(status.last_ingest_at).toLocaleDateString()
                : "—"}
            </Mono>
          </div>
          <div className="flex justify-between gap-3">
            <Mono size={9} color="var(--dim)">sleep · 7d</Mono>
            <Mono size={9}>{status.sleep_sessions_7d}</Mono>
          </div>
          <div className="flex justify-between gap-3">
            <Mono size={9} color="var(--dim)">summaries · 7d</Mono>
            <Mono size={9}>{status.daily_summaries_7d}</Mono>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * PageGoogleAccounts — Google-specific multi-account management surface.
 *
 * Rendered inside PageUser when provider.id === "google". Surfaces:
 *   - All connected Google accounts (email + state dot + primary badge)
 *   - Per-account: re-authorize / set-primary / grant-health / disconnect (w/ hard-delete)
 *   - "add another account" → OAuth with forceConsent + selectAccount (forces chooser)
 *   - Scope-set picker: grant Calendar / Drive (primary) + selective Health
 *     revoke (primary-only backend); Health grant is per-account [bu-kg2nl]
 *   - Google Health status card (hidden when no health scopes granted) [bu-hh875]
 *
 * [bu-ayp6v.7]
 */
export function PageGoogleAccounts() {
  const { data: accountsData, isLoading, error } = useGoogleAccounts();
  const accounts: GoogleAccount[] = accountsData ?? [];

  // Primary account's granted scopes (for scope-set picker).
  const primaryAccount = accounts.find((a) => a.is_primary) ?? accounts[0];
  const grantedScopes = primaryAccount?.granted_scopes ?? [];

  // Google Health connector status — drives the status card and test-mode banner [bu-hh875].
  // Poll only when the primary account has health scopes (avoids redundant calls
  // when the user has never granted health access).
  const hasHealth = hasHealthScopes(grantedScopes);
  const { data: healthStatus } = useGoogleHealthStatus({ enabled: hasHealth });

  // "add another account" — forceConsent + selectAccount forces Google's
  // account chooser even when the user is already signed in.
  function handleAddAccount() {
    const url = getGoogleOAuthStartUrl({
      forceConsent: true,
      selectAccount: true,
      pageOfOrigin: "secrets",
    });
    window.location.assign(url);
  }

  if (isLoading) {
    return (
      <div
        className="flex flex-col gap-3 p-3.5"
        style={{ border: "1px solid var(--border-soft)", background: "var(--bg-elev)" }}
        data-google-accounts-panel="true"
      >
        <Mono size={9} upper tracking="0.14em" color="var(--dim)">google accounts</Mono>
        <Mono size={11} color="var(--dim)">loading…</Mono>
      </div>
    );
  }

  if (error) {
    return (
      <div
        className="flex flex-col gap-3 p-3.5"
        style={{ border: "1px solid var(--border-soft)", background: "var(--bg-elev)" }}
        data-google-accounts-panel="true"
      >
        <Mono size={9} upper tracking="0.14em" color="var(--dim)">google accounts</Mono>
        <Mono size={11} color="var(--red)">
          {error instanceof Error ? error.message : "Could not load accounts."}
        </Mono>
      </div>
    );
  }

  return (
    <div
      className="flex flex-col gap-4.5 p-3.5"
      style={{ border: "1px solid var(--border-soft)", background: "var(--bg-elev)" }}
      data-google-accounts-panel="true"
    >
      {/* Account list */}
      <div>
        <div className="flex items-center justify-between gap-3 mb-1">
          <Mono size={9} upper tracking="0.14em" color="var(--dim)">google accounts</Mono>
          <Mono size={9} color="var(--dim)">{accounts.length} connected</Mono>
        </div>

        {accounts.length === 0 ? (
          /* Empty-state: no account connected — surface a prominent Connect CTA
           * so the owner can initiate the OAuth dance without a manual ?identity=
           * param or navigating away [bu-3gekd]. */
          <div
            className="flex flex-col gap-2.5 pt-2"
            data-google-connect-empty-state="true"
          >
            <Mono size={11} color="var(--dim)">no Google account connected</Mono>
            <PillBtn variant="commit" onClick={handleAddAccount}>
              connect Google
            </PillBtn>
          </div>
        ) : (
          accounts.map((account) => (
            <GoogleAccountRow
              key={account.id}
              account={account}
              totalAccounts={accounts.length}
            />
          ))
        )}
      </div>

      {/* Add another account — only shown when at least one account is connected */}
      {accounts.length > 0 && (
        <div
          className="pt-3"
          style={{ borderTop: "1px solid var(--border)" }}
        >
          <PillBtn variant="commit" onClick={handleAddAccount}>
            add another account
          </PillBtn>
        </div>
      )}

      {/* Scope-set picker */}
      <div
        className="pt-3"
        style={{ borderTop: "1px solid var(--border)" }}
      >
        <ScopeSetPicker
          grantedScopes={grantedScopes}
          primaryAccountEmail={primaryAccount?.email ?? undefined}
        />
      </div>

      {/* Health connector status card — hidden when no health scopes granted [bu-hh875] */}
      {hasHealth && healthStatus && (
        <div
          className="pt-3"
          style={{ borderTop: "1px solid var(--border)" }}
        >
          <GoogleHealthPassportStatusCard status={healthStatus} />
        </div>
      )}
    </div>
  );
}

// ── PageUser ─────────────────────────────────────────────────────────────────

/**
 * PageUser — user credential page (oauth / token / apikey / webhook).
 *
 * Per-kind variants via the same template. Provider-specific oddities (OwnTracks
 * webhook URL, etc.) live in a per-provider drawer dispatched by provider slug.
 *
 * Wired actions [bu-ayp6v.3]:
 *   re-authorize / connect — reauthorizeUserCredential → redirect to OAuth dance
 *   rotate                 — value-entry inline panel → useRotateUserSecret
 *   test                   — useProbeUserSecret; result surfaces in ProbeResult
 *   disconnect             — danger confirm inline panel → useDisconnectUserSecret
 *   reveal value           — REMOVED: OAuth refresh tokens are never returned by
 *                            the backend. There is no user-secret reveal path.
 */
export function PageUser({
  credential,
  provider,
  identities,
  showVerifyCmd = false,
  voiceParagraph = true,
}: {
  credential: UserCredential;
  provider: ProviderInfo;
  identities?: Identity[];
  showVerifyCmd?: boolean;
  voiceParagraph?: boolean;
}) {
  const meta = STATE_CATALOG[credential.state] ?? STATE_CATALOG.never_set;
  const color = toneColor(meta.tone);
  const grantedSet = new Set(credential.scopesGranted);
  const requiredSet = new Set(credential.scopesRequired);
  const allScopes = Array.from(new Set([...credential.scopesGranted, ...credential.scopesRequired]));
  const isOauth = provider.kind === "oauth";
  const isWebhook = provider.kind === "webhook";
  const isMissing = credential.state === "never_set";
  const sick = credential.state !== "ok" && credential.state !== "never_set";

  // ── Reauthorize / Connect ───────────────────────────────────────────────────
  // Both "re-authorize" (expired/revoked) and "connect" (never_set) flow through
  // the same endpoint — POST /api/secrets/user/<provider>/reauthorize — which
  // initiates the OAuth dance and returns a redirect_url.
  const [reauthPending, setReauthPending] = React.useState(false);
  const [reauthError, setReauthError] = React.useState<string | null>(null);
  // Honest "not yet available" notice: the backend returns HTTP 501 when a
  // catalog-declared oauth provider (e.g. whatsapp) has no OAuth integration
  // wired up. That is not a failure to apologise for in red — it is an honest
  // "not built yet" message, surfaced in a neutral tone below.
  const [reauthNotAvailable, setReauthNotAvailable] = React.useState<string | null>(null);

  async function handleReauthorize() {
    if (reauthPending) return;
    setReauthPending(true);
    setReauthError(null);
    setReauthNotAvailable(null);
    try {
      const resp = await reauthorizeUserCredential(credential.provider, credential.identity);
      // Follow the returned redirect_url to begin the OAuth dance.
      if (!resp?.data?.redirect_url) {
        throw new Error("No redirect URL returned from the server.");
      }
      window.location.href = resp.data.redirect_url;
    } catch (err) {
      if (err instanceof ApiError && err.status === 501) {
        // Provider OAuth is not yet available — honest, non-error messaging.
        setReauthNotAvailable(err.message || `${provider.label} connect is not yet available.`);
      } else {
        setReauthError(err instanceof Error ? err.message : "Reauthorization failed.");
      }
      setReauthPending(false);
    }
  }

  // ── Probe ───────────────────────────────────────────────────────────────────
  const probeMutation = useProbeUserSecret();

  function handleProbe() {
    if (probeMutation.isPending) return;
    probeMutation.mutate({ provider: credential.provider, identity: credential.identity });
  }

  // Merge the optimistic probe result back into the ProbeResult display.
  // The mutation hook invalidates the query cache on success, but while we wait
  // for the parent to re-render with fresh data we show the mutation result directly.
  const liveTest: typeof credential.test = (() => {
    if (probeMutation.data?.data) {
      const d = probeMutation.data.data;
      return {
        ok: d.ok,
        code: d.code ?? null,
        latencyMs: 0,
        at: d.at ?? "just now",
        message: d.message ?? undefined,
      };
    }
    return credential.test;
  })();

  // ── Rotate ──────────────────────────────────────────────────────────────────
  const [rotateOpen, setRotateOpen] = React.useState(false);
  const [rotateValue, setRotateValue] = React.useState("");
  const rotateMutation = useRotateUserSecret();

  function handleRotateSubmit() {
    if (!rotateValue.trim() || rotateMutation.isPending) return;
    rotateMutation.mutate(
      { provider: credential.provider, body: { value: rotateValue.trim() }, identity: credential.identity },
      {
        onSuccess: () => {
          setRotateOpen(false);
          setRotateValue("");
        },
      },
    );
  }

  function handleRotateCancel() {
    setRotateOpen(false);
    setRotateValue("");
    rotateMutation.reset();
  }

  // ── Disconnect ─────────────────────────────────────────────────────────────
  const [disconnectConfirm, setDisconnectConfirm] = React.useState(false);
  const disconnectMutation = useDisconnectUserSecret();

  function handleDisconnectConfirm() {
    if (disconnectMutation.isPending) return;
    disconnectMutation.mutate({ provider: credential.provider, identity: credential.identity });
  }

  function handleDisconnectCancel() {
    setDisconnectConfirm(false);
    disconnectMutation.reset();
  }

  const stateLines: string[] = [];
  if (credential.state === "expired" && credential.failureTail) stateLines.push(credential.failureTail);
  if (credential.state === "expiring" && credential.expires) stateLines.push(`expires ${credential.expires}`);
  if (credential.state === "ok" && credential.lastVerified) stateLines.push(`verified ${credential.lastVerified}`);
  if (credential.state === "scope_mismatch") {
    const missing = credential.scopesRequired.filter((s) => !grantedSet.has(s)).length;
    stateLines.push(`${missing} scope missing`);
  }
  if (credential.state === "never_set") stateLines.push("never connected");

  const identity = identities?.find((i) => i.id === credential.identity);

  return (
    <div
      className="flex flex-col gap-4.5 p-7"
      data-page="user"
      data-provider={credential.provider}
      data-credential-state={credential.state}
    >
      <HeadingBand
        eyebrowLeft="issuing authority"
        eyebrowSub={`kind · ${provider.kind}`}
        title={provider.label}
        subtitle={`${provider.authority} · ${provider.kind}`}
        mark={<ProviderMark glyph={provider.glyph} label={provider.label} size={36} />}
        stateColor={color}
        stateLabel={meta.label}
        stateLines={stateLines}
      />

      {voiceParagraph && (
        <Voice size={15} maxWidth="60ch">
          {provider.brief}
          {credential.feeds.length > 0 && (
            <> Feeds the {credential.feeds.join(" and ")} butler{credential.feeds.length === 1 ? "" : "s"}.</>
          )}
        </Voice>
      )}

      {/* Dense KV band */}
      <div
        className="py-3.5"
        style={{
          borderTop: "1px solid var(--border)",
          borderBottom: "1px solid var(--border)",
        }}
      >
        {isWebhook ? (
          <div
            className="grid gap-5 items-baseline"
            style={{ gridTemplateColumns: "180px 1fr 130px 130px" }}
          >
            <div>
              <Mono size={9} upper tracking="0.16em" color="var(--dim)">passport no.</Mono>
              <div className="mt-1">
                <FingerprintRow value={credential.fingerprint} size={13} showVerifyCmd={showVerifyCmd} />
              </div>
            </div>
            <KV mono label="incoming url" value={credential.webhook ?? "—"} size={12} />
            <KV label="issued" value={credential.issued ?? "—"} />
            <KV label="last seen" value={credential.lastVerified ?? "—"} />
          </div>
        ) : (
          <div
            className="grid gap-5 items-baseline"
            style={{ gridTemplateColumns: "180px 110px 110px 130px 130px 1fr" }}
          >
            <div>
              <Mono size={9} upper tracking="0.16em" color="var(--dim)">passport no.</Mono>
              <div className="mt-1">
                <FingerprintRow value={credential.fingerprint} size={13} showVerifyCmd={showVerifyCmd} />
              </div>
            </div>
            <KV
              label="issued"
              value={credential.issued ?? "—"}
              valueColor={credential.issued ? "var(--fg)" : "var(--dim)"}
            />
            <KV
              label="expires"
              value={credential.expires ?? "no expiry"}
              valueColor={
                credential.state === "expired"
                  ? "var(--red)"
                  : credential.state === "expiring"
                    ? "var(--amber)"
                    : credential.expires
                      ? "var(--fg)"
                      : "var(--mfg)"
              }
            />
            <KV label="last verified" value={credential.lastVerified ?? "—"} />
            <KV label="last used" value={credential.lastUsed ?? "—"} />
            <div>
              <Mono size={9} upper tracking="0.14em" color="var(--dim)">scopes</Mono>
              <div className="mt-1.5">
                {requiredSet.size > 0 ? (
                  <ScopeBalance granted={credential.scopesGranted} required={credential.scopesRequired} width={120} />
                ) : (
                  <Mono size={11} color="var(--dim)">no scope set</Mono>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Body: two columns */}
      <div className="grid gap-9" style={{ gridTemplateColumns: "1.1fr 1fr" }}>
        {/* Left */}
        <div className="flex flex-col gap-4.5">
          {requiredSet.size > 0 && (
            <div>
              <BlockHead
                eyebrow="visa permissions · scopes"
                right={`${[...grantedSet].filter((sc) => requiredSet.has(sc)).length}/${requiredSet.size} required`}
              />
              <div className="mt-2" style={{ borderTop: "1px solid var(--border)" }}>
                {allScopes.map((scope) => {
                  const state = grantedSet.has(scope)
                    ? requiredSet.has(scope)
                      ? "granted"
                      : "extra"
                    : "missing";
                  return <VisaRow key={scope} scope={scope} state={state} />;
                })}
              </div>
            </div>
          )}
          <WhatBreaks breaks={credential.breaks} state={credential.state} />
          {credential.feeds.length > 0 && (
            <div>
              <Mono size={9} upper tracking="0.14em" color="var(--dim)">feeds</Mono>
              <div
                className="flex gap-3.5 flex-wrap mt-2 pt-2"
                style={{ borderTop: "1px solid var(--border-soft)" }}
              >
                {credential.feeds.map((f) => (
                  <span key={f} className="inline-flex items-center gap-1.5">
                    <span
                      className="inline-flex items-center justify-center rounded-sm font-mono font-semibold text-[10px]"
                      style={{
                        width: 14,
                        height: 14,
                        background: "var(--mfg)",
                        color: "var(--bg)",
                      }}
                    >
                      {f[0].toUpperCase()}
                    </span>
                    <span className="font-sans text-[12.5px] text-[var(--fg)]">{f}</span>
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Right */}
        <div className="flex flex-col gap-4.5">
          <div>
            <BlockHead
              eyebrow="probe · last test"
              right={liveTest ? (liveTest.ok ? "ok" : "failed") : "never"}
            />
            <div className="mt-2.5 pt-2" style={{ borderTop: "1px solid var(--border)" }}>
              <ProbeResult test={liveTest} onProbe={!isMissing ? handleProbe : undefined} />
            </div>
          </div>
          <div>
            <BlockHead
              eyebrow="stamps · audit"
              right={`${credential.audit.length} event${credential.audit.length === 1 ? "" : "s"}`}
            />
            <div className="mt-2" style={{ borderTop: "1px solid var(--border)" }}>
              {credential.audit.length === 0 && (
                <span
                  className="block pt-2.5"
                  style={{
                    fontFamily: "var(--font-serif)",
                    fontStyle: "italic",
                    color: "var(--dim)",
                    fontSize: 13,
                  }}
                >
                  No stamps yet.
                </span>
              )}
              {credential.audit.map((e, i) => (
                <StampRow key={i} event={e} last={i === credential.audit.length - 1} />
              ))}
              {credential.audit.length > 0 && (
                <div className="pt-2.5">
                  <ActionArrow href={`/audit-log?key=u:${credential.provider}`}>
                    open /audit-log
                  </ActionArrow>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Google-specific multi-account management surface [bu-ayp6v.7] */}
      {provider.id === "google" && (
        <PageGoogleAccounts />
      )}

      {/* Provider-specific config drawers [bu-ayp6v.8/.9] — surfaced inline for
          already-connected providers so the owner can reconfigure without leaving
          the credential page. inline=true omits the standalone heading and dismiss
          button — the content is always visible within PageUser's own layout. */}
      {provider.id === "homeassistant" && (
        <HomeAssistantDrawer onClose={() => undefined} inline />
      )}
      {provider.id === "owntracks" && (
        <OwnTracksDrawer onClose={() => undefined} inline />
      )}
      {provider.id === "steam" && (
        <SteamDrawer onClose={() => undefined} inline />
      )}
      {provider.id === "spotify" && (
        <SpotifyDrawer onClose={() => undefined} inline />
      )}
      {provider.id === "whatsapp" && (
        <WhatsAppDrawer onClose={() => undefined} inline />
      )}

      {/* Cross-references */}
      <CrossRefFooter
        refs={[
          {
            eyebrow: "elsewhere",
            children: (
              <>
                <ActionArrow href={`/ingestion/connectors`}>
                  /ingestion/connectors/{credential.provider}
                </ActionArrow>
                {isOauth && (
                  <ActionArrow href={`https://${provider.authority}`}>
                    {provider.authority} ↗
                  </ActionArrow>
                )}
              </>
            ),
          },
          {
            eyebrow: "config",
            children: (
              <>
                <Mono size={11} color="var(--mfg)">kind · {provider.kind}</Mono>
                <Mono size={11} color="var(--mfg)">endpoint · {provider.authority}</Mono>
                <Mono size={11} color="var(--mfg)">cadence · {provider.cadence}</Mono>
              </>
            ),
          },
          {
            eyebrow: "identity",
            children: identity ? (
              <IdentityChip
                id={identity.id}
                label={identity.label}
                role={identity.role}
                hue={identity.hue}
                compact
              />
            ) : (
              <Mono size={11} color="var(--dim)">{credential.identity}</Mono>
            ),
          },
        ]}
      />

      {/* Rotate inline panel — value entry */}
      {rotateOpen && (
        <div
          className="flex flex-col gap-3 p-3.5"
          style={{ border: "1px solid var(--border-soft)", background: "var(--bg-elev)" }}
          data-rotate-panel="true"
        >
          <Mono size={9} upper tracking="0.14em" color="var(--dim)">new credential value</Mono>
          <textarea
            rows={3}
            value={rotateValue}
            onChange={(e) => setRotateValue(e.target.value)}
            placeholder="paste token here"
            className="font-mono text-[11px] p-2 resize-none outline-none w-full"
            style={{
              border: "1px solid var(--border-strong)",
              background: "var(--bg)",
              color: "var(--fg)",
              borderRadius: 3,
            }}
          />
          {rotateMutation.error && (
            <Mono size={11} color="var(--red)">
              {rotateMutation.error instanceof Error
                ? rotateMutation.error.message
                : "Rotate failed."}
            </Mono>
          )}
          <div className="flex gap-2">
            <PillBtn
              variant="commit"
              onClick={handleRotateSubmit}
              disabled={!rotateValue.trim() || rotateMutation.isPending}
            >
              {rotateMutation.isPending ? "saving…" : "save"}
            </PillBtn>
            <PillBtn onClick={handleRotateCancel} disabled={rotateMutation.isPending}>
              cancel
            </PillBtn>
          </div>
        </div>
      )}

      {/* Disconnect inline confirm */}
      {disconnectConfirm && (
        <div
          className="flex flex-col gap-3 p-3.5"
          style={{ border: "1px solid var(--red)", background: "var(--bg-elev)" }}
          data-disconnect-confirm="true"
        >
          <Mono size={11} color="var(--red)">
            Remove this credential? This cannot be undone.
          </Mono>
          {disconnectMutation.error && (
            <Mono size={11} color="var(--red)">
              {disconnectMutation.error instanceof Error
                ? disconnectMutation.error.message
                : "Disconnect failed."}
            </Mono>
          )}
          <div className="flex gap-2">
            <PillBtn
              variant="danger"
              onClick={handleDisconnectConfirm}
              disabled={disconnectMutation.isPending}
            >
              {disconnectMutation.isPending ? "removing…" : "yes, disconnect"}
            </PillBtn>
            <PillBtn onClick={handleDisconnectCancel} disabled={disconnectMutation.isPending}>
              cancel
            </PillBtn>
          </div>
        </div>
      )}

      {/* Footer */}
      {reauthError && (
        <Mono size={11} color="var(--red)" className="mt-1">
          {reauthError}
        </Mono>
      )}
      {reauthNotAvailable && (
        <div className="mt-1" data-reauth-not-available={credential.provider}>
          <Mono size={11} color="var(--dim)">
            {reauthNotAvailable}
          </Mono>
        </div>
      )}
      <CommitFooter
        left={
          <>
            {(credential.state === "expired" || credential.state === "revoked" || credential.state === "scope_mismatch") && (
              <PillBtn
                variant="commit"
                onClick={handleReauthorize}
                disabled={reauthPending}
              >
                {reauthPending ? "redirecting…" : "re-authorize"}
              </PillBtn>
            )}
            {credential.state === "expiring" && (
              <PillBtn
                variant="commit"
                onClick={() => { setRotateOpen(true); setDisconnectConfirm(false); }}
                disabled={rotateOpen}
              >
                rotate
              </PillBtn>
            )}
            {isMissing && (
              <PillBtn
                variant="commit"
                onClick={handleReauthorize}
                disabled={reauthPending}
              >
                {reauthPending ? "redirecting…" : "connect"}
              </PillBtn>
            )}
            {!isMissing && (
              <PillBtn
                onClick={handleProbe}
                disabled={probeMutation.isPending}
              >
                {probeMutation.isPending ? "testing…" : "test"}
              </PillBtn>
            )}
            {!isMissing && !sick && (
              <PillBtn
                onClick={() => { setRotateOpen(true); setDisconnectConfirm(false); }}
                disabled={rotateOpen}
              >
                rotate
              </PillBtn>
            )}
          </>
        }
        right={
          <>
            {/* reveal value is omitted for user secrets: OAuth refresh tokens are
                never returned by the backend — there is no user-secret reveal path. */}
            {!isMissing && (
              <PillBtn
                variant="danger"
                onClick={() => { setDisconnectConfirm(true); setRotateOpen(false); }}
                disabled={disconnectConfirm}
              >
                disconnect
              </PillBtn>
            )}
          </>
        }
      />
    </div>
  );
}

// ── PageSystem ───────────────────────────────────────────────────────────────

/**
 * PageSystem — system credential page (butler_secrets).
 *
 * Supports shared / local override / missing row states.
 * Actions wired [bu-ayp6v.4]:
 *   set value / rotate  — value-entry inline panel → useSetSystemSecret target="shared"
 *   override · per butler — butler-picker inline panel → useSetSystemSecret target="<butler>"
 *   test                — useProbeSystemSecret; 429 rate-limit surfaced as non-blocking hint
 *   delete              — danger confirm inline panel → useDeleteSystemSecret (correct target)
 */
export function PageSystem({
  credential,
  showVerifyCmd = false,
  voiceParagraph = true,
}: {
  credential: SystemCredential;
  showVerifyCmd?: boolean;
  voiceParagraph?: boolean;
}) {
  const isMissing = credential.rowState === "missing";
  const isLocal = credential.rowState === "local";
  const isPlain = !!credential.plainValue;
  // The Google OAuth app keys get a dedicated editor that writes via the oauth
  // PUT endpoint (the correct shared/public location). The generic mutate
  // controls are suppressed for them.
  const isGoogleApp = isGoogleAppCredential(credential);
  // Rows explicitly flagged read_only by the backend are suppressed from the
  // generic editor.  Shared-public rows (public.butler_secrets) are no longer
  // flagged read_only — they use target="shared-public" which routes mutations
  // to the correct pool. Only externally-managed or future reserved rows would
  // set read_only=true.
  const isSharedStore = !!credential.readOnly;
  const stateColor = isMissing ? "var(--dim)" : "var(--green)";
  const stateLabel = isMissing ? "not set" : isLocal ? "local override" : "shared default";
  const stateLines: string[] = [];
  if (isLocal) stateLines.push(`target · ${credential.target}`);
  else if (!isMissing && credential.lastVerified) stateLines.push(`verified ${credential.lastVerified}`);

  // ── Set value / Rotate ─────────────────────────────────────────────────────
  // "set value" (missing) and "rotate" (present, shared) both open the same
  // value-entry inline panel and write target="shared".
  const [setValueOpen, setSetValueOpen] = React.useState(false);
  const [setValue, setSetValue] = React.useState("");
  const setMutation = useSetSystemSecret();

  function handleSetValueOpen() {
    setSetValueOpen(true);
    setOverrideOpen(false);
    setDeleteConfirm(false);
  }

  function handleSetValueCancel() {
    setSetValueOpen(false);
    setSetValue("");
    setMutation.reset();
  }

  function handleSetValueSubmit() {
    if (!setValue.trim() || setMutation.isPending) return;
    // Use credential.target to route to the correct schema:
    // "shared-public" → public credential pool (what modules read)
    // "shared" → switchboard schema (legacy/explicit switchboard rows)
    // "<butler>" → per-butler override (local rows — this path sets, not overrides)
    setMutation.mutate(
      { key: credential.key, body: { value: setValue.trim(), target: credential.target } },
      {
        onSuccess: () => {
          setSetValueOpen(false);
          setSetValue("");
        },
      },
    );
  }

  // ── Override · per butler ──────────────────────────────────────────────────
  // Opens a butler-picker inline panel: select a butler, enter value, write
  // target="<butler>", creating (or replacing) a per-butler override row.
  const [overrideOpen, setOverrideOpen] = React.useState(false);
  const [overrideButler, setOverrideButler] = React.useState("");
  const [overrideValue, setOverrideValue] = React.useState("");
  const overrideMutation = useSetSystemSecret();
  const butlersQuery = useButlers();
  const butlerNames: string[] = React.useMemo(
    () => (butlersQuery.data?.data ?? []).map((b) => b.name).sort(),
    [butlersQuery.data],
  );

  function handleOverrideOpen() {
    setOverrideOpen(true);
    setSetValueOpen(false);
    setDeleteConfirm(false);
    setOverrideButler(butlerNames[0] ?? "");
    setOverrideValue("");
    overrideMutation.reset();
  }

  function handleOverrideCancel() {
    setOverrideOpen(false);
    setOverrideValue("");
    setOverrideButler("");
    overrideMutation.reset();
  }

  function handleOverrideSubmit() {
    if (!overrideButler || !overrideValue.trim() || overrideMutation.isPending) return;
    overrideMutation.mutate(
      { key: credential.key, body: { value: overrideValue.trim(), target: overrideButler } },
      {
        onSuccess: () => {
          setOverrideOpen(false);
          setOverrideValue("");
          setOverrideButler("");
        },
      },
    );
  }

  // ── Probe ──────────────────────────────────────────────────────────────────
  // Rate-limited at 5 s / key on the backend (HTTP 429). Show a non-blocking
  // hint line instead of an error toast when rate-limited.
  const probeMutation = useProbeSystemSecret();
  const [probeRateLimited, setProbeRateLimited] = React.useState(false);

  function handleProbe() {
    if (probeMutation.isPending) return;
    setProbeRateLimited(false);
    probeMutation.mutate(
      { key: credential.key },
      {
        onError: (err: Error) => {
          if (err instanceof ApiError && err.status === 429) {
            setProbeRateLimited(true);
          }
          // Non-429 errors are already surfaced as a toast by the hook.
        },
      },
    );
  }

  // Merge the optimistic probe result back into the ProbeResult display.
  const liveTest: typeof credential.test = (() => {
    if (probeMutation.data?.data) {
      const d = probeMutation.data.data;
      return {
        ok: d.ok,
        code: d.code ?? null,
        latencyMs: 0,
        at: d.at ?? "just now",
        message: d.message ?? undefined,
      };
    }
    return credential.test;
  })();

  // ── Delete ─────────────────────────────────────────────────────────────────
  // Passes the correct ?target= depending on whether this is a shared or local row.
  const [deleteConfirm, setDeleteConfirm] = React.useState(false);
  const deleteMutation = useDeleteSystemSecret();

  // The delete target routes to the correct schema:
  // - local overrides: credential.target holds the butler name
  // - shared-public rows: credential.target === "shared-public" → public pool
  // - shared (switchboard) rows: credential.target === "shared" → switchboard
  const deleteTarget = credential.target;

  function handleDeleteOpen() {
    setDeleteConfirm(true);
    setSetValueOpen(false);
    setOverrideOpen(false);
  }

  function handleDeleteConfirm() {
    if (deleteMutation.isPending) return;
    deleteMutation.mutate({ key: credential.key, target: deleteTarget });
  }

  function handleDeleteCancel() {
    setDeleteConfirm(false);
    deleteMutation.reset();
  }

  return (
    <div
      className="flex flex-col gap-4.5 p-7"
      data-page="system"
      data-key={credential.key}
      data-row-state={credential.rowState}
    >
      <HeadingBand
        eyebrowLeft={`category · ${credential.category}`}
        title={credential.key}
        titleMono
        stateColor={stateColor}
        stateLabel={stateLabel}
        stateLines={stateLines}
      />

      {voiceParagraph && credential.description && (
        <Voice size={15} maxWidth="60ch">
          {credential.description}
        </Voice>
      )}

      {/* Dense KV band */}
      <div
        className="py-3.5"
        style={{
          borderTop: "1px solid var(--border)",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <div
          className="grid gap-6 items-baseline"
          style={{ gridTemplateColumns: "200px 140px 1fr" }}
        >
          <div>
            <Mono size={9} upper tracking="0.16em" color="var(--dim)">
              {isPlain ? "value" : "fingerprint"}
            </Mono>
            <div className="mt-1">
              {isPlain ? (
                <Mono size={13}>{credential.plainValue!}</Mono>
              ) : (
                <FingerprintRow
                  value={credential.fingerprint}
                  size={13}
                  showVerifyCmd={showVerifyCmd && !isPlain}
                />
              )}
            </div>
          </div>
          <KV label="last verified" value={credential.lastVerified ?? "—"} />
          <div>
            <Mono size={9} upper tracking="0.14em" color="var(--dim)">used by</Mono>
            <div className="flex gap-3 flex-wrap mt-1.5">
              {credential.usedBy.length === 0 && (
                <Mono size={11} color="var(--dim)">nobody yet</Mono>
              )}
              {credential.usedBy[0] === "*" && (
                <span
                  style={{
                    fontFamily: "var(--font-serif)",
                    fontStyle: "italic",
                    fontSize: 13,
                    color: "var(--fg)",
                  }}
                >
                  every butler that talks to a model.
                </span>
              )}
              {credential.usedBy[0] !== "*" &&
                credential.usedBy.map((b) => (
                  <span key={b} className="inline-flex items-center gap-1.5">
                    <span className="font-sans text-[12.5px] text-[var(--fg)]">{b}</span>
                  </span>
                ))}
            </div>
          </div>
        </div>
      </div>

      {/* Body: two columns */}
      <div className="grid gap-9" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div className="flex flex-col gap-4.5">
          {credential.breaks && credential.breaks.length > 0 ? (
            <WhatBreaks
              breaks={credential.breaks}
              state={isMissing ? "never_set" : "ok"}
            />
          ) : (
            <div>
              <Mono size={9} upper tracking="0.14em" color="var(--dim)">what breaks</Mono>
              <div
                className="mt-2 pt-2.5"
                style={{ borderTop: "1px solid var(--border)" }}
              >
                <span
                  style={{
                    fontFamily: "var(--font-serif)",
                    fontStyle: "italic",
                    fontSize: 13,
                    color: "var(--dim)",
                  }}
                >
                  Nothing routed here yet.
                </span>
              </div>
            </div>
          )}
        </div>
        <div className="flex flex-col gap-4.5">
          {!isPlain && (
            <div>
              <BlockHead
                eyebrow="probe · last test"
                right={liveTest ? (liveTest.ok ? "ok" : "failed") : "never"}
              />
              <div className="mt-2.5 pt-2" style={{ borderTop: "1px solid var(--border)" }}>
                <ProbeResult test={liveTest} onProbe={!isMissing ? handleProbe : undefined} />
              </div>
            </div>
          )}
          <div>
            <BlockHead
              eyebrow="stamps · audit"
              right={`${credential.audit.length} event${credential.audit.length === 1 ? "" : "s"}`}
            />
            <div className="mt-2" style={{ borderTop: "1px solid var(--border)" }}>
              {credential.audit.length === 0 && (
                <span
                  className="block pt-2.5"
                  style={{
                    fontFamily: "var(--font-serif)",
                    fontStyle: "italic",
                    color: "var(--dim)",
                    fontSize: 13,
                  }}
                >
                  No stamps yet.
                </span>
              )}
              {credential.audit.map((e, i) => (
                <StampRow key={i} event={e} last={i === credential.audit.length - 1} />
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Cross-references */}
      <CrossRefFooter
        refs={[
          {
            eyebrow: "elsewhere",
            children: (
              <>
                <ActionArrow href={`/audit-log?key=${credential.key}`}>
                  /audit?key={credential.key}
                </ActionArrow>
                {credential.usedBy.length > 0 && credential.usedBy[0] !== "*" && (
                  <ActionArrow href={`/butlers/${credential.usedBy[0]}`}>
                    /butlers/{credential.usedBy[0]}
                  </ActionArrow>
                )}
              </>
            ),
          },
          {
            eyebrow: "storage",
            children: (
              <>
                <Mono size={11} color="var(--mfg)">
                  butler_secrets · {credential.target || "shared"}
                </Mono>
                <Mono size={11} color="var(--mfg)">category · {credential.category}</Mono>
                <Mono size={11} color="var(--mfg)">
                  scope · {isLocal ? "per butler" : "shared default"}
                </Mono>
              </>
            ),
          },
        ]}
      />

      {/* Set value inline panel — shared write (missing → set value, present → rotate) */}
      {setValueOpen && (
        <div
          className="flex flex-col gap-3 p-3.5"
          style={{ border: "1px solid var(--border-soft)", background: "var(--bg-elev)" }}
          data-set-value-panel="true"
        >
          <Mono size={9} upper tracking="0.14em" color="var(--dim)">
            {isMissing ? "new value" : "replacement value (shared)"}
          </Mono>
          <textarea
            rows={3}
            value={setValue}
            onChange={(e) => setSetValue(e.target.value)}
            placeholder="paste value here"
            className="font-mono text-[11px] p-2 resize-none outline-none w-full"
            style={{
              border: "1px solid var(--border-strong)",
              background: "var(--bg)",
              color: "var(--fg)",
              borderRadius: 3,
            }}
          />
          {setMutation.error && (
            <Mono size={11} color="var(--red)">
              {setMutation.error instanceof Error
                ? setMutation.error.message
                : "Save failed."}
            </Mono>
          )}
          <div className="flex gap-2">
            <PillBtn
              variant="commit"
              onClick={handleSetValueSubmit}
              disabled={!setValue.trim() || setMutation.isPending}
            >
              {setMutation.isPending ? "saving…" : "save"}
            </PillBtn>
            <PillBtn onClick={handleSetValueCancel} disabled={setMutation.isPending}>
              cancel
            </PillBtn>
          </div>
        </div>
      )}

      {/* Override inline panel — butler-picker + value entry */}
      {overrideOpen && (
        <div
          className="flex flex-col gap-3 p-3.5"
          style={{ border: "1px solid var(--border-soft)", background: "var(--bg-elev)" }}
          data-override-panel="true"
        >
          <Mono size={9} upper tracking="0.14em" color="var(--dim)">
            per-butler override
          </Mono>
          {/* Butler picker */}
          <div className="flex flex-col gap-1">
            <Mono size={9} upper tracking="0.12em" color="var(--dim)">butler</Mono>
            {butlersQuery.isLoading ? (
              <Mono size={11} color="var(--dim)">loading butlers…</Mono>
            ) : butlerNames.length === 0 ? (
              <Mono size={11} color="var(--dim)">no registered butlers</Mono>
            ) : (
              <select
                value={overrideButler}
                onChange={(e) => setOverrideButler(e.target.value)}
                className="font-mono text-[11px] p-1.5 outline-none"
                style={{
                  border: "1px solid var(--border-strong)",
                  background: "var(--bg)",
                  color: "var(--fg)",
                  borderRadius: 3,
                }}
                data-butler-picker="true"
              >
                {butlerNames.map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            )}
          </div>
          <textarea
            rows={3}
            value={overrideValue}
            onChange={(e) => setOverrideValue(e.target.value)}
            placeholder="paste override value here"
            className="font-mono text-[11px] p-2 resize-none outline-none w-full"
            style={{
              border: "1px solid var(--border-strong)",
              background: "var(--bg)",
              color: "var(--fg)",
              borderRadius: 3,
            }}
          />
          {overrideMutation.error && (
            <Mono size={11} color="var(--red)">
              {overrideMutation.error instanceof Error
                ? overrideMutation.error.message
                : "Override failed."}
            </Mono>
          )}
          <div className="flex gap-2">
            <PillBtn
              variant="commit"
              onClick={handleOverrideSubmit}
              disabled={!overrideButler || !overrideValue.trim() || overrideMutation.isPending}
            >
              {overrideMutation.isPending ? "saving…" : "save override"}
            </PillBtn>
            <PillBtn onClick={handleOverrideCancel} disabled={overrideMutation.isPending}>
              cancel
            </PillBtn>
          </div>
        </div>
      )}

      {/* Delete inline confirm */}
      {deleteConfirm && (
        <div
          className="flex flex-col gap-3 p-3.5"
          style={{ border: "1px solid var(--red)", background: "var(--bg-elev)" }}
          data-delete-confirm="true"
        >
          <Mono size={11} color="var(--red)">
            {isLocal
              ? `Remove per-butler override for ${deleteTarget}? This cannot be undone.`
              : "Remove this shared credential? This cannot be undone."}
          </Mono>
          {deleteMutation.error && (
            <Mono size={11} color="var(--red)">
              {deleteMutation.error instanceof Error
                ? deleteMutation.error.message
                : "Delete failed."}
            </Mono>
          )}
          <div className="flex gap-2">
            <PillBtn
              variant="danger"
              onClick={handleDeleteConfirm}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? "deleting…" : isLocal ? "yes, remove override" : "yes, delete"}
            </PillBtn>
            <PillBtn onClick={handleDeleteCancel} disabled={deleteMutation.isPending}>
              cancel
            </PillBtn>
          </div>
        </div>
      )}

      {/* Rate-limit hint for probe */}
      {probeRateLimited && (
        <Mono size={11} color="var(--dim)" className="mt-1" data-probe-rate-limited="true">
          try again in a moment
        </Mono>
      )}

      {/* Google OAuth app editor — replaces the generic mutate controls for
          the shared GOOGLE_OAUTH_CLIENT_ID / _SECRET keys. Writes via the
          oauth PUT endpoint (the correct public credential-pool location) and
          carries the (re-)authorize action that formerly lived on
          /settings/owner. */}
      {isGoogleApp && (
        <div className="pt-3.5" style={{ borderTop: "1px solid var(--border)" }}>
          <GoogleAppCredentials />
        </div>
      )}

      {/* Externally-managed or otherwise explicitly read_only rows that are
          not the Google app keys. Shared-public rows are now editable via
          target="shared-public" and will not trigger this gate. */}
      {isSharedStore && !isGoogleApp && (
        <div className="pt-3.5" style={{ borderTop: "1px solid var(--border)" }}>
          <Mono size={11} color="var(--dim)">
            Managed in the shared credential store · read-only here
          </Mono>
        </div>
      )}

      {/* Footer — generic mutate controls; suppressed for the Google app keys
          (dedicated editor above) and any explicitly read_only rows.
          Shared-public rows (public.butler_secrets) are editable here via
          target="shared-public". */}
      {!isGoogleApp && !isSharedStore && (
        <CommitFooter
          left={
            isMissing ? (
              <PillBtn
                variant="commit"
                onClick={handleSetValueOpen}
                disabled={setValueOpen}
              >
                set value
              </PillBtn>
            ) : (
              <>
                {!isPlain && (
                  <PillBtn
                    onClick={handleProbe}
                    disabled={probeMutation.isPending}
                  >
                    {probeMutation.isPending ? "testing…" : "test"}
                  </PillBtn>
                )}
                <PillBtn
                  onClick={handleSetValueOpen}
                  disabled={setValueOpen}
                >
                  rotate
                </PillBtn>
                {!isLocal && (
                  <PillBtn
                    onClick={handleOverrideOpen}
                    disabled={overrideOpen}
                  >
                    override · per butler
                  </PillBtn>
                )}
              </>
            )
          }
          right={
            <>
              {!isMissing && (
                <PillBtn
                  variant="danger"
                  onClick={handleDeleteOpen}
                  disabled={deleteConfirm}
                >
                  delete
                </PillBtn>
              )}
            </>
          }
        />
      )}
    </div>
  );
}

// ── PageCli ──────────────────────────────────────────────────────────────────

/**
 * CliDeviceAuthPanel — device-code reauth surface for a CLI runtime.
 *
 * Renders the verification URL + one-time code while a session awaits
 * authorization, and a status line for the other session states. Stays silent
 * (renders nothing) until a session is started.
 */
function CliDeviceAuthPanel({ auth }: { auth: CliDeviceAuthState }) {
  const session = auth.session;
  if (!session && !auth.error) return null;

  const statusLabel: Record<string, string> = {
    starting: "starting…",
    awaiting_auth: "waiting for authorization",
    success: "connected",
    failed: "failed",
    expired: "expired",
  };
  const statusColor =
    session?.state === "success"
      ? "var(--green)"
      : session?.state === "failed" || session?.state === "expired"
        ? "var(--red)"
        : "var(--amber)";

  const showCode =
    session?.state === "awaiting_auth" && !!session.auth_url && !!session.device_code;

  return (
    <div
      className="flex flex-col gap-3 p-3.5"
      style={{ border: "1px solid var(--border-soft)", background: "var(--bg-elev)" }}
      data-cli-device-auth="true"
    >
      {auth.error && (
        <Mono size={11} color="var(--red)">
          {auth.error}
        </Mono>
      )}
      {session && (
        <div className="flex items-center gap-2.5">
          <Mono size={11} upper tracking="0.16em" color={statusColor} weight={500}>
            {statusLabel[session.state] ?? session.state}
          </Mono>
          {session.message && session.state !== "awaiting_auth" && (
            <Mono size={11} color="var(--mfg)">
              {session.message}
            </Mono>
          )}
        </div>
      )}
      {showCode && (
        <div className="flex flex-col gap-2">
          <Voice size={13} maxWidth="60ch">
            Open{" "}
            <a
              href={session!.auth_url!}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                color: "var(--fg)",
                textDecoration: "underline",
                textUnderlineOffset: 3,
              }}
            >
              {session!.auth_url}
            </a>{" "}
            and enter this code:
          </Voice>
          <div className="flex items-center gap-3">
            <span
              className="px-3 py-1.5 tabular-nums"
              style={{
                fontFamily: "var(--font-mono, monospace)",
                fontSize: 22,
                fontWeight: 600,
                letterSpacing: "0.18em",
                border: "1px solid var(--border-strong)",
                background: "var(--bg)",
                color: "var(--fg)",
              }}
            >
              {session!.device_code}
            </span>
            <PillBtn onClick={() => navigator.clipboard?.writeText(session!.device_code!)}>
              copy
            </PillBtn>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * PageCli — CLI runtime credential page.
 *
 * Wired actions [bu-ayp6v.5]:
 *   rotate         — useRotateCliRuntime; returned value shown once in copy-once panel
 *   revoke         — danger confirm → useRevokeCliRuntime
 *   test           — useTestCLIAuthApiKey (works for all auth modes via /cli-auth/{p}/test)
 *   set token      — value-entry panel → useRotateCliRuntime (token-mode) OR
 *                    useSaveCLIAuthApiKey (api-key mode, e.g. Claude)
 *   api-key save   — useSaveCLIAuthApiKey; api-key mode providers can paste key
 *   api-key delete — useDeleteCLIAuthApiKey
 *
 * Device-code re-auth: wired to POST /api/secrets/cli/{id}/reauthorize
 *   (bu-ayp6v.10 audited endpoint) via handleReauthorize / useCliDeviceAuth.
 *
 * How-to-use snippet rendered as static literal (no LLM).
 *
 * When `deviceAuth` is supplied (by `PageCliConnected`) and the provider uses
 * the device-code flow, the footer surfaces a connect / re-authorize button and
 * the body renders the verification URL + one-time code.
 */
export function PageCli({
  credential,
  showVerifyCmd = false,
  deviceAuth,
}: {
  credential: CliCredential;
  showVerifyCmd?: boolean;
  /** Device-code reauth state; omit to disable the flow (e.g. in unit tests). */
  deviceAuth?: CliDeviceAuthState;
}) {
  const meta = STATE_CATALOG[credential.state] ?? STATE_CATALOG.never_set;
  const color = toneColor(meta.tone);
  const isMissing = credential.state === "never_set";
  const sick = credential.state !== "ok" && credential.state !== "never_set";
  const stateLines: string[] = [];
  if (credential.state === "expiring" && credential.expires) {
    stateLines.push(`expires ${credential.expires}`);
  }
  if (credential.state === "ok" && credential.lastUsed) {
    stateLines.push(`used ${credential.lastUsed}`);
  }
  if (isMissing) stateLines.push("paste a token to enable");

  const envVar = credential.id.toUpperCase().replace(/-/g, "_") + "_TOKEN";
  // Bare provider name for the cli-auth endpoints (e.g. "claude" from "claude-cli"
  // or "codex" from "cli-auth/codex").
  const providerName = deviceAuth?.providerName ?? cliAuthProviderName(credential.id);
  const isApiKeyMode = deviceAuth?.isApiKeyMode ?? false;

  const allScopes = Array.from(
    new Set([...credential.scopesGranted, ...credential.scopesRequired]),
  );
  const grantedSet = new Set(credential.scopesGranted);
  const requiredSet = new Set(credential.scopesRequired);

  // ── Rotate ────────────────────────────────────────────────────────────────
  // rotate() regenerates the token and returns the raw value ONCE.
  // The copy-once panel shows the value until dismissed — after that it's gone.
  const rotateMutation = useRotateCliRuntime();
  const [rotatedSecret, setRotatedSecret] = React.useState<string | null>(null);

  // Rotate (true server-generated): no value → backend mints a random token.
  function handleRotate() {
    if (rotateMutation.isPending) return;
    setRotatedSecret(null);
    rotateMutation.mutate(
      { id: credential.id },
      {
        onSuccess: (data) => {
          const val = (data as { data?: { value?: string } })?.data?.value ?? null;
          setRotatedSecret(val);
        },
      },
    );
  }

  // Set-token save (paste-to-save): persists the exact owner-supplied value
  // (not a random one) and works for never_set providers (first save).
  function handleSetTokenSave() {
    const trimmed = setTokenValue.trim();
    if (!trimmed || rotateMutation.isPending) return;
    setRotatedSecret(null);
    rotateMutation.mutate(
      { id: credential.id, value: trimmed },
      {
        onSuccess: () => {
          setSetTokenOpen(false);
          setSetTokenValue("");
        },
      },
    );
  }

  // ── Revoke ────────────────────────────────────────────────────────────────
  const revokeMutation = useRevokeCliRuntime();
  const [revokeConfirm, setRevokeConfirm] = React.useState(false);

  function handleRevokeConfirm() {
    if (revokeMutation.isPending) return;
    revokeMutation.mutate({ id: credential.id });
  }

  function handleRevokeCancel() {
    setRevokeConfirm(false);
    revokeMutation.reset();
  }

  // ── Test ──────────────────────────────────────────────────────────────────
  // POST /api/cli-auth/{provider}/test — validates the stored credential.
  // Works for both device_code and api_key auth modes.
  const testMutation = useTestCLIAuthApiKey();
  const [testResult, setTestResult] = React.useState<{ success: boolean; detail: string } | null>(null);

  function handleTest() {
    if (testMutation.isPending) return;
    setTestResult(null);
    testMutation.mutate(providerName, {
      onSuccess: (data) => {
        setTestResult({ success: data.success, detail: data.detail ?? "" });
      },
      onError: (err) => {
        setTestResult({ success: false, detail: err.message });
      },
    });
  }

  // ── Set token (token mode: paste into text entry) ─────────────────────────
  // For providers WITHOUT api_key mode, "set token" persists the pasted raw
  // value verbatim via the rotate endpoint (which accepts an owner-supplied
  // value and UPSERTs — so first-time set works without a 404).
  const [setTokenOpen, setSetTokenOpen] = React.useState(false);
  const [setTokenValue, setSetTokenValue] = React.useState("");
  // Re-use rotateMutation — rotate endpoint handles both create and rotation.

  function handleSetTokenOpen() {
    setSetTokenOpen(true);
    setRevokeConfirm(false);
  }

  function handleSetTokenCancel() {
    setSetTokenOpen(false);
    setSetTokenValue("");
    rotateMutation.reset();
  }

  // Token-mode "set token" persists the pasted value via the rotate endpoint
  // (handleSetTokenSave). For api_key mode (e.g. Claude): paste the key and
  // persist via useSaveCLIAuthApiKey.
  const saveApiKeyMutation = useSaveCLIAuthApiKey();
  const deleteApiKeyMutation = useDeleteCLIAuthApiKey();

  function handleSaveApiKey() {
    if (!setTokenValue.trim() || saveApiKeyMutation.isPending) return;
    saveApiKeyMutation.mutate(
      { provider: providerName, apiKey: setTokenValue.trim() },
      {
        onSuccess: () => {
          setSetTokenOpen(false);
          setSetTokenValue("");
        },
      },
    );
  }

  function handleDeleteApiKey() {
    if (deleteApiKeyMutation.isPending) return;
    deleteApiKeyMutation.mutate(providerName);
  }

  // ── Re-authorize (C10-BRIDGE: bu-ayp6v.10) ───────────────────────────────
  // Calls POST /api/secrets/cli/{id}/reauthorize (audited endpoint).
  // device_code response → useCliDeviceAuth drives the existing polling flow.
  // api_key response    → apiKeyReauthPending triggers the key-entry panel.
  function handleReauthorize() {
    if (deviceAuth?.reauthorizing || deviceAuth?.starting) return;
    deviceAuth?.reauthorize();
  }

  // When the reauthorize endpoint returns api_key mode, open the key-entry panel.
  React.useEffect(() => {
    if (deviceAuth?.apiKeyReauthPending) {
      setSetTokenOpen(true);
      deviceAuth.acknowledgeApiKeyReauth();
    }
  }, [deviceAuth?.apiKeyReauthPending, deviceAuth]);

  return (
    <div
      className="flex flex-col gap-4.5 p-7"
      data-page="cli"
      data-cli-id={credential.id}
      data-credential-state={credential.state}
    >
      <HeadingBand
        eyebrowLeft="command-line agent"
        eyebrowSub={credential.id}
        title={credential.label}
        subtitle={credential.id}
        stateColor={color}
        stateLabel={meta.label}
        stateLines={stateLines}
      />

      <Voice size={15} maxWidth="60ch">
        Token used by the {credential.label} CLI to authenticate against the system.
      </Voice>

      {/* Dense KV band */}
      <div
        className="py-3.5"
        style={{
          borderTop: "1px solid var(--border)",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <div
          className="grid gap-6 items-baseline"
          style={{ gridTemplateColumns: "200px 110px 130px 130px" }}
        >
          <div>
            <Mono size={9} upper tracking="0.16em" color="var(--dim)">passport no.</Mono>
            <div className="mt-1">
              <FingerprintRow value={credential.fingerprint} size={13} showVerifyCmd={showVerifyCmd} />
            </div>
          </div>
          <KV label="issued" value={credential.issued ?? "—"} />
          <KV
            label="expires"
            value={credential.expires ?? "no expiry"}
            valueColor={
              credential.state === "expiring" ? "var(--amber)" : credential.expires ? "var(--fg)" : "var(--mfg)"
            }
          />
          <KV label="last used" value={credential.lastUsed ?? "—"} />
        </div>
      </div>

      {/* Body: two columns */}
      <div className="grid gap-9" style={{ gridTemplateColumns: "1.1fr 1fr" }}>
        {/* Left */}
        <div className="flex flex-col gap-4.5">
          {requiredSet.size > 0 && (
            <div>
              <BlockHead
                eyebrow="capabilities"
                right={`${credential.scopesGranted.length}/${requiredSet.size} required`}
              />
              <div className="mt-2" style={{ borderTop: "1px solid var(--border)" }}>
                {allScopes.map((scope) => {
                  const state = grantedSet.has(scope)
                    ? requiredSet.has(scope)
                      ? "granted"
                      : "extra"
                    : "missing";
                  return <VisaRow key={scope} scope={scope} state={state} />;
                })}
              </div>
            </div>
          )}
          {/* How-to-use snippet — hard-coded literal, no LLM */}
          <div>
            <Mono size={9} upper tracking="0.14em" color="var(--dim)">how to use</Mono>
            <div
              className="mt-2 p-3.5"
              style={{
                border: "1px solid var(--border-soft)",
                background: "var(--bg-elev)",
              }}
            >
              <Mono size={11}>{`$ ${credential.id} --token \${${envVar}}`}</Mono>
            </div>
          </div>
        </div>

        {/* Right */}
        <div className="flex flex-col gap-4.5">
          <div>
            <BlockHead
              eyebrow="probe · last test"
              right={
                testResult
                  ? testResult.success ? "ok" : "failed"
                  : credential.test ? (credential.test.ok ? "ok" : "failed") : "never"
              }
            />
            <div className="mt-2.5 pt-2" style={{ borderTop: "1px solid var(--border)" }}>
              <ProbeResult
                test={
                  testResult
                    ? { ok: testResult.success, code: null, latencyMs: 0, at: "just now", message: testResult.detail || undefined }
                    : credential.test
                }
                onProbe={!isMissing ? handleTest : undefined}
              />
            </div>
            {testResult && (
              <Mono size={11} color={testResult.success ? "var(--green)" : "var(--red)"} className="mt-1.5">
                {testResult.detail}
              </Mono>
            )}
          </div>
        </div>
      </div>

      {/* Cross-references */}
      <CrossRefFooter
        refs={[
          {
            eyebrow: "elsewhere",
            children: (
              <ActionArrow href={`/audit-log?actor=${credential.id}`}>
                /audit?actor={credential.id}
              </ActionArrow>
            ),
          },
          {
            eyebrow: "config",
            children: (
              <>
                <Mono size={11} color="var(--mfg)">runtime · {credential.id}</Mono>
                <Mono size={11} color="var(--mfg)">scope · session-bound</Mono>
              </>
            ),
          },
        ]}
      />

      {/* Device-code reauth panel (verification URL + one-time code) */}
      {deviceAuth?.supported && <CliDeviceAuthPanel auth={deviceAuth} />}

      {/* Set token inline panel — paste value (token mode) or API key (api_key mode) */}
      {setTokenOpen && (
        <div
          className="flex flex-col gap-3 p-3.5"
          style={{ border: "1px solid var(--border-soft)", background: "var(--bg-elev)" }}
          data-set-token-panel="true"
        >
          <Mono size={9} upper tracking="0.14em" color="var(--dim)">
            {isApiKeyMode ? "api key" : "token value"}
          </Mono>
          <textarea
            rows={3}
            value={setTokenValue}
            onChange={(e) => setSetTokenValue(e.target.value)}
            placeholder={isApiKeyMode ? "paste api key here" : "paste token here"}
            className="font-mono text-[11px] p-2 resize-none outline-none w-full"
            style={{
              border: "1px solid var(--border-strong)",
              background: "var(--bg)",
              color: "var(--fg)",
              borderRadius: 3,
            }}
          />
          {saveApiKeyMutation.error && isApiKeyMode && (
            <Mono size={11} color="var(--red)">
              {saveApiKeyMutation.error instanceof Error
                ? saveApiKeyMutation.error.message
                : "Save failed."}
            </Mono>
          )}
          <div className="flex gap-2">
            <PillBtn
              variant="commit"
              onClick={isApiKeyMode ? handleSaveApiKey : handleSetTokenSave}
              disabled={!setTokenValue.trim() || (isApiKeyMode ? saveApiKeyMutation.isPending : rotateMutation.isPending)}
            >
              {(isApiKeyMode ? saveApiKeyMutation.isPending : rotateMutation.isPending) ? "saving…" : "save"}
            </PillBtn>
            <PillBtn onClick={handleSetTokenCancel} disabled={isApiKeyMode ? saveApiKeyMutation.isPending : rotateMutation.isPending}>
              cancel
            </PillBtn>
          </div>
        </div>
      )}

      {/* Rotate copy-once panel — new value returned from rotate(), shown once */}
      {rotatedSecret !== null && (
        <div
          className="flex flex-col gap-2 p-3.5"
          style={{ border: "1px solid var(--border-soft)", background: "var(--bg-elev)" }}
          data-rotated-secret-panel="true"
        >
          <Mono size={9} upper tracking="0.14em" color="var(--dim)">
            new token: copy now, won't be shown again
          </Mono>
          <Mono size={12}>{rotatedSecret}</Mono>
          <div className="flex gap-2">
            <PillBtn onClick={() => navigator.clipboard?.writeText(rotatedSecret)}>
              copy
            </PillBtn>
            <PillBtn onClick={() => setRotatedSecret(null)}>
              dismiss
            </PillBtn>
          </div>
        </div>
      )}

      {/* Revoke inline confirm */}
      {revokeConfirm && (
        <div
          className="flex flex-col gap-3 p-3.5"
          style={{ border: "1px solid var(--red)", background: "var(--bg-elev)" }}
          data-revoke-confirm="true"
        >
          <Mono size={11} color="var(--red)">
            Revoke this CLI token? The credential will be deleted and cannot be recovered.
          </Mono>
          {revokeMutation.error && (
            <Mono size={11} color="var(--red)">
              {revokeMutation.error instanceof Error
                ? revokeMutation.error.message
                : "Revoke failed."}
            </Mono>
          )}
          <div className="flex gap-2">
            <PillBtn
              variant="danger"
              onClick={handleRevokeConfirm}
              disabled={revokeMutation.isPending}
            >
              {revokeMutation.isPending ? "revoking…" : "yes, revoke"}
            </PillBtn>
            <PillBtn onClick={handleRevokeCancel} disabled={revokeMutation.isPending}>
              cancel
            </PillBtn>
          </div>
        </div>
      )}

      {/* Footer — device-code flow when supported, else rotate-and-revoke */}
      <CommitFooter
        left={
          deviceAuth?.supported ? (
            deviceAuth.inProgress ? (
              <PillBtn onClick={deviceAuth.cancel}>cancel</PillBtn>
            ) : (
              <>
                <PillBtn
                  variant={isMissing || sick ? "commit" : "pill"}
                  onClick={isMissing ? deviceAuth.start : handleReauthorize}
                  disabled={deviceAuth.starting || deviceAuth.reauthorizing}
                >
                  {(deviceAuth.starting || deviceAuth.reauthorizing)
                    ? "starting…"
                    : isMissing
                      ? "connect"
                      : "re-authorize"}
                </PillBtn>
                {!isMissing && (
                  <PillBtn
                    onClick={handleTest}
                    disabled={testMutation.isPending}
                  >
                    {testMutation.isPending ? "testing…" : "test"}
                  </PillBtn>
                )}
              </>
            )
          ) : isApiKeyMode ? (
            // api_key mode (e.g. Claude): save / test / delete key
            <>
              <PillBtn
                variant={isMissing ? "commit" : "pill"}
                onClick={handleSetTokenOpen}
                disabled={setTokenOpen}
              >
                {isMissing ? "save key" : "update key"}
              </PillBtn>
              {!isMissing && (
                <PillBtn
                  onClick={handleTest}
                  disabled={testMutation.isPending}
                >
                  {testMutation.isPending ? "testing…" : "test"}
                </PillBtn>
              )}
            </>
          ) : isMissing ? (
            <PillBtn
              variant="commit"
              onClick={handleSetTokenOpen}
              disabled={setTokenOpen}
            >
              set token
            </PillBtn>
          ) : (
            <>
              <PillBtn
                variant={credential.state === "expiring" ? "commit" : "pill"}
                onClick={handleRotate}
                disabled={rotateMutation.isPending}
              >
                {rotateMutation.isPending ? "rotating…" : "rotate"}
              </PillBtn>
              <PillBtn
                onClick={handleTest}
                disabled={testMutation.isPending}
              >
                {testMutation.isPending ? "testing…" : "test"}
              </PillBtn>
            </>
          )
        }
        right={
          <>
            {!isMissing && !isApiKeyMode && (
              <PillBtn
                variant="danger"
                onClick={() => { setRevokeConfirm(true); setSetTokenOpen(false); }}
                disabled={revokeConfirm}
              >
                revoke
              </PillBtn>
            )}
            {!isMissing && isApiKeyMode && (
              <PillBtn
                variant="danger"
                onClick={handleDeleteApiKey}
                disabled={deleteApiKeyMutation.isPending}
              >
                {deleteApiKeyMutation.isPending ? "deleting…" : "delete key"}
              </PillBtn>
            )}
          </>
        }
      />
    </div>
  );
}

/**
 * PageCliConnected — wires the live device-code reauth flow into PageCli.
 *
 * Kept separate from PageCli so the presentational component stays free of
 * react-query hooks (unit tests render PageCli directly without a provider).
 */
export function PageCliConnected({
  credential,
  showVerifyCmd = false,
}: {
  credential: CliCredential;
  showVerifyCmd?: boolean;
}) {
  const deviceAuth = useCliDeviceAuth(credential.id);
  return (
    <PageCli
      credential={credential}
      showVerifyCmd={showVerifyCmd}
      deviceAuth={deviceAuth}
    />
  );
}

// ── Empty state ──────────────────────────────────────────────────────────────

/** EmptyState: one serif-italic sentence, no illustration. */
export function PassportEmptyState() {
  return (
    <div className="p-10">
      <span
        style={{
          fontFamily: "var(--font-serif, 'Source Serif 4', serif)",
          fontStyle: "italic",
          color: "var(--dim)",
          fontSize: 15,
        }}
      >
        No page selected.
      </span>
    </div>
  );
}

// ── PassportAddPanel ──────────────────────────────────────────────────────────

/**
 * PassportAddPanel — creation flow for new credentials (bu-ayp6v.6).
 *
 * Step 1: choose family (system / user / provider)
 * Step 2a SYSTEM: key + value + category + target → useSetSystemSecret
 * Step 2b USER: type + value + label → useCreateUserSecret (entity_info)
 * Step 2c CONNECT: OAuth start (google) or per-provider drawers for others
 *
 * Design-language rules: no cards; commit-pill actions; inline panels.
 * Template suggestions sourced from SECRET_TEMPLATES (system) and
 * USER_SECRET_TEMPLATES / ENTITY_INFO_TYPES (user).
 */
export function PassportAddPanel({
  ownerEntityId,
  onClose,
  onSystemCreated,
}: {
  /** Owner entity UUID — required for user credential creation. */
  ownerEntityId?: string;
  /** Called when the panel should close (cancel or success). */
  onClose: () => void;
  /**
   * Called after a system secret is successfully created.
   * Parent can use this to navigate to the newly created credential.
   */
  onSystemCreated?: (key: string) => void;
}) {
  type AddFamily = "system" | "user" | "provider" | null;
  const [family, setFamily] = React.useState<AddFamily>(null);

  // ── Step reset ───────────────────────────────────────────────────────────
  function handleFamilySelect(f: AddFamily) {
    setFamily(f);
    // Reset sub-form state when switching families
    setSystemKey("");
    setSystemValue("");
    setSystemCategory("general");
    setSystemTarget("shared");
    setUserType(ENTITY_INFO_TYPES[0] as string);
    setUserValue("");
    setUserLabel("");
    setProviderSlug(null);
  }

  // ── SYSTEM sub-form ──────────────────────────────────────────────────────
  const [systemKey, setSystemKey] = React.useState("");
  const [systemValue, setSystemValue] = React.useState("");
  const [systemCategory, setSystemCategory] = React.useState<SecretCategory>("general");
  const [systemTarget, setSystemTarget] = React.useState("shared");
  const systemMutation = useSetSystemSecret();

  // Suggest category from key when key changes
  function handleSystemKeyChange(k: string) {
    setSystemKey(k);
    // Auto-fill category from template or key heuristic
    const tpl = SECRET_TEMPLATES.find((t) => t.key === k.toUpperCase());
    setSystemCategory(tpl?.category ?? categoryFromKey(k));
  }

  function handleSystemSubmit() {
    if (!systemKey.trim() || !systemValue.trim() || systemMutation.isPending) return;
    systemMutation.mutate(
      {
        key: systemKey.trim().toUpperCase(),
        body: {
          value: systemValue.trim(),
          target: systemTarget || "shared",
          category: systemCategory,
        },
      },
      {
        onSuccess: () => {
          const createdKey = systemKey.trim().toUpperCase();
          onSystemCreated?.(createdKey);
          onClose();
        },
      },
    );
  }

  // ── USER sub-form ─────────────────────────────────────────────────────────
  const [userType, setUserType] = React.useState<string>(ENTITY_INFO_TYPES[0] as string);
  const [userValue, setUserValue] = React.useState("");
  const [userLabel, setUserLabel] = React.useState("");
  const userMutation = useCreateUserSecret();

  // Auto-fill label from type template suggestion
  function handleUserTypeChange(t: string) {
    setUserType(t);
    const tpl = USER_SECRET_TEMPLATES.find((tmpl) => tmpl.type === t);
    if (tpl) setUserLabel(tpl.label);
  }

  function handleUserSubmit() {
    if (!userType || !userValue.trim() || userMutation.isPending) return;
    if (!ownerEntityId) {
      // No owner entity — degrade gracefully with an error hint
      return;
    }
    const tpl = USER_SECRET_TEMPLATES.find((t) => t.type === userType);
    userMutation.mutate(
      {
        entityId: ownerEntityId,
        request: {
          type: userType,
          value: userValue.trim(),
          label: userLabel.trim() || null,
          secured: tpl?.secured ?? false,
        },
      },
      {
        onSuccess: () => { onClose(); },
      },
    );
  }

  // ── CONNECT PROVIDER sub-form ─────────────────────────────────────────────
  // Providers with live OAuth: google → reauthorizeUserCredential redirect
  // HA/OwnTracks/Steam/Spotify/WhatsApp → per-provider config drawers
  const [providerSlug, setProviderSlug] = React.useState<string | null>(null);
  const [oauthPending, setOauthPending] = React.useState(false);
  const [oauthError, setOauthError] = React.useState<string | null>(null);

  // Providers served by real provider-config drawers (bu-ayp6v.8/.9)
  const DRAWER_PROVIDER_SLUGS = new Set(["homeassistant", "owntracks", "steam", "spotify", "whatsapp"]);

  const OAUTH_PROVIDERS = [
    { slug: "google", label: "Google" },
  ];

  const STUB_PROVIDERS = [
    { slug: "homeassistant", label: "Home Assistant" },
    { slug: "owntracks",     label: "OwnTracks"      },
    { slug: "steam",         label: "Steam"           },
    { slug: "spotify",       label: "Spotify"         },
    { slug: "whatsapp",      label: "WhatsApp"        },
  ];

  async function handleOAuthConnect(slug: string, identity?: string) {
    if (!ownerEntityId) return;
    setOauthPending(true);
    setOauthError(null);
    try {
      const resolvedIdentity = identity ?? ownerEntityId;
      const resp = await reauthorizeUserCredential(slug, resolvedIdentity);
      if (!resp?.data?.redirect_url) throw new Error("No redirect URL returned.");
      window.location.assign(resp.data.redirect_url);
    } catch (err) {
      setOauthError(err instanceof Error ? err.message : "Connection failed.");
      setOauthPending(false);
    }
  }

  function handleStubConnect(slug: string) {
    // Route to real provider-config drawers (bu-ayp6v.8/.9).
    setProviderSlug(slug);
  }

  return (
    <div
      className="flex flex-col gap-4.5 p-7"
      data-passport-add-panel="true"
    >
      {/* Heading */}
      <div>
        <Eyebrow>add credential</Eyebrow>
        <div className="mt-2.5 flex items-center justify-between">
          <h1
            className="m-0"
            style={{
              fontFamily: "var(--font-sans, 'Inter Tight', sans-serif)",
              fontSize: 28,
              fontWeight: 500,
              letterSpacing: "-0.025em",
              lineHeight: 1.08,
              color: "var(--fg)",
            }}
          >
            What would you like to add?
          </h1>
        </div>
      </div>

      {/* Step 1 — family chooser */}
      {family === null && (
        <div
          className="flex flex-col gap-3 pt-3"
          style={{ borderTop: "1px solid var(--border)" }}
          data-add-family-chooser="true"
        >
          <Mono size={9} upper tracking="0.14em" color="var(--dim)">
            credential family
          </Mono>
          <div className="flex gap-2 flex-wrap">
            <PillBtn
              variant="commit"
              onClick={() => handleFamilySelect("system")}
            >
              system secret
            </PillBtn>
            <PillBtn
              variant="commit"
              onClick={() => handleFamilySelect("user")}
              disabled={!ownerEntityId}
            >
              user credential
            </PillBtn>
            <PillBtn
              variant="commit"
              onClick={() => handleFamilySelect("provider")}
            >
              connect provider
            </PillBtn>
          </div>
          {!ownerEntityId && (
            <Mono size={11} color="var(--dim)">
              user credential creation requires the owner entity to be set up
            </Mono>
          )}
        </div>
      )}

      {/* Step 2a — SYSTEM secret form */}
      {family === "system" && (
        <div
          className="flex flex-col gap-3 p-3.5"
          style={{ border: "1px solid var(--border-soft)", background: "var(--bg-elev)" }}
          data-add-system-panel="true"
        >
          <Mono size={9} upper tracking="0.14em" color="var(--dim)">
            new system secret
          </Mono>

          {/* Key field with template suggestions */}
          <div className="flex flex-col gap-1">
            <Mono size={9} upper tracking="0.12em" color="var(--dim)">key</Mono>
            <input
              type="text"
              value={systemKey}
              onChange={(e) => handleSystemKeyChange(e.target.value)}
              placeholder="SECRET_KEY_NAME"
              list="system-key-suggestions"
              className="font-mono text-[11px] p-2 outline-none w-full"
              style={{
                border: "1px solid var(--border-strong)",
                background: "var(--bg)",
                color: "var(--fg)",
                borderRadius: 3,
              }}
              data-system-key-input="true"
            />
            <datalist id="system-key-suggestions">
              {SECRET_TEMPLATES.map((t) => (
                <option key={t.key} value={t.key}>{t.description}</option>
              ))}
            </datalist>
          </div>

          {/* Value field */}
          <div className="flex flex-col gap-1">
            <Mono size={9} upper tracking="0.12em" color="var(--dim)">value</Mono>
            <textarea
              rows={3}
              value={systemValue}
              onChange={(e) => setSystemValue(e.target.value)}
              placeholder="paste value here"
              className="font-mono text-[11px] p-2 resize-none outline-none w-full"
              style={{
                border: "1px solid var(--border-strong)",
                background: "var(--bg)",
                color: "var(--fg)",
                borderRadius: 3,
              }}
            />
          </div>

          {/* Category */}
          <div className="flex flex-col gap-1">
            <Mono size={9} upper tracking="0.12em" color="var(--dim)">category</Mono>
            <select
              value={systemCategory}
              onChange={(e) => setSystemCategory(e.target.value as SecretCategory)}
              className="font-mono text-[11px] p-1.5 outline-none"
              style={{
                border: "1px solid var(--border-strong)",
                background: "var(--bg)",
                color: "var(--fg)",
                borderRadius: 3,
              }}
            >
              {SECRET_CATEGORIES.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>

          {/* Target */}
          <div className="flex flex-col gap-1">
            <Mono size={9} upper tracking="0.12em" color="var(--dim)">target</Mono>
            <input
              type="text"
              value={systemTarget}
              onChange={(e) => setSystemTarget(e.target.value)}
              placeholder="shared"
              className="font-mono text-[11px] p-2 outline-none w-full"
              style={{
                border: "1px solid var(--border-strong)",
                background: "var(--bg)",
                color: "var(--fg)",
                borderRadius: 3,
              }}
            />
            <Mono size={9} color="var(--dim)">
              "shared" for global · butler name for per-butler override
            </Mono>
          </div>

          {systemMutation.error && (
            <Mono size={11} color="var(--red)">
              {systemMutation.error instanceof Error
                ? systemMutation.error.message
                : "Save failed."}
            </Mono>
          )}

          <div className="flex gap-2">
            <PillBtn
              variant="commit"
              onClick={handleSystemSubmit}
              disabled={!systemKey.trim() || !systemValue.trim() || systemMutation.isPending}
            >
              {systemMutation.isPending ? "saving…" : "create"}
            </PillBtn>
            <PillBtn onClick={() => handleFamilySelect(null)}>
              back
            </PillBtn>
            <PillBtn onClick={onClose}>
              cancel
            </PillBtn>
          </div>
        </div>
      )}

      {/* Step 2b — USER credential form */}
      {family === "user" && (
        <div
          className="flex flex-col gap-3 p-3.5"
          style={{ border: "1px solid var(--border-soft)", background: "var(--bg-elev)" }}
          data-add-user-panel="true"
        >
          <Mono size={9} upper tracking="0.14em" color="var(--dim)">
            new user credential
          </Mono>

          {/* Type with template suggestions */}
          <div className="flex flex-col gap-1">
            <Mono size={9} upper tracking="0.12em" color="var(--dim)">type</Mono>
            <select
              value={userType}
              onChange={(e) => handleUserTypeChange(e.target.value)}
              className="font-mono text-[11px] p-1.5 outline-none"
              style={{
                border: "1px solid var(--border-strong)",
                background: "var(--bg)",
                color: "var(--fg)",
                borderRadius: 3,
              }}
              data-user-type-select="true"
            >
              {ENTITY_INFO_TYPES.map((t) => (
                <option key={t} value={t}>{entityInfoTypeLabel(t)}</option>
              ))}
            </select>
          </div>

          {/* Value */}
          <div className="flex flex-col gap-1">
            <Mono size={9} upper tracking="0.12em" color="var(--dim)">value</Mono>
            <textarea
              rows={3}
              value={userValue}
              onChange={(e) => setUserValue(e.target.value)}
              placeholder={USER_SECRET_TEMPLATES.find((t) => t.type === userType)?.description ?? "credential value"}
              className="font-mono text-[11px] p-2 resize-none outline-none w-full"
              style={{
                border: "1px solid var(--border-strong)",
                background: "var(--bg)",
                color: "var(--fg)",
                borderRadius: 3,
              }}
            />
          </div>

          {/* Label */}
          <div className="flex flex-col gap-1">
            <Mono size={9} upper tracking="0.12em" color="var(--dim)">label (optional)</Mono>
            <input
              type="text"
              value={userLabel}
              onChange={(e) => setUserLabel(e.target.value)}
              placeholder="human-readable label"
              className="font-mono text-[11px] p-2 outline-none w-full"
              style={{
                border: "1px solid var(--border-strong)",
                background: "var(--bg)",
                color: "var(--fg)",
                borderRadius: 3,
              }}
            />
          </div>

          {!ownerEntityId && (
            <Mono size={11} color="var(--red)">
              owner entity ID not available: cannot create user credential
            </Mono>
          )}

          {userMutation.error && (
            <Mono size={11} color="var(--red)">
              {userMutation.error instanceof Error
                ? userMutation.error.message
                : "Save failed."}
            </Mono>
          )}

          <div className="flex gap-2">
            <PillBtn
              variant="commit"
              onClick={handleUserSubmit}
              disabled={!userType || !userValue.trim() || userMutation.isPending || !ownerEntityId}
            >
              {userMutation.isPending ? "saving…" : "create"}
            </PillBtn>
            <PillBtn onClick={() => handleFamilySelect(null)}>
              back
            </PillBtn>
            <PillBtn onClick={onClose}>
              cancel
            </PillBtn>
          </div>
        </div>
      )}

      {/* Step 2c — CONNECT PROVIDER */}
      {family === "provider" && (
        <div
          className="flex flex-col gap-3"
          style={{ borderTop: "1px solid var(--border)" }}
          data-add-provider-panel="true"
        >
          {/* OAuth providers (wired now) */}
          <div className="pt-3">
            <Mono size={9} upper tracking="0.14em" color="var(--dim)">
              oauth · connect now
            </Mono>
            <div className="flex gap-2 flex-wrap mt-2">
              {OAUTH_PROVIDERS.map(({ slug, label }) => (
                <PillBtn
                  key={slug}
                  variant="commit"
                  onClick={() => handleOAuthConnect(slug)}
                  disabled={!ownerEntityId || oauthPending}
                >
                  {oauthPending ? "redirecting…" : `connect ${label}`}
                </PillBtn>
              ))}
            </div>
            {!ownerEntityId && (
              <Mono size={11} color="var(--dim)" className="mt-2">
                owner entity ID not available: cannot connect provider
              </Mono>
            )}
            {oauthError && (
              <Mono size={11} color="var(--red)" className="mt-2">
                {oauthError}
              </Mono>
            )}
          </div>

          {/* Other integrations — all use real provider-config drawers [bu-ayp6v.8/.9]. */}
          <div>
            <Mono size={9} upper tracking="0.14em" color="var(--dim)">
              other integrations
            </Mono>
            <div className="flex gap-2 flex-wrap mt-2">
              {STUB_PROVIDERS.map(({ slug, label }) => (
                <PillBtn
                  key={slug}
                  onClick={() => handleStubConnect(slug)}
                  disabled={providerSlug === slug}
                >
                  {label}
                </PillBtn>
              ))}
            </div>

            {/* Real provider-config drawers for HA / OwnTracks / Steam / Spotify / WhatsApp */}
            {providerSlug !== null && DRAWER_PROVIDER_SLUGS.has(providerSlug) && (
              <div className="mt-3" data-provider-connect-drawer={providerSlug}>
                {providerSlug === "homeassistant" && (
                  <HomeAssistantDrawer onClose={() => setProviderSlug(null)} />
                )}
                {providerSlug === "owntracks" && (
                  <OwnTracksDrawer onClose={() => setProviderSlug(null)} />
                )}
                {providerSlug === "steam" && (
                  <SteamDrawer onClose={() => setProviderSlug(null)} />
                )}
                {providerSlug === "spotify" && (
                  <SpotifyDrawer onClose={() => setProviderSlug(null)} />
                )}
                {providerSlug === "whatsapp" && (
                  <WhatsAppDrawer onClose={() => setProviderSlug(null)} />
                )}
              </div>
            )}
          </div>

          <div className="flex gap-2 pt-1">
            <PillBtn onClick={() => handleFamilySelect(null)}>
              back
            </PillBtn>
            <PillBtn onClick={onClose}>
              cancel
            </PillBtn>
          </div>
        </div>
      )}

      {/* Footer cancel (when family is null) */}
      {family === null && (
        <div className="flex gap-2 pt-1">
          <PillBtn onClick={onClose}>cancel</PillBtn>
        </div>
      )}
    </div>
  );
}
