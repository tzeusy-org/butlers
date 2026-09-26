// ---------------------------------------------------------------------------
// SecurityPostureTile -- dashboard security-posture indicator
// (bu-dl98i.1.4, bu-dl98i.6.3, bu-zxxyo)
//
// Data source: useHealthPosture -> GET /api/health
// Fields used: auth.owner_auth_enabled, auth.owner_auth_available, auth.export_secret_insecure_default
//              security.insecure_infra_defaults
//              security.role_enforcement_disabled
//
// Displays boolean posture indicators only.  No secret values are ever
// shown or transmitted; the backend enforces this at the source.
// ---------------------------------------------------------------------------

import {
  Tile,
  TileContent,
  TileDescription,
  TileHeader,
  TileTitle,
} from "@/components/ui/Tile"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { useHealthPosture } from "@/hooks/use-system"

// ---------------------------------------------------------------------------
// Loading / error sub-components
// ---------------------------------------------------------------------------

function TileSkeleton() {
  return (
    <Tile>
      <TileHeader>
        <TileTitle>Security Posture</TileTitle>
        <TileDescription>Auth and secrets configuration</TileDescription>
      </TileHeader>
      <TileContent>
        <div data-testid="security-posture-tile-skeleton" className="space-y-2">
          <Skeleton className="h-5 w-48" />
          <Skeleton className="h-5 w-56" />
        </div>
      </TileContent>
    </Tile>
  )
}

function TileError() {
  return (
    <Tile>
      <TileHeader>
        <TileTitle>Security Posture</TileTitle>
        <TileDescription>Auth and secrets configuration</TileDescription>
      </TileHeader>
      <TileContent>
        <p data-testid="security-posture-tile-error" className="text-destructive text-sm">
          Could not load security posture.
        </p>
      </TileContent>
    </Tile>
  )
}

// ---------------------------------------------------------------------------
// PostureRow
// ---------------------------------------------------------------------------

interface PostureRowProps {
  label: string;
  /** When true the posture is secure (green). When false it is a warning (amber). */
  secure: boolean;
  secureLabel: string;
  insecureLabel: string;
  testId: string;
}

function PostureRow({ label, secure, secureLabel, insecureLabel, testId }: PostureRowProps) {
  return (
    <div className="flex items-center justify-between gap-2 py-1">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="m-0">
        <Badge
          variant={secure ? "default" : "outline"}
          className={secure ? "bg-[var(--green)] hover:bg-[var(--green)] text-white" : "text-[var(--amber-text)] border-[var(--amber)]"}
          data-testid={testId}
        >
          {secure ? secureLabel : insecureLabel}
        </Badge>
      </dd>
    </div>
  )
}

// ---------------------------------------------------------------------------
// SecurityPostureTile
// ---------------------------------------------------------------------------

/**
 * Displays security-posture booleans from the health endpoint.
 *
 * Indicators:
 *   - Owner authentication: whether the mandatory boundary is enabled and available
 *   - Export secret: whether DASHBOARD_EXPORT_SECRET is explicitly configured
 *   - Infra defaults: whether any infra credential is at its known default or
 *     Grafana anonymous access is enabled outside dev posture
 *   - DB role enforcement: whether SET ROLE schema-isolation is active for
 *     the managed database connections (disabled in dev posture)
 *
 * Values are booleans only — no secret material is displayed or fetched.
 */
export function SecurityPostureTile() {
  const { data: response, isPending, isError } = useHealthPosture()

  if (isPending) return <TileSkeleton />
  if (isError) return <TileError />

  const posture = response?.auth
  const security = response?.security

  return (
    <Tile>
      <TileHeader>
        <TileTitle>Security Posture</TileTitle>
        <TileDescription>Auth and secrets configuration</TileDescription>
      </TileHeader>
      <TileContent data-testid="security-posture-tile-content">
        <dl className="divide-y divide-border">
          <PostureRow
            label="Owner authentication"
            secure={Boolean(posture?.owner_auth_enabled && posture?.owner_auth_available)}
            secureLabel="Enabled"
            insecureLabel="Unavailable"
            testId="posture-owner-auth"
          />
          <PostureRow
            label="Export secret"
            secure={!(posture?.export_secret_insecure_default ?? true)}
            secureLabel="Configured"
            insecureLabel="Insecure default"
            testId="posture-export-secret"
          />
          <PostureRow
            label="Infra credentials"
            secure={!(security?.insecure_infra_defaults ?? true)}
            secureLabel="Hardened"
            insecureLabel="Insecure defaults active"
            testId="posture-infra-defaults"
          />
          <PostureRow
            label="DB role enforcement"
            secure={!(security?.role_enforcement_disabled ?? true)}
            secureLabel="Active"
            insecureLabel="Disabled (dev posture)"
            testId="posture-role-enforcement"
          />
        </dl>
      </TileContent>
    </Tile>
  )
}
