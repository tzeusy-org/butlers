// ---------------------------------------------------------------------------
// SecurityPostureTile -- dashboard security-posture indicator
// (bu-dl98i.1.4)
//
// Data source: useHealthPosture -> GET /api/health
// Fields used: auth.api_key_auth_enabled, auth.export_secret_insecure_default
//
// Displays boolean posture indicators only.  No secret values are ever
// shown or transmitted; the backend enforces this at the source.
// ---------------------------------------------------------------------------

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { useHealthPosture } from "@/hooks/use-system"

// ---------------------------------------------------------------------------
// Loading / error sub-components
// ---------------------------------------------------------------------------

function TileSkeleton() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Security Posture</CardTitle>
        <CardDescription>Auth and secrets configuration</CardDescription>
      </CardHeader>
      <CardContent>
        <div data-testid="security-posture-tile-skeleton" className="space-y-2">
          <Skeleton className="h-5 w-48" />
          <Skeleton className="h-5 w-56" />
        </div>
      </CardContent>
    </Card>
  )
}

function TileError() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Security Posture</CardTitle>
        <CardDescription>Auth and secrets configuration</CardDescription>
      </CardHeader>
      <CardContent>
        <p data-testid="security-posture-tile-error" className="text-destructive text-sm">
          Could not load security posture.
        </p>
      </CardContent>
    </Card>
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
          className={secure ? "bg-green-600 hover:bg-green-600 text-white" : "text-amber-600 border-amber-400"}
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
 *   - API key auth: whether the dashboard requires an X-API-Key header
 *   - Export secret: whether DASHBOARD_EXPORT_SECRET is explicitly configured
 *
 * Values are booleans only — no secret material is displayed or fetched.
 */
export function SecurityPostureTile() {
  const { data: response, isPending, isError } = useHealthPosture()

  if (isPending) return <TileSkeleton />
  if (isError) return <TileError />

  const posture = response?.auth

  return (
    <Card>
      <CardHeader>
        <CardTitle>Security Posture</CardTitle>
        <CardDescription>Auth and secrets configuration</CardDescription>
      </CardHeader>
      <CardContent data-testid="security-posture-tile-content">
        <dl className="divide-y divide-border">
          <PostureRow
            label="API key auth"
            secure={posture?.api_key_auth_enabled ?? false}
            secureLabel="Enabled"
            insecureLabel="Disabled (network-only)"
            testId="posture-api-key-auth"
          />
          <PostureRow
            label="Export secret"
            secure={!(posture?.export_secret_insecure_default ?? true)}
            secureLabel="Configured"
            insecureLabel="Insecure default"
            testId="posture-export-secret"
          />
        </dl>
      </CardContent>
    </Card>
  )
}
