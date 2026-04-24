/**
 * GoogleHealthStatusCard — connector status card for Google Health.
 *
 * Appears below the Google account section when the primary Google account
 * has granted the three Google Health scopes. Not rendered otherwise —
 * ``GoogleOAuthSection``'s scope-set picker already surfaces a CTA for the
 * ungranted case so there's no "connect" action on this card itself.
 *
 * Polls ``GET /api/connectors/google-health/status`` every 30 seconds while
 * the tab is visible (React Query's ``refetchIntervalInBackground: false``
 * pauses the poller automatically when the browser backgrounds the tab).
 *
 * Spec: openspec/changes/google-health-connector/specs/
 *       dashboard-google-accounts/spec.md
 *       - "Google Health Connector Status Card"
 *       - "Test-Mode Pre-Verification Warning"
 */

import { formatDistanceToNow } from "date-fns";
import { Loader2 } from "lucide-react";

import {
  getGoogleOAuthStartUrl,
  type GoogleHealthConnectorState,
  type GoogleHealthStatusResponse,
} from "@/api/index.ts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useGoogleHealthStatus } from "@/hooks/use-google-health";
import { useGoogleAccounts } from "@/hooks/use-secrets";
import {
  computeTestModeBannerVariant,
  TEST_MODE_LEARN_MORE_URL,
} from "@/lib/google-health-test-mode";
import { computeTokenExpiry } from "./GoogleHealthStatusCard.utils";

// ---------------------------------------------------------------------------
// State pill
// ---------------------------------------------------------------------------

interface StatePillSpec {
  label: string;
  variant: "default" | "secondary" | "destructive" | "outline";
  className?: string;
}

function statePillSpec(state: GoogleHealthConnectorState): StatePillSpec {
  switch (state) {
    case "healthy":
      return {
        label: "Healthy",
        variant: "default",
        className: "bg-emerald-500/15 text-emerald-700 border-emerald-500/30",
      };
    case "degraded":
      return {
        label: "Degraded",
        variant: "secondary",
        className: "bg-amber-500/15 text-amber-800 border-amber-500/30",
      };
    case "error":
      return {
        label: "Error",
        variant: "destructive",
      };
    case "not_configured":
      return {
        label: "Not configured",
        variant: "outline",
      };
  }
}

// ---------------------------------------------------------------------------
// Relative time formatter — tolerant of bad inputs.
// ---------------------------------------------------------------------------

function formatRelative(iso: string | null): string {
  if (!iso) return "never";
  try {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    return formatDistanceToNow(date, { addSuffix: true });
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// Test-mode banner (orange + red variants)
// ---------------------------------------------------------------------------

interface TestModeBannerProps {
  lastTokenRefreshAt: string | null;
  reconsentUrl: string;
}

function TestModeBanner({
  lastTokenRefreshAt,
  reconsentUrl,
}: TestModeBannerProps) {
  const variant = computeTestModeBannerVariant(lastTokenRefreshAt);

  if (variant === "red") {
    return (
      <div
        role="alert"
        data-testid="gh-test-mode-banner-red"
        className="rounded-md border border-destructive/40 bg-destructive/10 p-3 space-y-2"
      >
        <p className="text-sm font-medium text-destructive">
          Your Google Health consent is about to expire. Re-grant scopes to
          avoid an ingestion gap.
        </p>
        <a href={reconsentUrl} target="_blank" rel="noopener noreferrer">
          <Button size="sm" variant="destructive">
            Re-grant Google Health
          </Button>
        </a>
      </div>
    );
  }

  return (
    <div
      role="status"
      data-testid="gh-test-mode-banner-orange"
      className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 space-y-1"
    >
      <p className="text-sm text-amber-900">
        This OAuth client is in Google&apos;s test mode. Your consent expires
        every 7 days until the production-mode verification completes. You may
        need to re-grant Google Health scopes periodically.
      </p>
      <a
        href={TEST_MODE_LEARN_MORE_URL}
        target="_blank"
        rel="noopener noreferrer"
        className="text-xs underline underline-offset-2 text-amber-900"
      >
        Learn more
      </a>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Status card body
// ---------------------------------------------------------------------------

function StatusCardBody({ data }: { data: GoogleHealthStatusResponse }) {
  const pill = statePillSpec(data.state);
  const reconsentUrl = getGoogleOAuthStartUrl({
    forceConsent: true,
    scopeSet: "health",
  });

  return (
    <div className="space-y-3">
      {data.test_mode && (
        <TestModeBanner
          lastTokenRefreshAt={data.last_token_refresh_at}
          reconsentUrl={reconsentUrl}
        />
      )}

      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <p className="text-xs text-muted-foreground">Connection state</p>
          <Badge variant={pill.variant} className={pill.className}>
            {pill.label}
          </Badge>
        </div>
        <div className="text-right min-w-0">
          <p className="text-xs text-muted-foreground">Last ingest</p>
          <p className="text-sm font-medium">
            {formatRelative(data.last_ingest_at)}
          </p>
        </div>
      </div>

      {data.last_token_refresh_at && (
        <div>
          <p className="text-xs text-muted-foreground">Last token refresh</p>
          <p className="text-sm">{formatRelative(data.last_token_refresh_at)}</p>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        <div data-testid="gh-sleep-sessions-7d">
          <p className="text-xs text-muted-foreground">Sleep sessions (7d)</p>
          <p className="text-sm font-medium">{data.sleep_sessions_7d}</p>
        </div>
        <div data-testid="gh-daily-summaries-7d">
          <p className="text-xs text-muted-foreground">Daily summaries (7d)</p>
          <p className="text-sm font-medium">{data.daily_summaries_7d}</p>
        </div>
      </div>

      {/*
        Rate-limit headroom row is hidden whenever rate_limit_remaining is
        null — the connector has not yet observed an X-RateLimit header.
        This is explicit per the spec: null is distinct from 0.
      */}
      {data.rate_limit_remaining !== null && (
        <div data-testid="gh-rate-limit-row">
          <p className="text-xs text-muted-foreground">Rate limit remaining</p>
          <p className="text-sm font-mono">{data.rate_limit_remaining}</p>
        </div>
      )}

      <div data-testid="gh-token-expiry-row">
        <p className="text-xs text-muted-foreground">Estimated expiry</p>
        <p className="text-sm">
          {computeTokenExpiry(data.test_mode, data.last_token_refresh_at)}
        </p>
      </div>

      {data.state === "error" && (
        <div
          role="alert"
          className="rounded-md border border-destructive/40 bg-destructive/10 p-3"
        >
          <p className="text-sm font-medium text-destructive">
            The Google Health connector is in an error state. Re-grant scopes
            or check connector logs.
          </p>
          <a
            href={reconsentUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-block mt-2"
          >
            <Button size="sm" variant="destructive">
              Re-grant Google Health
            </Button>
          </a>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// GoogleHealthStatusCard — public entry point
// ---------------------------------------------------------------------------

export function GoogleHealthStatusCard() {
  const accountsQuery = useGoogleAccounts();
  const accounts = accountsQuery.data ?? [];
  const primary = accounts.find((acct) => acct.is_primary);

  // Scope-presence check — matches the backend
  // ``router.GOOGLE_HEALTH_SCOPE_URLS`` set exactly.
  const primaryHasHealthScopes = primary
    ? [
        "https://www.googleapis.com/auth/googlehealth.sleep",
        "https://www.googleapis.com/auth/googlehealth.activity_and_fitness",
        "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements",
      ].every((scope) => primary.granted_scopes.includes(scope))
    : false;

  // Card is only enabled (rendered + polled) when the primary has Google
  // Health scopes. See "Health-card state when scopes absent" scenario —
  // the ungranted CTA lives on the scope-set picker, not here.
  const statusQuery = useGoogleHealthStatus({
    enabled: primaryHasHealthScopes,
  });

  if (!primaryHasHealthScopes) return null;

  return (
    <Card data-testid="google-health-status-card">
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          Google Health
          {statusQuery.isFetching && (
            <Loader2
              data-testid="gh-refresh-indicator"
              className="h-3.5 w-3.5 animate-spin text-muted-foreground"
            />
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {statusQuery.isLoading && !statusQuery.data ? (
          <div className="space-y-2">
            <Skeleton className="h-6 w-24" />
            <Skeleton className="h-4 w-48" />
          </div>
        ) : statusQuery.isError ? (
          <p className="text-sm text-destructive">
            Failed to load Google Health status. Ensure the dashboard API is
            running.
          </p>
        ) : statusQuery.data ? (
          <StatusCardBody data={statusQuery.data} />
        ) : null}
      </CardContent>
    </Card>
  );
}
