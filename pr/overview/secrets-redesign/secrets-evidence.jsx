// Evidence atoms — the new vocabulary added in Stage 3 deep-dive.
// Loaded after secrets-shared.jsx, before any page or spine file.
// Each atom answers a precise question:
//
//   WhatBreaks    — if this credential is sick, which butler features
//                   go silent? Single block, one row per feature, sorted
//                   by severity. The dramatic anchor of a sick page.
//   ProbeResult   — last-test summary: latency · code · timestamp,
//                   plus a serif-italic message tail for failure modes.
//                   Replaces the old "throughput sparkline" — a 1-call
//                   probe is honest; a fake sparkline is decoration.
//   ScopeBalance  — single numeric ratio (granted/required) with a 1-row
//                   bar. Read at-a-glance when scrolling the spine; the
//                   full visa list lives below.
//   IdentityChip  — name + role + a small colour-coded dot. Used in the
//                   page top-right and on the spine header for owner-vs-
//                   member context.
//   StampGlyph    — small mono shape next to each audit event. Action-
//                   aware: ✓ verified, ↻ rotated, ✕ failed, ⨯ revoked,
//                   ⊕ connected, ⊘ disconnected, ⚠ warned, ⤳ overrode,
//                   ▷ attempted, ⊙ set.
//   FingerprintRow— two-line stack: scheme · hash (mono), then
//                   "verify with: openssl …" (mono, dim, expandable).
//                   Used on the page header instead of the bare hash.
//   SeverityPip   — one-character mono pip for breaks lines. high/mid/low.

const Cs_E = window.C;

// ── WhatBreaks ───────────────────────────────────────────────────────

const SEVERITY_META = {
  high:   { glyph: '▰', label: 'breaks',   tone: 'red'   },
  medium: { glyph: '▰', label: 'degrades', tone: 'amber' },
  low:    { glyph: '▱', label: 'minor',    tone: 'dim'   },
};

function SeverityPip({ severity }) {
  const meta = SEVERITY_META[severity] || SEVERITY_META.low;
  const color = meta.tone === 'red' ? Cs_E.red : meta.tone === 'amber' ? Cs_E.amber : Cs_E.dim;
  return <window.Mono size={11} color={color}>{meta.glyph}</window.Mono>;
}

// `state` lets the block dim its rhetoric on a healthy page — what
// COULD break, in present-tense, vs what HAS broken.
function WhatBreaks({ breaks, state }) {
  if (!breaks || breaks.length === 0) return null;
  const sick = state !== 'ok' && state !== 'never_set';
  const presentTense = sick;
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <window.Mono size={10} upper track="0.14em" color={Cs_E.dim}>
          {presentTense ? 'what breaks' : 'what would break'}
        </window.Mono>
        <window.Mono size={9} color={Cs_E.dim}>
          {breaks.length} feature{breaks.length === 1 ? '' : 's'}
        </window.Mono>
      </div>
      <div style={{ marginTop: 8, borderTop: `1px solid ${Cs_E.border}` }}>
        {breaks.map((b, i) => {
          const meta = SEVERITY_META[b.severity] || SEVERITY_META.low;
          const color = sick && b.severity === 'high'   ? Cs_E.red
                      : sick && b.severity === 'medium' ? Cs_E.amber
                      : Cs_E.fg;
          return (
            <div key={i} style={{
              display: 'grid', gridTemplateColumns: '14px 1fr auto 80px',
              columnGap: 10, alignItems: 'baseline', padding: '7px 0',
              borderBottom: i === breaks.length - 1 ? 'none' : `1px solid ${Cs_E.borderSoft}`,
            }}>
              <SeverityPip severity={b.severity} />
              <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                {b.butler && b.butler !== '*' && <window.ButlerMark name={b.butler} size={14} tone="fill" />}
                {b.butler === '*' && (
                  <span style={{
                    fontFamily: 'var(--font-mono)', fontSize: 10, color: Cs_E.dim,
                    textTransform: 'uppercase', letterSpacing: '0.10em',
                  }}>any</span>
                )}
                <span style={{
                  fontFamily: 'var(--font-sans)', fontSize: 13, color,
                  letterSpacing: '-0.005em',
                }}>{b.feature}</span>
              </span>
              <window.Mono size={9} upper track="0.10em" color={color}>
                {presentTense ? meta.label : 'ok'}
              </window.Mono>
              <window.Mono size={9} upper track="0.10em" color={Cs_E.dim}>
                {b.butler === '*' ? 'all butlers' : b.butler}
              </window.Mono>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── ProbeResult ──────────────────────────────────────────────────────

function ProbeResult({ test, onTest }) {
  if (!test) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <window.Mono size={11} color={Cs_E.dim}>never probed</window.Mono>
        <window.PillBtn onClick={onTest}>run probe</window.PillBtn>
      </div>
    );
  }
  const color = test.ok ? Cs_E.green : Cs_E.red;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{
          width: 6, height: 6, borderRadius: 999, background: color,
          display: 'inline-block', alignSelf: 'center',
        }} />
        <window.Mono size={12} color={color}>{test.code || '—'}</window.Mono>
        <window.Mono size={10} color={Cs_E.dim}>·</window.Mono>
        <window.Mono size={11} color={Cs_E.fg}>{test.latencyMs}ms</window.Mono>
      </div>
      <window.Mono size={10} color={Cs_E.dim}>at {test.at}</window.Mono>
      {test.message && (
        <span style={{
          fontFamily: 'var(--font-serif)', fontStyle: 'italic',
          fontSize: 12, color: test.ok ? Cs_E.mfg : Cs_E.red,
        }}>{test.message}</span>
      )}
      <window.PillBtn onClick={onTest}>probe again</window.PillBtn>
    </div>
  );
}

// ── ScopeBalance ─────────────────────────────────────────────────────
// A single-line "3 of 4 granted" with a 1px-thick segmented bar.

function ScopeBalance({ granted = [], required = [], width = 160 }) {
  if (required.length === 0) return null;
  const grantedSet = new Set(granted);
  const have = required.filter((s) => grantedSet.has(s)).length;
  const missing = required.length - have;
  const color = missing > 0 ? Cs_E.amber : Cs_E.green;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <window.Mono size={11} color={color}>{have}/{required.length}</window.Mono>
      <span style={{ display: 'inline-flex', width, height: 2, background: Cs_E.borderSoft }}>
        {required.map((scope, i) => {
          const isHave = grantedSet.has(scope);
          return (
            <span key={i} style={{
              flex: 1, height: '100%',
              borderRight: i < required.length - 1 ? `1px solid ${Cs_E.bg}` : 'none',
              background: isHave ? color : 'transparent',
            }} />
          );
        })}
      </span>
      <window.Mono size={10} upper track="0.10em" color={Cs_E.dim}>scopes</window.Mono>
    </div>
  );
}

// ── IdentityChip ─────────────────────────────────────────────────────

function IdentityChip({ identity, compact = false, onClick }) {
  if (!identity) return null;
  return (
    <button onClick={onClick} style={{
      display: 'inline-flex', alignItems: 'center', gap: 8,
      padding: compact ? '3px 8px' : '4px 10px',
      background: 'transparent', border: `1px solid ${Cs_E.borderStrong}`,
      borderRadius: 3, cursor: onClick ? 'pointer' : 'default',
    }}>
      <span style={{
        width: 8, height: 8, borderRadius: 999, background: identity.hue || Cs_E.fg,
      }} />
      <span style={{
        fontFamily: 'var(--font-sans)', fontSize: compact ? 12 : 13,
        color: Cs_E.fg, fontWeight: 500, letterSpacing: '-0.005em',
      }}>{identity.label}</span>
      <window.Mono size={9} upper track="0.12em" color={Cs_E.dim}>
        {identity.role}{identity.pronoun ? ` · ${identity.pronoun}` : ''}
      </window.Mono>
      {onClick && <window.Mono size={11} color={Cs_E.mfg}>▾</window.Mono>}
    </button>
  );
}

// ── StampGlyph ───────────────────────────────────────────────────────

const STAMP_GLYPHS = {
  verified:    { glyph: '✓', tone: 'ok'    },
  rotated:     { glyph: '↻', tone: 'fg'    },
  failed:      { glyph: '✕', tone: 'red'   },
  revoked:     { glyph: '⊘', tone: 'red'   },
  connected:   { glyph: '⊕', tone: 'fg'    },
  disconnected:{ glyph: '⊖', tone: 'dim'   },
  warned:      { glyph: '!', tone: 'amber' },
  overrode:    { glyph: '⤳', tone: 'fg'    },
  attempted:   { glyph: '▷', tone: 'dim'   },
  set:         { glyph: '⊙', tone: 'fg'    },
};

function StampGlyph({ action, size = 14 }) {
  const meta = STAMP_GLYPHS[action] || { glyph: '·', tone: 'dim' };
  const color = meta.tone === 'ok' ? Cs_E.green
              : meta.tone === 'red' ? Cs_E.red
              : meta.tone === 'amber' ? Cs_E.amber
              : meta.tone === 'dim' ? Cs_E.dim
              : Cs_E.fg;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      width: size + 4, height: size + 4, borderRadius: 2,
      border: `1px solid ${color}`, color,
      fontFamily: 'var(--font-mono)', fontSize: Math.round(size * 0.85), lineHeight: 1,
    }}>{meta.glyph}</span>
  );
}

// ── FingerprintRow (with verify-cmd) ─────────────────────────────────

function FingerprintRow({ value, size = 14, withCmd = true }) {
  const [open, setOpen] = React.useState(false);
  if (!value) return <window.Mono size={size} color={Cs_E.dim}>—</window.Mono>;
  const showCmd = withCmd && (window.__showVerifyCmd !== false);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <window.Fingerprint value={value} size={size} />
      {showCmd && (
        <div>
          <button onClick={() => setOpen((o) => !o)} style={{
            background: 'transparent', border: 'none', cursor: 'pointer',
            color: Cs_E.dim, fontFamily: 'var(--font-mono)', fontSize: 9,
            letterSpacing: '0.10em', textTransform: 'uppercase', padding: 0,
          }}>{open ? '— hide verify cmd' : '+ verify cmd'}</button>
          {open && (
            <div style={{ marginTop: 4 }}>
              <window.Mono size={10} color={Cs_E.mfg}>
                $ echo -n "$KEY" | shasum -a 256 | cut -c1-8
              </window.Mono>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Severity rank helper (used by spine sort) ────────────────────────

function severityRank(state) {
  return (window.STATE_CATALOG[state] || {}).rank ?? 99;
}

Object.assign(window, {
  WhatBreaks, ProbeResult, ScopeBalance, IdentityChip,
  StampGlyph, FingerprintRow, SeverityPip, SEVERITY_META,
  severityRank,
});
