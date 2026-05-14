// Shared primitives across all four overview directions.

// Dark + light palettes. Each has the same key shape so consumers swap painlessly.
const PALETTES = {
  dark: {
    bg: 'oklch(0.145 0 0)',
    bgElev: 'oklch(0.205 0 0)',
    bgDeep: 'oklch(0.115 0 0)',
    bgRail: 'oklch(0.115 0 0)',
    fg: 'oklch(0.985 0 0)',
    mfg: 'oklch(0.708 0 0)',
    dim: 'oklch(0.55 0 0)',
    border: 'oklch(1 0 0 / 0.10)',
    borderSoft: 'oklch(1 0 0 / 0.06)',
    borderStrong: 'oklch(1 0 0 / 0.18)',
    muted: 'oklch(0.269 0 0)',
    ink: 'oklch(0.985 0 0)',
    red:   'oklch(0.685 0.250 29.2)',
    amber: 'oklch(0.810 0.185 84.0)',
    green: 'oklch(0.790 0.195 148.2)',
    blue:  'oklch(0.680 0.195 259.0)',
  },
  light: {
    // Calmer than stark white — a paper-warm neutral, like an ivory dispatch
    bg:     'oklch(0.985 0.003 85)',
    bgElev: 'oklch(1 0 0)',
    bgDeep: 'oklch(0.965 0.005 85)',
    bgRail: 'oklch(0.97 0.004 85)',
    fg:     'oklch(0.18 0 0)',
    mfg:    'oklch(0.46 0 0)',
    dim:    'oklch(0.62 0 0)',
    border:       'oklch(0 0 0 / 0.10)',
    borderSoft:   'oklch(0 0 0 / 0.05)',
    borderStrong: 'oklch(0 0 0 / 0.20)',
    muted:  'oklch(0.94 0.005 85)',
    ink:    'oklch(0.18 0 0)',
    red:    'oklch(0.527 0.235 29.2)',
    amber:  'oklch(0.66 0.145 75)',
    green:  'oklch(0.50 0.140 152.0)',
    blue:   'oklch(0.50 0.180 259.0)',
  },
};

// `C` is the live palette — patched on theme change. Components read from it directly.
const C = { ...PALETTES.dark };
function applyTheme(name) {
  Object.assign(C, PALETTES[name] || PALETTES.dark);
  window.__theme = name;
}
window.applyTheme = applyTheme;
window.PALETTES = PALETTES;
window.C = C;

// First-letter mark used as the canonical butler glyph (matches Sidebar.tsx)
function ButlerMark({ name, size = 16, tone = 'neutral' }) {
  const ch = (name || '?')[0].toUpperCase();
  const colorMap = {
    relationship: 'var(--category-1)',
    health:       'var(--category-4)',
    calendar:     'var(--category-3)',
    qa:           'var(--category-7)',
    memory:       'var(--category-2)',
    education:    'var(--category-6)',
    chronicler:   'var(--category-8)',
    household:    'var(--category-5)',
  };
  const bg = tone === 'fill' ? (colorMap[name] || C.ink) : 'transparent';
  const fg = tone === 'fill' ? '#fff' : (colorMap[name] || C.fg);
  const border = tone === 'fill' ? 'transparent' : C.border;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      width: size, height: size, borderRadius: 4, background: bg, color: fg,
      fontWeight: 600, fontSize: Math.round(size * 0.6), border: `1px solid ${border}`,
      flexShrink: 0,
    }}>{ch}</span>
  );
}

function StatusDot({ status, size = 6 }) {
  const map = { ok: C.green, degraded: C.amber, error: C.red, waiting: C.mfg };
  const color = map[status] || C.mfg;
  return <span style={{
    display: 'inline-block', width: size, height: size, borderRadius: 999,
    background: color, flexShrink: 0,
    boxShadow: status === 'ok' ? `0 0 0 0 ${color}` : 'none',
  }} />;
}

// Sparkline — minimal SVG, no axes, no chartjunk
function Spark({ data, w = 80, h = 18, color = C.fg, fill = false, strokeWidth = 1 }) {
  if (!data || !data.length) return null;
  const min = Math.min(...data), max = Math.max(...data);
  const range = max - min || 1;
  const step = w / (data.length - 1);
  const pts = data.map((v, i) => `${i * step},${h - ((v - min) / range) * (h - 2) - 1}`).join(' ');
  return (
    <svg width={w} height={h} style={{ display: 'block' }}>
      {fill && <polygon points={`0,${h} ${pts} ${w},${h}`} fill={color} opacity="0.08" />}
      <polyline points={pts} fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

// Stripe chart — 24h activity per butler. Used in v1 + v3.
function StripeChart({ data, hourLabels = true, cellSize = 14, gap = 2, labelWidth = 100 }) {
  const hours = Array.from({ length: 24 }, (_, i) => i);
  return (
    <div style={{ display: 'inline-block', fontFamily: 'var(--font-mono)', fontSize: 10 }}>
      {hourLabels && (
        <div style={{ display: 'flex', alignItems: 'flex-end', height: 14, marginLeft: labelWidth, color: C.mfg }}>
          {hours.map((h) => (
            <div key={h} style={{ width: cellSize + gap, textAlign: 'left', visibility: h % 3 === 0 ? 'visible' : 'hidden' }}>
              {String(h).padStart(2, '0')}
            </div>
          ))}
        </div>
      )}
      {data.map((row) => (
        <div key={row.butler} style={{ display: 'flex', alignItems: 'center', height: cellSize + gap }}>
          <div style={{ width: labelWidth, color: C.fg, fontFamily: 'var(--font-sans)', fontSize: 11, paddingRight: 8, textTransform: 'lowercase', letterSpacing: '0.01em' }}>
            {row.butler}
          </div>
          {row.row.map((v, i) => {
            const intensity = Math.min(1, v / 4);
            const isDark = window.__theme !== 'light';
            const empty = isDark ? 'oklch(1 0 0 / 0.04)' : 'oklch(0 0 0 / 0.04)';
            const filled = isDark
              ? `oklch(0.985 0 0 / ${0.15 + intensity * 0.55})`
              : `oklch(0.18 0 0 / ${0.18 + intensity * 0.55})`;
            return (
              <div key={i} style={{
                width: cellSize, height: cellSize, marginRight: gap,
                background: v === 0 ? empty : filled,
                borderRadius: 1.5,
              }} />
            );
          })}
        </div>
      ))}
    </div>
  );
}

// "Now" — clock + greeting
function NowMark({ now, greeting }) {
  const t = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  const d = now.toLocaleDateString([], { weekday: 'long', month: 'short', day: 'numeric' });
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, fontFamily: 'var(--font-mono)' }}>
      <span style={{ fontSize: 13, color: C.fg, fontWeight: 500 }} className="tnum">{t}</span>
      <span style={{ fontSize: 11, color: C.mfg }}>{d}</span>
    </div>
  );
}

// Severity glyph — one of three states, no emoji
function Sev({ level, size = 6 }) {
  const m = { high: C.red, medium: C.amber, low: C.mfg };
  return (
    <span style={{
      width: size, height: size, borderRadius: 1,
      background: m[level] || C.mfg, display: 'inline-block', flexShrink: 0,
    }} />
  );
}

Object.assign(window, { C, ButlerMark, StatusDot, Spark, StripeChart, NowMark, Sev });
