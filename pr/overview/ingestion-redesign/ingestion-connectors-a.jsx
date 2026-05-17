// Connectors · variant A — "The Roster"
//
// Direction: each connector is a hairline row. Left half is identity +
// description; right half is throughput in numbers and a 24h sparkline.
// Auth state is one mono pill on the right; health is a single dot in the
// gutter. Disabled/unconfigured rows fall to the bottom in serif italic.
//
// Goal: a register you can scan at 3am during an outage and see what's
// where, without having to click into anything.

function ConnectorsRoster({ onOpen }) {
  const C = window.C;
  const conns = window.CONNECTOR_DETAILS;
  const live = conns.filter((c) => c.enabled);
  const dormant = conns.filter((c) => !c.enabled);

  const tot = {
    connectors: live.length,
    events24h:  live.reduce((s, c) => s + c.events24h, 0),
    sessions24h: live.reduce((s, c) => s + c.sessions24h, 0),
    cost24h:    live.reduce((s, c) => s + c.cost24h, 0),
  };

  const issues = live.filter((c) => c.auth.status !== 'ok' || c.health !== 'ok');

  return (
    <div style={{ background: C.bg, color: C.fg, minHeight: '100%' }}>
      <div style={{ maxWidth: 1500, margin: '0 auto', padding: '40px 56px 80px' }}>

        <window.PageHeader
          eyebrow={`Ingestion · connectors · ${live.length} live · ${dormant.length} available`}
          title="Where signals come from."
          sub="Every channel the house listens on, with the count, the cadence, and whether the credential is still good. Click a row to manage its scopes, replay, or pause."
          right={(
            <div style={{ textAlign: 'right' }}>
              <Eyebrow>24h volume</Eyebrow>
              <div className="tnum" style={{
                marginTop: 4, fontFamily: 'var(--font-mono)', fontSize: 30,
                fontWeight: 500, letterSpacing: '-0.025em',
              }}>{tot.events24h.toLocaleString()}</div>
              <Mono color={C.dim} size={10}>events · {tot.sessions24h} sessions · {window.fmtCost(tot.cost24h)}</Mono>
            </div>
          )}
        />

        {/* Attention strip (only renders if any issues) */}
        {issues.length > 0 && (
          <div style={{
            marginTop: 24, padding: '14px 0',
            borderTop: `1px solid ${C.border}`, borderBottom: `1px solid ${C.border}`,
            display: 'flex', alignItems: 'baseline', gap: 16, flexWrap: 'wrap',
          }}>
            <Eyebrow>needs attention</Eyebrow>
            <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', flex: 1 }}>
              {issues.map((c) => (
                <a key={c.id} href="#" onClick={(ev) => ev.preventDefault()}
                  style={{
                    display: 'inline-flex', alignItems: 'baseline', gap: 8,
                    textDecoration: 'underline', textDecorationColor: C.borderStrong,
                    textUnderlineOffset: 4, color: C.fg,
                  }}>
                  <ChannelGlyph channel={c.id} size={14} />
                  <span style={{ fontSize: 13, letterSpacing: '-0.005em' }}>{c.label}</span>
                  <Mono size={10} color={authToneColor(c.auth.status)}>{c.auth.status === 'needs_reauth' ? 'reauth · scope drift' : c.auth.status === 'expiring' ? `expires ${c.auth.expires}` : c.note}</Mono>
                </a>
              ))}
            </div>
          </div>
        )}

        {/* Column header */}
        <div style={{
          marginTop: 32,
          display: 'grid',
          gridTemplateColumns: '14px 200px 1fr 140px 130px 90px 80px 90px 28px',
          gap: 16,
          padding: '12px 0 10px',
          borderBottom: `1px solid ${C.border}`,
          fontFamily: 'var(--font-mono)', fontSize: 9.5, color: C.mfg,
          letterSpacing: '0.14em', textTransform: 'uppercase',
        }}>
          <span></span>
          <span>channel</span>
          <span>function</span>
          <span>24h activity</span>
          <span>auth</span>
          <span style={{ textAlign: 'right' }}>events</span>
          <span style={{ textAlign: 'right' }}>sess</span>
          <span style={{ textAlign: 'right' }}>cost</span>
          <span></span>
        </div>

        {/* Live rows */}
        <div>
          {live.map((c) => <ConnectorRow key={c.id} connector={c} onOpen={onOpen} />)}
        </div>

        {/* Dormant section */}
        {dormant.length > 0 && (
          <div style={{ marginTop: 36 }}>
            <Eyebrow style={{ marginBottom: 10 }}>available · not connected</Eyebrow>
            {dormant.map((c) => (
              <div key={c.id} style={{
                display: 'grid',
                gridTemplateColumns: '14px 200px 1fr auto',
                gap: 16, padding: '12px 0',
                borderBottom: `1px solid ${C.borderSoft}`,
                alignItems: 'center',
              }}>
                <span style={{ width: 6, height: 6, borderRadius: 999, background: C.dim, opacity: 0.6 }} />
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <ChannelGlyph channel={c.id} size={16} />
                  <span style={{ fontSize: 13.5, color: C.mfg }}>{c.label}</span>
                </div>
                <div style={{
                  fontFamily: 'var(--font-serif)', fontStyle: 'italic',
                  fontSize: 13, color: C.dim, letterSpacing: 0,
                }}>{c.description}</div>
                <PillBtn>connect →</PillBtn>
              </div>
            ))}
          </div>
        )}

        {/* Footer KPIs */}
        <div style={{
          marginTop: 36, padding: '18px 0 0',
          borderTop: `1px solid ${C.border}`,
          display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 24,
        }}>
          {[
            { k: 'connectors · live', v: tot.connectors },
            { k: 'events · 24h',      v: tot.events24h.toLocaleString() },
            { k: 'filtered · 24h',    v: live.reduce((s, c) => s + c.filtered24h, 0).toLocaleString() },
            { k: 'sessions · 24h',    v: tot.sessions24h },
            { k: 'cost · 24h',        v: window.fmtCost(tot.cost24h) },
          ].map((it, i) => (
            <div key={i}>
              <Eyebrow>{it.k}</Eyebrow>
              <div className="tnum" style={{
                marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 22,
                fontWeight: 500, color: C.fg, letterSpacing: '-0.02em',
              }}>{it.v}</div>
            </div>
          ))}
        </div>

        {/* Bottom action */}
        <div style={{ marginTop: 32, display: 'flex', gap: 10 }}>
          <PillBtn kind="commit">+ add connector</PillBtn>
          <PillBtn>view archived</PillBtn>
        </div>
      </div>
    </div>
  );
}

function ConnectorRow({ connector, onOpen }) {
  const C = window.C;
  const c = connector;
  const max = Math.max(...c.spark24h, 1);
  const auth = c.auth;
  const authTone = authToneColor(auth.status);
  const errored = c.health !== 'ok';

  return (
    <div onClick={() => onOpen && onOpen(c.id)} style={{
      display: 'grid',
      gridTemplateColumns: '14px 200px 1fr 140px 130px 90px 80px 90px 28px',
      gap: 16, padding: '18px 0',
      borderBottom: `1px solid ${C.borderSoft}`,
      alignItems: 'center', position: 'relative',
      cursor: onOpen ? 'pointer' : 'default',
    }}>
      {/* Severity rail for non-ok */}
      {errored && (
        <div style={{
          position: 'absolute', left: -10, top: 0, bottom: 0, width: 2,
          background: auth.status === 'needs_reauth' ? C.red : C.amber,
        }} />
      )}

      {/* Health dot */}
      <span style={{
        width: 6, height: 6, borderRadius: 999,
        background: c.health === 'ok' ? C.green : (auth.status === 'needs_reauth' ? C.red : C.amber),
      }} />

      {/* Channel */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <ChannelGlyph channel={c.id} size={20} />
        <div>
          <div style={{ fontSize: 14, fontWeight: 500, letterSpacing: '-0.01em' }}>{c.label}</div>
          <Mono color={C.dim} size={10}>{c.kind}</Mono>
        </div>
      </div>

      {/* Function (description) */}
      <div style={{ minWidth: 0 }}>
        <div style={{
          fontFamily: 'var(--font-serif)', fontSize: 13.5, color: C.fg,
          lineHeight: 1.4, letterSpacing: 0,
        }}>{c.description}</div>
        <div style={{ marginTop: 4, display: 'flex', alignItems: 'center', gap: 10 }}>
          <Mono color={C.dim} size={10}>last · {c.lastEventAt}</Mono>
          <Mono color={C.dim} size={10}>·</Mono>
          <Mono color={C.dim} size={10}>{c.config.cadence}</Mono>
          <Mono color={C.dim} size={10}>·</Mono>
          <Mono color={C.dim} size={10}>{c.routedPct}% routed</Mono>
        </div>
      </div>

      {/* 24h sparkline */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <Sparkline data={c.spark24h} max={max} height={28} />
        <div style={{ display: 'flex', justifyContent: 'space-between',
          fontFamily: 'var(--font-mono)', fontSize: 9, color: C.dim, letterSpacing: '0.04em',
        }}>
          <span>00</span><span>12</span><span>24</span>
        </div>
      </div>

      {/* Auth pill */}
      <div>
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 4, height: 4, borderRadius: 999, background: authTone }} />
          <Mono color={authTone} size={10} style={{ letterSpacing: '0.06em', textTransform: 'uppercase' }}>
            {authLabel(auth.status)}
          </Mono>
        </div>
        <Mono color={C.dim} size={10} style={{ display: 'block', marginTop: 4 }}>{auth.note}</Mono>
      </div>

      <Mono size={12} style={{ textAlign: 'right' }}>{formatNum(c.events24h)}</Mono>
      <Mono size={12} style={{ textAlign: 'right' }}>{c.sessions24h || '—'}</Mono>
      <Mono size={12} style={{ textAlign: 'right', color: c.cost24h > 0 ? C.fg : C.dim }}>
        {window.fmtCost(c.cost24h)}
      </Mono>
      <a href="#" onClick={(ev) => ev.preventDefault()}
        style={{ color: C.dim, textDecoration: 'none', fontSize: 13, justifySelf: 'end' }}>›</a>
    </div>
  );
}

// 24-bar mini histogram styled as a sparkline.
function Sparkline({ data, max, height = 28 }) {
  const C = window.C;
  const fg = window.__theme === 'light' ? 'oklch(0.18 0 0 / 0.6)' : 'oklch(0.985 0 0 / 0.65)';
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height, width: '100%' }}>
      {data.map((v, i) => {
        const h = Math.max(1, (v / (max || 1)) * height);
        return (
          <div key={i} style={{
            flex: 1, height: h, background: v === 0 ? C.borderSoft : fg, borderRadius: 0.5,
          }} />
        );
      })}
    </div>
  );
}

function authToneColor(status) {
  const C = window.C;
  if (status === 'ok') return C.green;
  if (status === 'expiring') return C.amber;
  if (status === 'needs_reauth') return C.red;
  if (status === 'unconfigured') return C.dim;
  return C.mfg;
}

function authLabel(status) {
  return {
    ok: 'authorized',
    expiring: 'expiring',
    needs_reauth: 'reauth',
    unconfigured: 'not set',
  }[status] || status;
}

function formatNum(n) {
  if (n >= 10000) return (n / 1000).toFixed(0) + 'k';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
  return String(n);
}

window.ConnectorsRoster = ConnectorsRoster;
