/**
 * GoogleHealthStatusCard — per-account Google Health connector status.
 *
 * Renders one widget per entry in the API's `accounts[]` list. When only
 * one account is present the single-card layout is visually identical to
 * the pre-multi-account shape (back-compat requirement, ADR-1).
 *
 * When `accounts` is empty or the API returns `state = "not_configured"`,
 * a single "not configured" card is shown instead.
 *
 * State → colour mapping follows the StateDot / Dispatch §4e convention:
 *   healthy   → green
 *   degraded  → amber
 *   error     → red
 *   not_configured → muted
 *
 * Spec: openspec/changes/connector-google-health-multi-account/specs/
 *       connector-google-health/spec.md §"Health Status Reporting"
 *
 * bead: bu-91zdb.8
 */

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Time } from "@/components/ui/time";
import { StateDot } from "@/components/ui/StateDot";
import type {
  GoogleHealthAccountStatus,
  GoogleHealthConnectorState,
  GoogleHealthStatusResponse,
} from "@/api/types";

// ---------------------------------------------------------------------------
// State-colour helpers
// ---------------------------------------------------------------------------

/** CSS class for state text colour, matching Dispatch §4e tokens. */
function stateTextClass(state: GoogleHealthConnectorState): string {
  switch (state) {
    case "healthy":
      return "text-[color:var(--green,oklch(0.72_0.17_150))]";
    case "degraded":
      return "text-[color:var(--amber,oklch(0.72_0.12_70))]";
    case "error":
      return "text-[color:var(--red,oklch(0.62_0.20_25))]";
    default:
      return "text-muted-foreground";
  }
}

/**
 * Maps GoogleHealthConnectorState to a StateDot-compatible state.
 * `not_configured` falls back to `archived` (muted) since StateDot does not
 * have a direct equivalent.
 */
function toDotState(state: GoogleHealthConnectorState) {
  switch (state) {
    case "healthy":
      return "healthy" as const;
    case "degraded":
      return "degraded" as const;
    case "error":
      return "error" as const;
    default:
      return "archived" as const;
  }
}

// ---------------------------------------------------------------------------
// Connector-failure (degraded/error) message helper
// ---------------------------------------------------------------------------

/**
 * Maps a connector-reported `error_message` to a human-readable, owner-facing
 * sentence. The connector emits short machine codes (e.g. `api_forbidden`,
 * `scope_missing`); the dashboard turns them into actionable text so a failing
 * connector reads as "unavailable" rather than as an empty / no-data state.
 *
 * Unknown codes fall through to a generic "unavailable" message that still
 * carries the raw code so nothing is silently swallowed.
 */
function formatConnectorError(code: string): string {
  switch (code) {
    case "api_forbidden":
      return "Google Health connector unavailable (403). The Google Health API rejected the request. This usually means the OAuth grant is still in test mode or the account is not on the Google Health access allowlist.";
    case "scope_missing":
      return "Google Health connector unavailable. Required Google Health scopes are missing. Re-grant the scopes in Settings.";
    case "token_invalid":
      return "Google Health connector unavailable. The Google account's authorization was revoked. Reconnect the account in Settings.";
    case "source_api_unreachable":
      return "Google Health connector unavailable. The Google Health API could not be reached.";
    case "no_primary_account":
      return "Google Health connector unavailable. No Google account is connected.";
    default:
      return `Google Health connector unavailable (${code}).`;
  }
}

/**
 * A connector-failure banner shown inside an account widget when the account's
 * state is degraded/error AND the connector reported a failure reason. This is
 * the degraded signal that distinguishes "connector failing" from "no data".
 */
function ConnectorErrorBanner({ code }: { code: string }) {
  return (
    <div
      role="alert"
      data-testid="connector-error-banner"
      className="mb-3 rounded border border-[color:var(--red,oklch(0.62_0.20_25))]/40 bg-[color:var(--red,oklch(0.62_0.20_25))]/10 px-2.5 py-2 text-xs text-[color:var(--red,oklch(0.62_0.20_25))]"
    >
      {formatConnectorError(code)}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Scope-count summary helper
// ---------------------------------------------------------------------------

const GOOGLE_HEALTH_SCOPES = [
  "https://www.googleapis.com/auth/fitness.sleep.read",
  "https://www.googleapis.com/auth/fitness.activity.read",
  "https://www.googleapis.com/auth/fitness.heart_rate.read",
];

/**
 * Returns a short human-readable summary of which Google Health scopes are
 * granted, e.g. "3 / 3 scopes" or "1 / 3 scopes".
 * Uses exact URL matching against the known scope list.
 */
function formatScopeSummary(scopesGranted: string[]): string {
  const count = GOOGLE_HEALTH_SCOPES.filter((scope) => scopesGranted.includes(scope)).length;
  const total = GOOGLE_HEALTH_SCOPES.length;
  return `${count} / ${total} scopes`;
}

// ---------------------------------------------------------------------------
// AccountWidget — renders a single account's status
// ---------------------------------------------------------------------------

interface AccountWidgetProps {
  account: GoogleHealthAccountStatus;
  isPrimary: boolean;
}

function AccountWidget({ account, isPrimary }: AccountWidgetProps) {
  return (
    <Card data-testid="google-health-account-widget">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            {/* State dot — uses StateDot primitive for consistent token-based colour */}
            <StateDot state={toDotState(account.state)} size={8} />
            <span className="font-mono text-xs truncate" data-testid="account-email">
              {account.email}
            </span>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {isPrimary && (
              <Badge variant="outline" className="text-[10px] py-0 px-1.5">
                primary
              </Badge>
            )}
            <span
              className={`text-xs font-mono ${stateTextClass(account.state)}`}
              data-testid="account-state"
            >
              {account.state}
            </span>
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {(account.state === "degraded" || account.state === "error") &&
        account.error_message ? (
          <ConnectorErrorBanner code={account.error_message} />
        ) : null}
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
          <dt className="text-muted-foreground">Scopes</dt>
          <dd className="text-right font-mono text-xs tabular-nums">
            {formatScopeSummary(account.scopes_granted)}
          </dd>

          <dt className="text-muted-foreground">Last ingest</dt>
          <dd className="text-right text-xs text-muted-foreground">
            {account.last_ingest_at ? (
              <Time value={account.last_ingest_at} mode="relative" />
            ) : (
              "—"
            )}
          </dd>

          <dt className="text-muted-foreground">Sleep sessions · 7d</dt>
          <dd className="text-right font-mono tabular-nums" data-testid="sleep-sessions-7d">
            {account.sleep_sessions_7d}
          </dd>

          <dt className="text-muted-foreground">Daily summaries · 7d</dt>
          <dd className="text-right font-mono tabular-nums" data-testid="daily-summaries-7d">
            {account.daily_summaries_7d}
          </dd>
        </dl>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// NotConfiguredCard — shown when state = "not_configured" or accounts is empty
// ---------------------------------------------------------------------------

function NotConfiguredCard() {
  return (
    <Card data-testid="google-health-not-configured">
      <CardContent className="pt-4">
        <p className="text-sm text-muted-foreground italic">
          Google Health is not configured. Grant the Google Health scopes in
          Settings to enable data ingestion.
        </p>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// GoogleHealthStatusCard — public entry point
// ---------------------------------------------------------------------------

export interface GoogleHealthStatusCardProps {
  /** Live data from GET /api/connectors/google-health/status. */
  status: GoogleHealthStatusResponse;
}

/**
 * Renders one AccountWidget per `status.accounts[]` entry.
 * Falls back to a single not-configured card when accounts is empty.
 *
 * Single-account back-compat: when `accounts.length === 1` the output is
 * visually identical to the pre-multi-account shape — one card with the same
 * fields and the same state colour.
 */
export function GoogleHealthStatusCard({ status }: GoogleHealthStatusCardProps) {
  const accounts = status.accounts ?? [];
  const primaryEmail = status.primary_account_email ?? null;

  if (accounts.length === 0) {
    return (
      <div data-testid="google-health-status-card">
        <NotConfiguredCard />
      </div>
    );
  }

  return (
    <div data-testid="google-health-status-card">
      <div
        className={
          accounts.length === 1
            ? "space-y-3"
            : "grid grid-cols-1 gap-3 sm:grid-cols-2"
        }
      >
        {accounts.map((account) => (
          <AccountWidget
            key={account.email}
            account={account}
            isPrimary={account.email === primaryEmail}
          />
        ))}
      </div>
    </div>
  );
}
