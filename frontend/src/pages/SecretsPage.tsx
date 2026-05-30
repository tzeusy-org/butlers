// ---------------------------------------------------------------------------
// SecretsPage — /secrets route [bu-q77du]
//
// Mounts DirectionPassport as the sole surface for the /secrets route.
// Inventory is fetched from GET /api/secrets/inventory?identity=<uuid> via
// TanStack Query (bu-nrgk9). The ?identity= URL param drives the query input.
//
// Cross-page reauth bookkeeping (§Cross-Page Reauth Bookkeeping):
//   ?toast=connected  → green sonner toast + strip param
//   ?oauth_error=<e>  → amber sonner toast + strip param
//
// Spec: openspec/changes/redesign-secrets-passport/specs/butler-secrets/spec.md
//   §Passport-Book Information Architecture
//   §Deep-Link Focus Routing
//   §Projection-Lens Identity Switcher
//   §Cross-Page Reauth Bookkeeping
// ---------------------------------------------------------------------------

import * as React from "react";
import { useSearchParams } from "react-router";
import { toast } from "sonner";

import { DirectionPassport } from "@/components/secrets/passport";
import { useSecretsInventory } from "@/hooks/use-secrets-inventory.ts";

export default function SecretsPage() {
  const [searchParams, setSearchParams] = useSearchParams();

  // Derive stable primitives to keep the effect dep array honest.
  const toastParam = searchParams.get("toast");
  const errorParam = searchParams.get("oauth_error");
  const identityParam = searchParams.get("identity");

  // Cross-page reauth bookkeeping: surface ?toast= / ?oauth_error= once then
  // strip the params so a refresh does not re-show the same toast.
  React.useEffect(() => {
    if (!toastParam && !errorParam) return;

    const params = new URLSearchParams(searchParams);

    if (toastParam === "connected") {
      const focusParam = params.get("focus");
      const provider = focusParam?.startsWith("u:") ? focusParam.slice(2) : null;
      const msg = provider
        ? `${provider.charAt(0).toUpperCase() + provider.slice(1)} connected.`
        : "Connection successful.";
      toast.success(msg);
    }

    if (errorParam) {
      toast.warning(`OAuth error: ${errorParam.replace(/_/g, " ")}`);
    }

    params.delete("toast");
    params.delete("oauth_error");
    setSearchParams(params, { replace: true });
  }, [toastParam, errorParam, searchParams, setSearchParams]);

  // Fetch the credential inventory. The ?identity= URL param scopes the user
  // credential array to a specific entity (projection-lens semantics).
  const { data: inventory, isLoading, isError } = useSecretsInventory({
    identity: identityParam ?? undefined,
  });

  if (isLoading) {
    return (
      <div
        data-direction-passport="true"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "100%",
          color: "var(--dim)",
          fontFamily: "var(--font-mono, monospace)",
          fontSize: 13,
        }}
      >
        Loading credentials…
      </div>
    );
  }

  if (isError || !inventory) {
    return (
      <div
        data-direction-passport="true"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "100%",
          color: "var(--red)",
          fontFamily: "var(--font-mono, monospace)",
          fontSize: 13,
        }}
      >
        Failed to load credentials.
      </div>
    );
  }

  return <DirectionPassport inventory={inventory} />;
}
