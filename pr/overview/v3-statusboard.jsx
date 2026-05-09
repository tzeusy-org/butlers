// V3 — Status board. Glanceable across the room.
// Big colored signals dominate. KPIs in giant numerals. A 4×N tile grid for butlers.

function V3StatusBoard({ density = 'comfortable' }) {
  const d = window.BUTLERS_DATA;
  const compact = density === 'compact';
  const gap = compact ? 10 : 14;

  // Highest-priority attention item — the "headline alert"
  const lead = d.attention[0];

  return (
    <div style={{
      width: '100%', height: '100%', background: C.bgDeep, color: C.fg,
      fontFamily: 'var(--font-sans)', overflow: 'auto', padding: compact ? 16 : 24,
    }}>
      {/* Top bar — static system status */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 16,
        marginBottom: 18,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{
            width: 10, height: 10, borderRadius: 999, background: C.amber,
          }} />
          <span style={{
            fontWeight: 600, letterSpacing: '-0.01em', fontSize: 14,
          }}>SYSTEM · DEGRADED</span>
        </div>
        <span style={{ color: C.mfg, fontSize: 12 }}>1 butler paused · 2 need you</span>
        <div style={{ flex: 1 }} />
        <NowMark now={d.now} greeting={d.user.greeting} />
      </div>

      {/* Lead alert — full-width, impossible to miss */}
      <a href={`#${lead.id}`} style={{
        display: 'grid',
        gridTemplateColumns: 'auto 1fr auto',
        gap: 18, alignItems: 'center',
        padding: compact ? '14px 18px' : '20px 22px',
        background: 'oklch(0.30 0.10 29.2 / 0.20)',
        border: `1px solid ${C.red}`,
        marginBottom: gap, color: C.fg, textDecoration: 'none',
      }}>
        <div style={{
          width: 8, height: 50, background: C.red,
        }} />
        <div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 10,
            color: C.red, letterSpacing: '0.12em', textTransform: 'uppercase',
          }}>Action required · {lead.age}</div>
          <div style={{ fontSize: 22, fontWeight: 600, letterSpacing: '-0.01em', marginTop: 2 }}>
            {lead.title}
          </div>
          <div style={{ color: C.mfg, fontSize: 13, marginTop: 2 }}>
            {lead.detail}
          </div>
        </div>
        <div style={{
          background: C.red, color: '#fff', padding: '12px 22px',
          fontWeight: 600, fontSize: 14, borderRadius: 4,
        }}>{lead.action} →</div>
      </a>

      {/* KPI mega-tiles */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(4, 1fr)',
        gap, marginBottom: gap,
      }}>
        {[
          { label: 'sessions',  value: d.kpis.sessionsToday.value, sub: 'today', delta: '+18%', spark: d.kpis.sessionsToday.sparkline },
          { label: 'cost',      value: '$' + d.kpis.costToday.value.toFixed(2), sub: 'today', delta: '−4%', spark: d.kpis.costToday.sparkline, deltaTone: C.green },
          { label: 'moments',   value: d.kpis.momentsLogged.value, sub: 'indexed', delta: '+9%', spark: d.kpis.momentsLogged.sparkline },
          { label: 'awaiting',  value: d.attention.length, sub: 'you', delta: 'oldest 1d', deltaTone: C.amber, big: true },
        ].map((k, i) => (
          <div key={i} style={{
            background: C.bgElev, border: `1px solid ${C.border}`,
            padding: compact ? 14 : 18,
            display: 'flex', flexDirection: 'column', justifyContent: 'space-between',
            minHeight: compact ? 110 : 130,
          }}>
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
              textTransform: 'uppercase', letterSpacing: '0.1em',
            }}>{k.label}</div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
              <span className="tnum" style={{
                fontSize: compact ? 38 : 50, fontWeight: 600,
                letterSpacing: '-0.04em', lineHeight: 0.95,
              }}>{k.value}</span>
              <span style={{ color: C.mfg, fontSize: 12 }}>{k.sub}</span>
            </div>
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              fontFamily: 'var(--font-mono)', fontSize: 10,
              color: k.deltaTone || C.green,
            }}>
              <span>{k.delta}</span>
              {k.spark && <Spark data={k.spark} w={80} h={16} color={C.fg} />}
            </div>
          </div>
        ))}
      </div>

      {/* Butler tile grid */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(4, 1fr)',
        gap, marginBottom: gap,
      }}>
        {d.butlers.map((b) => (
          <a key={b.name} href={`/butlers/${b.name}`} style={{
            background: C.bgElev,
            border: `1px solid ${b.status === 'degraded' ? C.amber : b.status === 'error' ? C.red : C.border}`,
            padding: compact ? 12 : 14,
            color: C.fg, textDecoration: 'none',
            display: 'flex', flexDirection: 'column', gap: 8,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <ButlerMark name={b.name} size={20} tone="fill" />
              <span style={{
                fontWeight: 600, fontSize: 14, letterSpacing: '-0.01em',
                textTransform: 'capitalize', flex: 1,
              }}>{b.label}</span>
              <StatusDot status={b.status} size={8} />
            </div>
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
              textTransform: 'uppercase', letterSpacing: '0.06em',
              minHeight: 12,
            }}>{b.activity}</div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
              <span className="tnum" style={{ fontSize: 22, fontWeight: 600, letterSpacing: '-0.02em' }}>
                {b.sessions24h}
              </span>
              <span style={{ color: C.mfg, fontSize: 10, fontFamily: 'var(--font-mono)' }}>
                /24h · ${b.costToday.toFixed(2)}
              </span>
            </div>
            <div style={{ height: 3, background: 'oklch(1 0 0 / 0.06)', borderRadius: 1, marginTop: 'auto' }}>
              <div style={{
                width: `${b.loadPct}%`, height: '100%',
                background: b.loadPct > 60 ? C.amber : C.fg,
                borderRadius: 1,
              }} />
            </div>
          </a>
        ))}
      </div>

      {/* Bottom: stripe + queue */}
      <div style={{
        display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap,
      }}>
        <div style={{
          background: C.bgElev, border: `1px solid ${C.border}`,
          padding: compact ? 14 : 18,
        }}>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
            textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 10,
          }}>24h activity</div>
          <StripeChart data={d.sessionGrid} cellSize={compact ? 12 : 14} gap={2} labelWidth={88} />
        </div>

        <div style={{
          background: C.bgElev, border: `1px solid ${C.border}`,
        }}>
          <div style={{
            padding: '12px 14px', borderBottom: `1px solid ${C.border}`,
            fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
            textTransform: 'uppercase', letterSpacing: '0.1em',
          }}>Queue · {d.attention.length}</div>
          {d.attention.slice(1).map((a, i) => (
            <a key={a.id} href={`#${a.id}`} style={{
              display: 'grid', gridTemplateColumns: '8px 1fr auto',
              gap: 10, alignItems: 'center',
              padding: '10px 14px',
              borderBottom: i < d.attention.length - 2 ? `1px solid ${C.borderSoft}` : 'none',
              color: C.fg, textDecoration: 'none',
            }}>
              <Sev level={a.severity} size={8} />
              <div>
                <div style={{ fontWeight: 500, fontSize: 13 }}>{a.title}</div>
                <div style={{ color: C.mfg, fontSize: 11, marginTop: 1 }}>{a.detail}</div>
              </div>
              <span style={{ color: C.dim, fontFamily: 'var(--font-mono)', fontSize: 10 }} className="tnum">
                {a.age}
              </span>
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}

window.V3StatusBoard = V3StatusBoard;
