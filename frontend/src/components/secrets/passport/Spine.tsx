// ---------------------------------------------------------------------------
// Spine — left-hand credential index for /secrets [bu-qu8v8]
//
// Spec: butler-secrets §Passport-Book Information Architecture
//       §Spine grouping order
//
// Structure:
//   - IdentityChip strip at the top (viewing context)
//   - SpineSearch (text filter)
//   - SortPicker (severity | recency | alpha)
//   - Groups: needs-hand (pinned) | cli runtimes | system | user
//   - SpineRow: relative position + sliver + dot + label + subline + right-glyph
//
// One-row-template uniformity: every family uses SpineRow with identical
// grid layout. Only `data-family` and `data-key` differ.
// ---------------------------------------------------------------------------

import * as React from "react";

import { cn } from "@/lib/utils";
import type { SpineEntry, SpineSortMode, Identity } from "./types.ts";
import { needsHand, severityRank } from "./constants.ts";
import { CredentialDot, Sliver, Mono, IdentityChip, ProviderMark } from "./atoms.tsx";

// ── Sorters ─────────────────────────────────────────────────────────────────

const SORTERS: Record<SpineSortMode, (a: SpineEntry, b: SpineEntry) => number> = {
  severity: (a, b) => severityRank(a.state) - severityRank(b.state),
  recency:  (a, b) => (a.lastTouchOrder ?? 999) - (b.lastTouchOrder ?? 999),
  alpha:    (a, b) => (a.label ?? "").toLowerCase().localeCompare((b.label ?? "").toLowerCase()),
};

// ── SpineSearch ──────────────────────────────────────────────────────────────

export function SpineSearch({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div
      className="relative mx-3 mb-1"
      style={{ borderBottom: "1px solid var(--border-soft)" }}
    >
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="search"
        aria-label="Search credentials"
        className="w-full py-1.5 pl-4 pr-6 bg-transparent border-none outline-none font-mono text-[11px] tracking-[0.04em] text-[var(--fg)] placeholder:text-[var(--dim)]"
        data-spine-search="true"
      />
      <span
        className="absolute left-0 top-1.5 font-mono text-[11px]"
        aria-hidden="true"
        style={{ color: "var(--dim)" }}
      >
        /
      </span>
      {value && (
        <button
          type="button"
          onClick={() => onChange("")}
          aria-label="Clear search"
          className="absolute right-0 top-1.5 bg-transparent border-none cursor-pointer font-mono text-[11px] p-0"
          style={{ color: "var(--dim)" }}
        >
          ×
        </button>
      )}
    </div>
  );
}

// ── SortPicker ───────────────────────────────────────────────────────────────

export function SortPicker({
  mode,
  onChange,
}: {
  mode: SpineSortMode;
  onChange: (m: SpineSortMode) => void;
}) {
  const opts: Array<{ id: SpineSortMode; label: string }> = [
    { id: "severity", label: "severity" },
    { id: "recency",  label: "recency"  },
    { id: "alpha",    label: "alpha"    },
  ];
  return (
    <div className="flex items-baseline gap-1.5 px-3.5 pb-2.5">
      <Mono size={9} upper tracking="0.14em" color="var(--dim)">
        sort ·
      </Mono>
      {opts.map((o, i) => (
        <React.Fragment key={o.id}>
          {i > 0 && (
            <Mono size={9} color="var(--dim)">
              ·
            </Mono>
          )}
          <button
            type="button"
            onClick={() => onChange(o.id)}
            aria-pressed={mode === o.id}
            data-sort-mode={o.id}
            className={cn(
              "bg-transparent border-none cursor-pointer p-0 font-mono text-[9.5px] uppercase tracking-[0.08em] pb-px",
              mode === o.id
                ? "text-[var(--fg)] border-b border-[var(--fg)]"
                : "text-[var(--dim)]",
            )}
          >
            {o.label}
          </button>
        </React.Fragment>
      ))}
    </div>
  );
}

// ── SpineRow ─────────────────────────────────────────────────────────────────

/**
 * SpineRow — ONE ROW TEMPLATE for all three families.
 *
 * Per spec §One Row Template Across All Three Families:
 * "the row has the same vertical rhythm (10px vertical padding), same column
 * layout (sliver | dot | label | subline | right-aligned glyph), and same
 * hairline separators"
 *
 * Identical HTML structure modulo data-* attributes and text content.
 */
export function SpineRow({
  entry,
  n,
  active,
  onClick,
  providerGlyph,
  providerLabel,
}: {
  entry: SpineEntry;
  n: number;
  active: boolean;
  onClick: () => void;
  providerGlyph?: string;
  providerLabel?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-spine-row="true"
      data-family={entry.family}
      data-key={entry.key}
      data-state={entry.state}
      data-active={active}
      className={cn(
        "relative w-full text-left border-none cursor-pointer",
        "grid items-center gap-2",
        "px-3.5 py-2.5",            // 10px vertical padding per spec
        "transition-colors",
        active
          ? "bg-[var(--bg-elev)]"
          : "bg-transparent hover:bg-[oklch(1_0_0/0.06)]",
      )}
      style={{
        gridTemplateColumns: "32px 1fr 8px",
        borderLeft: active ? "2px solid var(--fg)" : "2px solid transparent",
      }}
    >
      {/* Left-edge sliver — attention on sick rows only */}
      {!active && <Sliver state={entry.state} />}

      {/* Sequence number */}
      <Mono size={10} color={active ? "var(--fg)" : "var(--dim)"}>
        §{String(n).padStart(2, "0")}
      </Mono>

      {/* Label + subline */}
      <div className="flex flex-col gap-0.5 min-w-0">
        <div className="flex items-center gap-1.5 min-w-0">
          {providerGlyph && providerLabel && (
            <ProviderMark glyph={providerGlyph} label={providerLabel} size={14} />
          )}
          <span
            className="truncate"
            style={{
              fontFamily: entry.mono
                ? "var(--font-mono, monospace)"
                : "var(--font-sans, sans-serif)",
              fontSize: entry.mono ? 10.5 : 12.5,
              fontWeight: 500,
              color: active ? "var(--fg)" : "var(--mfg)",
              letterSpacing: entry.mono ? "normal" : "-0.005em",
            }}
          >
            {entry.label}
          </span>
        </div>
        {/* State subline */}
        <span
          className="font-mono truncate"
          style={{
            fontSize: 9.5,
            letterSpacing: "0.04em",
            textTransform: "lowercase",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          <CredentialDot
            state={entry.state}
            size={5}
            className="inline-block mr-1 align-middle"
          />
          <span style={{ color: "var(--mfg)" }}>{entry.subline}</span>
        </span>
      </div>

      {/* Right state dot */}
      <CredentialDot state={entry.state} />
    </button>
  );
}

// ── SpineGroup ───────────────────────────────────────────────────────────────

/** SpineGroup: section eyebrow + rows. Hidden when empty (calm-day invariant). */
export function SpineGroup({
  eyebrow,
  hint,
  items,
  n0,
  activeKey,
  onSelect,
  providers,
}: {
  eyebrow: string;
  hint?: string;
  items: SpineEntry[];
  n0: number;
  activeKey: string;
  onSelect: (key: string) => void;
  providers?: Record<string, { glyph: string; label: string }>;
}) {
  if (items.length === 0) return null;
  return (
    <div className="pb-3" data-spine-group={eyebrow}>
      <div
        className="flex items-baseline justify-between px-3.5 pb-1.5 pt-3"
      >
        <Mono size={9} upper tracking="0.14em" color="var(--dim)">
          {eyebrow}
        </Mono>
        {hint && (
          <Mono size={9} color="var(--dim)">
            {hint}
          </Mono>
        )}
      </div>
      {items.map((entry, i) => {
        const providerInfo = entry.provider ? providers?.[entry.provider] : undefined;
        return (
          <SpineRow
            key={entry.key}
            entry={entry}
            n={n0 + i + 1}
            active={activeKey === entry.key}
            onClick={() => onSelect(entry.key)}
            providerGlyph={providerInfo?.glyph}
            providerLabel={providerInfo?.label}
          />
        );
      })}
    </div>
  );
}

// ── SpineAddButton ────────────────────────────────────────────────────────────

/**
 * Single commit-pill affordance in the spine footer for creating new credentials.
 * Opens the PassportAddPanel when clicked.
 */
export function SpineAddButton({
  onClick,
  active,
}: {
  onClick: () => void;
  active: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={active}
      data-spine-add="true"
      aria-label="Add credential or connect provider"
      className={cn(
        "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-sm font-mono text-[11px] cursor-pointer border transition-colors leading-tight",
        "disabled:pointer-events-none disabled:opacity-40",
        "bg-[var(--fg)] text-[var(--bg)] border-[var(--fg)]",
      )}
    >
      + add
    </button>
  );
}

// ── Spine ──────────────────────────────────────────────────────────────────

export function Spine({
  entries,
  activeKey,
  onSelect,
  sortMode = "severity",
  onSortChange,
  search,
  onSearchChange,
  identities,
  activeIdentityId,
  onIdentityChange,
  providers,
}: {
  entries: SpineEntry[];
  activeKey: string;
  onSelect: (key: string) => void;
  sortMode?: SpineSortMode;
  onSortChange: (m: SpineSortMode) => void;
  search: string;
  onSearchChange: (v: string) => void;
  identities: Identity[];
  activeIdentityId: string;
  onIdentityChange: (id: string) => void;
  providers?: Record<string, { glyph: string; label: string }>;
}) {
  const cmp = SORTERS[sortMode] ?? SORTERS.severity;

  const filtered = React.useMemo(
    () =>
      entries.filter((e) => {
        if (!search) return true;
        return e.label.toLowerCase().includes(search.toLowerCase());
      }),
    [entries, search],
  );

  const needsHandGroup = React.useMemo(
    () => filtered.filter((e) => needsHand(e.state)).sort(cmp),
    [filtered, cmp],
  );
  const restCli = React.useMemo(
    () => filtered.filter((e) => e.family === "cli" && !needsHand(e.state)).sort(cmp),
    [filtered, cmp],
  );
  const restSys = React.useMemo(
    () => filtered.filter((e) => e.family === "system" && !needsHand(e.state)).sort(cmp),
    [filtered, cmp],
  );
  const restUsr = React.useMemo(
    () => filtered.filter((e) => e.family === "user" && !needsHand(e.state)).sort(cmp),
    [filtered, cmp],
  );

  // Running counter for global §N numbering — computed declaratively to
  // avoid React Compiler immutability complaints about render-time mutation.
  const n0NeedsHand = 0;
  const n0Cli = needsHandGroup.length;
  const n0Sys = n0Cli + restCli.length;
  const n0Usr = n0Sys + restSys.length;

  const activeIdentity = identities.find((i) => i.id === activeIdentityId);
  const showSwitcher = identities.length > 1;

  return (
    <nav
      className="flex flex-col overflow-y-auto"
      style={{
        background: "var(--bg-deep)",
        borderRight: "1px solid var(--border)",
      }}
      aria-label="Credentials index"
    >
      {/* Identity strip */}
      <div
        className="flex flex-col gap-2 p-3.5 pb-3"
        style={{ borderBottom: "1px solid var(--border-soft)" }}
      >
        <Mono size={9} upper tracking="0.14em" color="var(--dim)">
          viewing
        </Mono>
        {showSwitcher ? (
          <div className="flex gap-1.5 flex-wrap">
            {identities.map((id) => (
              <IdentityChip
                key={id.id}
                id={id.id}
                label={id.label}
                role={id.role}
                hue={id.hue}
                active={id.id === activeIdentityId}
                onClick={() => onIdentityChange(id.id)}
              />
            ))}
          </div>
        ) : (
          activeIdentity && (
            <IdentityChip
              id={activeIdentity.id}
              label={activeIdentity.label}
              role={activeIdentity.role}
              hue={activeIdentity.hue}
            />
          )
        )}
      </div>

      <SpineSearch value={search} onChange={onSearchChange} />
      <SortPicker mode={sortMode} onChange={onSortChange} />

      <div className="flex-1 min-h-0">
        {/* Needs-hand group: always pinned, severity-sorted */}
        <SpineGroup
          eyebrow={`needs hand · ${needsHandGroup.length}`}
          hint={needsHandGroup.length > 0 ? "pinned" : ""}
          items={needsHandGroup}
          n0={n0NeedsHand}
          activeKey={activeKey}
          onSelect={onSelect}
          providers={providers}
        />
        {needsHandGroup.length > 0 && (
          <div
            aria-hidden="true"
            className="mx-0 my-1"
            style={{ height: 1, background: "var(--border)" }}
          />
        )}

        <SpineGroup
          eyebrow={`cli runtimes · ${restCli.length}`}
          items={restCli}
          n0={n0Cli}
          activeKey={activeKey}
          onSelect={onSelect}
          providers={providers}
        />
        <SpineGroup
          eyebrow={`system · ${restSys.length}`}
          items={restSys}
          n0={n0Sys}
          activeKey={activeKey}
          onSelect={onSelect}
          providers={providers}
        />
        <SpineGroup
          eyebrow={`integrations · ${restUsr.length}`}
          items={restUsr}
          n0={n0Usr}
          activeKey={activeKey}
          onSelect={onSelect}
          providers={providers}
        />
      </div>

      {/* Footer */}
      <div
        className="flex justify-between items-center px-3.5 pb-4 pt-3"
        style={{ borderTop: "1px solid var(--border-soft)" }}
      >
        <Mono size={9} color="var(--dim)">
          {filtered.length} of {entries.length}
        </Mono>
      </div>
    </nav>
  );
}
