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

      {/* State plaque — rotated stamp */}
      <div
        className="flex flex-col gap-0.5 items-end shrink-0 p-2"
        style={{
          border: `1.5px solid ${stateColor}`,
          color: stateColor,
          transform: "rotate(1.5deg)",
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
              right={credential.test ? (credential.test.ok ? "ok" : "failed") : "never"}
            />
            <div className="mt-2.5 pt-2" style={{ borderTop: "1px solid var(--border)" }}>
              <ProbeResult test={credential.test} />
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

      {/* Footer */}
      <CommitFooter
        left={
          <>
            {(credential.state === "expired" || credential.state === "revoked" || credential.state === "scope_mismatch") && (
              <PillBtn variant="commit">re-authorize</PillBtn>
            )}
            {credential.state === "expiring" && <PillBtn variant="commit">rotate</PillBtn>}
            {isMissing && <PillBtn variant="commit">connect</PillBtn>}
            {!isMissing && <PillBtn>test</PillBtn>}
            {!isMissing && !sick && <PillBtn>rotate</PillBtn>}
          </>
        }
        right={
          <>
            {credential.fingerprint && <PillBtn>reveal value</PillBtn>}
            {!isMissing && <PillBtn variant="danger">disconnect</PillBtn>}
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
 * Override modal triggered from footer.
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
  const stateColor = isMissing ? "var(--dim)" : "var(--green)";
  const stateLabel = isMissing ? "not set" : isLocal ? "local override" : "shared default";
  const stateLines: string[] = [];
  if (isLocal) stateLines.push(`target · ${credential.target}`);
  else if (!isMissing && credential.lastVerified) stateLines.push(`verified ${credential.lastVerified}`);

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
              right={credential.test ? (credential.test.ok ? "ok" : "failed") : "never"}
            />
            <div className="mt-2.5 pt-2" style={{ borderTop: "1px solid var(--border)" }}>
              <ProbeResult test={credential.test} />
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

      {/* Footer */}
      <CommitFooter
        left={
          isMissing ? (
            <PillBtn variant="commit">set value</PillBtn>
          ) : (
            <>
              <PillBtn>test</PillBtn>
              <PillBtn>rotate</PillBtn>
              {!isLocal && <PillBtn>override · per butler</PillBtn>}
            </>
          )
        }
        right={
          <>
            {credential.fingerprint && !isPlain && <PillBtn>reveal value</PillBtn>}
            {!isMissing && <PillBtn variant="danger">delete</PillBtn>}
          </>
        }
      />
    </div>
  );
}

// ── PageCli ──────────────────────────────────────────────────────────────────

/**
 * PageCli — CLI runtime credential page.
 *
 * Supports rotate-with-reveal flow: rotate returns the raw value once.
 * How-to-use snippet rendered as static literal (no LLM).
 */
export function PageCli({
  credential,
  showVerifyCmd = false,
  revealMode = "eye",
}: {
  credential: CliCredential;
  showVerifyCmd?: boolean;
  /** Controls the reveal-token eye button. "never" hides it. */
  revealMode?: RevealMode;
}) {
  const meta = STATE_CATALOG[credential.state] ?? STATE_CATALOG.never_set;
  const color = toneColor(meta.tone);
  const isMissing = credential.state === "never_set";
  const stateLines: string[] = [];
  if (credential.state === "expiring" && credential.expires) {
    stateLines.push(`expires ${credential.expires}`);
  }
  if (credential.state === "ok" && credential.lastUsed) {
    stateLines.push(`used ${credential.lastUsed}`);
  }
  if (isMissing) stateLines.push("paste a token to enable");

  const envVar = credential.id.toUpperCase().replace(/-/g, "_") + "_TOKEN";

  const allScopes = Array.from(
    new Set([...credential.scopesGranted, ...credential.scopesRequired]),
  );
  const grantedSet = new Set(credential.scopesGranted);
  const requiredSet = new Set(credential.scopesRequired);

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
              right={credential.test ? (credential.test.ok ? "ok" : "failed") : "never"}
            />
            <div className="mt-2.5 pt-2" style={{ borderTop: "1px solid var(--border)" }}>
              <ProbeResult test={credential.test} />
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

      {/* Footer — rotate-with-reveal flow */}
      <CommitFooter
        left={
          isMissing ? (
            <PillBtn variant="commit">set token</PillBtn>
          ) : (
            <>
              <PillBtn variant={credential.state === "expiring" ? "commit" : "pill"}>
                rotate
              </PillBtn>
              <PillBtn>test</PillBtn>
            </>
          )
        }
        right={
          <>
            {credential.fingerprint && revealMode !== "never" && <PillBtn>reveal token</PillBtn>}
            {!isMissing && <PillBtn variant="danger">revoke</PillBtn>}
          </>
        }
      />
    </div>
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
