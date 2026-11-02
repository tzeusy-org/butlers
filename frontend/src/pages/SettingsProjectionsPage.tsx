/**
 * SettingsProjectionsPage — /settings/projections
 *
 * The owner's list of ambient projection grants (wall display, e-ink panel,
 * Home Assistant sensor). One row per grant with its last fetch, so a dark
 * surface is visible here as well as in the attention strip that links to this
 * page. Verbs: rotate the token in one step, or revoke the grant.
 *
 * Minting stays on the API; this page lists, rotates and revokes
 * (add-ambient-projection-grants tasks 2.2). Tokens are returned once by
 * rotate and are never listed, so the new token is shown once, in place.
 *
 * Design language: Dispatch. Hairline rows, no card chrome, state is a dot and
 * colour only when state demands.
 */

import { useState } from "react";
import { toast } from "sonner";

import type { MintedProjection, ProjectionGrant } from "@/api/projections";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { InlineActionLink } from "@/components/ui/inline-action-link";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { useProjections, useRevokeProjection, useRotateProjection } from "@/hooks/use-projections";
import { cn } from "@/lib/utils";

const SURFACE_LABEL: Record<string, string> = {
  wall_display: "Wall display",
  eink: "E-ink",
  home_assistant: "Home Assistant",
  other: "Other",
};

/** A surface lapses once its glance's own valid_until (2x interval) has passed. */
function isLapsed(grant: ProjectionGrant): boolean {
  const seen = Date.parse(grant.last_fetch_at ?? grant.created_at);
  return Date.now() - seen > 2 * grant.expected_fetch_interval_seconds * 1000;
}

function intervalLabel(seconds: number): string {
  return seconds % 60 === 0 ? `${seconds / 60}m` : `${seconds}s`;
}

function NewToken({ minted, onDismiss }: { minted: MintedProjection; onDismiss: () => void }) {
  async function copy() {
    try {
      await navigator.clipboard.writeText(minted.token);
      toast.success("Token copied");
    } catch {
      toast.error("Could not copy; select the token instead");
    }
  }
  return (
    <section
      aria-label={`New token for ${minted.label}`}
      className="attention-row px-4 py-3 space-y-2"
      data-tone="amber"
    >
      <p className="text-sm">
        New token for {minted.label}, shown once. The previous token no longer works; paste this
        one into the surface now.
      </p>
      <code className="block break-all font-mono text-xs">{minted.token}</code>
      <p className="font-mono text-xs text-muted-foreground">{minted.path}</p>
      <div className="flex gap-2">
        <Button size="xs" variant="outline" onClick={() => void copy()}>
          Copy token
        </Button>
        <Button size="xs" variant="ghost" onClick={onDismiss}>
          Dismiss
        </Button>
      </div>
    </section>
  );
}

function FetchCell({ grant, lapsed }: { grant: ProjectionGrant; lapsed: boolean }) {
  if (grant.revoked_at) return <span className="text-muted-foreground">revoked</span>;
  return (
    <span className="inline-flex items-center gap-2">
      <span
        aria-hidden
        className={cn(
          "size-1.5 rounded-full",
          lapsed ? "bg-[var(--amber-text)]" : "bg-[var(--green-text)]",
        )}
      />
      {grant.last_fetch_at ? (
        <>
          <Time value={grant.last_fetch_at} mode="relative-compact" />
          {grant.last_fetch_status === "degraded" && (
            <span className="text-muted-foreground">degraded</span>
          )}
        </>
      ) : (
        <span>never fetched</span>
      )}
      {lapsed && <span className="text-[var(--amber-text)]">lapsed</span>}
    </span>
  );
}

export default function SettingsProjectionsPage() {
  const { data, isLoading, isError, refetch } = useProjections();
  const revoke = useRevokeProjection();
  const rotate = useRotateProjection();
  const [revoking, setRevoking] = useState<ProjectionGrant | null>(null);
  const [minted, setMinted] = useState<MintedProjection | null>(null);

  const grants = data?.data ?? [];

  function onRotate(grant: ProjectionGrant) {
    rotate.mutate(grant.id, {
      onSuccess: (response: { data: MintedProjection }) => setMinted(response.data),
      onError: () => toast.error(`Could not rotate the token for ${grant.label}`),
    });
  }

  function onRevoke() {
    if (!revoking) return;
    const target = revoking;
    revoke.mutate(target.id, {
      onSuccess: () => {
        setRevoking(null);
        setMinted((current) => (current?.id === target.id ? null : current));
        toast.success(`Revoked ${target.label}`);
      },
      onError: () => toast.error(`Could not revoke ${target.label}`),
    });
  }

  return (
    <div className="space-y-6">
      <div>
        <Eyebrow as="p" className="mb-2">
          settings · projections
        </Eyebrow>
        <h1 className="text-3xl font-medium tracking-tight leading-tight">Projections</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Glance surfaces that read one route with their own token. A surface that stops fetching
          is flagged here and on the dashboard.
        </p>
      </div>

      {minted && <NewToken minted={minted} onDismiss={() => setMinted(null)} />}

      {isLoading ? (
        <div className="space-y-1">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : isError ? (
        <div
          className="attention-row px-4 py-3 flex items-center justify-between"
          data-tone="amber"
          role="alert"
        >
          <p className="text-sm">Could not load projections.</p>
          <InlineActionLink onClick={() => void refetch()}>Retry →</InlineActionLink>
        </div>
      ) : grants.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No projection grants yet. Mint one from the API to connect a wall display, e-ink panel,
          or Home Assistant sensor.
        </p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border/60 text-left">
              {["Surface", "Audience", "Every", "Last fetch"].map((heading) => (
                <th key={heading} scope="col" className="py-2 pr-4 font-normal">
                  <Eyebrow>{heading}</Eyebrow>
                </th>
              ))}
              <th scope="col" className="py-2 font-normal">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {grants.map((grant) => {
              const lapsed = !grant.revoked_at && isLapsed(grant);
              return (
                <tr
                  key={grant.id}
                  data-lapsed={lapsed ? "true" : "false"}
                  className={cn(
                    "border-b border-border/60",
                    grant.revoked_at && "text-muted-foreground",
                  )}
                >
                  <td className="py-2 pr-4">
                    <div className="font-medium">{grant.label}</div>
                    <div className="text-xs text-muted-foreground">
                      {SURFACE_LABEL[grant.surface_kind] ?? grant.surface_kind}
                    </div>
                  </td>
                  <td className="py-2 pr-4">
                    {grant.audience_class === "household" ? "Household" : "Owner private"}
                  </td>
                  <td className="py-2 pr-4 tabular-nums">
                    {intervalLabel(grant.expected_fetch_interval_seconds)}
                  </td>
                  <td className="py-2 pr-4">
                    <FetchCell grant={grant} lapsed={lapsed} />
                  </td>
                  <td className="py-2 text-right">
                    {!grant.revoked_at && (
                      <span className="inline-flex gap-2">
                        <Button
                          size="xs"
                          variant="outline"
                          disabled={rotate.isPending}
                          aria-label={`Rotate token for ${grant.label}`}
                          onClick={() => onRotate(grant)}
                        >
                          Rotate
                        </Button>
                        <Button
                          size="xs"
                          variant="outline"
                          aria-label={`Revoke ${grant.label}`}
                          onClick={() => setRevoking(grant)}
                        >
                          Revoke
                        </Button>
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      <ConfirmDialog
        open={revoking !== null}
        onOpenChange={(open) => !open && setRevoking(null)}
        title={`Revoke ${revoking?.label ?? "projection"}?`}
        description="The surface stops receiving its glance immediately. This cannot be undone; mint a new grant to reconnect it."
        confirmLabel="Revoke"
        pendingLabel="Revoking…"
        variant="destructive"
        pending={revoke.isPending}
        onConfirm={onRevoke}
        testId="revoke-projection-dialog"
      />
    </div>
  );
}
