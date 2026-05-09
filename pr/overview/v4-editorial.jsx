// V4 with live briefing + backend integration sketch shown below.

function V4WithBriefing({ data, useLLM }) {
  const d = data;
  const briefing = window.useBriefing(d, { useLLM });

  return (
    <div style={{ background: C.bg, color: C.fg, fontFamily: 'var(--font-sans)' }}>
      <div style={{
        display: 'grid',
        gridTemplateColumns: '1.4fr 1fr',
        gap: 56,
        padding: '48px 56px',
        maxWidth: 1280, margin: '0 auto',
      }}>
        {/* LEFT */}
        <div>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 12,
            fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
            textTransform: 'uppercase', letterSpacing: '0.14em', marginBottom: 16,
          }}>
            <span>Overview · {d.now.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })} · {d.now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })}</span>
            <span style={{ flex: 1 }} />
            <BriefingStatus status={briefing.status} onRefresh={briefing.refresh} />
          </div>

          <h1 style={{
            fontFamily: 'var(--font-sans)', fontWeight: 500,
            fontSize: 44, lineHeight: 1.08, letterSpacing: '-0.025em',
            margin: 0, marginBottom: 18, maxWidth: '14ch',
          }}>
            <span style={{ color: C.mfg }}>{briefing.headline.greet}</span><br/>
            {briefing.headline.body}
          </h1>

          <div style={{
            fontFamily: 'var(--font-serif)', fontSize: 16, lineHeight: 1.6,
            color: C.mfg, maxWidth: '50ch', marginBottom: 36, minHeight: 60,
            opacity: briefing.status === 'loading' ? 0.4 : 1,
            transition: 'opacity 200ms cubic-bezier(0.22, 1, 0.36, 1)',
          }}>
            {briefing.elaboration}
          </div>

          {/* Attention items */}
          <div style={{ borderTop: `1px solid ${C.border}`, marginBottom: 36 }}>
            {d.attention.length === 0 ? (
              <div style={{
                padding: '40px 0', color: C.dim, fontSize: 13,
                fontFamily: 'var(--font-serif)', fontStyle: 'italic',
              }}>Nothing waiting.</div>
            ) : d.attention.map((a) => (
              <a key={a.id} href={`#${a.id}`} style={{
                display: 'grid',
                gridTemplateColumns: '24px 1fr auto',
                gap: 18, padding: '18px 0',
                borderBottom: `1px solid ${C.border}`,
                color: C.fg, textDecoration: 'none', alignItems: 'baseline',
              }}>
                <span style={{
                  fontFamily: 'var(--font-mono)', fontSize: 11,
                  color: a.severity === 'high' ? C.red : a.severity === 'medium' ? C.amber : C.dim,
                  textTransform: 'uppercase', letterSpacing: '0.06em',
                }}>{a.kind === 'reauth' ? '⚠' : '◇'}</span>
                <div>
                  <div style={{ fontSize: 17, fontWeight: 500, letterSpacing: '-0.01em' }}>{a.title}</div>
                  <div style={{ color: C.mfg, fontSize: 13, marginTop: 4, fontFamily: 'var(--font-serif)' }}>
                    {a.detail} · {a.butler} butler · {a.age}
                  </div>
                </div>
                <span style={{
                  color: C.fg, textDecoration: 'underline',
                  textUnderlineOffset: 4, textDecorationColor: C.borderStrong,
                  fontWeight: 500, fontSize: 14, whiteSpace: 'nowrap',
                }}>{a.action} →</span>
              </a>
            ))}
          </div>

          {/* KPIs */}
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
            textTransform: 'uppercase', letterSpacing: '0.14em', marginBottom: 14,
          }}>Today, in numbers</div>
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)',
            borderTop: `1px solid ${C.border}`,
            borderBottom: `1px solid ${C.border}`,
          }}>
            {[
              { label: 'sessions', value: d.kpis.sessionsToday.value, delta: '+18%' },
              { label: 'spend',    value: '$' + d.kpis.costToday.value.toFixed(2), delta: '−4%' },
              { label: 'moments',  value: d.kpis.momentsLogged.value, delta: '+9%' },
              { label: 'healthy',  value: `${d.butlers.filter(b => b.status === 'ok').length}/${d.butlers.length}`, delta: 'butlers' },
            ].map((k, i) => (
              <div key={i} style={{
                padding: '20px 0',
                borderRight: i < 3 ? `1px solid ${C.border}` : 'none',
                paddingLeft: i === 0 ? 0 : 20,
              }}>
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
                  textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6,
                }}>{k.label}</div>
                <div className="tnum" style={{
                  fontSize: 32, fontWeight: 500, letterSpacing: '-0.03em', lineHeight: 1,
                }}>{k.value}</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg, marginTop: 4 }}>{k.delta}</div>
              </div>
            ))}
          </div>
        </div>

        {/* RIGHT */}
        <div style={{ paddingTop: 8 }}>
          <Section title="Butlers">
            {d.butlers.map((b) => (
              <a key={b.name} href={`/butlers/${b.name}`} style={{
                display: 'grid', gridTemplateColumns: '8px 1fr auto auto',
                gap: 10, padding: '10px 0',
                borderBottom: `1px solid ${C.borderSoft}`,
                color: C.fg, textDecoration: 'none', alignItems: 'baseline',
              }}>
                <StatusDot status={b.status} />
                <span style={{ textTransform: 'capitalize', fontSize: 14 }}>{b.label}</span>
                <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: C.mfg }}>{b.sessions24h}</span>
                <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: C.dim, width: 50, textAlign: 'right' }}>${b.costToday.toFixed(2)}</span>
              </a>
            ))}
          </Section>
          <Section title="Next">
            {d.upcoming.slice(0, 5).map((u, i) => (
              <div key={i} style={{
                display: 'grid', gridTemplateColumns: '50px 1fr auto',
                gap: 10, padding: '8px 0',
                borderBottom: i < 4 ? `1px solid ${C.borderSoft}` : 'none',
                alignItems: 'baseline',
              }}>
                <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: C.dim }}>{u.time}</span>
                <span style={{ fontSize: 13 }}>{u.label}</span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{u.kind}</span>
              </div>
            ))}
          </Section>
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div style={{ marginBottom: 32 }}>
      <div style={{
        display: 'flex', alignItems: 'baseline',
        fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
        textTransform: 'uppercase', letterSpacing: '0.14em',
        marginBottom: 8, paddingBottom: 6, borderBottom: `1px solid ${C.border}`,
      }}><span>{title}</span></div>
      {children}
    </div>
  );
}

function BriefingStatus({ status, onRefresh }) {
  const map = {
    loading:  { dot: C.amber, label: 'composing…' },
    llm:      { dot: C.green, label: 'llm · cached 5m' },
    fallback: { dot: C.dim,   label: 'templated' },
  };
  const cur = map[status] || map.fallback;
  return (
    <button onClick={onRefresh} style={{
      background: 'transparent', border: `1px solid ${C.border}`,
      padding: '3px 8px', borderRadius: 3, cursor: 'pointer',
      display: 'flex', alignItems: 'center', gap: 6,
      fontFamily: 'var(--font-mono)', fontSize: 9,
      color: C.mfg, textTransform: 'uppercase', letterSpacing: '0.08em',
    }}>
      <span style={{ width: 6, height: 6, borderRadius: 999, background: cur.dot }} />
      {cur.label}
      <span style={{ color: C.dim, marginLeft: 4 }}>↻</span>
    </button>
  );
}

window.V4WithBriefing = V4WithBriefing;
window.Section = Section;
