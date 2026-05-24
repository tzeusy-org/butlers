// Shared atoms specific to /secrets. All three directions consume these.
// Read these once before reading any direction file — every component
// here is canonical Dispatch, not a deviation.

const Cs = window.C;
const STATE = window.STATE_CATALOG;

// ── Typographic atoms ────────────────────────────────────────────────

function Eyebrow({ children, sub }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
      <span style={{
        fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.14em',
        textTransform: 'uppercase', color: Cs.mfg,
      }}>{children}</span>
      {sub && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: Cs.dim }}>{sub}</span>}
    </div>
  );
}

function Mono({ children, size = 11, color, upper = false, track = 'normal', weight = 400 }) {
  return (
    <span style={{
      fontFamily: 'var(--font-mono)', fontSize: size, color: color || Cs.fg,
      textTransform: upper ? 'uppercase' : 'none', letterSpacing: track, fontWeight: weight,
    }} className="tnum">{children}</span>
  );
}

function Voice({ children, italic = false, size = 16, color, maxWidth }) {
  return (
    <p style={{
      fontFamily: 'var(--font-serif)', fontSize: size, lineHeight: 1.55,
      fontStyle: italic ? 'italic' : 'normal',
      color: color || Cs.mfg, margin: 0, maxWidth,
    }}>{children}</p>
  );
}

function Display({ children, color, size = 44, maxWidth = '14ch' }) {
  return (
    <h1 style={{
      fontFamily: 'var(--font-sans)', fontSize: size, fontWeight: 500,
      letterSpacing: '-0.025em', lineHeight: 1.08, color: color || Cs.fg,
      margin: 0, maxWidth, textWrap: 'pretty',
    }}>{children}</h1>
  );
}

function Title({ children, color, size = 22 }) {
  return (
    <h2 style={{
      fontFamily: 'var(--font-sans)', fontSize: size, fontWeight: 500,
      letterSpacing: '-0.015em', lineHeight: 1.2, color: color || Cs.fg,
      margin: 0,
    }}>{children}</h2>
  );
}

// ── Provider letter-mark ──────────────────────────────────────────────
// Square, hairline-bordered, mono initial. NOT coloured by provider —
// providers are not butlers; they are external authorities. Mono is the
// honest signal here.

function ProviderMark({ provider, size = 22 }) {
  const p = window.PROVIDERS[provider];
  const ch = p ? p.glyph : '?';
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      width: size, height: size, borderRadius: 3,
      border: `1px solid ${Cs.borderStrong}`,
      background: 'transparent', color: Cs.fg,
      fontFamily: 'var(--font-mono)', fontSize: Math.round(size * 0.5), fontWeight: 500,
      flexShrink: 0,
    }}>{ch}</span>
  );
}

// ── State dot · sliver · plaque ───────────────────────────────────────

function StateDot({ state, size = 6 }) {
  const meta = STATE[state] || STATE.never_set;
  const tone = meta.tone;
  const color = tone === 'ok' ? Cs.green : tone === 'amber' ? Cs.amber : tone === 'red' ? Cs.red : tone === 'dim' ? Cs.dim : Cs.mfg;
  return (
    <span style={{
      display: 'inline-block', width: size, height: size, borderRadius: 999,
      background: color, flexShrink: 0,
    }} />
  );
}

// Vertical 2px rail used by direction proposals when a row demands attention.
function Sliver({ state, height = '100%', width = 2 }) {
  const meta = STATE[state] || {};
  if (!meta.sliver) return null;
  const tone = meta.tone;
  const color = tone === 'amber' ? Cs.amber : tone === 'red' ? Cs.red : Cs.mfg;
  return (
    <span style={{
      position: 'absolute', top: 0, left: 0, width, height,
      background: color, flexShrink: 0,
    }} />
  );
}

// State label — mono, lowercase, colour drawn from state. Used as the
// "status as one of {dot, sliver, numeral, colour}" affordance in
// rows where the dot alone is not enough.
function StateLabel({ state }) {
  const meta = STATE[state] || STATE.never_set;
  const tone = meta.tone;
  const color = tone === 'ok' ? Cs.green : tone === 'amber' ? Cs.amber : tone === 'red' ? Cs.red : tone === 'dim' ? Cs.dim : Cs.mfg;
  return (
    <Mono size={10} color={color} upper track="0.10em">{meta.label}</Mono>
  );
}

// ── Fingerprint ──────────────────────────────────────────────────────
// The single most important atom of this redesign. Replaces the
// "••••••••" masked-value blob with evidence about the value.

function Fingerprint({ value, size = 11, dim = false }) {
  if (!value) {
    return <Mono size={size} color={Cs.dim}>—</Mono>;
  }
  // Render the hash prefix in fg, the suffix in mfg. `sha256:7a3f9e2c` →
  // `sha256:` is mfg, `7a3f9e2c` is fg. Reads as "this is a hash of this
  // shape".
  const [scheme, hash] = value.split(':');
  return (
    <span style={{ fontFamily: 'var(--font-mono)', fontSize: size, letterSpacing: '0.01em' }}>
      <span style={{ color: dim ? Cs.dim : Cs.mfg }}>{scheme}:</span>
      <span style={{ color: dim ? Cs.mfg : Cs.fg }}>{hash}</span>
    </span>
  );
}

// ── Scope row ────────────────────────────────────────────────────────
// `granted vs required` with mismatches called out. Used inline in the
// detail surfaces; also used in compact form on rows.

function ScopeRow({ granted = [], required = [], compact = false }) {
  const grantedSet = new Set(granted);
  const all = Array.from(new Set([...granted, ...required]));
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: compact ? 6 : 10, alignItems: 'center' }}>
      {all.map((scope) => {
        const have = grantedSet.has(scope);
        const want = required.includes(scope);
        const missing = want && !have;
        return (
          <span key={scope} style={{
            display: 'inline-flex', alignItems: 'center', gap: 4,
            fontFamily: 'var(--font-mono)', fontSize: compact ? 10 : 11,
            color: missing ? Cs.amber : have ? Cs.fg : Cs.dim,
          }}>
            <span style={{ width: 4, height: 4, borderRadius: 999, background: missing ? Cs.amber : have ? Cs.green : Cs.dim }} />
            {scope}
            {missing && <span style={{ color: Cs.amber, fontFamily: 'var(--font-sans)', fontStyle: 'italic' }}>· missing</span>}
          </span>
        );
      })}
    </div>
  );
}

// ── Pill buttons (Dispatch §4c) ──────────────────────────────────────

function PillBtn({ children, variant = 'pill', onClick, mono = true, disabled }) {
  const base = {
    display: 'inline-flex', alignItems: 'center', gap: 6,
    padding: '4px 10px', borderRadius: 3,
    fontFamily: mono ? 'var(--font-mono)' : 'var(--font-sans)', fontSize: 11,
    cursor: disabled ? 'default' : 'pointer', userSelect: 'none', flexShrink: 0,
    opacity: disabled ? 0.4 : 1, lineHeight: 1.2,
    textDecoration: 'none',
  };
  if (variant === 'commit') {
    return (
      <button onClick={onClick} disabled={disabled} style={{
        ...base, background: Cs.fg, color: Cs.bg, border: `1px solid ${Cs.fg}`,
      }}>{children}</button>
    );
  }
  if (variant === 'danger') {
    return (
      <button onClick={onClick} disabled={disabled} style={{
        ...base, background: 'transparent', color: Cs.red,
        border: `1px solid ${Cs.red}`,
      }}>{children}</button>
    );
  }
  return (
    <button onClick={onClick} disabled={disabled} style={{
      ...base, background: 'transparent', color: Cs.fg,
      border: `1px solid ${Cs.borderStrong}`,
    }}>{children}</button>
  );
}

// Action arrow — used in rows. Underlined word ending in →.
function ActionArrow({ children, color }) {
  return (
    <a style={{
      color: color || Cs.fg, textDecoration: 'underline',
      textUnderlineOffset: 4, textDecorationColor: Cs.borderStrong,
      fontSize: 13, cursor: 'pointer', whiteSpace: 'nowrap',
    }}>{children} →</a>
  );
}

// Kind tag — mono uppercase, muted. Used to label a kind, not celebrate one.
function KindTag({ children }) {
  return (
    <span style={{
      fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.10em',
      textTransform: 'uppercase', color: Cs.mfg, whiteSpace: 'nowrap',
    }}>{children}</span>
  );
}

// ── Reveal toggle ────────────────────────────────────────────────────
// Eye icon. Stays available but no longer the primary affordance — the
// fingerprint is.

function RevealEye({ revealed, onClick }) {
  return (
    <button onClick={onClick} title={revealed ? 'hide value' : 'reveal value'}
      style={{
        background: 'transparent', border: 'none', cursor: 'pointer',
        color: Cs.mfg, padding: 2, display: 'inline-flex', alignItems: 'center',
      }}>
      {revealed ? (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
          <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
          <line x1="1" y1="1" x2="23" y2="23" />
        </svg>
      ) : (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
          <circle cx="12" cy="12" r="3" />
        </svg>
      )}
    </button>
  );
}

// ── Page chrome (used by all three directions) ───────────────────────

function FakeRail() {
  return (
    <div style={{
      width: 56, background: Cs.bgDeep, borderRight: `1px solid ${Cs.border}`,
      flexShrink: 0, display: 'flex', flexDirection: 'column', alignItems: 'center',
      padding: '14px 0', gap: 10,
    }}>
      {/* a few placeholder rail items so the page reads as inside the shell */}
      {[0, 1, 2, 3, 4, 5, 6].map((i) => (
        <div key={i} style={{
          width: 22, height: 22, borderRadius: 3,
          background: i === 5 ? Cs.bgElev : 'transparent',
          border: `1px solid ${i === 5 ? Cs.borderStrong : Cs.borderSoft}`,
        }} />
      ))}
    </div>
  );
}

function PageHeader({ eyebrow, eyebrowSub, headline, voice, right, headlineMaxWidth }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 56, alignItems: 'end', marginBottom: 32 }}>
      <div>
        <Eyebrow sub={eyebrowSub}>{eyebrow}</Eyebrow>
        <div style={{ marginTop: 14 }}>
          <Display maxWidth={headlineMaxWidth}>{headline}</Display>
        </div>
        {voice && (
          <div style={{ marginTop: 14 }}>
            <Voice maxWidth="60ch">{voice}</Voice>
          </div>
        )}
      </div>
      {right && <div style={{ paddingBottom: 4 }}>{right}</div>}
    </div>
  );
}

// KPI strip (Dispatch §4b) — 4-cell hairline divided.
function KpiStrip({ items }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: `repeat(${items.length}, 1fr)`,
      borderTop: `1px solid ${Cs.border}`, borderBottom: `1px solid ${Cs.border}`,
    }}>
      {items.map((it, i) => (
        <div key={it.label} style={{
          padding: '14px 18px',
          borderLeft: i === 0 ? 'none' : `1px solid ${Cs.border}`,
          display: 'flex', flexDirection: 'column', gap: 6,
        }}>
          <Mono size={10} upper track="0.14em" color={Cs.mfg}>{it.label}</Mono>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 28, fontWeight: 500, letterSpacing: '-0.02em', color: Cs.fg }} className="tnum">
            {it.value}
          </div>
          {it.delta && <Mono size={10} color={it.deltaColor || Cs.dim}>{it.delta}</Mono>}
        </div>
      ))}
    </div>
  );
}

// Section eyebrow + hairline rule.
function SectionHead({ eyebrow, right }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
      paddingBottom: 8, borderBottom: `1px solid ${Cs.border}`, marginBottom: 14,
    }}>
      <Eyebrow>{eyebrow}</Eyebrow>
      {right}
    </div>
  );
}

Object.assign(window, {
  Eyebrow, Mono, Voice, Display, Title,
  ProviderMark, StateDot, Sliver, StateLabel,
  Fingerprint, ScopeRow, PillBtn, ActionArrow, KindTag, RevealEye,
  FakeRail, PageHeader, KpiStrip, SectionHead,
});
