// /butlers/ — Status Board.
// 4×2 panel grid, NOC-style. Everything visible at once. No prose.
// Hairline rules ARE the chrome — no card backgrounds, no shadows.
// Hue lives only on the letter-mark; state color only when state demands.

const ROLE = {
  relationship: 'contacts, drafts, warmth',
  health:       'measurements, movement, sleep',
  calendar:     'mirrors and schedules',
  qa:           'patrols and investigates',
  memory:       'short → mid → long term',
  education:    'reviews and prompts',
  chronicler:   'reconstructs the day',
  household:    'orders, schedules, restocks',
};

const ACTIVE_VERBS = new Set(['running', 'patrol', 'consolidating', 'ingesting']);

function activityColor(b) {
  if (b.status === 'degraded' || b.activity === 'paused') return C.red;
  if (b.activity === 'awaiting approval' || b.status === 'waiting') return C.amber;
  if (ACTIVE_VERBS.has(b.activity)) return C.green;
  return C.dim;
}

const portFor = (name) => '847' + (name.charCodeAt(0) % 10);

// ─── Cell ─────────────────────────────────────────────────────────────────

function BoardCell({ b, stripe, col, row, totalCols, totalRows }) {
  const tone = activityColor(b);
  const isActive = ACTIVE_VERBS.has(b.activity);
  const isPaused = b.status === 'degraded' || b.activity === 'paused';
  const isWaiting = b.status === 'waiting' || b.activity === 'awaiting approval';
  const stateful = isPaused || isWaiting;
  const [hover, setHover] = React.useState(false);
  const isDark = window.__theme !== 'light';

  return (
    <a
      href={`/butlers/${b.name}`}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        position: 'relative',
        display: 'flex', flexDirection: 'column', gap: 14,
        padding: '20px 22px',
        borderRight: col < totalCols - 1 ? `1px solid ${C.border}` : 'none',
        borderBottom: row < totalRows - 1 ? `1px solid ${C.border}` : 'none',
        background: hover
          ? (isDark ? 'oklch(1 0 0 / 0.025)' : 'oklch(0 0 0 / 0.02)')
          : 'transparent',
        color: C.fg, textDecoration: 'none',
        transition: 'background 120ms ease',
        minHeight: 220,
      }}
    >
      {/* State color rail (left edge) — only when state demands */}
      {stateful && (
        <div style={{
          position: 'absolute', left: 0, top: 0, bottom: 0, width: 2,
          background: tone,
        }} />
      )}

      {/* Header row: mark + label + activity */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <ButlerMark name={b.name} size={28} tone={isActive ? 'fill' : 'neutral'} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{
            fontSize: 16, fontWeight: 500, letterSpacing: '-0.015em',
            textTransform: 'capitalize',
          }}>{b.label}</div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 9.5, color: C.dim,
            letterSpacing: '0.06em', marginTop: 2,
          }}>:{portFor(b.name)} · {ROLE[b.name]}</div>
        </div>
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 9.5, letterSpacing: '0.14em',
          textTransform: 'uppercase', color: tone, whiteSpace: 'nowrap',
        }}>● {b.activity}</div>
      </div>

      {/* KPI quartet */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)',
        gap: 10,
        paddingTop: 12, paddingBottom: 4,
        borderTop: `1px solid ${C.borderSoft}`,
      }}>
        {[
          { k: 'sess · 24h', v: b.sessions24h },
          { k: 'spend',      v: '$' + b.costToday.toFixed(2) },
          { k: 'load',       v: b.loadPct + '%' },
          { k: 'last',       v: b.lastRun.replace(' ago', '') },
        ].map((kpi, j) => (
          <div key={j}>
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: 8.5, letterSpacing: '0.14em',
              textTransform: 'uppercase', color: C.mfg, marginBottom: 4,
            }}>{kpi.k}</div>
            <div className="tnum" style={{
              fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 500, color: C.fg,
              letterSpacing: '-0.01em',
            }}>{kpi.v}</div>
          </div>
        ))}
      </div>

      {/* 24h stripe */}
      <div style={{ marginTop: 'auto' }}>
        <div style={{
          display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
          marginBottom: 6,
        }}>
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: 8.5, letterSpacing: '0.14em',
            textTransform: 'uppercase', color: C.dim,
          }}>24h activity</span>
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: 8.5, color: C.dim,
            letterSpacing: '0.06em',
          }}>00 — now</span>
        </div>
        <div style={{ display: 'flex', gap: 1, height: 22 }}>
          {stripe.map((v, k) => {
            const on = v / 4;
            const empty  = isDark ? 'oklch(1 0 0 / 0.05)' : 'oklch(0 0 0 / 0.05)';
            const filled = isDark
              ? `oklch(0.985 0 0 / ${0.20 + on * 0.55})`
              : `oklch(0.18 0 0 / ${0.22 + on * 0.55})`;
            return (
              <div key={k} style={{
                flex: 1,
                background: v === 0 ? empty : filled,
                borderRadius: 1,
              }} />
            );
          })}
        </div>
      </div>

      {/* Hover affordance */}
      <span style={{
        position: 'absolute', right: 16, bottom: 14,
        fontFamily: 'var(--font-mono)', fontSize: 10, color: C.fg,
        letterSpacing: '0.06em', opacity: hover ? 0.85 : 0,
        transition: 'opacity 120ms ease', pointerEvents: 'none',
      }}>open →</span>
    </a>
  );
}

// ─── Header ───────────────────────────────────────────────────────────────

function BoardHeader({ data }) {
  const total = data.butlers.length;
  const healthy = data.butlers.filter((b) => b.status === 'ok').length;
  const allHealthy = healthy === total;
  const t = data.now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  const dateStr = data.now.toLocaleDateString([], {
    weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
  });

  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '1fr auto',
      gap: 24, alignItems: 'baseline',
      padding: '0 28px 18px',
      borderBottom: `1px solid ${C.border}`,
    }}>
      <div>
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.14em',
          textTransform: 'uppercase', color: C.mfg, marginBottom: 8,
        }}>Butlers · Status board</div>
        <div style={{
          display: 'flex', alignItems: 'baseline', gap: 16, flexWrap: 'wrap',
        }}>
          <h1 style={{
            margin: 0, fontSize: 28, fontWeight: 500, letterSpacing: '-0.025em',
          }}>The staff, at a glance</h1>
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: 11, color: C.dim,
            letterSpacing: '0.04em',
          }}>{total} butlers · refreshes every 5s</span>
        </div>
      </div>

      <div style={{
        display: 'flex', alignItems: 'center', gap: 14,
      }}>
        <span style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          border: `1px solid ${C.border}`, padding: '4px 10px', borderRadius: 3,
          fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
          textTransform: 'uppercase', letterSpacing: '0.10em',
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: 999,
            background: allHealthy ? C.green : C.amber,
          }} />
          {healthy}/{total} reporting
        </span>
        <div style={{ textAlign: 'right' }}>
          <div className="tnum" style={{
            fontFamily: 'var(--font-mono)', fontSize: 18, fontWeight: 500,
            color: C.fg, letterSpacing: '-0.02em',
          }}>{t}</div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 9.5, color: C.dim,
            letterSpacing: '0.10em', textTransform: 'uppercase', marginTop: 2,
          }}>{dateStr}</div>
        </div>
      </div>
    </div>
  );
}

// ─── Footer summary band ──────────────────────────────────────────────────

function BoardFooter({ data }) {
  const totalSess = data.butlers.reduce((s, b) => s + b.sessions24h, 0);
  const totalSpend = data.butlers.reduce((s, b) => s + b.costToday, 0);
  const avgLoad = Math.round(data.butlers.reduce((s, b) => s + b.loadPct, 0) / data.butlers.length);
  const paused = data.butlers.filter((b) => b.activity === 'paused' || b.status === 'degraded').length;
  const waiting = data.butlers.filter((b) => b.activity === 'awaiting approval' || b.status === 'waiting').length;
  const active = data.butlers.filter((b) => ACTIVE_VERBS.has(b.activity)).length;

  const stats = [
    { k: 'active',   v: active,      tone: active ? C.green : C.dim },
    { k: 'paused',   v: paused,      tone: paused ? C.red : C.dim },
    { k: 'awaiting', v: waiting,     tone: waiting ? C.amber : C.dim },
    { k: 'sessions · 24h', v: totalSess.toLocaleString() },
    { k: 'spend · today',  v: '$' + totalSpend.toFixed(2) },
    { k: 'avg load',       v: avgLoad + '%' },
  ];

  return (
    <div style={{
      borderTop: `1px solid ${C.border}`,
      padding: '16px 28px',
      display: 'grid',
      gridTemplateColumns: 'repeat(6, 1fr)',
      gap: 16,
    }}>
      {stats.map((s, i) => (
        <div key={i} style={{
          display: 'flex', flexDirection: 'column', gap: 4,
        }}>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.14em',
            textTransform: 'uppercase', color: C.mfg,
          }}>{s.k}</div>
          <div className="tnum" style={{
            fontFamily: 'var(--font-mono)', fontSize: 16, fontWeight: 500, color: C.fg,
            display: 'flex', alignItems: 'center', gap: 8,
          }}>
            {s.tone && (
              <span style={{
                width: 6, height: 6, borderRadius: 999, background: s.tone,
              }} />
            )}
            {s.v}
          </div>
        </div>
      ))}
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────

function ButlersIndex({ data }) {
  const stripeMap = Object.fromEntries(data.sessionGrid.map((s) => [s.butler, s.row]));
  const cells = [...data.butlers].sort((a, b) => b.sessions24h - a.sessions24h);
  const cols = 4;
  const rows = Math.ceil(cells.length / cols);

  return (
    <div style={{
      background: C.bg, color: C.fg, fontFamily: 'var(--font-sans)',
      minHeight: '100%', display: 'flex', flexDirection: 'column',
      padding: '24px 0',
    }}>
      <BoardHeader data={data} />

      <div style={{ flex: 1, padding: '0 28px', display: 'flex', flexDirection: 'column' }}>
        <div style={{
          flex: 1, marginTop: 0,
          display: 'grid',
          gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
          gridAutoRows: 'minmax(220px, auto)',
        }}>
          {cells.map((b, i) => (
            <BoardCell key={b.name}
              b={b}
              stripe={stripeMap[b.name] || Array(24).fill(0)}
              col={i % cols}
              row={Math.floor(i / cols)}
              totalCols={cols}
              totalRows={rows}
            />
          ))}
        </div>
      </div>

      <BoardFooter data={data} />
    </div>
  );
}

window.ButlersIndex = ButlersIndex;
