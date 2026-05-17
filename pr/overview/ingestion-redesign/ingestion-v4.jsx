// V4 — Tape Log
//
// Direction: nothing collapsed, nothing hidden. The page is a continuous tape
// of events, each printed with its full nutritional information — sessions,
// models, tokens, flame — separated by hairlines. The mono gutter on the
// left stamps every entry with its time / id / cost the way a printed
// receipt would.
//
// For the operator who wants to read the day end-to-end. Pure engineering
// surface. Reads like a `tail -f` you would actually trust.

function V4_Tape() {
  const C = window.C;
  const events = window.EVENTS;
  const tot = window.totals(events);

  // Density toggle — exposed as a small pill row above the tape.
  const [density, setDensity] = React.useState('full'); // 'full' | 'compact'
  const [showFiltered, setShowFiltered] = React.useState(true);

  const visible = events.filter((e) => showFiltered || e.status !== 'filtered');

  return (
    <div style={{ background: C.bg, color: C.fg, minHeight: '100%' }}>
      <div style={{ maxWidth: 1500, margin: '0 auto', padding: '40px 56px 80px' }}>

        <window.PageHeader
          eyebrow={`Ingestion · tape · ${tot.count} events · ${tot.sessions} sessions`}
          title="The tape, unrolled."
          sub="Every event today, opened. Sessions, models, tokens, flame — nothing hidden. Scroll for the day."
          right={(
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
              <Eyebrow>spend · today</Eyebrow>
              <div className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 26,
                fontWeight: 500, letterSpacing: '-0.02em' }}>{window.fmtCost(tot.cost)}</div>
              <Mono color={C.dim} size={10}>{window.fmtTok(tot.tokensIn)} in · {window.fmtTok(tot.tokensOut)} out</Mono>
            </div>
          )}
        />

        {/* Controls */}
        <div style={{
          marginTop: 32, paddingBottom: 14, borderBottom: `1px solid ${C.border}`,
          display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap',
        }}>
          <Eyebrow>density</Eyebrow>
          <PillBtn kind={density === 'full' ? 'commit' : 'pill'} onClick={() => setDensity('full')}>full</PillBtn>
          <PillBtn kind={density === 'compact' ? 'commit' : 'pill'} onClick={() => setDensity('compact')}>compact</PillBtn>
          <span style={{ width: 24 }} />
          <Eyebrow>filtered</Eyebrow>
          <PillBtn kind={showFiltered ? 'commit' : 'pill'} onClick={() => setShowFiltered(!showFiltered)}>
            {showFiltered ? 'shown' : 'hidden'}
          </PillBtn>
          <span style={{ marginLeft: 'auto' }} />
          <Mono color={C.dim} size={10}>{visible.length} / {events.length} entries</Mono>
        </div>

        {/* The tape */}
        <div>
          {visible.map((e, i) => (
            <TapeEntry key={e.id} event={e} compact={density === 'compact'} index={i} />
          ))}
        </div>

        {/* End-of-tape rule */}
        <div style={{
          marginTop: 14, padding: '24px 0', borderTop: `1px solid ${C.border}`,
          textAlign: 'center', fontFamily: 'var(--font-serif)', fontStyle: 'italic',
          fontSize: 13, color: C.dim,
        }}>End of tape · {visible.length} events · {tot.sessions} sessions · {window.fmtCost(tot.cost)}</div>
      </div>
    </div>
  );
}

function TapeEntry({ event, compact, index }) {
  const C = window.C;
  const e = event;
  const errored = e.status === 'replay_pending' || e.status === 'error' || e.status === 'replay_failed';
  const connector = (window.CONNECTORS || []).find((c) => c.id === e.channel);

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '120px 1fr',
      gap: 28,
      padding: compact ? '14px 0' : '20px 0',
      borderBottom: `1px solid ${C.borderSoft}`,
      position: 'relative',
    }}>
      {errored && (
        <div style={{
          position: 'absolute', left: -10, top: 0, bottom: 0, width: 2,
          background: e.status === 'replay_pending' ? C.amber : C.red,
        }} />
      )}

      {/* GUTTER — timestamp / id / cost */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4,
        fontFamily: 'var(--font-mono)', textAlign: 'left',
      }}>
        <span className="tnum" style={{ fontSize: 13.5, fontWeight: 500, color: C.fg,
          letterSpacing: '-0.01em' }}>{e.t}</span>
        <Mono color={C.dim} size={10}>{window.shortId(e.id)}</Mono>
        <Mono color={e.cost > 0 ? C.fg : C.dim} size={11} style={{ marginTop: 4 }}>
          {window.fmtCost(e.cost)}
        </Mono>
        <Mono color={C.dim} size={10}>{window.fmtDur(e.durationMs)}</Mono>
      </div>

      {/* BODY */}
      <div style={{ minWidth: 0 }}>
        {/* line 1 — header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <ChannelGlyph channel={e.channel} size={16} />
          <Mono color={C.fg} size={11} style={{ letterSpacing: '0.04em' }}>
            {connector?.label || e.channel}
          </Mono>
          <Mono color={C.dim} size={10}>· {e.kind}</Mono>
          <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 14 }}>
            <Mono color={C.dim} size={10}>tier {e.tier}</Mono>
            <StatusBadge status={e.status} />
            <button type="button" title="Replay"
              style={{
                background: 'transparent', border: `1px solid ${C.border}`, borderRadius: 2,
                color: C.fg, cursor: 'pointer', padding: '3px 6px',
                display: 'inline-flex', alignItems: 'center', gap: 4,
                fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em',
                textTransform: 'uppercase',
              }}>
              <ReplayIcon size={10} /> replay
            </button>
          </span>
        </div>

        {/* line 2 — sender (the big line) */}
        <div style={{
          marginTop: 8, fontSize: compact ? 14 : 17,
          fontWeight: 500, letterSpacing: '-0.015em', color: C.fg,
          whiteSpace: compact ? 'nowrap' : 'normal',
          overflow: compact ? 'hidden' : 'visible',
          textOverflow: compact ? 'ellipsis' : 'clip',
        }}>{e.sender}</div>

        {/* line 3 — summary in serif */}
        <div style={{
          marginTop: 4, fontFamily: 'var(--font-serif)', fontSize: 14,
          color: C.mfg, lineHeight: 1.45,
        }}>{e.summary}</div>

        {/* SESSIONS (rendered as a tight tabular block) */}
        {!compact && e.butlers.length > 0 && (
          <div style={{ marginTop: 14 }}>
            <div style={{
              display: 'grid',
              gridTemplateColumns: '24px 110px 1fr 130px 110px 70px',
              gap: 12, padding: '6px 0',
              borderTop: `1px solid ${C.borderSoft}`,
              borderBottom: `1px solid ${C.borderSoft}`,
              fontFamily: 'var(--font-mono)', fontSize: 9, color: C.mfg,
              letterSpacing: '0.14em', textTransform: 'uppercase',
            }}>
              <span></span>
              <span>butler</span>
              <span>model · session</span>
              <span style={{ textAlign: 'right' }}>tokens</span>
              <span style={{ textAlign: 'right' }}>started</span>
              <span style={{ textAlign: 'right' }}>dur</span>
            </div>
            {e.butlers.map((b, k) => (
              <a key={k} href="#" onClick={(ev) => ev.preventDefault()}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '24px 110px 1fr 130px 110px 70px',
                  gap: 12, padding: '8px 0',
                  alignItems: 'baseline',
                  borderBottom: `1px solid ${C.borderSoft}`,
                  textDecoration: 'none', color: C.fg,
                }}>
                <BMark name={b.name} size={14} tone="fill" />
                <span style={{ fontSize: 12.5, letterSpacing: '-0.005em' }}>{b.name}</span>
                <span style={{ minWidth: 0, display: 'flex', alignItems: 'baseline', gap: 8 }}>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11,
                    color: C.fg, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{b.model}</span>
                  <Mono color={C.dim} size={10}>{b.session}</Mono>
                  {b.status === 'error' && <Mono color={C.red} size={10}>· {b.error || 'failed'}</Mono>}
                </span>
                <Mono size={11} style={{ textAlign: 'right' }} color={b.status === 'error' ? C.red : C.fg}>
                  {window.fmtTok(b.tokensIn)} → {window.fmtTok(b.tokensOut)}
                </Mono>
                <Mono size={11} color={C.dim} style={{ textAlign: 'right' }}>{b.startedAt}</Mono>
                <Mono size={11} style={{ textAlign: 'right' }}>{window.fmtDur(b.durationMs)}</Mono>
              </a>
            ))}
          </div>
        )}

        {/* compact session row */}
        {compact && e.butlers.length > 0 && (
          <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            {e.butlers.map((b, k) => (
              <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <BMark name={b.name} size={12} tone="fill" />
                <Mono size={11}>{b.name}</Mono>
                <Mono size={10} color={C.dim}>{b.model}</Mono>
                <Mono size={10} color={C.dim}>· {window.fmtDur(b.durationMs)}</Mono>
              </span>
            ))}
          </div>
        )}

        {/* FLAME — only in full density */}
        {!compact && e.butlers.length > 0 && (
          <div style={{ marginTop: 14 }}>
            <FlameStrip event={e} mode="rows" height={18} />
          </div>
        )}

        {/* Empty / filtered note */}
        {e.butlers.length === 0 && (
          <div style={{
            marginTop: 10, fontFamily: 'var(--font-serif)', fontSize: 13,
            fontStyle: 'italic', color: C.dim,
          }}>
            {e.status === 'filtered'
              ? `Filtered — ${e.hopFiltered || 'rule matched'}.`
              : 'Stored without dispatch.'}
          </div>
        )}
      </div>
    </div>
  );
}

window.V4_Tape = V4_Tape;
