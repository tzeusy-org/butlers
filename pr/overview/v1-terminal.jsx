// V1 — Terminal / control-tower density.
// Bloomberg vibes: monospace, hairline rules, KPI strip, stripe chart hero,
// dense feed, status grid. No cards-on-cards. Single elevation.

function V1Terminal({ density = 'comfortable' }) {
  const d = window.BUTLERS_DATA;
  const compact = density === 'compact';
  const pad = compact ? 12 : 16;
  const fs = compact ? 11 : 12;

  return (
    <div style={{
      width: '100%', height: '100%', background: C.bg, color: C.fg,
      fontFamily: 'var(--font-sans)', fontSize: fs, overflow: 'hidden',
      display: 'grid', gridTemplateColumns: '64px 1fr', gridTemplateRows: '40px 1fr',
    }}>
      {/* Brand strip */}
      <div style={{
        gridColumn: '1 / -1', gridRow: '1', display: 'flex', alignItems: 'center',
        borderBottom: `1px solid ${C.border}`, padding: '0 16px', gap: 16,
      }}>
        <span style={{ fontWeight: 700, letterSpacing: '-0.01em', fontSize: 14 }}>Butlers</span>
        <span style={{ color: C.dim, fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.08em', textTransform: 'uppercase' }}>Overview · Terminal</span>
        <div style={{ flex: 1 }} />
        <NowMark now={d.now} greeting={d.user.greeting} />
      </div>

      {/* Mini sidebar (representative) */}
      <div style={{
        gridColumn: 1, gridRow: 2, borderRight: `1px solid ${C.border}`,
        display: 'flex', flexDirection: 'column', alignItems: 'center', paddingTop: 12, gap: 6,
      }}>
        {['O','B','M','E','C','S','A','T'].map((ch, i) => (
          <div key={i} style={{
            width: 28, height: 28, borderRadius: 4,
            background: i === 0 ? C.muted : 'transparent',
            color: i === 0 ? C.fg : C.mfg,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 11, fontWeight: 600,
          }}>{ch}</div>
        ))}
      </div>

      {/* Main */}
      <div style={{ gridColumn: 2, gridRow: 2, overflow: 'auto', padding: pad }}>
        {/* HERO ROW: Attention queue (left) + KPI ticker (right) */}
        <div style={{
          display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: pad,
          marginBottom: pad,
        }}>
          {/* Attention queue */}
          <div style={{ border: `1px solid ${C.border}`, background: C.bgElev }}>
            <div style={{
              padding: '8px 12px', borderBottom: `1px solid ${C.border}`,
              display: 'flex', alignItems: 'center', gap: 8,
              fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.08em',
              textTransform: 'uppercase', color: C.mfg,
            }}>
              <Sev level="high" />
              <span>Requires you</span>
              <span style={{ color: C.dim }}>· {d.attention.length}</span>
              <div style={{ flex: 1 }} />
              <span style={{ color: C.dim }}>oldest 1d 3h</span>
            </div>
            {d.attention.map((a) => (
              <a key={a.id} href={`#${a.id}`} style={{
                display: 'grid',
                gridTemplateColumns: '8px 18px 1fr auto auto',
                gap: 10, alignItems: 'center',
                padding: compact ? '8px 12px' : '10px 12px',
                borderBottom: `1px solid ${C.borderSoft}`,
                color: C.fg, textDecoration: 'none',
              }}>
                <Sev level={a.severity} />
                <ButlerMark name={a.butler} size={18} />
                <div>
                  <div style={{ fontWeight: 500 }}>{a.title}</div>
                  <div style={{ color: C.mfg, fontSize: fs - 1, marginTop: 1 }}>{a.detail}</div>
                </div>
                <span style={{ color: C.dim, fontFamily: 'var(--font-mono)', fontSize: 10 }} className="tnum">{a.age}</span>
                <button style={{
                  background: a.severity === 'high' ? C.red : C.muted,
                  color: a.severity === 'high' ? '#fff' : C.fg,
                  border: 'none', padding: '5px 10px', borderRadius: 3,
                  fontSize: fs - 1, fontWeight: 500, cursor: 'pointer',
                }}>{a.action}</button>
              </a>
            ))}
          </div>

          {/* KPI ticker */}
          <div style={{
            border: `1px solid ${C.border}`, background: C.bgElev,
            display: 'grid', gridTemplateColumns: '1fr 1fr', gridAutoRows: '1fr',
          }}>
            {[
              { label: 'sessions today',   value: d.kpis.sessionsToday.value, delta: d.kpis.sessionsToday.delta, spark: d.kpis.sessionsToday.sparkline, deltaColor: C.green },
              { label: 'cost today (USD)', value: d.kpis.costToday.value.toFixed(2), delta: d.kpis.costToday.delta, spark: d.kpis.costToday.sparkline, deltaColor: C.green },
              { label: 'moments logged',   value: d.kpis.momentsLogged.value, delta: d.kpis.momentsLogged.delta, spark: d.kpis.momentsLogged.sparkline, deltaColor: C.green },
              { label: 'butlers healthy',  value: `${d.kpis.healthyButlers.value}/${d.kpis.healthyButlers.total}`, delta: '1 degraded', spark: null, deltaColor: C.amber },
            ].map((k, i) => (
              <div key={i} style={{
                padding: compact ? 10 : 14,
                borderRight: i % 2 === 0 ? `1px solid ${C.borderSoft}` : 'none',
                borderBottom: i < 2 ? `1px solid ${C.borderSoft}` : 'none',
                display: 'flex', flexDirection: 'column', justifyContent: 'space-between',
              }}>
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 9, color: C.mfg,
                  textTransform: 'uppercase', letterSpacing: '0.08em',
                }}>{k.label}</div>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 4 }}>
                  <span className="tnum" style={{
                    fontSize: compact ? 22 : 28, fontWeight: 600,
                    fontFamily: 'var(--font-sans)', letterSpacing: '-0.02em',
                  }}>{k.value}</span>
                  <span style={{ color: k.deltaColor, fontFamily: 'var(--font-mono)', fontSize: 10 }}>{k.delta}</span>
                </div>
                {k.spark && <Spark data={k.spark} w={140} h={20} color={C.fg} fill />}
              </div>
            ))}
          </div>
        </div>

        {/* Stripe chart — 24h activity */}
        <div style={{
          border: `1px solid ${C.border}`, background: C.bgElev, padding: pad, marginBottom: pad,
        }}>
          <div style={{
            display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 10,
          }}>
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
              textTransform: 'uppercase', letterSpacing: '0.08em',
            }}>Sessions · last 24h</span>
            <span style={{ color: C.dim, fontSize: 11 }}>local time</span>
            <div style={{ flex: 1 }} />
            <span style={{ color: C.dim, fontSize: 11, fontFamily: 'var(--font-mono)' }} className="tnum">
              {d.kpis.sessionsToday.value} total
            </span>
          </div>
          <StripeChart data={d.sessionGrid} cellSize={compact ? 12 : 14} gap={2} labelWidth={92} />
        </div>

        {/* Two-col bottom: Feed + Butler grid */}
        <div style={{ display: 'grid', gridTemplateColumns: '1.3fr 1fr', gap: pad }}>
          {/* Feed */}
          <div style={{ border: `1px solid ${C.border}`, background: C.bgElev }}>
            <div style={{
              padding: '8px 12px', borderBottom: `1px solid ${C.border}`,
              fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
              textTransform: 'uppercase', letterSpacing: '0.08em',
            }}>Activity feed · last 6h</div>
            {d.feed.slice(0, 10).map((f, i) => (
              <a key={i} href="#feed" style={{
                display: 'grid',
                gridTemplateColumns: '54px 18px 1fr auto',
                gap: 10, alignItems: 'baseline',
                padding: compact ? '6px 12px' : '8px 12px',
                borderBottom: i < 9 ? `1px solid ${C.borderSoft}` : 'none',
                color: C.fg, textDecoration: 'none',
              }}>
                <span style={{ color: C.dim, fontFamily: 'var(--font-mono)', fontSize: 10 }} className="tnum">{f.time}</span>
                <ButlerMark name={f.butler} size={16} />
                <div>
                  <span style={{
                    color: f.kind === 'error' ? C.red : f.kind === 'awaiting' || f.kind === 'draft' ? C.amber : C.fg,
                  }}>{f.text}</span>
                  {f.meta && <span style={{ color: C.dim, marginLeft: 8 }}>· {f.meta}</span>}
                </div>
                <span style={{ color: C.dim, fontFamily: 'var(--font-mono)', fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                  {f.kind}
                </span>
              </a>
            ))}
          </div>

          {/* Butler grid */}
          <div style={{ border: `1px solid ${C.border}`, background: C.bgElev }}>
            <div style={{
              padding: '8px 12px', borderBottom: `1px solid ${C.border}`,
              fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
              textTransform: 'uppercase', letterSpacing: '0.08em',
            }}>Butlers · 8</div>
            {d.butlers.map((b, i) => (
              <a key={b.name} href={`/butlers/${b.name}`} style={{
                display: 'grid',
                gridTemplateColumns: '6px 18px 1fr 60px 50px 50px',
                gap: 8, alignItems: 'center',
                padding: compact ? '6px 12px' : '8px 12px',
                borderBottom: i < d.butlers.length - 1 ? `1px solid ${C.borderSoft}` : 'none',
                color: C.fg, textDecoration: 'none',
                fontSize: fs,
              }}>
                <StatusDot status={b.status} />
                <ButlerMark name={b.name} size={16} />
                <div>
                  <div style={{ fontWeight: 500, textTransform: 'capitalize' }}>{b.label}</div>
                  <div style={{ color: C.dim, fontSize: 10, fontFamily: 'var(--font-mono)' }}>{b.activity}</div>
                </div>
                <span className="tnum" style={{ color: C.mfg, fontFamily: 'var(--font-mono)', fontSize: 10, textAlign: 'right' }}>
                  {b.sessions24h}
                </span>
                <span className="tnum" style={{ color: C.mfg, fontFamily: 'var(--font-mono)', fontSize: 10, textAlign: 'right' }}>
                  ${b.costToday.toFixed(2)}
                </span>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <div style={{ flex: 1, height: 3, background: 'oklch(1 0 0 / 0.06)', borderRadius: 1 }}>
                    <div style={{
                      width: `${b.loadPct}%`, height: '100%',
                      background: b.loadPct > 60 ? C.amber : C.fg,
                      borderRadius: 1,
                    }} />
                  </div>
                </div>
              </a>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

window.V1Terminal = V1Terminal;
