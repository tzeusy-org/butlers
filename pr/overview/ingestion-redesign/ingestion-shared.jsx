// Shared helpers for the four Ingestion design directions.
//
// Everything here is small, low-opinion, and reused. Each variant
// composes these into its layout.

// ─── Status pill ────────────────────────────────────────────────────────
function StatusBadge({ status, size = 'sm' }) {
  const map = {
    ingested:        { label: 'ingested',   tone: window.C.green, glyph: '●' },
    filtered:        { label: 'filtered',   tone: window.C.dim,   glyph: '○' },
    error:           { label: 'error',      tone: window.C.red,   glyph: '■' },
    replay_pending:  { label: 'replay · pending',  tone: window.C.amber, glyph: '◐' },
    replay_complete: { label: 'replay · complete', tone: window.C.green, glyph: '●' },
    replay_failed:   { label: 'replay · failed',   tone: window.C.red,   glyph: '■' },
  };
  const m = map[status] || { label: status, tone: window.C.dim, glyph: '·' };
  const tnums = { fontVariantNumeric: 'tabular-nums' };
  if (size === 'lg') {
    return (
      <span style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        fontFamily: 'var(--font-mono)', fontSize: 11,
        letterSpacing: '0.06em', textTransform: 'uppercase',
        color: m.tone, ...tnums,
      }}>
        <span style={{ fontSize: 8 }}>{m.glyph}</span>{m.label}
      </span>
    );
  }
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      fontFamily: 'var(--font-mono)', fontSize: 10,
      letterSpacing: '0.06em', textTransform: 'uppercase',
      color: m.tone, ...tnums,
    }}>
      <span style={{ fontSize: 7 }}>{m.glyph}</span>{m.label}
    </span>
  );
}

// ─── Channel glyph (square letter mark, neutral) ───────────────────────
function ChannelGlyph({ channel, size = 16 }) {
  const m = (window.CONNECTORS || []).find((c) => c.id === channel);
  const ch = (m?.glyph || channel[0]).toUpperCase();
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      width: size, height: size, borderRadius: 2,
      border: `1px solid ${window.C.border}`,
      fontFamily: 'var(--font-mono)', fontWeight: 500,
      fontSize: Math.round(size * 0.55),
      color: window.C.fg, background: 'transparent',
      letterSpacing: 0, flexShrink: 0,
    }}>{ch}</span>
  );
}

// ─── Butler mark (re-uses primitives.ButlerMark idea, with extended hue map) ─
function BMark({ name, size = 14, tone = 'neutral' }) {
  const hue = window.bh(name);
  const ch = (name || '?')[0].toUpperCase();
  const bg = tone === 'fill' ? hue : 'transparent';
  const fg = tone === 'fill' ? '#fff' : hue;
  const border = tone === 'fill' ? 'transparent' : window.C.border;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      width: size, height: size, borderRadius: 3,
      background: bg, color: fg,
      fontWeight: 600, fontSize: Math.round(size * 0.6),
      border: `1px solid ${border}`,
      flexShrink: 0,
    }}>{ch}</span>
  );
}

// ─── Eyebrow label ─────────────────────────────────────────────────────
function Eyebrow({ children, style }) {
  return (
    <div style={{
      fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 400,
      letterSpacing: '0.14em', textTransform: 'uppercase', color: window.C.mfg,
      ...(style || {}),
    }}>{children}</div>
  );
}

// ─── Mono inline label ─────────────────────────────────────────────────
function Mono({ children, color, size = 11, style }) {
  return (
    <span className="tnum" style={{
      fontFamily: 'var(--font-mono)', fontSize: size, color: color || window.C.fg,
      letterSpacing: '0.01em', ...(style || {}),
    }}>{children}</span>
  );
}

// ─── Pill button (commit / cycle) ───────────────────────────────────────
function PillBtn({ children, kind = 'pill', onClick, title, style }) {
  const C = window.C;
  const base = {
    display: 'inline-flex', alignItems: 'center', gap: 6,
    border: `1px solid ${C.border}`, borderRadius: 3,
    padding: '3px 8px',
    fontFamily: 'var(--font-mono)', fontSize: 10,
    letterSpacing: '0.06em', textTransform: 'uppercase',
    background: 'transparent', color: C.fg,
    cursor: 'pointer',
  };
  const commit = kind === 'commit' ? {
    background: C.fg, color: C.bg, borderColor: C.fg,
  } : {};
  return (
    <button type="button" onClick={onClick} title={title} style={{ ...base, ...commit, ...(style || {}) }}>
      {children}
    </button>
  );
}

// ─── Replay glyph (re-circle, stroke only) ─────────────────────────────
function ReplayIcon({ size = 12 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none"
         stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M13 5.5A5.5 5.5 0 1 0 14 8" />
      <path d="M13 2v3.5H9.5" />
    </svg>
  );
}

// ─── Flame strip — proportional bar(s) of butler durations ─────────────
// Modes:
//   'inline' — single ~18px row, butlers stacked as compact 4px slivers
//   'rows'   — one row per butler, with labels left, step segmentation inside
//   'bars'   — one row per butler, no labels (caller provides them)
//
// Sub-steps inside a bar are rendered as alternating opacity ramps to give
// the bar texture without inventing new colors.
function FlameStrip({ event, mode = 'inline', height, scaleMs, showAxis = false }) {
  const C = window.C;
  const max = scaleMs || event.durationMs || 1;
  const butlers = event.butlers || [];

  if (mode === 'inline') {
    const h = height || 6;
    return (
      <div style={{ position: 'relative', width: '100%', height: h,
        background: 'transparent',
        borderTop: `1px solid ${C.borderSoft}`,
        borderBottom: `1px solid ${C.borderSoft}`,
      }}>
        {butlers.map((b, i) => {
          const left = (b.startOffsetMs / max) * 100;
          const width = (b.durationMs / max) * 100;
          return (
            <div key={i} title={`${b.name} · ${(b.durationMs/1000).toFixed(1)}s`}
                 style={{
              position: 'absolute', left: left + '%', width: width + '%',
              top: 0, height: '100%',
              background: window.bh(b.name),
              opacity: 0.88,
            }} />
          );
        })}
        {!butlers.length && (
          <div style={{ position: 'absolute', inset: 0, background: 'transparent' }} />
        )}
      </div>
    );
  }

  // ROWS / BARS — one row per butler with optional left label
  const rowH = height || 18;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {showAxis && (
        <FlameAxis maxMs={max} />
      )}
      {butlers.map((b, i) => {
        const left = (b.startOffsetMs / max) * 100;
        const width = (b.durationMs / max) * 100;
        const stepsTotal = (b.steps || []).reduce((s, st) => s + st.durMs, 0) || b.durationMs;
        let xOff = 0;
        return (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {mode === 'rows' && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, width: 132, flexShrink: 0 }}>
                <BMark name={b.name} size={12} tone="fill" />
                <span style={{ fontSize: 11.5, color: C.fg, letterSpacing: '-0.005em' }}>{b.name}</span>
              </div>
            )}
            <div style={{ position: 'relative', flex: 1, height: rowH,
              background: window.__theme === 'light' ? 'oklch(0 0 0 / 0.025)' : 'oklch(1 0 0 / 0.025)',
              borderRadius: 1,
            }}>
              <div style={{
                position: 'absolute', left: left + '%', width: width + '%',
                top: 0, height: '100%',
                display: 'flex', alignItems: 'stretch',
                borderRadius: 1, overflow: 'hidden',
              }}>
                {(b.steps && b.steps.length ? b.steps : [{ name: b.name, durMs: b.durationMs, status: b.status }]).map((st, k) => {
                  const w = (st.durMs / stepsTotal) * 100;
                  const isErr = st.status === 'error';
                  const tint = isErr ? C.red : window.bh(b.name);
                  const op = isErr ? 0.95 : (0.55 + (k % 3) * 0.15);
                  xOff += w;
                  return (
                    <div key={k} title={`${b.name} · ${st.name} · ${(st.durMs/1000).toFixed(2)}s${isErr ? ' · error' : ''}`}
                         style={{
                      width: w + '%',
                      background: tint, opacity: op,
                      borderRight: k < (b.steps?.length || 1) - 1 ? '1px solid oklch(0 0 0 / 0.25)' : 'none',
                    }} />
                  );
                })}
              </div>
              {/* duration label */}
              <span className="tnum" style={{
                position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)',
                fontFamily: 'var(--font-mono)', fontSize: 9.5, color: C.mfg,
                letterSpacing: '0.04em',
                pointerEvents: 'none',
              }}>{fmtDur(b.durationMs)}</span>
            </div>
          </div>
        );
      })}
      {!butlers.length && (
        <div style={{
          fontFamily: 'var(--font-serif)', fontSize: 12, fontStyle: 'italic', color: C.dim,
          padding: '6px 0',
        }}>No butler accepted this event.</div>
      )}
    </div>
  );
}

function FlameAxis({ maxMs }) {
  const C = window.C;
  // 5 ticks
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  return (
    <div style={{ position: 'relative', height: 14, marginLeft: 142 }}>
      {ticks.map((t, i) => (
        <span key={i} className="tnum" style={{
          position: 'absolute', left: `calc(${t * 100}% - 12px)`, top: 0,
          fontFamily: 'var(--font-mono)', fontSize: 9, color: C.dim,
          letterSpacing: '0.04em',
        }}>{(t * maxMs / 1000).toFixed(1)}s</span>
      ))}
    </div>
  );
}

// ─── Formatters ────────────────────────────────────────────────────────
function fmtTok(n) {
  if (!n) return '—';
  if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + 'k';
  return String(n);
}
function fmtCost(c) {
  if (!c) return '$0';
  if (c < 0.001) return '<$0.001';
  if (c < 0.01) return '$' + c.toFixed(4);
  return '$' + c.toFixed(2);
}
function fmtDur(ms) {
  if (ms < 1000) return ms + 'ms';
  if (ms < 60_000) return (ms / 1000).toFixed(ms < 10_000 ? 2 : 1) + 's';
  return (ms / 60_000).toFixed(1) + 'm';
}
function shortId(id) {
  return id.split('-')[0];
}

// ─── Tabs row ───────────────────────────────────────────────────────────
function TabsRow({ tabs, active, onChange, right }) {
  const C = window.C;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 24,
      borderBottom: `1px solid ${C.border}`,
      paddingBottom: 0,
    }}>
      {tabs.map((tab) => {
        const isActive = tab.id === active;
        return (
          <button key={tab.id} type="button" onClick={() => onChange(tab.id)}
            style={{
              background: 'transparent', border: 'none', cursor: 'pointer',
              padding: '0 0 12px 0',
              fontFamily: 'var(--font-sans)', fontSize: 13,
              fontWeight: isActive ? 500 : 400,
              letterSpacing: '-0.005em',
              color: isActive ? C.fg : C.mfg,
              borderBottom: `1px solid ${isActive ? C.fg : 'transparent'}`,
              marginBottom: -1,
            }}>
            {tab.label}
            {tab.count != null && (
              <span className="tnum" style={{
                fontFamily: 'var(--font-mono)', fontSize: 10, color: C.dim,
                marginLeft: 6, letterSpacing: '0.04em',
              }}>{tab.count}</span>
            )}
          </button>
        );
      })}
      {right && <div style={{ marginLeft: 'auto' }}>{right}</div>}
    </div>
  );
}

// ─── PageHeader (eyebrow · display title · meta) ───────────────────────
function PageHeader({ eyebrow, title, sub, right }) {
  const C = window.C;
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '1fr auto', gap: 24,
      alignItems: 'baseline',
    }}>
      <div>
        {eyebrow && <Eyebrow style={{ marginBottom: 6 }}>{eyebrow}</Eyebrow>}
        <h1 style={{
          margin: 0, fontSize: 32, fontWeight: 500, letterSpacing: '-0.025em',
          color: C.fg, lineHeight: 1.1,
        }}>{title}</h1>
        {sub && (
          <div style={{
            marginTop: 8, fontFamily: 'var(--font-serif)', fontSize: 14,
            color: C.mfg, fontStyle: 'normal', maxWidth: '64ch', lineHeight: 1.5,
          }}>{sub}</div>
        )}
      </div>
      {right && <div>{right}</div>}
    </div>
  );
}

Object.assign(window, {
  StatusBadge, ChannelGlyph, BMark, Eyebrow, Mono, PillBtn,
  ReplayIcon, FlameStrip, FlameAxis,
  fmtTok, fmtCost, fmtDur, shortId,
  TabsRow, PageHeader,
});
