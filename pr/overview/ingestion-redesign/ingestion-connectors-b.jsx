// Connectors · variant B — "The Board"
//
// Direction: group by what the operator actually wants to know — what's
// broken, what's loud, what's quiet, what's possible. Within each group the
// row is compact (two lines: label · auth state, then function · cadence).
// The right column carries 24h throughput as a horizontal bar so groups read
// visually before they read textually.
//
// This is the same data as the Roster, organised by attention. Use when the
// operator's question is "what should I be looking at?" rather than "give me
// the full register".

function ConnectorsBoard() {
  const C = window.C;
  const conns = window.CONNECTOR_DETAILS;
  const live = conns.filter((c) => c.enabled);
  const dormant = conns.filter((c) => !c.enabled);

  // Bucket
  const needsAttention = live.filter((c) => c.auth.status !== 'ok' || c.health !== 'ok');
  const steady    = live.filter((c) => !needsAttention.includes(c) && c.events24h >= 100);
  const quiet     = live.filter((c) => !needsAttention.includes(c) && c.events24h < 100);

  const maxEvents = Math.max(...live.map((c) => c.events24h), 1);

  return (
    <div style={{ background: C.bg, color: C.fg, minHeight: '100%' }}>
      <div style={{ maxWidth: 1500, margin: '0 auto', padding: '40px 56px 80px' }}>

        <window.PageHeader
          eyebrow={`Ingestion · connectors · grouped by attention · ${new Date().toLocaleString('en-GB', { weekday: 'short', day: '2-digit', month: 'short' })}`}
          title="Where the house is listening."
          sub="Channels are sorted by what wants your eye first — credential trouble, then heavy traffic, then quiet sources, then what's still on the shelf."
        />

        {/* The board — three groups */}
        <div style={{ marginTop: 36 }}>
          {needsAttention.length > 0 && (
            <BoardGroup
              eyebrow="needs attention"
              note={`${needsAttention.length} channel${needsAttention.length === 1 ? '' : 's'} · credential or health`}
              connectors={needsAttention}
              maxEvents={maxEvents}
              tone="warn"
            />
          )}
          <BoardGroup
            eyebrow="steady"
            note={`${steady.length} channels · ≥ 100 events / 24h`}
            connectors={steady}
            maxEvents={maxEvents}
          />
          {quiet.length > 0 && (
            <BoardGroup
              eyebrow="quiet"
              note={`${quiet.length} channels · under 100 events / 24h`}
              connectors={quiet}
              maxEvents={maxEvents}
            />
          )}
          {dormant.length > 0 && (
            <BoardGroup
              eyebrow="available"
              note={`${dormant.length} not connected`}
              connectors={dormant}
              maxEvents={maxEvents}
              dormant
            />
          )}
        </div>

        {/* Bottom action */}
        <div style={{ marginTop: 40, padding: '24px 0 0', borderTop: `1px solid ${C.border}`,
          display: 'flex', alignItems: 'baseline', gap: 16 }}>
          <Eyebrow>add channel</Eyebrow>
          <Mono color={C.dim} size={11}>
            connect a new source. webhook / IMAP / poll / long-poll / file drop.
          </Mono>
          <span style={{ marginLeft: 'auto' }} />
          <PillBtn kind="commit">+ add connector</PillBtn>
        </div>
      </div>
    </div>
  );
}

function BoardGroup({ eyebrow, note, connectors, maxEvents, tone, dormant }) {
  const C = window.C;
  if (!connectors || connectors.length === 0) return null;
  return (
    <div style={{ marginBottom: 32 }}>
      <div style={{
        display: 'flex', alignItems: 'baseline', gap: 12,
        padding: '14px 0 10px',
        borderBottom: `1px solid ${tone === 'warn' ? C.border : C.borderSoft}`,
      }}>
        <Eyebrow style={{ color: tone === 'warn' ? C.amber : C.mfg }}>{eyebrow}</Eyebrow>
        <Mono color={C.dim} size={10}>{note}</Mono>
      </div>

      {connectors.map((c) => (
        dormant
          ? <BoardRowDormant key={c.id} connector={c} />
          : <BoardRow key={c.id} connector={c} maxEvents={maxEvents} />
      ))}
    </div>
  );
}

function BoardRow({ connector, maxEvents }) {
  const C = window.C;
  const c = connector;
  const auth = c.auth;
  const tone = authToneColorB(auth.status);
  const widthPct = (c.events24h / (maxEvents || 1)) * 100;

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '8px 240px 1fr 340px 90px 24px',
      gap: 20, padding: '18px 0',
      borderBottom: `1px solid ${C.borderSoft}`,
      alignItems: 'center',
    }}>
      {/* Health dot */}
      <span style={{
        width: 6, height: 6, borderRadius: 999,
        background: c.health === 'ok' ? C.green : (auth.status === 'needs_reauth' ? C.red : C.amber),
      }} />

      {/* Identity column */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <ChannelGlyph channel={c.id} size={22} />
        <div>
          <div style={{ fontSize: 15, fontWeight: 500, letterSpacing: '-0.01em' }}>{c.label}</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 3 }}>
            <span style={{ width: 4, height: 4, borderRadius: 999, background: tone }} />
            <Mono size={9.5} color={tone} style={{ letterSpacing: '0.06em', textTransform: 'uppercase' }}>
              {authLabelB(auth.status)}
            </Mono>
            <Mono color={C.dim} size={9.5}>· {c.kind}</Mono>
          </div>
        </div>
      </div>

      {/* Function / serif gloss */}
      <div style={{ minWidth: 0 }}>
        <div style={{
          fontFamily: 'var(--font-serif)', fontSize: 14.5, color: C.fg,
          lineHeight: 1.4, letterSpacing: 0, maxWidth: '52ch',
        }}>{c.description}</div>
        <Mono color={C.dim} size={10} style={{ display: 'block', marginTop: 4 }}>
          {c.config.cadence} · last {c.lastEventAt} · {c.routedPct}% routed to butlers
        </Mono>
      </div>

      {/* Throughput bar */}
      <div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <Mono size={14} style={{ fontVariantNumeric: 'tabular-nums' }}>
            {c.events24h.toLocaleString()}
          </Mono>
          <Mono color={C.dim} size={10}>events · 24h</Mono>
          <span style={{ marginLeft: 'auto' }} />
          <Mono color={C.dim} size={10}>filtered {c.filtered24h.toLocaleString()}</Mono>
        </div>
        <div style={{
          marginTop: 6, height: 4, width: '100%', background: C.borderSoft,
          position: 'relative',
        }}>
          <div style={{
            position: 'absolute', left: 0, top: 0, bottom: 0,
            width: widthPct + '%', background: C.fg, opacity: 0.85,
          }} />
        </div>
      </div>

      {/* Cost / sessions */}
      <div style={{ textAlign: 'right' }}>
        <Mono size={13} style={{ display: 'block', color: c.cost24h > 0 ? C.fg : C.dim }}>
          {window.fmtCost(c.cost24h)}
        </Mono>
        <Mono color={C.dim} size={10} style={{ display: 'block', marginTop: 2 }}>
          {c.sessions24h || 0} sess
        </Mono>
      </div>

      {/* Disclosure */}
      <a href="#" onClick={(ev) => ev.preventDefault()}
        style={{ color: C.dim, textDecoration: 'none', fontSize: 13, justifySelf: 'end' }}>›</a>
    </div>
  );
}

function BoardRowDormant({ connector }) {
  const C = window.C;
  const c = connector;
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '8px 240px 1fr 120px',
      gap: 20, padding: '14px 0',
      borderBottom: `1px solid ${C.borderSoft}`,
      alignItems: 'center',
    }}>
      <span style={{ width: 6, height: 6, borderRadius: 999, background: C.dim, opacity: 0.5 }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <ChannelGlyph channel={c.id} size={20} />
        <div style={{ fontSize: 14, color: C.mfg }}>{c.label}</div>
      </div>
      <div style={{
        fontFamily: 'var(--font-serif)', fontStyle: 'italic',
        fontSize: 13, color: C.dim, maxWidth: '52ch',
      }}>{c.description}</div>
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <PillBtn>connect →</PillBtn>
      </div>
    </div>
  );
}

function authToneColorB(status) {
  const C = window.C;
  if (status === 'ok') return C.green;
  if (status === 'expiring') return C.amber;
  if (status === 'needs_reauth') return C.red;
  if (status === 'unconfigured') return C.dim;
  return C.mfg;
}

function authLabelB(status) {
  return {
    ok: 'authorized',
    expiring: 'expiring',
    needs_reauth: 'reauth',
    unconfigured: 'not set',
  }[status] || status;
}

window.ConnectorsBoard = ConnectorsBoard;
