// ---------------------------------------------------------------------------
// Passport atoms — typographic + state primitives for /secrets [bu-qu8v8]
//
// Typography: Inter Tight (sans), Source Serif 4 (serif), JetBrains Mono.
// Design language: DESIGN_LANGUAGE.md §1-4.
// ---------------------------------------------------------------------------

import * as React from "react";

import { cn } from "@/lib/utils";
import type { CredentialState } from "./types.ts";
import { STATE_CATALOG, STAMP_GLYPHS, SEVERITY_META } from "./constants.ts";

// ── Token helpers ──────────────────────────────────────────────────────────

/** Map a state tone to a CSS color token. */
// eslint-disable-next-line react-refresh/only-export-components
export function toneColor(tone: string): string {
  switch (tone) {
    case "ok":    return "var(--green, oklch(0.790 0.195 148))";
    case "amber": return "var(--amber, oklch(0.810 0.185 84))";
    case "red":   return "var(--red, oklch(0.685 0.250 29))";
    case "dim":   return "var(--mfg, oklch(0.55 0 0))";
    default:      return "var(--fg, oklch(0.985 0 0))";
  }
}

/** Color for a credential state. */
// eslint-disable-next-line react-refresh/only-export-components
export function stateColor(state: CredentialState): string {
  const meta = STATE_CATALOG[state];
  if (!meta) return "var(--mfg)";
  return toneColor(meta.tone);
}

// ── Typography ─────────────────────────────────────────────────────────────

/** Mono eyebrow: 10px / uppercase / 0.14em tracking / muted. */
export function Eyebrow({
  children,
  sub,
  className,
}: {
  children: React.ReactNode;
  sub?: string;
  className?: string;
}) {
  return (
    <div className={cn("flex items-baseline gap-2.5", className)}>
      <span
        className="font-mono text-[10px] uppercase tracking-[0.14em]"
        style={{ color: "var(--mfg, oklch(0.708 0 0))" }}
      >
        {children}
      </span>
      {sub && (
        <span
          className="font-mono text-[10px]"
          style={{ color: "var(--dim, oklch(0.55 0 0))" }}
        >
          {sub}
        </span>
      )}
    </div>
  );
}

/** Mono inline: 11px default, tabular numerals. */
export function Mono({
  children,
  size = 11,
  color,
  upper = false,
  tracking = "normal",
  weight = 400,
  className,
  "data-testid": testId,
}: {
  children: React.ReactNode;
  size?: number;
  color?: string;
  upper?: boolean;
  tracking?: string;
  weight?: number;
  className?: string;
  "data-testid"?: string;
}) {
  return (
    <span
      className={cn("font-mono tabular-nums", className)}
      style={{
        fontSize: size,
        color: color ?? "var(--fg, oklch(0.985 0 0))",
        textTransform: upper ? "uppercase" : "none",
        letterSpacing: tracking,
        fontWeight: weight,
      }}
      data-testid={testId}
    >
      {children}
    </span>
  );
}

/** Source Serif 4 voice paragraph. */
export function Voice({
  children,
  italic = false,
  size = 16,
  color,
  maxWidth,
  className,
}: {
  children: React.ReactNode;
  italic?: boolean;
  size?: number;
  color?: string;
  maxWidth?: string;
  className?: string;
}) {
  return (
    <p
      className={cn("m-0", className)}
      style={{
        fontFamily: "var(--font-serif, 'Source Serif 4', serif)",
        fontSize: size,
        lineHeight: 1.55,
        fontStyle: italic ? "italic" : "normal",
        color: color ?? "var(--mfg, oklch(0.708 0 0))",
        maxWidth,
      }}
    >
      {children}
    </p>
  );
}

/** Display headline: 44px / sans 500 / tight tracking. */
export function Display({
  children,
  color,
  size = 44,
  maxWidth = "14ch",
  className,
}: {
  children: React.ReactNode;
  color?: string;
  size?: number;
  maxWidth?: string;
  className?: string;
}) {
  return (
    <h1
      className={cn("m-0", className)}
      style={{
        fontFamily: "var(--font-sans, 'Inter Tight', sans-serif)",
        fontSize: size,
        fontWeight: 500,
        letterSpacing: "-0.025em",
        lineHeight: 1.08,
        color: color ?? "var(--fg, oklch(0.985 0 0))",
        maxWidth,
        textWrap: "pretty",
      }}
    >
      {children}
    </h1>
  );
}

// ── State atoms ─────────────────────────────────────────────────────────────

/**
 * 6px state dot for credential state.
 * On a calm day (all ok), renders in dim color.
 */
export function CredentialDot({
  state,
  size = 6,
  className,
}: {
  state: CredentialState;
  size?: number;
  className?: string;
}) {
  const color = stateColor(state);
  const label = STATE_CATALOG[state]?.label ?? state;
  return (
    <span
      role="img"
      aria-label={label}
      data-credential-state={state}
      className={cn("inline-block shrink-0 rounded-full", className)}
      style={{ width: size, height: size, backgroundColor: color }}
    />
  );
}

/**
 * 2px vertical left-edge sliver: visible only when state demands.
 * Per spec §Severity Earns Visual Authority Only When State Demands.
 */
export function Sliver({
  state,
  className,
}: {
  state: CredentialState;
  className?: string;
}) {
  const meta = STATE_CATALOG[state];
  if (!meta?.sliver) return null;
  const color = toneColor(meta.tone);
  return (
    <span
      aria-hidden="true"
      data-sliver="true"
      className={cn("absolute inset-y-0 left-0 w-0.5", className)}
      style={{ backgroundColor: color }}
    />
  );
}

/** Mono lowercase state label (one of dot/sliver/numeral/colour). */
export function StateLabel({
  state,
  className,
}: {
  state: CredentialState;
  className?: string;
}) {
  const meta = STATE_CATALOG[state];
  const color = meta ? toneColor(meta.tone) : "var(--mfg)";
  return (
    <Mono
      size={10}
      color={color}
      upper
      tracking="0.10em"
      className={className}
      data-state-label={state}
    >
      {meta?.label ?? state}
    </Mono>
  );
}

// ── Provider mark ────────────────────────────────────────────────────────────

/**
 * Mono square letter-mark for external providers.
 * No color — providers are not butlers. Mono initial, hairline border.
 */
export function ProviderMark({
  glyph,
  label,
  size = 22,
  className,
}: {
  glyph: string;
  label: string;
  size?: number;
  className?: string;
}) {
  return (
    <span
      aria-label={label}
      data-provider-mark="true"
      className={cn(
        "inline-flex items-center justify-center shrink-0 font-mono font-medium",
        className,
      )}
      style={{
        width: size,
        height: size,
        borderRadius: 3,
        border: "1px solid var(--border-strong, oklch(1 0 0 / 0.18))",
        background: "transparent",
        color: "var(--fg, oklch(0.985 0 0))",
        fontSize: Math.round(size * 0.5),
      }}
    >
      {glyph}
    </span>
  );
}

// ── Identity chip ────────────────────────────────────────────────────────────

/** Compact identity chip: name + role + colour dot. */
export function IdentityChip({
  id,
  label,
  role,
  hue,
  compact = false,
  active = false,
  onClick,
  className,
}: {
  id: string;
  label: string;
  role: string;
  hue?: string;
  compact?: boolean;
  active?: boolean;
  onClick?: () => void;
  className?: string;
}) {
  const chipClass = cn(
    "inline-flex items-center gap-2",
    compact ? "px-2 py-1" : "px-2.5 py-1",
    "border rounded-sm bg-transparent transition-colors",
    active
      ? "border-[var(--border-strong)] bg-[var(--bg-elev)]"
      : "border-[var(--border-soft)] hover:border-[var(--border)]",
    onClick ? "cursor-pointer" : "cursor-default",
    className,
  );
  const chipContent = (
    <>
      <span
        aria-hidden="true"
        className="w-2 h-2 rounded-full shrink-0"
        style={{ backgroundColor: hue ?? "var(--fg)" }}
      />
      <span
        className="font-sans text-[var(--fg)] font-medium"
        style={{ fontSize: compact ? 12 : 13, letterSpacing: "-0.005em" }}
      >
        {label}
      </span>
      <Mono size={9} upper tracking="0.12em" color="var(--dim)">
        {role}
      </Mono>
      {onClick && (
        <Mono size={11} color="var(--mfg)">
          ▾
        </Mono>
      )}
    </>
  );
  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        data-identity-id={id}
        data-active={active}
        className={chipClass}
      >
        {chipContent}
      </button>
    );
  }
  return (
    <div data-identity-id={id} data-active={active} className={chipClass}>
      {chipContent}
    </div>
  );
}

// ── Fingerprint ───────────────────────────────────────────────────────────────

/**
 * Fingerprint display: scheme:hash with split coloring.
 * Per spec: `sha256:7a3f…` — scheme in mfg, hash in fg.
 */
export function Fingerprint({
  value,
  size = 11,
  dim = false,
  className,
}: {
  value: string | null;
  size?: number;
  dim?: boolean;
  className?: string;
}) {
  if (!value) {
    return (
      <Mono size={size} color="var(--dim)" className={className}>
        —
      </Mono>
    );
  }
  const colonIdx = value.indexOf(":");
  const scheme = colonIdx !== -1 ? value.slice(0, colonIdx + 1) : "";
  const hash = colonIdx !== -1 ? value.slice(colonIdx + 1) : value;
  return (
    <span
      className={cn("font-mono tabular-nums", className)}
      style={{ fontSize: size, letterSpacing: "0.01em" }}
      data-fingerprint="true"
    >
      <span style={{ color: dim ? "var(--dim)" : "var(--mfg)" }}>{scheme}</span>
      <span style={{ color: dim ? "var(--mfg)" : "var(--fg)" }}>{hash}</span>
    </span>
  );
}

/**
 * FingerprintRow: hash + optional verify-cmd expander.
 * Per spec: verify cmd is always `echo -n '<value>' | sha256sum | cut -c1-8`.
 */
export function FingerprintRow({
  value,
  size = 13,
  showVerifyCmd = false,
  className,
}: {
  value: string | null;
  size?: number;
  showVerifyCmd?: boolean;
  className?: string;
}) {
  const [open, setOpen] = React.useState(false);
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <Fingerprint value={value} size={size} />
      {value && showVerifyCmd && (
        <div>
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            className="font-mono text-[9px] uppercase tracking-[0.10em] bg-transparent border-none cursor-pointer p-0"
            style={{ color: "var(--dim)" }}
          >
            {open ? "− hide verify cmd" : "+ verify cmd"}
          </button>
          {open && (
            <div className="mt-1">
              <Mono size={10} color="var(--mfg)">
                {"echo -n '<value>' | sha256sum | cut -c1-8"}
              </Mono>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Stamp glyph ────────────────────────────────────────────────────────────

/**
 * StampGlyph: 1-char mono shape per audit action.
 * Actions: verified/rotated/failed/revoked/connected/disconnected/warned/overrode/attempted/set
 */
export function StampGlyph({
  action,
  size = 14,
  className,
}: {
  action: string;
  size?: number;
  className?: string;
}) {
  const meta = STAMP_GLYPHS[action] ?? { glyph: "·", tone: "dim" };
  const color = toneColor(meta.tone);
  return (
    <span
      data-stamp-action={action}
      className={cn("inline-flex items-center justify-center font-mono", className)}
      style={{
        width: size + 4,
        height: size + 4,
        borderRadius: 2,
        border: `1px solid ${color}`,
        color,
        fontSize: Math.round(size * 0.85),
        lineHeight: 1,
        flexShrink: 0,
      }}
    >
      {meta.glyph}
    </span>
  );
}

// ── Stamp row ───────────────────────────────────────────────────────────────

/** StampRow: glyph + date/time + action + actor + serif note. */
export function StampRow({
  event,
  last = false,
}: {
  event: { ts: string; actor: string; action: string; note: string };
  last?: boolean;
}) {
  const spaceIdx = event.ts.indexOf(" ");
  const date = spaceIdx !== -1 ? event.ts.slice(0, spaceIdx) : event.ts;
  const time = spaceIdx !== -1 ? event.ts.slice(spaceIdx + 1) : "";
  return (
    <div
      className={cn(
        "grid gap-3 py-2 items-start",
        !last && "border-b border-[var(--border-soft)]",
      )}
      style={{ gridTemplateColumns: "22px 76px 1fr" }}
      data-stamp-row="true"
    >
      <div className="pt-0.5">
        <StampGlyph action={event.action} size={12} />
      </div>
      <div>
        <Mono size={10} color="var(--fg)">
          {date}
        </Mono>
        <div>
          <Mono size={9} color="var(--dim)">
            {time}
          </Mono>
        </div>
      </div>
      <div className="flex flex-col gap-0.5">
        <div className="flex items-baseline gap-1.5">
          <Mono size={10} upper tracking="0.10em" color="var(--fg)">
            {event.action}
          </Mono>
          <Mono size={9} color="var(--dim)">
            · {event.actor}
          </Mono>
        </div>
        <span
          style={{
            fontFamily: "var(--font-serif, 'Source Serif 4', serif)",
            fontSize: 12,
            color: "var(--mfg)",
            lineHeight: 1.4,
          }}
        >
          {event.note}
        </span>
      </div>
    </div>
  );
}

// ── Severity pip ─────────────────────────────────────────────────────────────

/** SeverityPip: 1-char mono pip for WhatBreaks rows. */
export function SeverityPip({
  severity,
  className,
}: {
  severity: "high" | "medium" | "low";
  className?: string;
}) {
  const meta = SEVERITY_META[severity] ?? SEVERITY_META.low;
  const color = toneColor(meta.tone);
  return (
    <Mono size={11} color={color} className={className}>
      {meta.glyph}
    </Mono>
  );
}

// ── BlockHead ───────────────────────────────────────────────────────────────

/** BlockHead: mono eyebrow with optional right caption. */
export function BlockHead({
  eyebrow,
  right,
  className,
}: {
  eyebrow: string;
  right?: string;
  className?: string;
}) {
  return (
    <div className={cn("flex items-baseline justify-between", className)}>
      <Mono size={10} upper tracking="0.14em" color="var(--dim)">
        {eyebrow}
      </Mono>
      {right && (
        <Mono size={9} color="var(--dim)">
          {right}
        </Mono>
      )}
    </div>
  );
}

// ── Scope atoms ───────────────────────────────────────────────────────────────

/** VisaRow: single scope with granted/missing/extra state. */
export function VisaRow({
  scope,
  state,
}: {
  scope: string;
  state: "granted" | "missing" | "extra";
}) {
  const color =
    state === "missing"
      ? "var(--amber)"
      : state === "granted"
        ? "var(--fg)"
        : "var(--dim)";
  return (
    <div
      className="grid gap-2.5 items-baseline py-1.5 border-b border-[var(--border-soft)]"
      style={{ gridTemplateColumns: "12px 1fr auto" }}
      data-scope-state={state}
    >
      <Mono size={10} color={state === "missing" ? "var(--amber)" : "var(--mfg)"}>
        {state === "missing" ? "∅" : "✓"}
      </Mono>
      <Mono size={11} color={color}>
        {scope}
      </Mono>
      <Mono size={9} upper tracking="0.10em" color={color}>
        {state === "missing" ? "not granted" : state === "granted" ? "granted" : "extra"}
      </Mono>
    </div>
  );
}

/** ScopeBalance: ratio + segmented bar. */
export function ScopeBalance({
  granted = [],
  required = [],
  width = 160,
}: {
  granted?: string[];
  required?: string[];
  width?: number;
}) {
  if (required.length === 0) return null;
  const grantedSet = new Set(granted);
  const have = required.filter((s) => grantedSet.has(s)).length;
  const missing = required.length - have;
  const color = missing > 0 ? "var(--amber)" : "var(--green)";
  return (
    <div className="flex items-center gap-2.5">
      <Mono size={11} color={color}>
        {have}/{required.length}
      </Mono>
      <span
        className="inline-flex"
        style={{ width, height: 2, background: "var(--border-soft)" }}
      >
        {required.map((scope, i) => (
          <span
            key={scope}
            className="flex-1 h-full"
            style={{
              borderRight:
                i < required.length - 1
                  ? "1px solid var(--bg)"
                  : "none",
              background: grantedSet.has(scope) ? color : "transparent",
            }}
          />
        ))}
      </span>
      <Mono size={10} upper tracking="0.10em" color="var(--dim)">
        scopes
      </Mono>
    </div>
  );
}

// ── ProbeResult ─────────────────────────────────────────────────────────────

/** ProbeResult: latency / code / timestamp / serif-italic message. */
export function ProbeResult({
  test,
  onProbe,
}: {
  test: { ok: boolean; code?: number | null; latencyMs: number; at: string; message?: string | null } | null;
  onProbe?: () => void;
}) {
  if (!test) {
    return (
      <div className="flex items-center gap-3">
        <Mono size={11} color="var(--dim)">
          never probed
        </Mono>
        {onProbe && (
          <button
            type="button"
            onClick={onProbe}
            className="font-mono text-[11px] px-2.5 py-1 border border-[var(--border-strong)] rounded-sm bg-transparent text-[var(--fg)] cursor-pointer"
          >
            run probe
          </button>
        )}
      </div>
    );
  }
  const color = test.ok ? "var(--green)" : "var(--red)";
  return (
    <div className="flex flex-wrap items-center gap-3.5">
      <div className="flex items-baseline gap-2">
        <span
          className="inline-block self-center rounded-full"
          style={{ width: 6, height: 6, backgroundColor: color }}
        />
        <Mono size={12} color={color}>
          {test.code ?? "—"}
        </Mono>
        <Mono size={10} color="var(--dim)">
          ·
        </Mono>
        <Mono size={11}>{test.latencyMs}ms</Mono>
      </div>
      <Mono size={10} color="var(--dim)">
        at {test.at}
      </Mono>
      {test.message && (
        <span
          style={{
            fontFamily: "var(--font-serif, 'Source Serif 4', serif)",
            fontStyle: "italic",
            fontSize: 12,
            color: test.ok ? "var(--mfg)" : "var(--red)",
          }}
        >
          {test.message}
        </span>
      )}
      {onProbe && (
        <button
          type="button"
          onClick={onProbe}
          className="font-mono text-[11px] px-2.5 py-1 border border-[var(--border-strong)] rounded-sm bg-transparent text-[var(--fg)] cursor-pointer"
        >
          probe again
        </button>
      )}
    </div>
  );
}

// ── WhatBreaks ───────────────────────────────────────────────────────────────

/**
 * WhatBreaks list: butler features that go silent when credential is sick.
 * Severity pip per row. Sourced from server-side catalogue.
 */
export function WhatBreaks({
  breaks,
  state,
}: {
  breaks: Array<{ butler: string; feature: string; severity: "high" | "medium" | "low" }>;
  state: CredentialState;
}) {
  if (!breaks || breaks.length === 0) return null;
  const sick = state !== "ok" && state !== "never_set";
  const presentTense = sick;
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <Mono size={10} upper tracking="0.14em" color="var(--dim)">
          {presentTense ? "what breaks" : "what would break"}
        </Mono>
        <Mono size={9} color="var(--dim)">
          {breaks.length} feature{breaks.length === 1 ? "" : "s"}
        </Mono>
      </div>
      <div className="mt-2 border-t border-[var(--border)]">
        {breaks.map((b, i) => {
          const meta = SEVERITY_META[b.severity] ?? SEVERITY_META.low;
          const color =
            sick && b.severity === "high"
              ? "var(--red)"
              : sick && b.severity === "medium"
                ? "var(--amber)"
                : "var(--fg)";
          return (
            <div
              key={`${b.butler}-${b.feature}-${i}`}
              className={cn(
                "grid gap-2.5 items-baseline py-1.5",
                i < breaks.length - 1 && "border-b border-[var(--border-soft)]",
              )}
              style={{ gridTemplateColumns: "14px 1fr auto 80px" }}
            >
              <SeverityPip severity={b.severity} />
              <span className="flex items-center gap-2">
                <span
                  className="font-sans"
                  style={{ fontSize: 13, color, letterSpacing: "-0.005em" }}
                >
                  {b.feature}
                </span>
              </span>
              <Mono size={9} upper tracking="0.10em" color={color}>
                {presentTense ? meta.label : "ok"}
              </Mono>
              <Mono size={9} upper tracking="0.10em" color="var(--dim)">
                {b.butler === "*" ? "all butlers" : b.butler}
              </Mono>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Pill button ───────────────────────────────────────────────────────────────

/** Dispatch commit/pill/danger button per §4c. */
export function PillBtn({
  children,
  variant = "pill",
  onClick,
  disabled,
  className,
  ...rest
}: {
  children: React.ReactNode;
  variant?: "pill" | "commit" | "danger";
  onClick?: () => void;
  disabled?: boolean;
  className?: string;
} & Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "type" | "onClick" | "disabled" | "className">) {
  const base = cn(
    "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-sm font-mono text-[11px] cursor-pointer border transition-colors leading-tight",
    "disabled:pointer-events-none disabled:opacity-40",
    className,
  );
  if (variant === "commit") {
    return (
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        className={cn(base, "bg-[var(--fg)] text-[var(--bg)] border-[var(--fg)]")}
        {...rest}
      >
        {children}
      </button>
    );
  }
  if (variant === "danger") {
    return (
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        className={cn(base, "bg-transparent text-[var(--red)] border-[var(--red)]")}
        {...rest}
      >
        {children}
      </button>
    );
  }
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(base, "bg-transparent text-[var(--fg)] border-[var(--border-strong)]")}
      {...rest}
    >
      {children}
    </button>
  );
}

// ── KV pair ─────────────────────────────────────────────────────────────────

/** Generic label + mono value pair. */
export function KV({
  label,
  value,
  valueColor,
  mono = true,
  size = 13,
}: {
  label: string;
  value: string;
  valueColor?: string;
  mono?: boolean;
  size?: number;
}) {
  return (
    <div>
      <Mono size={9} upper tracking="0.14em" color="var(--dim)">
        {label}
      </Mono>
      <div className="mt-1">
        {mono ? (
          <Mono size={size} color={valueColor ?? "var(--fg)"}>
            {value}
          </Mono>
        ) : (
          <span
            className="font-sans"
            style={{ fontSize: size, color: valueColor ?? "var(--fg)" }}
          >
            {value}
          </span>
        )}
      </div>
    </div>
  );
}
