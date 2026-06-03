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
  RevealMode,
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
  useRevealSystemSecret,
  useRotateCliRuntime,
  useRevokeCliRuntime,
} from "@/hooks/use-secrets-mutations.ts";
import { useButlers } from "@/hooks/use-butlers";

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

  async function handleReauthorize() {
    if (reauthPending) return;
    setReauthPending(true);
    setReauthError(null);
    try {
      const resp = await reauthorizeUserCredential(credential.provider, credential.identity);
      // Follow the returned redirect_url to begin the OAuth dance.
      if (!resp?.data?.redirect_url) {
        throw new Error("No redirect URL returned from the server.");
      }
      window.location.href = resp.data.redirect_url;
    } catch (err) {
      setReauthError(err instanceof Error ? err.message : "Reauthorization failed.");
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
 *   reveal value        — useRevealSystemSecret; honors revealMode prop
 *   delete              — danger confirm inline panel → useDeleteSystemSecret (correct target)
 */
export function PageSystem({
  credential,
  showVerifyCmd = false,
  voiceParagraph = true,
  revealMode = "eye",
}: {
  credential: SystemCredential;
  showVerifyCmd?: boolean;
  voiceParagraph?: boolean;
  /** Controls the reveal-value eye button. "never" hides it. */
  revealMode?: RevealMode;
}) {
  const isMissing = credential.rowState === "missing";
  const isLocal = credential.rowState === "local";
  const isPlain = !!credential.plainValue;
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
    setMutation.mutate(
      { key: credential.key, body: { value: setValue.trim(), target: "shared" } },
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

  // ── Reveal value ───────────────────────────────────────────────────────────
  // System secrets CAN be revealed (unlike user secrets).
  // Plain-value credentials skip the eye button — the value is already shown.
  const revealMutation = useRevealSystemSecret();
  const [revealedValue, setRevealedValue] = React.useState<string | null>(null);

  function handleReveal() {
    if (revealMutation.isPending) return;
    const butler = credential.target || "shared";
    revealMutation.mutate(
      { butler, key: credential.key },
      {
        onSuccess: (data) => {
          const val = (data as { data?: { value?: string } })?.data?.value ?? null;
          setRevealedValue(val);
        },
      },
    );
  }

  // ── Delete ─────────────────────────────────────────────────────────────────
  // Passes the correct ?target= depending on whether this is a shared or local row.
  const [deleteConfirm, setDeleteConfirm] = React.useState(false);
  const deleteMutation = useDeleteSystemSecret();

  // The delete target: shared rows delete the shared row; local overrides delete
  // the butler-specific row (use credential.target which holds the butler name).
  const deleteTarget = isLocal ? credential.target : "shared";

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

      {/* Revealed value display */}
      {revealedValue !== null && (
        <div
          className="flex flex-col gap-2 p-3.5"
          style={{ border: "1px solid var(--border-soft)", background: "var(--bg-elev)" }}
          data-revealed-value="true"
        >
          <Mono size={9} upper tracking="0.14em" color="var(--dim)">revealed value</Mono>
          <Mono size={12}>{revealedValue}</Mono>
          <PillBtn onClick={() => setRevealedValue(null)}>dismiss</PillBtn>
        </div>
      )}

      {/* Rate-limit hint for probe */}
      {probeRateLimited && (
        <Mono size={11} color="var(--dim)" className="mt-1" data-probe-rate-limited="true">
          try again in a moment
        </Mono>
      )}

      {/* Footer */}
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
              <PillBtn
                onClick={handleProbe}
                disabled={probeMutation.isPending}
              >
                {probeMutation.isPending ? "testing…" : "test"}
              </PillBtn>
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
            {credential.fingerprint && !isPlain && revealMode !== "never" && (
              <PillBtn
                onClick={handleReveal}
                disabled={revealMutation.isPending || revealedValue !== null}
              >
                {revealMutation.isPending ? "revealing…" : "reveal value"}
              </PillBtn>
            )}
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
 *   reveal token   — useRevealSystemSecret (switchboard pool); honors revealMode
 *   test           — useTestCLIAuthApiKey (works for all auth modes via /cli-auth/{p}/test)
 *   set token      — value-entry panel → useRotateCliRuntime (token-mode) OR
 *                    useSaveCLIAuthApiKey (api-key mode, e.g. Claude)
 *   api-key save   — useSaveCLIAuthApiKey; api-key mode providers can paste key
 *   api-key delete — useDeleteCLIAuthApiKey
 *
 * Device-code re-auth: wired to existing deviceAuth.start/cancel flow.
 * C10 (bu-ayp6v.10) bridge: once built, connect via the handleReauthorize
 * integration point below (search for C10-BRIDGE).
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
  revealMode = "eye",
  deviceAuth,
}: {
  credential: CliCredential;
  showVerifyCmd?: boolean;
  /** Controls the reveal-token eye button. "never" hides it. */
  revealMode?: RevealMode;
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

  // ── Reveal token ──────────────────────────────────────────────────────────
  // CLI tokens are stored in the shared butler_secrets pool (switchboard schema).
  // revealSecret("switchboard", key) is the read path; display once until dismissed.
  const revealMutation = useRevealSystemSecret();
  const [revealedToken, setRevealedToken] = React.useState<string | null>(null);

  function handleReveal() {
    if (revealMutation.isPending) return;
    revealMutation.mutate(
      { butler: "switchboard", key: credential.id },
      {
        onSuccess: (data) => {
          const val = (data as { data?: { value?: string } })?.data?.value ?? null;
          setRevealedToken(val);
        },
      },
    );
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
  // For providers WITHOUT api_key mode, "set token" pastes the raw value
  // using the rotate endpoint (which also persists for first-time set).
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

  // For token-mode "set token": currently the rotate endpoint generates a new
  // random value server-side (no paste). A future endpoint may accept a
  // user-supplied value. For now, clicking "set token" opens the rotate flow
  // OR the api-key save flow depending on provider mode.
  // For api_key mode (e.g. Claude): paste the key → useSaveCLIAuthApiKey.
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
              onClick={isApiKeyMode ? handleSaveApiKey : handleRotate}
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
            new token — copy now, won't be shown again
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

      {/* Revealed token display */}
      {revealedToken !== null && (
        <div
          className="flex flex-col gap-2 p-3.5"
          style={{ border: "1px solid var(--border-soft)", background: "var(--bg-elev)" }}
          data-revealed-token="true"
        >
          <Mono size={9} upper tracking="0.14em" color="var(--dim)">revealed token</Mono>
          <Mono size={12}>{revealedToken}</Mono>
          <PillBtn onClick={() => setRevealedToken(null)}>dismiss</PillBtn>
        </div>
      )}

      {/* Footer — device-code flow when supported, else rotate-with-reveal */}
      <CommitFooter
        left={
          deviceAuth?.supported ? (
            deviceAuth.inProgress ? (
              <PillBtn onClick={deviceAuth.cancel}>cancel</PillBtn>
            ) : (
              <>
                {/* C10-BRIDGE: once bu-ayp6v.10 is built, the re-authorize button
                    can be wired to the C10 reauth bridge. Until then, existing
                    deviceAuth.start covers both initial connect and re-auth. */}
                <PillBtn
                  variant={isMissing || sick ? "commit" : "pill"}
                  onClick={deviceAuth.start}
                  disabled={deviceAuth.starting}
                >
                  {deviceAuth.starting
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
            {credential.fingerprint && revealMode !== "never" && (
              <PillBtn
                onClick={handleReveal}
                disabled={revealMutation.isPending || revealedToken !== null}
              >
                {revealMutation.isPending ? "revealing…" : "reveal token"}
              </PillBtn>
            )}
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
  revealMode = "eye",
}: {
  credential: CliCredential;
  showVerifyCmd?: boolean;
  revealMode?: RevealMode;
}) {
  const deviceAuth = useCliDeviceAuth(credential.id);
  return (
    <PageCli
      credential={credential}
      showVerifyCmd={showVerifyCmd}
      revealMode={revealMode}
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
