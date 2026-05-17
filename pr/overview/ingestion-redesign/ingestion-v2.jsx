// V2 — Trace Console
//
// Direction: borrow from distributed-tracing tools (Honeycomb, Tempo) and
// translate to the Dispatch palette. The page is a CHANNEL-LANE board: each
// connector is a horizontal lane that spans the day. Events appear on their
// lane as tick-marks whose width is the event's duration. Click a tick to
// load the event into the detail panel below.
//
// Macro first (where was the load coming from?), then drill into one event.

const V2_WINDOW_START_H = 7;   // 07:00
const V2_WINDOW_END_H   = 12;  // 12:00

function timeToPct(t) {
  const [hh, mm, ss] = t.split(':').map(Number);
  const mins = hh * 60 + mm + (ss || 0) / 60;
  const start = V2_WINDOW_START_H * 60;
  const end = V2_WINDOW_END_H * 60;
  return ((mins - start) / (end - start)) * 100;
}

function V2_Trace() {
  const C = window.C;
  const [selectedId, setSelectedId] = React.useState('019e2e8c-7f12-71fa-9f1d-2244aabb55cc');
  const [hoverId, setHoverId] = React.useState(null);

  const events = window.EVENTS;
  const tot = window.totals(events);
  const channels = window.byConnector(events)
    .sort((a, b) => b.events - a.events);
  const selected = events.find((e) => e.id === selectedId);

  return (
    <div style={{ background: C.bg, color: C.fg, minHeight: '100%' }}>
      <div style={{ maxWidth: 1500, margin: '0 auto', padding: '40px 56px 80px' }}>

        <window.PageHeader
          eyebrow={`Ingestion · trace · ${V2_WINDOW_START_H.toString().padStart(2,'0')}:00 — ${V2_WINDOW_END_H.toString().padStart(2,'0')}:00 · ${tot.count} events`}
          title="Today, by channel."
          sub="One lane per source, time across. A tick is one event. Hover for the headline, click to load the full flame."
          right={(
            <div style={{ textAlign: 'right' }}>
              <Eyebrow>cost · window</Eyebrow>
              <div className="tnum" style={{
                marginTop: 4, fontFamily: 'var(--font-mono)', fontSize: 28,
                fontWeight: 500, letterSpacing: '-0.02em',
              }}>{window.fmtCost(tot.cost)}</div>
              <Mono color={C.dim} size={10}>{window.fmtTok(tot.tokensIn)} in · {window.fmtTok(tot.tokensOut)} out</Mono>
            </div>
          )}
        />

        {/* Lane chart */}
        <div style={{ marginTop: 36 }}>
          <LaneAxis />
          <div style={{ borderTop: `1px solid ${C.border}` }}>
            {channels.map((c) => (
              <ChannelLane
                key={c.id}
                channel={c}
                events={events.filter((e) => e.channel === c.id)}
                selectedId={selectedId}
                hoverId={hoverId}
                onHover={setHoverId}
                onSelect={setSelectedId}
              />
            ))}
          </div>
          <div style={{
            borderTop: `1px solid ${C.border}`,
            padding: '10px 0',
            display: 'flex', gap: 22, fontFamily: 'var(--font-mono)', fontSize: 10,
            color: C.dim, letterSpacing: '0.04em',
          }}>
            <span><span style={{ color: C.green }}>●</span> ingested</span>
            <span><span style={{ color: C.amber }}>◐</span> replay pending</span>
            <span><span style={{ color: C.red }}>■</span> error</span>
            <span><span style={{ color: C.dim }}>○</span> filtered</span>
            <span style={{ marginLeft: 'auto' }}>tick width = end-to-end duration · color = butler set</span>
          </div>
        </div>

        {/* Detail strip — selected event */}
        {selected && <TraceDetail event={selected} />}
      </div>
    </div>
  );
}

function LaneAxis() {
  const C = window.C;
  const hours = [];
  for (let h = V2_WINDOW_START_H; h <= V2_WINDOW_END_H; h += 0.5) hours.push(h);
  return (
    <div style={{ marginLeft: 220, marginRight: 90, position: 'relative', height: 16,
      borderBottom: `1px solid ${C.borderSoft}` }}>
      {hours.map((h, i) => {
        const left = ((h - V2_WINDOW_START_H) / (V2_WINDOW_END_H - V2_WINDOW_START_H)) * 100;
        const isFull = Number.isInteger(h);
        return (
          <div key={i} style={{
            position: 'absolute', left: left + '%', top: 0, bottom: 0,
          }}>
            <div style={{ width: 1, height: isFull ? 8 : 4, background: C.border, position: 'absolute', bottom: 0 }} />
            {isFull && (
              <span className="tnum" style={{
                position: 'absolute', bottom: 8, left: -14,
                fontFamily: 'var(--font-mono)', fontSize: 9.5, color: C.mfg,
                letterSpacing: '0.04em',
              }}>{String(h).padStart(2,'0')}:00</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ChannelLane({ channel, events, selectedId, hoverId, onHover, onSelect }) {
  const C = window.C;
  const c = (window.CONNECTORS || []).find((x) => x.id === channel.id);
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '220px 1fr 90px',
      alignItems: 'stretch',
      borderBottom: `1px solid ${C.borderSoft}`,
      minHeight: 56,
    }}>
      {/* Channel header cell */}
      <div style={{
        padding: '10px 14px 10px 0', borderRight: `1px solid ${C.borderSoft}`,
        display: 'flex', flexDirection: 'column', gap: 4, justifyContent: 'center',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <ChannelGlyph channel={channel.id} size={20} />
          <div>
            <div style={{ fontSize: 13.5, fontWeight: 500, letterSpacing: '-0.005em', textTransform: 'capitalize' }}>
              {c?.label || channel.id.replace('_', ' ')}
            </div>
            <Mono color={C.dim} size={9.5}>{c?.kind || 'connector'}</Mono>
          </div>
        </div>
        <div style={{ marginTop: 4, display: 'flex', gap: 14,
          fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg, letterSpacing: '0.04em',
        }}>
          <span><span style={{ color: C.fg }} className="tnum">{channel.events}</span> evt</span>
          <span><span style={{ color: C.fg }} className="tnum">{channel.sessions}</span> sess</span>
          {channel.errors > 0 && <span style={{ color: C.amber }}><span className="tnum">{channel.errors}</span> needs replay</span>}
        </div>
      </div>

      {/* Lane area */}
      <div style={{ position: 'relative' }}>
        {/* gridlines */}
        {Array.from({ length: V2_WINDOW_END_H - V2_WINDOW_START_H }).map((_, i) => {
          const left = ((i + 1) / (V2_WINDOW_END_H - V2_WINDOW_START_H)) * 100;
          return (
            <div key={i} style={{
              position: 'absolute', top: 0, bottom: 0, left: left + '%',
              width: 1, background: C.borderSoft,
            }} />
          );
        })}
        {events.map((e) => (
          <TraceTick key={e.id} event={e}
            selected={selectedId === e.id}
            hover={hoverId === e.id}
            onHover={onHover}
            onSelect={onSelect}
          />
        ))}
      </div>

      {/* Rollup cell */}
      <div style={{ padding: '10px 0 10px 14px', borderLeft: `1px solid ${C.borderSoft}`,
        display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 2,
      }}>
        <Mono size={11.5} style={{ textAlign: 'right' }}>{window.fmtCost(channel.cost)}</Mono>
        <Mono size={9.5} color={C.dim} style={{ textAlign: 'right' }}>
          {window.fmtTok(channel.tokensIn)} → {window.fmtTok(channel.tokensOut)}
        </Mono>
      </div>
    </div>
  );
}

function TraceTick({ event, selected, hover, onHover, onSelect }) {
  const C = window.C;
  const left = timeToPct(event.t);
  const minWidth = 0.25;
  // width scaled by duration but capped so short events stay visible
  const widthPct = Math.max(minWidth, Math.min(8, (event.durationMs / (60 * 60 * 1000)) * 100));
  const hue = event.butlers[0] ? window.bh(event.butlers[0].name) :
              (event.status === 'filtered' ? C.dim : C.mfg);
  const errored = event.status === 'replay_pending' || event.status === 'error' || event.status === 'replay_failed';

  return (
    <div
      onMouseEnter={() => onHover(event.id)}
      onMouseLeave={() => onHover(null)}
      onClick={() => onSelect(event.id)}
      style={{
        position: 'absolute',
        left: left + '%',
        top: 16, bottom: 16,
        width: widthPct + '%',
        minWidth: 3,
        cursor: 'pointer',
        zIndex: selected ? 3 : (hover ? 2 : 1),
      }}
    >
      <div style={{
        position: 'absolute', inset: 0,
        background: errored ? C.red : hue,
        opacity: errored ? 0.9 : (selected ? 0.95 : (hover ? 0.85 : 0.7)),
        borderRadius: 1,
        border: selected ? `1px solid ${C.fg}` : 'none',
        outline: hover && !selected ? `1px solid ${C.fg}` : 'none',
      }} />
      {/* Multi-butler stack — additional bars below */}
      {event.butlers.slice(1).map((b, i) => (
        <div key={i} style={{
          position: 'absolute', left: 0, right: 0,
          top: `${100 + i * 18}%`,
          height: 4,
          background: window.bh(b.name),
          opacity: 0.6,
        }} />
      ))}
      {/* Hover tooltip */}
      {hover && (
        <div style={{
          position: 'absolute', left: '50%', bottom: 'calc(100% + 6px)',
          transform: 'translateX(-50%)',
          background: window.PALETTES[window.__theme || 'dark'].bgElev,
          border: `1px solid ${C.border}`,
          padding: '8px 10px', borderRadius: 3,
          minWidth: 240, zIndex: 20,
          boxShadow: '0 6px 16px oklch(0 0 0 / 0.3)',
          pointerEvents: 'none',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Mono size={10}>{event.t}</Mono>
            <StatusBadge status={event.status} />
          </div>
          <div style={{ fontSize: 12, color: C.fg, marginTop: 4, letterSpacing: '-0.005em',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {event.senderShort || event.sender}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg, marginTop: 4,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {event.summary}
          </div>
          <div style={{ marginTop: 6, display: 'flex', justifyContent: 'space-between',
            fontFamily: 'var(--font-mono)', fontSize: 10, color: C.dim }}>
            <span>{event.butlers.length} sess · {window.fmtDur(event.durationMs)}</span>
            <span className="tnum">{window.fmtCost(event.cost)}</span>
          </div>
        </div>
      )}
    </div>
  );
}

function TraceDetail({ event }) {
  const C = window.C;
  const e = event;
  return (
    <div style={{
      marginTop: 36, padding: '24px 0 0',
      borderTop: `1px solid ${C.border}`,
      display: 'grid', gridTemplateColumns: '1fr 380px', gap: 40,
    }}>
      {/* LEFT — flame graph dominant */}
      <div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 16, marginBottom: 14 }}>
          <Eyebrow>selected · {window.shortId(e.id)}</Eyebrow>
          <span style={{ marginLeft: 'auto' }} />
          <StatusBadge status={e.status} size="lg" />
        </div>

        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
          <ChannelGlyph channel={e.channel} size={20} />
          <span style={{ fontSize: 22, fontWeight: 500, letterSpacing: '-0.02em', color: C.fg }}>
            {e.senderShort || e.sender}
          </span>
          <Mono color={C.dim}>{e.t} · {e.tier}</Mono>
        </div>
        <div style={{ fontFamily: 'var(--font-serif)', fontSize: 15, color: C.mfg, marginTop: 8, maxWidth: '64ch' }}>
          {e.summary}
        </div>

        {/* KPI strip */}
        <div style={{
          marginTop: 22,
          display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)',
          gap: 24, padding: '14px 0',
          borderTop: `1px solid ${C.border}`, borderBottom: `1px solid ${C.border}`,
        }}>
          {[
            { k: 'end-to-end', v: window.fmtDur(e.durationMs) },
            { k: 'butlers',    v: e.butlers.length },
            { k: 'tokens in',  v: window.fmtTok(e.tokensIn) },
            { k: 'tokens out', v: window.fmtTok(e.tokensOut) },
            { k: 'cost',       v: window.fmtCost(e.cost) },
          ].map((it, i) => (
            <div key={i}>
              <Eyebrow>{it.k}</Eyebrow>
              <div className="tnum" style={{
                marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 20,
                fontWeight: 500, color: C.fg, letterSpacing: '-0.015em',
              }}>{it.v}</div>
            </div>
          ))}
        </div>

        {/* Flame */}
        <div style={{ marginTop: 22 }}>
          <Eyebrow style={{ marginBottom: 10 }}>flame · end-to-end</Eyebrow>
          <FlameStrip event={e} mode="rows" height={24} showAxis />
        </div>
      </div>

      {/* RIGHT — sessions + actions */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Eyebrow>sessions ({e.butlers.length})</Eyebrow>
          <span style={{ marginLeft: 'auto' }} />
          <PillBtn kind="commit"><ReplayIcon size={11} /> replay</PillBtn>
        </div>
        <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 0 }}>
          {e.butlers.map((b, i) => (
            <div key={i} style={{ padding: '12px 0', borderBottom: `1px solid ${C.borderSoft}` }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <BMark name={b.name} size={16} tone="fill" />
                <span style={{ fontSize: 13.5, fontWeight: 500, letterSpacing: '-0.005em' }}>{b.name}</span>
                <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: 9.5,
                  color: b.status === 'error' ? C.red : C.green,
                  letterSpacing: '0.06em', textTransform: 'uppercase' }}>{b.status}</span>
              </div>
              <div style={{ marginTop: 4, fontFamily: 'var(--font-mono)', fontSize: 10.5, color: C.mfg }}>
                {b.model}
              </div>
              <div style={{ marginTop: 4, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4,
                fontFamily: 'var(--font-mono)', fontSize: 10, color: C.dim }}>
                <span className="tnum">{window.fmtDur(b.durationMs)} · {b.startedAt}</span>
                <span className="tnum" style={{ textAlign: 'right' }}>{window.fmtTok(b.tokensIn)} → {window.fmtTok(b.tokensOut)}</span>
              </div>
              <div style={{ marginTop: 6 }}>
                <a href="#" onClick={(ev) => ev.preventDefault()} style={{
                  fontFamily: 'var(--font-mono)', fontSize: 10, color: C.fg,
                  textDecoration: 'underline', textUnderlineOffset: 3,
                  textDecorationColor: C.borderStrong,
                }}>open session {b.session.split('-')[0]} →</a>
              </div>
            </div>
          ))}
          {!e.butlers.length && (
            <div style={{
              fontFamily: 'var(--font-serif)', fontSize: 13, fontStyle: 'italic', color: C.mfg,
              padding: '12px 0',
            }}>
              {e.status === 'filtered'
                ? `Filtered before routing — ${e.hopFiltered || 'rule matched'}.`
                : 'No butler subscribed to this signal.'}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

window.V2_Trace = V2_Trace;
