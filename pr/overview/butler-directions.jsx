// Four non-editorial directions for the /butlers/ page.
// Each is a self-contained mockup; the design canvas hosts them side-by-side.
//
// Shared rules (from Dispatch language): hue only on letter-mark, state color
// only when state demands, surfaces not cards, hairline rules for structure,
// tabular numerals everywhere, no decorative SVG.

const D = window.BUTLERS_DATA;
const ROLE = {
  relationship: 'contacts, drafts, warmth',
  health: 'measurements, movement, sleep',
  calendar: 'mirrors and schedules',
  qa: 'patrols and investigates',
  memory: 'short → mid → long term',
  education: 'reviews and prompts',
  chronicler: 'reconstructs the day',
  household: 'orders, schedules, restocks',
};
const ACTIVE = new Set(['running', 'patrol', 'consolidating', 'ingesting']);

const stripeOf = (name) => (D.sessionGrid.find((s) => s.butler === name) || {}).row || Array(24).fill(0);

const isDark = () => window.__theme !== 'light';
const palDark = {
  bg: 'oklch(0.145 0 0)', surface: 'oklch(0.165 0 0)', deep: 'oklch(0.115 0 0)',
  fg: 'oklch(0.985 0 0)', mfg: 'oklch(0.708 0 0)', dim: 'oklch(0.55 0 0)',
  border: 'oklch(1 0 0 / 0.10)', borderSoft: 'oklch(1 0 0 / 0.06)', borderStrong: 'oklch(1 0 0 / 0.18)',
  red: 'oklch(0.685 0.250 29.2)', amber: 'oklch(0.810 0.185 84.0)', green: 'oklch(0.790 0.195 148.2)',
};

const cap = (s) => s ? s[0].toUpperCase() + s.slice(1) : s;
const stateColor = (b, P) => {
  if (b.status === 'degraded' || b.activity === 'paused') return P.red;
  if (b.activity === 'awaiting approval' || b.status === 'waiting') return P.amber;
  if (ACTIVE.has(b.activity)) return P.green;
  return P.dim;
};

// ─── Variant A · Status Board ────────────────────────────────────────────
// 4×2 panel grid, NOC-style. Everything visible at once. No prose. The
// hairline grid IS the chrome — no card backgrounds, no shadows.

function StatusBoardVariant() {
  const P = palDark;
  const cells = [...D.butlers].sort((a, b) => b.sessions24h - a.sessions24h);
  return (
    <div style={{
      width: '100%', height: '100%', background: P.bg, color: P.fg,
      fontFamily: 'var(--font-sans)', padding: '24px 28px',
      display: 'flex', flexDirection: 'column', boxSizing: 'border-box',
    }}>
      <BoardHeader title="Status board" sub="all eight, at a glance" P={P} />
      <div style={{
        flex: 1, display: 'grid',
        gridTemplateColumns: 'repeat(4, 1fr)', gridTemplateRows: 'repeat(2, 1fr)',
        border: `1px solid ${P.border}`, marginTop: 18,
      }}>
        {cells.map((b, i) => (
          <BoardCell key={b.name} b={b} i={i} P={P} />
        ))}
      </div>
      <BoardFooter P={P} />
    </div>
  );
}

function BoardCell({ b, i, P }) {
  const col = i % 4, row = Math.floor(i / 4);
  const tone = stateColor(b, P);
  const stripe = stripeOf(b.name);
  const totalSpark = b.sessions24h;
  const isActive = ACTIVE.has(b.activity);
  return (
    <div style={{
      borderRight: col < 3 ? `1px solid ${P.border}` : 'none',
      borderBottom: row < 1 ? `1px solid ${P.border}` : 'none',
      padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 12,
      position: 'relative', minHeight: 0,
    }}>
      {/* Top: mark + label + state pip */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <ButlerMark name={b.name} size={22} tone={isActive ? 'fill' : 'neutral'} />
        <span style={{ fontSize: 14, fontWeight: 500, textTransform: 'capitalize', flex: 1 }}>{b.label}</span>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.14em',
          textTransform: 'uppercase', color: tone,
        }}>{b.activity}</span>
      </div>

      {/* Mid: KPI quartet */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 8 }}>
        {[
          { k: 'sessions', v: b.sessions24h, m: '24h' },
          { k: 'spend',    v: '$' + b.costToday.toFixed(2), m: 'today' },
          { k: 'load',     v: b.loadPct + '%', m: 'now' },
          { k: 'last',     v: b.lastRun, m: '' },
        ].map((kpi, j) => (
          <div key={j}>
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: 8, letterSpacing: '0.14em',
              textTransform: 'uppercase', color: P.mfg, marginBottom: 2,
            }}>{kpi.k}</div>
            <div className="tnum" style={{
              fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 500, color: P.fg,
            }}>{kpi.v} <span style={{ color: P.dim, fontSize: 9, marginLeft: 2 }}>{kpi.m}</span></div>
          </div>
        ))}
      </div>

      {/* Bottom: 24h stripe */}
      <div style={{ marginTop: 'auto' }}>
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 8, letterSpacing: '0.14em',
          textTransform: 'uppercase', color: P.dim, marginBottom: 4,
        }}>24h activity</div>
        <div style={{ display: 'flex', gap: 1, height: 16 }}>
          {stripe.map((v, k) => {
            const on = v / 4;
            return (
              <div key={k} style={{
                flex: 1,
                background: v === 0 ? 'oklch(1 0 0 / 0.04)' : `oklch(0.985 0 0 / ${0.18 + on * 0.55})`,
                borderRadius: 1,
              }} />
            );
          })}
        </div>
      </div>

      {/* State color rail (left edge) — only when state demands */}
      {b.status !== 'ok' && (
        <div style={{
          position: 'absolute', left: 0, top: 0, bottom: 0, width: 2,
          background: tone,
        }} />
      )}
    </div>
  );
}

function BoardHeader({ title, sub, P }) {
  const t = D.now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
      <span style={{
        fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.14em',
        textTransform: 'uppercase', color: P.mfg,
      }}>Butlers · {title}</span>
      <span style={{ color: P.dim, fontSize: 12 }}>{sub}</span>
      <span style={{ flex: 1 }} />
      <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: P.fg }}>{t}</span>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: P.mfg }}>local</span>
    </div>
  );
}
function BoardFooter({ P }) {
  const ok = D.butlers.filter((b) => b.status === 'ok').length;
  return (
    <div style={{
      marginTop: 12, paddingTop: 10, borderTop: `1px solid ${P.borderSoft}`,
      display: 'flex', gap: 18, alignItems: 'baseline',
      fontFamily: 'var(--font-mono)', fontSize: 10, color: P.mfg, letterSpacing: '0.08em',
    }}>
      <span>● {ok}/{D.butlers.length} healthy</span>
      <span>● {D.butlers.reduce((s, b) => s + b.sessions24h, 0)} sessions</span>
      <span>● ${D.butlers.reduce((s, b) => s + b.costToday, 0).toFixed(2)} spend</span>
      <span style={{ flex: 1 }} />
      <span>refreshed every 5s</span>
    </div>
  );
}

// ─── Variant B · Swim Lanes ──────────────────────────────────────────────
// Time-first. Each butler is a horizontal lane; today's activity reads
// left→right. Vertical "now" line pins the present.

function SwimLanesVariant() {
  const P = palDark;
  const ordered = [...D.butlers].sort((a, b) => b.sessions24h - a.sessions24h);
  const nowH = D.now.getHours() + D.now.getMinutes() / 60;
  return (
    <div style={{
      width: '100%', height: '100%', background: P.bg, color: P.fg,
      fontFamily: 'var(--font-sans)', padding: '24px 28px',
      display: 'flex', flexDirection: 'column', boxSizing: 'border-box',
    }}>
      <BoardHeader title="Today, by lane" sub="00:00 → 24:00 · activity intensity" P={P} />

      {/* Hour ruler */}
      <div style={{
        display: 'grid', gridTemplateColumns: '180px 1fr 90px',
        gap: 16, marginTop: 22, paddingBottom: 8,
        borderBottom: `1px solid ${P.border}`,
      }}>
        <div />
        <div style={{ position: 'relative', height: 14 }}>
          {[0, 3, 6, 9, 12, 15, 18, 21, 24].map((h) => (
            <span key={h} style={{
              position: 'absolute', left: `${(h / 24) * 100}%`, transform: 'translateX(-50%)',
              fontFamily: 'var(--font-mono)', fontSize: 9, color: P.dim, letterSpacing: '0.08em',
            }}>{String(h).padStart(2, '0')}</span>
          ))}
        </div>
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 9, color: P.dim,
          textAlign: 'right', letterSpacing: '0.14em', textTransform: 'uppercase',
        }}>sessions</div>
      </div>

      {/* Lanes */}
      <div style={{ flex: 1, position: 'relative', display: 'flex', flexDirection: 'column' }}>
        {/* Now line */}
        <div style={{
          position: 'absolute',
          left: `calc(180px + 16px + ${(nowH / 24) * 100}% * (100% - 180px - 90px - 32px) / 100%)`,
          top: 0, bottom: 0, width: 1, background: P.amber, opacity: 0.6, pointerEvents: 'none',
        }} />

        {ordered.map((b, i) => (
          <Lane key={b.name} b={b} P={P} last={i === ordered.length - 1} nowH={nowH} />
        ))}
      </div>
    </div>
  );
}

function Lane({ b, P, last, nowH }) {
  const stripe = stripeOf(b.name);
  const tone = stateColor(b, P);
  const isActive = ACTIVE.has(b.activity);
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '180px 1fr 90px',
      gap: 16, padding: '14px 0', alignItems: 'center',
      borderBottom: last ? 'none' : `1px solid ${P.borderSoft}`,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <ButlerMark name={b.name} size={20} tone={isActive ? 'fill' : 'neutral'} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 500, textTransform: 'capitalize' }}>{b.label}</div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.12em',
            textTransform: 'uppercase', color: tone, marginTop: 2,
          }}>{b.activity}</div>
        </div>
      </div>

      {/* Activity bars across 24h */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 1, height: 26 }}>
        {stripe.map((v, k) => {
          const on = v / 4;
          const h = v === 0 ? 2 : 4 + on * 22;
          const past = (k + 1) <= nowH;
          return (
            <div key={k} style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <div style={{
                width: '100%', height: h,
                background: v === 0 ? 'oklch(1 0 0 / 0.06)'
                  : `oklch(0.985 0 0 / ${past ? 0.20 + on * 0.55 : 0.10 + on * 0.25})`,
                borderRadius: 1,
              }} />
            </div>
          );
        })}
      </div>

      <div className="tnum" style={{
        fontFamily: 'var(--font-mono)', fontSize: 12, color: P.fg, textAlign: 'right',
      }}>
        {b.sessions24h}
        <div style={{ fontSize: 10, color: P.dim, marginTop: 2 }}>${b.costToday.toFixed(2)}</div>
      </div>
    </div>
  );
}

// ─── Variant C · Master/Detail ───────────────────────────────────────────
// Mail.app three-pane. Click any butler on the left to load the right pane.
// State color appears only on the selected butler's hero.

function MasterDetailVariant() {
  const P = palDark;
  const [selected, setSelected] = React.useState('health');
  const ordered = [...D.butlers].sort((a, b) => a.label.localeCompare(b.label));
  const sel = D.butlers.find((b) => b.name === selected) || D.butlers[0];

  return (
    <div style={{
      width: '100%', height: '100%', background: P.bg, color: P.fg,
      fontFamily: 'var(--font-sans)', display: 'grid',
      gridTemplateColumns: '300px 1fr', boxSizing: 'border-box',
    }}>
      {/* Master */}
      <div style={{
        background: P.deep, borderRight: `1px solid ${P.border}`,
        padding: '24px 0', overflowY: 'auto', minHeight: 0,
      }}>
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.14em',
          textTransform: 'uppercase', color: P.mfg, padding: '0 20px 12px',
          borderBottom: `1px solid ${P.borderSoft}`, marginBottom: 6,
        }}>The staff · {D.butlers.length}</div>
        {ordered.map((b) => {
          const active = b.name === selected;
          const tone = stateColor(b, P);
          const isActive = ACTIVE.has(b.activity);
          return (
            <button key={b.name} onClick={() => setSelected(b.name)} style={{
              display: 'grid', gridTemplateColumns: '24px 1fr auto',
              gap: 12, alignItems: 'center', width: '100%',
              padding: '12px 20px', background: active ? 'oklch(1 0 0 / 0.06)' : 'transparent',
              border: 'none', borderLeft: active ? `2px solid ${P.fg}` : '2px solid transparent',
              color: P.fg, textAlign: 'left', cursor: 'pointer',
              fontFamily: 'inherit',
            }}>
              <ButlerMark name={b.name} size={22} tone={active || isActive ? 'fill' : 'neutral'} />
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 13, textTransform: 'capitalize', color: P.fg }}>{b.label}</div>
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.10em',
                  color: tone, textTransform: 'uppercase', marginTop: 1,
                }}>{b.activity}</div>
              </div>
              <span className="tnum" style={{
                fontFamily: 'var(--font-mono)', fontSize: 11, color: P.dim,
              }}>{b.sessions24h}</span>
            </button>
          );
        })}
      </div>

      {/* Detail */}
      <div style={{ padding: '32px 36px', overflowY: 'auto', minHeight: 0 }}>
        <DetailPane b={sel} P={P} />
      </div>
    </div>
  );
}

function DetailPane({ b, P }) {
  const stripe = stripeOf(b.name);
  const tone = stateColor(b, P);
  const isActive = ACTIVE.has(b.activity);
  const recent = D.feed.filter((f) => f.butler === b.name).slice(0, 4);
  return (
    <div>
      {/* Hero */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 18, marginBottom: 24 }}>
        <ButlerMark name={b.name} size={56} tone={isActive ? 'fill' : 'neutral'} />
        <div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 10, color: P.mfg,
            letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 4,
          }}>Butler · {b.name}</div>
          <div style={{
            fontSize: 28, fontWeight: 500, letterSpacing: '-0.02em',
            textTransform: 'capitalize',
          }}>{b.label}</div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.10em',
            textTransform: 'uppercase', color: tone, marginTop: 6,
          }}>● {b.activity} · last run {b.lastRun}</div>
        </div>
        <div style={{ flex: 1 }} />
        <button style={{
          background: P.fg, color: P.bg, border: 'none', padding: '8px 16px',
          fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.06em',
          cursor: 'pointer', borderRadius: 3,
        }}>Open logs →</button>
      </div>

      {/* KPI strip */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)',
        borderTop: `1px solid ${P.border}`, borderBottom: `1px solid ${P.border}`,
        marginBottom: 28,
      }}>
        {[
          { l: 'sessions · 24h', v: b.sessions24h },
          { l: 'spend · today', v: '$' + b.costToday.toFixed(2) },
          { l: 'load · now', v: b.loadPct + '%' },
          { l: 'port', v: '847' + (b.name.charCodeAt(0) % 10) },
        ].map((k, i) => (
          <div key={i} style={{
            padding: '16px 0',
            paddingLeft: i === 0 ? 0 : 16,
            borderRight: i < 3 ? `1px solid ${P.border}` : 'none',
          }}>
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: 9, color: P.mfg,
              letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 6,
            }}>{k.l}</div>
            <div className="tnum" style={{
              fontSize: 24, fontWeight: 500, letterSpacing: '-0.025em', lineHeight: 1,
            }}>{k.v}</div>
          </div>
        ))}
      </div>

      {/* 24h chart */}
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: 9, color: P.mfg,
        letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 8,
      }}>Activity · 24h</div>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 56, marginBottom: 28 }}>
        {stripe.map((v, k) => (
          <div key={k} style={{ flex: 1, height: v === 0 ? 4 : 4 + (v / 4) * 48,
            background: v === 0 ? 'oklch(1 0 0 / 0.06)' : `oklch(0.985 0 0 / ${0.20 + (v / 4) * 0.55})`,
            borderRadius: 1,
          }} />
        ))}
      </div>

      {/* Recent feed */}
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: 9, color: P.mfg,
        letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 8,
        paddingBottom: 8, borderBottom: `1px solid ${P.border}`,
      }}>Recent</div>
      {recent.map((e, i) => (
        <div key={i} style={{
          display: 'grid', gridTemplateColumns: '50px 1fr auto',
          gap: 12, padding: '10px 0',
          borderBottom: i < recent.length - 1 ? `1px solid ${P.borderSoft}` : 'none',
        }}>
          <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: P.dim }}>{e.time}</span>
          <span style={{ fontSize: 13 }}>{e.text}</span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: P.mfg, letterSpacing: '0.08em', textTransform: 'uppercase' }}>{e.kind}</span>
        </div>
      ))}
    </div>
  );
}

// ─── Variant D · Directory / Dossiers ────────────────────────────────────
// Two-column directory. Each entry is a dossier card with a left identity
// strip (mark + status word vertically) and a right detail block. No
// chrome — each entry is bordered by hairlines like a paper directory.

function DirectoryVariant() {
  const P = palDark;
  const ordered = [...D.butlers].sort((a, b) => a.label.localeCompare(b.label));
  return (
    <div style={{
      width: '100%', height: '100%', background: P.bg, color: P.fg,
      fontFamily: 'var(--font-sans)', padding: '28px 32px',
      display: 'flex', flexDirection: 'column', boxSizing: 'border-box',
    }}>
      <BoardHeader title="Directory" sub="alphabetical · eight in service" P={P} />

      <div style={{
        marginTop: 22, flex: 1,
        display: 'grid', gridTemplateColumns: '1fr 1fr',
        columnGap: 32, rowGap: 0,
        borderTop: `1px solid ${P.border}`,
      }}>
        {ordered.map((b) => (
          <Dossier key={b.name} b={b} P={P} />
        ))}
      </div>
    </div>
  );
}

function Dossier({ b, P }) {
  const stripe = stripeOf(b.name);
  const tone = stateColor(b, P);
  const isActive = ACTIVE.has(b.activity);
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '76px 1fr',
      gap: 16, padding: '20px 0',
      borderBottom: `1px solid ${P.borderSoft}`,
    }}>
      {/* Identity column */}
      <div style={{
        display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 10,
        paddingRight: 12, borderRight: `1px solid ${P.borderSoft}`,
      }}>
        <ButlerMark name={b.name} size={44} tone={isActive ? 'fill' : 'neutral'} />
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.14em',
          textTransform: 'uppercase', color: tone, lineHeight: 1.3,
        }}>● {b.activity}</div>
      </div>

      {/* Detail column */}
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
          <span style={{
            fontSize: 18, fontWeight: 500, letterSpacing: '-0.015em',
            textTransform: 'capitalize',
          }}>{b.label}</span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: P.dim }}>
            {b.lastRun}
          </span>
        </div>
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 11, color: P.mfg,
          marginTop: 4, letterSpacing: '0.02em',
        }}>{ROLE[b.name]}</div>

        {/* Mini stripe */}
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 1, height: 12, marginTop: 12 }}>
          {stripe.map((v, k) => (
            <div key={k} style={{
              flex: 1, height: v === 0 ? 2 : 2 + (v / 4) * 10,
              background: v === 0 ? 'oklch(1 0 0 / 0.06)' : `oklch(0.985 0 0 / ${0.18 + (v / 4) * 0.55})`,
              borderRadius: 1,
            }} />
          ))}
        </div>

        {/* Numbers row */}
        <div style={{
          display: 'flex', gap: 18, marginTop: 12,
          fontFamily: 'var(--font-mono)', fontSize: 11, color: P.mfg,
          paddingTop: 10, borderTop: `1px solid ${P.borderSoft}`,
        }}>
          <span><span style={{ color: P.dim }}>sess</span> <span className="tnum" style={{ color: P.fg }}>{b.sessions24h}</span></span>
          <span><span style={{ color: P.dim }}>spend</span> <span className="tnum" style={{ color: P.fg }}>${b.costToday.toFixed(2)}</span></span>
          <span><span style={{ color: P.dim }}>load</span> <span className="tnum" style={{ color: P.fg }}>{b.loadPct}%</span></span>
          <span style={{ flex: 1 }} />
          <a href="#" style={{
            color: P.fg, textDecoration: 'underline', textUnderlineOffset: 3,
            textDecorationColor: P.borderStrong, fontFamily: 'var(--font-sans)', fontSize: 12,
          }}>open →</a>
        </div>
      </div>
    </div>
  );
}

window.BUTLER_VARIANTS = {
  StatusBoardVariant, SwimLanesVariant, MasterDetailVariant, DirectoryVariant,
};
