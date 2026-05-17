// V3 — Editorial Chronology
//
// Direction: extend the Overview's two-column editorial shell to a chronological
// stream + locked detail. The left is the day's index — one line per event,
// mono timestamp in the gutter, status as a single dot. The right is a generous
// detail panel locked to the selected event: a full flame, the sessions, the
// replay action. Keyboard up/down moves through the day.
//
// This is the slowest, calmest reading of the four.

function V3_Editorial() {
  const C = window.C;
  const events = window.EVENTS;
  const tot = window.totals(events);
  const [selectedId, setSelectedId] = React.useState('019e2e8c-7f12-71fa-9f1d-2244aabb55cc');
  const idx = events.findIndex((e) => e.id === selectedId);
  const selected = events[idx] || events[0];

  React.useEffect(() => {
    function onKey(ev) {
      if (ev.key === 'ArrowDown' || ev.key === 'j') {
        ev.preventDefault();
        setSelectedId(events[Math.min(events.length - 1, idx + 1)].id);
      }
      if (ev.key === 'ArrowUp' || ev.key === 'k') {
        ev.preventDefault();
        setSelectedId(events[Math.max(0, idx - 1)].id);
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [idx, events]);

  return (
    <div style={{ background: C.bg, color: C.fg, minHeight: '100%' }}>
      <div style={{ maxWidth: 1500, margin: '0 auto', padding: '40px 56px 80px' }}>

        <window.PageHeader
          eyebrow={`Ingestion · ${tot.count} events · ${tot.sessions} sessions today`}
          title={`A day, in order of arrival.`}
          sub="On the left, every external item the system saw — newest first. On the right, the one in front of you. Use ↑ ↓ to move through the day."
          right={(
            <div style={{ textAlign: 'right' }}>
              <Eyebrow>cost · today</Eyebrow>
              <div className="tnum" style={{
                marginTop: 4, fontFamily: 'var(--font-mono)', fontSize: 32,
                fontWeight: 500, letterSpacing: '-0.025em', color: C.fg,
              }}>{window.fmtCost(tot.cost)}</div>
              <Mono color={C.dim} size={10}>{tot.accepted} accepted · {tot.failed} need replay</Mono>
            </div>
          )}
        />

        {/* Two-column editorial split */}
        <div style={{
          marginTop: 36,
          display: 'grid', gridTemplateColumns: '1fr 1.3fr', gap: 56,
          alignItems: 'start',
        }}>
          {/* LEFT — chronology */}
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 14,
              paddingBottom: 8, borderBottom: `1px solid ${C.border}` }}>
              <Eyebrow>chronology · today</Eyebrow>
              <span style={{ marginLeft: 'auto' }} />
              <Mono color={C.dim} size={10}>↑ ↓ navigate · enter open</Mono>
            </div>

            <div>
              {events.map((e) => (
                <ChronologyRow key={e.id} event={e}
                  selected={e.id === selectedId}
                  onClick={() => setSelectedId(e.id)} />
              ))}
            </div>
          </div>

          {/* RIGHT — locked detail */}
          <div style={{ position: 'sticky', top: 24 }}>
            <EditorialDetail event={selected} />
          </div>
        </div>
      </div>
    </div>
  );
}

function ChronologyRow({ event, selected, onClick }) {
  const C = window.C;
  const e = event;
  const [hover, setHover] = React.useState(false);
  const errored = e.status === 'replay_pending' || e.status === 'error' || e.status === 'replay_failed';
  const dot = e.status === 'ingested' ? C.green
            : e.status === 'filtered' ? C.dim
            : errored ? C.amber : C.dim;

  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'grid',
        gridTemplateColumns: '64px 14px 1fr',
        gap: 14,
        padding: '14px 0',
        borderBottom: `1px solid ${C.borderSoft}`,
        cursor: 'pointer',
        background: selected
          ? (window.__theme === 'light' ? 'oklch(0 0 0 / 0.025)' : 'oklch(1 0 0 / 0.03)')
          : (hover ? (window.__theme === 'light' ? 'oklch(0 0 0 / 0.012)' : 'oklch(1 0 0 / 0.015)') : 'transparent'),
        position: 'relative',
      }}
    >
      {selected && (
        <div style={{
          position: 'absolute', left: -12, top: 0, bottom: 0, width: 2,
          background: C.fg,
        }} />
      )}
      <Mono size={11} color={selected ? C.fg : C.mfg} style={{ paddingTop: 2 }}>{e.t}</Mono>
      <span style={{
        width: 6, height: 6, borderRadius: 999, background: dot,
        marginTop: 7,
      }} />
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, minWidth: 0 }}>
          <span style={{
            fontSize: 13.5, color: selected ? C.fg : C.fg,
            fontWeight: selected ? 500 : 400, letterSpacing: '-0.005em',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', flex: 1, minWidth: 0,
          }}>
            {e.senderShort || e.sender}
          </span>
          {e.cost > 0 && <Mono color={C.dim} size={10.5} style={{ flexShrink: 0 }}>{window.fmtCost(e.cost)}</Mono>}
        </div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginTop: 4, minWidth: 0 }}>
          <Mono color={C.dim} size={10}>{e.channel.replace('_', ' ')}</Mono>
          <span style={{
            fontFamily: 'var(--font-serif)', fontSize: 13, fontStyle: 'italic',
            color: C.mfg, letterSpacing: 0,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', flex: 1, minWidth: 0,
          }}>
            {e.summary}
          </span>
        </div>
        {e.butlers.length > 0 && (
          <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ flex: 1 }}>
              <FlameStrip event={e} mode="inline" height={4} />
            </div>
            <Mono color={C.dim} size={9.5}>
              {e.butlers.map((b) => b.name).join(' · ')}
            </Mono>
          </div>
        )}
      </div>
    </div>
  );
}

function EditorialDetail({ event }) {
  const C = window.C;
  const e = event;
  return (
    <div style={{
      border: `1px solid ${C.border}`,
      padding: '28px 32px',
      background: window.__theme === 'light' ? 'oklch(0 0 0 / 0.01)' : 'oklch(1 0 0 / 0.012)',
    }}>
      {/* Eyebrow + status */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <Eyebrow>event · {window.shortId(e.id)} · {e.t}</Eyebrow>
        <span style={{ marginLeft: 'auto' }} />
        <StatusBadge status={e.status} size="lg" />
      </div>

      {/* Sender */}
      <div style={{ marginTop: 14, display: 'flex', alignItems: 'center', gap: 12 }}>
        <ChannelGlyph channel={e.channel} size={24} />
        <div style={{ minWidth: 0 }}>
          <div style={{
            fontSize: 22, fontWeight: 500, letterSpacing: '-0.02em', color: C.fg,
            lineHeight: 1.15,
          }}>{e.senderShort || e.sender}</div>
          <Mono color={C.dim} size={10.5} style={{ marginTop: 4 }}>
            {(window.CONNECTORS.find((c) => c.id === e.channel) || {}).label || e.channel} · tier {e.tier}
          </Mono>
        </div>
      </div>

      {/* Summary in serif */}
      <div style={{
        marginTop: 14, fontFamily: 'var(--font-serif)', fontSize: 16, color: C.fg,
        lineHeight: 1.55, maxWidth: '50ch',
      }}>{e.summary}</div>

      {/* KPI strip */}
      <div style={{
        marginTop: 22,
        display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 18,
        padding: '14px 0', borderTop: `1px solid ${C.border}`, borderBottom: `1px solid ${C.border}`,
      }}>
        {[
          { k: 'end-to-end', v: window.fmtDur(e.durationMs) },
          { k: 'sessions',   v: e.butlers.length },
          { k: 'tokens',     v: `${window.fmtTok(e.tokensIn)} → ${window.fmtTok(e.tokensOut)}` },
          { k: 'cost',       v: window.fmtCost(e.cost) },
        ].map((it, i) => (
          <div key={i}>
            <Eyebrow>{it.k}</Eyebrow>
            <div className="tnum" style={{
              marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 18,
              fontWeight: 500, color: C.fg, letterSpacing: '-0.015em',
            }}>{it.v}</div>
          </div>
        ))}
      </div>

      {/* Flame */}
      <div style={{ marginTop: 22 }}>
        <Eyebrow style={{ marginBottom: 10 }}>flame</Eyebrow>
        <FlameStrip event={e} mode="rows" height={22} showAxis />
      </div>

      {/* Sessions */}
      {e.butlers.length > 0 && (
        <div style={{ marginTop: 26 }}>
          <Eyebrow style={{ marginBottom: 10 }}>sessions</Eyebrow>
          <div>
            {e.butlers.map((b, i) => (
              <a key={i} href="#" onClick={(ev) => ev.preventDefault()}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '32px 1fr auto',
                  gap: 14, alignItems: 'center',
                  padding: '12px 0',
                  borderBottom: `1px solid ${C.borderSoft}`,
                  textDecoration: 'none', color: C.fg,
                }}>
                <BMark name={b.name} size={20} tone="fill" />
                <div style={{ minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
                    <span style={{ fontSize: 14, fontWeight: 500, letterSpacing: '-0.005em' }}>{b.name}</span>
                    <Mono color={C.dim} size={10}>{b.model}</Mono>
                  </div>
                  <Mono color={C.dim} size={10} style={{ marginTop: 4, display: 'block' }}>
                    {b.session} · {b.startedAt}
                  </Mono>
                </div>
                <div style={{ textAlign: 'right', display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <Mono size={11}>{window.fmtDur(b.durationMs)}</Mono>
                  <Mono color={C.dim} size={10}>{window.fmtTok(b.tokensIn)} → {window.fmtTok(b.tokensOut)}</Mono>
                </div>
              </a>
            ))}
          </div>
        </div>
      )}

      {!e.butlers.length && (
        <div style={{
          marginTop: 20, fontFamily: 'var(--font-serif)', fontSize: 14,
          fontStyle: 'italic', color: C.mfg,
        }}>
          {e.status === 'filtered'
            ? `Filtered before routing — ${e.hopFiltered || 'rule matched'}. Stored for replay.`
            : 'Stored without dispatch. No butler subscribed to this signal.'}
        </div>
      )}

      {/* Actions */}
      <div style={{ marginTop: 26, display: 'flex', gap: 10, alignItems: 'center' }}>
        <PillBtn kind="commit"><ReplayIcon size={11} /> replay event</PillBtn>
        <PillBtn>copy id</PillBtn>
        <span style={{ marginLeft: 'auto' }} />
        <a href="#" onClick={(ev) => ev.preventDefault()} style={{
          fontFamily: 'var(--font-mono)', fontSize: 10.5, color: C.fg,
          textDecoration: 'underline', textUnderlineOffset: 4,
          textDecorationColor: C.borderStrong, letterSpacing: '0.04em', textTransform: 'uppercase',
        }}>view raw payload →</a>
      </div>
    </div>
  );
}

window.V3_Editorial = V3_Editorial;
