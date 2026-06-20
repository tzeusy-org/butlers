// ---------------------------------------------------------------------------
// DirectionPassport — top-level orchestrator for /secrets [bu-qu8v8]
//
// Spec: butler-secrets §Passport-Book Information Architecture
//       §Deep-Link Focus Routing  (?focus=u:<p>|s:<K>|c:<id>)
//       §Projection-Lens Identity Switcher  (?identity=<id>)
//
// URL state:
//   ?focus=<key>    — open credential detail
//   ?identity=<id>  — project User group to this identity
//   ?sort=<mode>    — default sort
//
// No LLM calls. All text is stored-prose, templated, verbatim, or literal.
// ---------------------------------------------------------------------------

import * as React from "react";
import { useSearchParams } from "react-router";

import type { InventoryResponse, SpineSortMode } from "./types.ts";
import { parseFocus } from "./constants.ts";
import { buildSpineEntries, pickDefaultKey } from "./spine-builder.ts";
import { Spine, SpineAddButton } from "./Spine.tsx";
import { PageUser, PageSystem, PageCliConnected, PassportEmptyState, PassportAddPanel } from "./pages.tsx";
import { Eyebrow, Mono, Voice, IdentityChip } from "./atoms.tsx";
import { needsHand } from "./constants.ts";

// ── KPI cell ─────────────────────────────────────────────────────────────────

function KpiCell({
  label,
  value,
  caption,
  captionTone = "dim",
}: {
  label: string;
  value: string;
  caption: string;
  captionTone?: "dim" | "amber" | "red";
}) {
  const captionColor =
    captionTone === "amber"
      ? "var(--amber)"
      : captionTone === "red"
        ? "var(--red)"
        : "var(--dim)";
  return (
    <div className="flex flex-col gap-0.5 items-end min-w-24">
      <Mono size={9} upper tracking="0.14em" color="var(--dim)">
        {label}
      </Mono>
      <span
        className="tabular-nums"
        style={{
          fontFamily: "var(--font-sans, 'Inter Tight', sans-serif)",
          fontSize: 22,
          fontWeight: 500,
          letterSpacing: "-0.02em",
          color: "var(--fg)",
        }}
      >
        {value}
      </span>
      <Mono size={9} color={captionColor}>
        {caption}
      </Mono>
    </div>
  );
}

function KpiSep() {
  return (
    <span
      aria-hidden="true"
      className="self-center"
      style={{ width: 1, height: 36, background: "var(--border)" }}
    />
  );
}

// ── DirectionPassport ─────────────────────────────────────────────────────────

/**
 * DirectionPassport — renders the full /secrets passport book:
 * page header + spine + page body.
 *
 * Receives inventory as a prop. Data fetching is handled by the parent
 * (SecretsPage) via useSecretsInventory, which calls GET /api/secrets/inventory.
 */
export function DirectionPassport({
  inventory,
}: {
  inventory: InventoryResponse;
}) {
  const [searchParams, setSearchParams] = useSearchParams();

  // ── URL state ───────────────────────────────────────────────────────────
  const focusParam = searchParams.get("focus");
  const identityParam = searchParams.get("identity");
  const sortParam = searchParams.get("sort") as SpineSortMode | null;

  // Active identity: URL param or first identity (owner).
  const defaultIdentity = inventory.identities[0]?.id ?? "";
  const identityId = identityParam ?? defaultIdentity;

  // In the owner-default view (?identity= absent), the backend already gated
  // the response to owner-relevant companion entities (e.g. primary Google
  // account).  Surface ALL returned identities' credentials so the Google spine
  // entry is reachable without a manual ?identity= switch [bu-3gekd].
  //
  // When an explicit identity is selected via the chip switcher, scope to just
  // that identity — preserving the per-member projection-lens contract.
  const spineIdentityIds: string | string[] = React.useMemo(
    () =>
      identityParam === null
        ? inventory.identities.map((i) => i.id)
        : identityId,
    [identityParam, identityId, inventory.identities],
  );

  // Spine entries for current identity (or all owner-default identities).
  const entries = React.useMemo(
    () => buildSpineEntries(inventory, spineIdentityIds),
    [inventory, spineIdentityIds],
  );

  // Focus key: derived from URL param — URL is the single source of truth.
  //
  // Legacy deep links addressed CLI runtime auth tokens with the system prefix
  // (e.g. `s:cli-auth/codex`); the spine now keys them under the CLI family
  // (`c:cli-auth/codex`). Canonicalize the stale `s:` form to the matching CLI
  // entry so those bookmarks still land on the runtime page.
  const activeKey = React.useMemo(() => {
    if (focusParam) {
      if (entries.some((e) => e.key === focusParam)) return focusParam;
      const parsedFocus = parseFocus(focusParam);
      if (parsedFocus?.family === "s") {
        const cliKey = `c:${parsedFocus.id}`;
        if (entries.some((e) => e.key === cliKey)) return cliKey;
      }
    }
    return pickDefaultKey(entries);
  }, [focusParam, entries]);

  const sortMode: SpineSortMode = sortParam ?? "severity";

  // ── Search ──────────────────────────────────────────────────────────────
  const [search, setSearch] = React.useState("");

  // ── Add panel ────────────────────────────────────────────────────────────
  const [addOpen, setAddOpen] = React.useState(false);

  // ── URL writers ─────────────────────────────────────────────────────────
  function handleSelectKey(key: string) {
    const params = new URLSearchParams(searchParams);
    params.set("focus", key);
    setSearchParams(params, { replace: true });
  }

  function handleIdentityChange(id: string) {
    const params = new URLSearchParams(searchParams);
    if (id === defaultIdentity) {
      params.delete("identity");
    } else {
      params.set("identity", id);
    }
    setSearchParams(params, { replace: true });
  }

  function handleSortChange(m: SpineSortMode) {
    const params = new URLSearchParams(searchParams);
    params.set("sort", m);
    setSearchParams(params, { replace: true });
  }

  // ── Resolved page ───────────────────────────────────────────────────────
  const parsed = parseFocus(activeKey);

  type ResolvedPage =
    | { kind: "user"; credential: NonNullable<(typeof inventory.user)[number]> }
    | { kind: "system"; credential: NonNullable<(typeof inventory.system)[number]> }
    | { kind: "cli"; credential: NonNullable<(typeof inventory.cli)[number]> }
    | { kind: null };

  const resolved = React.useMemo((): ResolvedPage => {
    if (!parsed) return { kind: null };
    if (parsed.family === "u") {
      // In the owner-default view, spineIdentityIds is an array of all returned
      // identities (owner + companion entities). The credential lookup must
      // search across all of them, not just the single ownerIdentityId.
      const spineIdSet = new Set(
        Array.isArray(spineIdentityIds) ? spineIdentityIds : [spineIdentityIds],
      );
      const record = inventory.user.find(
        (s) => s.provider === parsed.id && spineIdSet.has(s.identity),
      );
      return record ? { kind: "user", credential: record } : { kind: null };
    }
    if (parsed.family === "s") {
      const record = inventory.system.find((s) => s.key === parsed.id);
      return record ? { kind: "system", credential: record } : { kind: null };
    }
    if (parsed.family === "c") {
      const record = inventory.cli.find((r) => r.id === parsed.id);
      return record ? { kind: "cli", credential: record } : { kind: null };
    }
    return { kind: null };
  }, [parsed, spineIdentityIds, inventory]);

  // ── KPIs ─────────────────────────────────────────────────────────────────
  const spineIdSet = new Set(
    Array.isArray(spineIdentityIds) ? spineIdentityIds : [spineIdentityIds],
  );
  const userForIdentity = inventory.user.filter((s) => spineIdSet.has(s.identity));
  const kpis = {
    integrations: {
      total:    userForIdentity.length,
      healthy:  userForIdentity.filter((x) => x.state === "ok").length,
      needsHand:userForIdentity.filter((x) => needsHand(x.state)).length,
    },
    system: {
      total:      inventory.system.length,
      configured: inventory.system.filter((x) => x.rowState !== "missing").length,
      missing:    inventory.system.filter((x) => x.rowState === "missing").length,
    },
    cli: {
      total:     inventory.cli.length,
      ok:        inventory.cli.filter((x) => x.state === "ok").length,
      attention: inventory.cli.filter((x) => needsHand(x.state)).length,
    },
  };
  const needsAttention = kpis.integrations.needsHand + kpis.cli.attention;

  // Hide identity chip when only one identity is present.
  const showIdentityChip = inventory.identities.length > 1;
  const activeIdentity = inventory.identities.find((i) => i.id === identityId);

  return (
    <div
      className="flex min-h-full"
      style={{ background: "var(--bg)", color: "var(--fg)" }}
      data-direction-passport="true"
    >
      <div className="flex-1 min-w-0 flex flex-col">
        {/* Page header */}
        <div
          className="flex justify-between items-end gap-6 px-9 pt-7 pb-4.5"
          style={{ borderBottom: "1px solid var(--border)" }}
        >
          <div className="min-w-0">
            <Eyebrow sub={new Date().toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" })}>
              secrets
            </Eyebrow>
            <div className="mt-2.5">
              <h1
                className="m-0"
                style={{
                  fontFamily: "var(--font-sans, 'Inter Tight', sans-serif)",
                  fontSize: 32,
                  fontWeight: 500,
                  letterSpacing: "-0.025em",
                  lineHeight: 1.08,
                  maxWidth: "28ch",
                }}
              >
                {needsAttention === 0
                  ? "Every credential, accounted for."
                  : needsAttention === 1
                    ? "One credential needs attention."
                    : `${needsAttention} credentials need attention.`}
              </h1>
            </div>
            {needsAttention > 0 && (
              <div className="mt-2.5">
                <Voice maxWidth="60ch">
                  {kpis.integrations.needsHand > 0 && (
                    <>
                      {kpis.integrations.needsHand} integration
                      {kpis.integrations.needsHand === 1 ? "" : "s"} sick.{" "}
                    </>
                  )}
                  {kpis.cli.attention > 0 && (
                    <>
                      {kpis.cli.attention} runtime token expiring.{" "}
                    </>
                  )}
                  Everything else verified within the hour.
                </Voice>
              </div>
            )}
          </div>

          <div className="flex flex-col items-end gap-3">
            <div className="flex items-center gap-2">
              {showIdentityChip && activeIdentity && (
                <IdentityChip
                  id={activeIdentity.id}
                  label={activeIdentity.label}
                  role={activeIdentity.role}
                  hue={activeIdentity.hue}
                  compact
                  onClick={() => {/* handled via spine */}}
                />
              )}
              <SpineAddButton onClick={() => setAddOpen(true)} active={addOpen} />
            </div>
            <div className="flex gap-3.5 items-baseline">
              <KpiCell
                label="integrations"
                value={`${kpis.integrations.healthy}/${kpis.integrations.total}`}
                caption={`${kpis.integrations.needsHand} need hand`}
                captionTone={kpis.integrations.needsHand > 0 ? "amber" : "dim"}
              />
              <KpiSep />
              <KpiCell
                label="system"
                value={`${kpis.system.configured}/${kpis.system.total}`}
                caption={`${kpis.system.missing} unset`}
              />
              <KpiSep />
              <KpiCell
                label="cli"
                value={`${kpis.cli.ok}/${kpis.cli.total}`}
                caption={kpis.cli.attention > 0 ? `${kpis.cli.attention} expiring` : "all ok"}
                captionTone={kpis.cli.attention > 0 ? "amber" : "dim"}
              />
            </div>
          </div>
        </div>

        {/* Book body: spine + page */}
        <div
          className="grid flex-1 min-h-0"
          style={{ gridTemplateColumns: "296px 1fr" }}
        >
          <Spine
            entries={entries}
            activeKey={activeKey}
            onSelect={handleSelectKey}
            sortMode={sortMode}
            onSortChange={handleSortChange}
            search={search}
            onSearchChange={setSearch}
            identities={inventory.identities}
            activeIdentityId={identityId}
            onIdentityChange={handleIdentityChange}
            providers={inventory.providers}
          />

          <div className="overflow-y-auto min-w-0">
            {addOpen ? (
              <PassportAddPanel
                ownerEntityId={inventory.ownerEntityId}
                onClose={() => setAddOpen(false)}
                onSystemCreated={(key) => {
                  setAddOpen(false);
                  // Navigate to the newly created system credential
                  handleSelectKey(`s:${key}`);
                }}
              />
            ) : (
              <>
                {resolved.kind === "user" && (
                  <PageUser
                    credential={resolved.credential}
                    provider={inventory.providers[resolved.credential.provider]!}
                    identities={inventory.identities}
                  />
                )}
                {resolved.kind === "system" && (
                  <PageSystem
                    credential={resolved.credential}
                  />
                )}
                {resolved.kind === "cli" && (
                  <PageCliConnected
                    credential={resolved.credential}
                  />
                )}
                {resolved.kind === null && <PassportEmptyState />}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// Re-export types needed by the page route and tests.
export type { InventoryResponse } from "./types.ts";

// Default export for the page route integration.
export default DirectionPassport;
