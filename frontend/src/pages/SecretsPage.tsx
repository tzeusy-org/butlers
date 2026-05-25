// ---------------------------------------------------------------------------
// SecretsPage — /secrets route [bu-q77du]
//
// Mounts DirectionPassport as the sole surface for the /secrets route.
// All URL state (?focus=, ?identity=, ?sort=) is managed inside DirectionPassport.
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

export default function SecretsPage() {
  const [searchParams, setSearchParams] = useSearchParams();

  // Cross-page reauth bookkeeping: surface ?toast= / ?oauth_error= once then
  // strip the params so a refresh does not re-show the same toast.
  React.useEffect(() => {
    const toastParam = searchParams.get("toast");
    const errorParam = searchParams.get("oauth_error");

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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <DirectionPassport />;
}
