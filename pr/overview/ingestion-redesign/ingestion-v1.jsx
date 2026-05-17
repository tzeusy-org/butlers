// V1 — Ledger Stream (deep-dive).
//
// The Ingestion page in canonical form. The shape is: header → toolbar (time
// range, search, filters, saved views) → connector chip strip → grouped
// event ledger (hour-headed rows, inline flame, click to drawer) → rollup.
//
// The drawer is the page's "second screen": flame graph, step ledger, raw
// payload, replay history. Everything the operator needs to debug a
// specific event without leaving the page.

function V1_Ledger() {
  const C = window.C;

  // ── State
  const [range,       setRange]       = React.useState('24h');
  const [statusMask,  setStatusMask]  = React.useState({
    ingested: true, filtered: false, replay_pending: true, error: true,
    replay_complete: true, replay_failed: true,
  });
  const [channelFilter, setChannelFilter] = React.useState('all');
  const [savedView,   setSavedView]   = React.useState(null);
  const [search,      setSearch]      = React.useState('');
  const [openId,      setOpenId]      = React.useState('019e2e8c-7f12-71fa-9f1d-2244aabb55cc');
  const [selected,    setSelected]    = React.useState(new Set());

  // Apply saved view side-effects when picked.
  React.useEffect(() => {
    if (savedView === 'errors') {
      setStatusMask({
        ingested: false, filtered: false, replay_pending: true, error: true,
        replay_complete: false, replay_failed: true,
      });
    } else if (savedView === 'priority') {
      setStatusMask({
        ingested: true, filtered: false, replay_pending: true, error: true,
        replay_complete: true, replay_failed: true,
      });
    } else if (savedView === 'spend') {
      setStatusMask({
        ingested: true, filtered: false, replay_pending: true, error: true,
        replay_complete: true, replay_failed: true,
      });
    }
  }, [savedView]);

  // ── Derive event set
  const baseEvents = window.EVENTS.filter((e) => statusMask[e.status])
    .filter((e) => channelFilter === 'all' ? true : e.channel === channelFilter)
    .filter((e) => savedView === 'priority' ? e.tier === 'priority' : true)
    .filter((e) => savedView === 'spend' ? e.cost > 0.001 : true)
    .filter((e) => {
      if (!search) return true;
      const q = search.toLowerCase();
      const fields = [e.sender, e.summary, e.kind, e.channel, e.id,
        ...e.butlers.map((b) => b.session),
        ...e.butlers.map((b) => b.name),
        ...e.butlers.map((b) => b.model),
      ];
      return fields.some((s) => (s || '').toLowerCase().includes(q));
    });

  // For saved-view "spend", sort by cost desc instead of chronological.
  const events = savedView === 'spend'
    ? [...baseEvents].sort((a, b) => b.cost - a.cost)
    : baseEvents;

  const tot = window.totals(events);
  const maxDur = Math.max(...window.EVENTS.map((e) => e.durationMs));

  // Group by hour for the chronological views (not for sorted-by-spend).
  const grouped = (savedView === 'spend')
    ? null
    : groupByHour(events);

  // Channel summary chips
  const chipChannels = window.byConnector(events);

  // Selection helpers
  const toggleSel = (id) => {
    const next = new Set(selected);
    next.has(id) ? next.delete(id) : next.add(id);
    setSelected(next);
  };
  const clearSel = () => setSelected(new Set());

  return (
    <div style={{ background: C.bg, color: C.fg, minHeight: '100%' }}>
      <div style={{ maxWidth: 1500, margin: '0 auto', padding: '40px 56px 80px' }}>

        {/* ─── Header ───────────────────────────────────────────────── */}
        <div style={{
          display: 'grid', gridTemplateColumns: '1fr auto', gap: 24,
          alignItems: 'baseline',
        }}>
          <div>
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
              letterSpacing: '0.14em', textTransform: 'uppercase',
              display: 'flex', alignItems: 'center', gap: 14,
              marginBottom: 8,
            }}>
              <span>Ingestion · timeline</span>
              <LiveStatusPill />
            </div>
            <h1 style={{
              margin: 0, fontSize: 38, fontWeight: 500,
              letterSpacing: '-0.025em', color: C.fg, lineHeight: 1.08,
            }}>
              {rangeTitle(range)}
            </h1>
            <div style={{
              marginTop: 10, fontFamily: 'var(--font-serif)', fontSize: 15,
              color: C.mfg, maxWidth: '58ch', lineHeight: 1.5,
            }}>
              Every external item the system received, end-to-end through the
              butler pipeline. Click any row to open the flame, the raw
              payload, or replay it.
            </div>
          </div>

          {/* Right meta — KPIs */}
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)',
            gap: 24, minWidth: 380,
          }}>
            {[
              { k: 'events', v: tot.count },
              { k: 'sessions', v: tot.sessions },
              { k: 'cost', v: window.fmtCost(tot.cost) },
            ].map((it, i) => (
              <div key={i} style={{ textAlign: 'right' }}>
                <Eyebrow>{it.k}</Eyebrow>
                <div className="tnum" style={{
                  marginTop: 4, fontFamily: 'var(--font-mono)', fontSize: 24,
                  fontWeight: 500, letterSpacing: '-0.02em', color: C.fg,
                }}>{it.v}</div>
              </div>
            ))}
          </div>
        </div>

        {/* ─── Toolbar ──────────────────────────────────────────────── */}
        <div style={{
          marginTop: 28, padding: '14px 0', borderTop: `1px solid ${C.border}`,
          display: 'grid', gridTemplateColumns: 'auto 1fr auto', gap: 24,
          alignItems: 'center',
        }}>
          <RangePicker value={range} onChange={setRange} />

          <SearchInput value={search} onChange={setSearch} />

          <SavedViews value={savedView} onChange={setSavedView} />
        </div>

        {/* ─── Channel chip strip ───────────────────────────────────── */}
        <div style={{
          padding: '16px 0',
          borderTop: `1px solid ${C.borderSoft}`,
          borderBottom: `1px solid ${C.border}`,
          display: 'flex', alignItems: 'center', gap: 18, flexWrap: 'wrap',
        }}>
          <Eyebrow>channels</Eyebrow>
          <ChannelChip
            active={channelFilter === 'all'}
            label="all" count={baseEvents.length}
            onClick={() => setChannelFilter('all')}
          />
          {chipChannels.map((c) => (
            <ChannelChip
              key={c.id}
              channel={c.id}
              active={channelFilter === c.id}
              count={c.events}
              errors={c.errors}
              cost={c.cost}
              onClick={() => setChannelFilter(channelFilter === c.id ? 'all' : c.id)}
            />
          ))}
          <span style={{ marginLeft: 'auto' }} />
          <StatusFilter mask={statusMask} onChange={setStatusMask} />
        </div>

        {/* ─── Bulk action bar (shown when selection > 0) ──────────── */}
        {selected.size > 0 && (
          <div style={{
            marginTop: 0, padding: '10px 14px',
            borderBottom: `1px solid ${C.border}`,
            background: window.__theme === 'light' ? 'oklch(0 0 0 / 0.025)' : 'oklch(1 0 0 / 0.04)',
            display: 'flex', alignItems: 'center', gap: 16,
          }}>
            <Mono size={11}>{selected.size} selected</Mono>
            <span style={{ marginLeft: 'auto' }} />
            <PillBtn kind="commit"><ReplayIcon size={11} /> replay all</PillBtn>
            <PillBtn>copy ids</PillBtn>
            <PillBtn onClick={clearSel}>clear</PillBtn>
          </div>
        )}

        {/* ─── Column header ────────────────────────────────────────── */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: '20px 64px 84px 132px 1fr 320px 64px 64px 64px 32px',
          gap: 16,
          padding: '12px 0 10px',
          borderBottom: `1px solid ${C.borderSoft}`,
          fontFamily: 'var(--font-mono)', fontSize: 9.5, color: C.mfg,
          letterSpacing: '0.14em', textTransform: 'uppercase',
        }}>
          <span></span>
          <span>id</span>
          <span>time</span>
          <span>channel</span>
          <span>sender · payload</span>
          <span>pipeline</span>
          <span style={{ textAlign: 'right' }}>tok in</span>
          <span style={{ textAlign: 'right' }}>tok out</span>
          <span style={{ textAlign: 'right' }}>cost</span>
          <span></span>
        </div>

        {/* ─── The ledger ───────────────────────────────────────────── */}
        <div>
          {grouped ? (
            grouped.map((g) => (
              <HourBlock key={g.hour} hour={g.hour} events={g.events}
                maxDur={maxDur} openId={openId} setOpenId={setOpenId}
                selected={selected} toggleSel={toggleSel} />
            ))
          ) : (
            <SortedByCost events={events} maxDur={maxDur} openId={openId}
              setOpenId={setOpenId} selected={selected} toggleSel={toggleSel} />
          )}
          {events.length === 0 && (
            <div style={{
              padding: '60px 0', textAlign: 'center',
              fontFamily: 'var(--font-serif)', fontStyle: 'italic',
              fontSize: 15, color: C.mfg,
            }}>Nothing matches.</div>
          )}
        </div>

        {/* ─── Rollup band ──────────────────────────────────────────── */}
        <RollupBand totals={tot} range={range} />
      </div>
    </div>
  );
}

// ─── Range picker ─────────────────────────────────────────────────────
function RangePicker({ value, onChange }) {
  const C = window.C;
  const opts = [
    { id: 'live', label: 'live'   },
    { id: '1h',   label: '1 hour' },
    { id: '24h',  label: '24 hours' },
    { id: '7d',   label: '7 days'  },
  ];
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <Eyebrow>range</Eyebrow>
      <div style={{ display: 'flex', gap: 0, border: `1px solid ${C.border}`, borderRadius: 3 }}>
        {opts.map((o, i) => {
          const active = value === o.id;
          return (
            <button key={o.id} type="button" onClick={() => onChange(o.id)}
              style={{
                background: active ? C.fg : 'transparent',
                color: active ? C.bg : C.fg,
                border: 'none',
                borderRight: i < opts.length - 1 ? `1px solid ${C.border}` : 'none',
                padding: '4px 12px', cursor: 'pointer',
                fontFamily: 'var(--font-mono)', fontSize: 10,
                letterSpacing: '0.10em', textTransform: 'uppercase',
              }}>
              {o.label}
            </button>
          );
        })}
        <button type="button" title="Custom range" style={{
          background: 'transparent', border: 'none', borderLeft: `1px solid ${C.border}`,
          padding: '4px 10px', cursor: 'pointer', color: C.dim,
          fontFamily: 'var(--font-mono)', fontSize: 10,
        }}>custom…</button>
      </div>
    </div>
  );
}

function rangeTitle(range) {
  if (range === 'live') return 'Live, as it arrives.';
  if (range === '1h')   return 'The last hour.';
  if (range === '24h')  return 'Today, in order of arrival.';
  if (range === '7d')   return 'The last seven days.';
  return 'Custom range.';
}

// ─── Search ───────────────────────────────────────────────────────────
function SearchInput({ value, onChange }) {
  const C = window.C;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8,
      border: `1px solid ${C.border}`, borderRadius: 3,
      padding: '4px 10px', maxWidth: 360,
    }}>
      <span style={{ color: C.dim, fontFamily: 'var(--font-mono)', fontSize: 10 }}>find</span>
      <input
        value={value}
        onChange={(ev) => onChange(ev.target.value)}
        placeholder="sender · payload · session id · channel"
        style={{
          background: 'transparent', border: 'none', outline: 'none',
          color: C.fg, fontFamily: 'var(--font-sans)', fontSize: 12.5,
          letterSpacing: '-0.005em', width: '100%',
        }}
      />
      {value && (
        <a href="#" onClick={(ev) => { ev.preventDefault(); onChange(''); }}
          style={{ color: C.dim, fontSize: 11, textDecoration: 'none' }}>×</a>
      )}
    </div>
  );
}

// ─── Saved views ──────────────────────────────────────────────────────
function SavedViews({ value, onChange }) {
  const C = window.C;
  const views = [
    { id: null,      label: 'all' },
    { id: 'errors',  label: 'errors' },
    { id: 'priority',label: 'priority' },
    { id: 'spend',   label: 'spend' },
  ];
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <Eyebrow>view</Eyebrow>
      <div style={{ display: 'flex', gap: 6 }}>
        {views.map((v) => {
          const active = value === v.id;
          return (
            <button key={String(v.id)} type="button" onClick={() => onChange(v.id)}
              style={{
                background: active ? C.fg : 'transparent',
                color: active ? C.bg : C.fg,
                border: `1px solid ${active ? C.fg : C.border}`,
                borderRadius: 3, padding: '3px 9px', cursor: 'pointer',
                fontFamily: 'var(--font-mono)', fontSize: 10,
                letterSpacing: '0.06em', textTransform: 'uppercase',
              }}>
              {v.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ─── Live status pill ─────────────────────────────────────────────────
function LiveStatusPill() {
  const C = window.C;
  const [state, setState] = React.useState('composing');
  React.useEffect(() => {
    const t1 = setTimeout(() => setState('fresh'), 1400);
    const t2 = setInterval(() => setState((s) => s === 'fresh' ? 'composing' : 'fresh'), 18000);
    return () => { clearTimeout(t1); clearInterval(t2); };
  }, []);
  const colors = {
    composing: C.amber,
    fresh: C.green,
  };
  const labels = {
    composing: 'composing…',
    fresh: 'fresh · 4s',
  };
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      border: `1px solid ${C.border}`, borderRadius: 999,
      padding: '1px 8px',
      fontFamily: 'var(--font-mono)', fontSize: 9, color: colors[state],
      letterSpacing: '0.06em', textTransform: 'uppercase',
    }}>
      <span style={{ width: 4, height: 4, borderRadius: 999, background: colors[state] }} />
      {labels[state]}
      <span style={{ color: C.dim, marginLeft: 2 }}>↻</span>
    </span>
  );
}

// ─── Channel chip ─────────────────────────────────────────────────────
function ChannelChip({ channel, label, active, count, errors, cost, onClick }) {
  const C = window.C;
  return (
    <button type="button" onClick={onClick}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 8,
        background: active ? C.fg : 'transparent', color: active ? C.bg : C.fg,
        border: `1px solid ${active ? C.fg : C.border}`,
        borderRadius: 3, padding: '5px 10px', cursor: 'pointer',
      }}>
      {channel && <ChannelGlyph channel={channel} size={12} />}
      <span style={{ fontFamily: 'var(--font-sans)', fontSize: 11.5, letterSpacing: '-0.005em' }}>
        {label || (channel ? channel.replace('_', ' ') : '')}
      </span>
      <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 10,
        color: active ? C.bg : C.dim, letterSpacing: '0.04em' }}>
        {count}
      </span>
      {errors > 0 && (
        <span style={{ width: 4, height: 4, borderRadius: 999, background: C.amber }} />
      )}
    </button>
  );
}

// ─── Status filter ────────────────────────────────────────────────────
function StatusFilter({ mask, onChange }) {
  const C = window.C;
  const opts = [
    { id: 'ingested',       label: 'ok',      tone: C.green },
    { id: 'filtered',       label: 'filtered',tone: C.dim },
    { id: 'replay_pending', label: 'replay',  tone: C.amber },
    { id: 'error',          label: 'error',   tone: C.red },
  ];
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <Eyebrow>status</Eyebrow>
      <div style={{ display: 'flex', gap: 4 }}>
        {opts.map((o) => {
          const on = !!mask[o.id];
          return (
            <button key={o.id} type="button"
              onClick={() => onChange({ ...mask, [o.id]: !on })}
              style={{
                border: `1px solid ${C.border}`, background: 'transparent',
                color: on ? C.fg : C.dim,
                fontFamily: 'var(--font-mono)', fontSize: 10,
                letterSpacing: '0.04em', padding: '3px 7px',
                borderRadius: 3, cursor: 'pointer',
                opacity: on ? 1 : 0.5,
                display: 'inline-flex', alignItems: 'center', gap: 5,
              }}>
              <span style={{ width: 5, height: 5, borderRadius: 999, background: o.tone }} />
              {o.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ─── Group by hour ────────────────────────────────────────────────────
function groupByHour(events) {
  const map = {};
  for (const e of events) {
    const h = e.t.split(':')[0] + ':00';
    if (!map[h]) map[h] = [];
    map[h].push(e);
  }
  return Object.entries(map)
    .sort((a, b) => b[0].localeCompare(a[0]))
    .map(([hour, events]) => ({ hour, events }));
}

function HourBlock({ hour, events, maxDur, openId, setOpenId, selected, toggleSel }) {
  const C = window.C;
  const cost = events.reduce((s, e) => s + e.cost, 0);
  return (
    <div>
      <div style={{
        display: 'grid', gridTemplateColumns: 'auto auto 1fr auto',
        gap: 14, alignItems: 'baseline',
        padding: '20px 0 10px',
      }}>
        <Mono size={10.5} color={C.mfg} style={{ letterSpacing: '0.06em' }}>{hour}</Mono>
        <Mono size={10} color={C.dim}>· {events.length} event{events.length === 1 ? '' : 's'}</Mono>
        <span></span>
        <Mono size={10} color={C.dim}>{window.fmtCost(cost)}</Mono>
      </div>
      {events.map((e) => (
        <LedgerRow key={e.id} event={e} maxDur={maxDur}
          open={openId === e.id}
          onToggle={() => setOpenId(openId === e.id ? null : e.id)}
          selected={selected.has(e.id)}
          onSelect={() => toggleSel(e.id)}
        />
      ))}
    </div>
  );
}

function SortedByCost({ events, maxDur, openId, setOpenId, selected, toggleSel }) {
  const C = window.C;
  return (
    <div>
      <div style={{
        padding: '14px 0',
        fontFamily: 'var(--font-serif)', fontStyle: 'italic',
        fontSize: 13, color: C.mfg,
      }}>Sorted by cost · highest first.</div>
      {events.map((e) => (
        <LedgerRow key={e.id} event={e} maxDur={maxDur}
          open={openId === e.id}
          onToggle={() => setOpenId(openId === e.id ? null : e.id)}
          selected={selected.has(e.id)}
          onSelect={() => toggleSel(e.id)}
        />
      ))}
    </div>
  );
}

// ─── One row ──────────────────────────────────────────────────────────
function LedgerRow({ event, maxDur, open, onToggle, selected, onSelect }) {
  const C = window.C;
  const [hover, setHover] = React.useState(false);
  const e = event;
  const errored = e.status === 'replay_pending' || e.status === 'error' || e.status === 'replay_failed';

  return (
    <div style={{ borderBottom: `1px solid ${C.borderSoft}` }}>
      <div
        onClick={onToggle}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
          display: 'grid',
          gridTemplateColumns: '20px 64px 84px 132px 1fr 320px 64px 64px 64px 32px',
          gap: 16, padding: '12px 0',
          alignItems: 'center',
          background: hover ? (window.__theme === 'light' ? 'oklch(0 0 0 / 0.025)' : 'oklch(1 0 0 / 0.03)') : 'transparent',
          cursor: 'pointer',
          position: 'relative',
        }}>
        {errored && (
          <div style={{
            position: 'absolute', left: -10, top: 0, bottom: 0, width: 2,
            background: e.status === 'replay_pending' ? C.amber : C.red,
          }} />
        )}

        {/* checkbox */}
        <label onClick={(ev) => ev.stopPropagation()}
          style={{ display: 'inline-flex', alignItems: 'center', cursor: 'pointer' }}>
          <input type="checkbox" checked={selected} onChange={onSelect}
            style={{ width: 13, height: 13, accentColor: C.fg, margin: 0 }} />
        </label>

        <Mono color={C.dim} size={10.5}>{window.shortId(e.id)}</Mono>
        <Mono size={11}>{e.t}</Mono>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          <ChannelGlyph channel={e.channel} size={14} />
          <span style={{
            fontSize: 12, color: C.fg, letterSpacing: '-0.005em', textTransform: 'capitalize',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>{e.channel.replace('_', ' ')}</span>
          {e.tier === 'priority' && (
            <Mono size={9} color={C.amber} style={{ letterSpacing: '0.10em', textTransform: 'uppercase' }}>★</Mono>
          )}
        </div>

        <div style={{ minWidth: 0 }}>
          <div style={{
            fontSize: 13, color: C.fg, letterSpacing: '-0.005em',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>{e.senderShort || e.sender}</div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 10.5, color: C.dim,
            letterSpacing: '0.01em', marginTop: 2,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>{e.summary}</div>
        </div>

        <div>
          <FlameStrip event={e} mode="inline" height={10} scaleMs={maxDur} />
          <div style={{
            marginTop: 4, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            fontFamily: 'var(--font-mono)', fontSize: 9.5, color: C.dim,
          }}>
            <span style={{ display: 'flex', gap: 8 }}>
              {e.butlers.length > 0 ? (
                e.butlers.map((b, i) => <span key={i} style={{ color: window.bh(b.name) }}>{b.name}</span>)
              ) : <span>—</span>}
            </span>
            <span className="tnum">{window.fmtDur(e.durationMs)}</span>
          </div>
        </div>

        <Mono size={11} style={{ textAlign: 'right' }}>{window.fmtTok(e.tokensIn)}</Mono>
        <Mono size={11} style={{ textAlign: 'right' }}>{window.fmtTok(e.tokensOut)}</Mono>
        <Mono size={11} style={{ textAlign: 'right', color: e.cost > 0 ? C.fg : C.dim }}>
          {window.fmtCost(e.cost)}
        </Mono>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 6 }}>
          <button type="button" title="Replay"
            onClick={(ev) => { ev.stopPropagation(); }}
            style={{
              background: 'transparent', border: `1px solid ${C.border}`, borderRadius: 2,
              color: C.fg, cursor: 'pointer', padding: '3px 4px',
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            }}>
            <ReplayIcon size={11} />
          </button>
          <span style={{ color: C.dim, fontFamily: 'var(--font-mono)', fontSize: 10,
            transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 120ms ease' }}>›</span>
        </div>
      </div>

      {open && <ExpandedDrawer event={event} />}
    </div>
  );
}

// ─── Expanded drawer ──────────────────────────────────────────────────
function ExpandedDrawer({ event }) {
  const C = window.C;
  const e = event;
  const [tab, setTab] = React.useState('flame'); // flame | raw | replay

  return (
    <div style={{
      background: window.__theme === 'light' ? 'oklch(0 0 0 / 0.018)' : 'oklch(1 0 0 / 0.02)',
      padding: '20px 24px 24px',
      borderTop: `1px solid ${C.border}`,
      borderBottom: `1px solid ${C.border}`,
      display: 'grid', gridTemplateColumns: '1fr 320px', gap: 32,
    }}>
      {/* LEFT — tabbed work area */}
      <div>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 22,
          borderBottom: `1px solid ${C.borderSoft}`, marginBottom: 16,
        }}>
          {[
            { id: 'flame',  label: 'flame · step ledger' },
            { id: 'raw',    label: 'raw payload' },
            { id: 'replay', label: 'replay history' },
          ].map((t) => (
            <button key={t.id} type="button" onClick={() => setTab(t.id)}
              style={{
                background: 'transparent', border: 'none', cursor: 'pointer',
                padding: '0 0 10px 0',
                fontFamily: 'var(--font-mono)', fontSize: 10,
                letterSpacing: '0.10em', textTransform: 'uppercase',
                color: tab === t.id ? C.fg : C.mfg,
                borderBottom: `1px solid ${tab === t.id ? C.fg : 'transparent'}`,
                marginBottom: -1,
              }}>{t.label}</button>
          ))}
        </div>

        {tab === 'flame'  && <DrawerFlame event={e} />}
        {tab === 'raw'    && <DrawerRaw event={e} />}
        {tab === 'replay' && <DrawerReplay event={e} />}
      </div>

      {/* RIGHT — meta + sessions */}
      <div>
        <Eyebrow style={{ marginBottom: 10 }}>request</Eyebrow>
        <div style={{ display: 'grid', gap: 8, fontSize: 12 }}>
          <KV label="id"        value={<Mono size={10.5}>{e.id}</Mono>} />
          <KV label="received"  value={<Mono>{e.t}</Mono>} />
          <KV label="channel"   value={<span style={{ display:'inline-flex', alignItems:'center', gap: 6 }}><ChannelGlyph channel={e.channel} size={12} /> {e.channel}</span>} />
          <KV label="kind"      value={<Mono>{e.kind}</Mono>} />
          <KV label="tier"      value={<Mono color={e.tier === 'priority' ? C.amber : C.fg}>{e.tier}</Mono>} />
          <KV label="sender"    value={<span style={{ fontSize: 11.5 }}>{e.sender}</span>} />
          {e.error && <KV label="error" value={<Mono color={C.red} size={10.5}>{e.error}</Mono>} />}
          {e.hopFiltered && <KV label="filtered" value={<Mono color={C.mfg}>{e.hopFiltered}</Mono>} />}
          <KV label="cost" value={<Mono>{window.fmtCost(e.cost)} · in {window.fmtTok(e.tokensIn)} · out {window.fmtTok(e.tokensOut)}</Mono>} />
        </div>

        <Eyebrow style={{ marginTop: 24, marginBottom: 10 }}>sessions ({e.butlers.length})</Eyebrow>
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          {e.butlers.map((b, i) => <SessionIndex key={i} butler={b} />)}
          {!e.butlers.length && (
            <div style={{
              fontFamily: 'var(--font-serif)', fontStyle: 'italic', fontSize: 13,
              color: C.mfg, padding: '6px 0',
            }}>
              {e.status === 'filtered'
                ? `Filtered before routing — ${e.hopFiltered || 'rule matched'}.`
                : 'Stored without dispatch.'}
            </div>
          )}
        </div>

        {/* Drawer footer actions */}
        <div style={{ marginTop: 18, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {e.status !== 'ingested' && (
            <PillBtn kind="commit"><ReplayIcon size={11} /> replay event</PillBtn>
          )}
          <PillBtn>copy id</PillBtn>
          <PillBtn>copy curl</PillBtn>
        </div>
      </div>
    </div>
  );
}

// Drawer: flame + per-session step ledger (grouped, session id at the head)
function DrawerFlame({ event: e }) {
  const C = window.C;
  return (
    <div>
      <Eyebrow style={{ marginBottom: 10 }}>flame · {window.fmtDur(e.durationMs)} end-to-end</Eyebrow>
      <FlameStrip event={e} mode="rows" height={20} showAxis />

      {/* Per-session step ledger. Each session gets a header (mark + name +
          full session id + model + status + session totals), then a tight
          per-step table with duration / share / tokens in / tokens out /
          cost. The session id is mono and click-to-copy. */}
      {e.butlers.length > 0 && (
        <div style={{ marginTop: 26 }}>
          <Eyebrow style={{ marginBottom: 10 }}>step ledger · {e.butlers.length} session{e.butlers.length === 1 ? '' : 's'}</Eyebrow>
          {e.butlers.map((b, i) => (
            <SessionStepBlock key={i} butler={b} eventDurMs={e.durationMs} />
          ))}
        </div>
      )}

      {!e.butlers.length && (
        <div style={{
          marginTop: 14, padding: '10px 0',
          fontFamily: 'var(--font-serif)', fontSize: 13, fontStyle: 'italic', color: C.mfg,
        }}>
          {e.status === 'filtered'
            ? `Filtered before routing — ${e.hopFiltered || 'rule matched'}.`
            : 'Stored without dispatch — no butler subscribed to this signal.'}
        </div>
      )}
    </div>
  );
}

function SessionStepBlock({ butler, eventDurMs }) {
  const C = window.C;
  const b = butler;
  const sessionTotal = (b.steps || []).reduce((s, st) => s + st.cost, 0);
  return (
    <div id={`session-${b.session}`} style={{
      marginBottom: 24, paddingBottom: 6,
      borderTop: `1px solid ${C.border}`,
    }}>
      {/* Session header */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'auto 1fr auto',
        gap: 16, padding: '14px 0 10px',
        alignItems: 'baseline',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <window.BMark name={b.name} size={16} tone="fill" />
          <span style={{ fontSize: 14.5, fontWeight: 500, letterSpacing: '-0.01em' }}>
            {b.name}
          </span>
          <Mono color={b.status === 'error' ? C.red : C.green} size={9.5}
            style={{ letterSpacing: '0.08em', textTransform: 'uppercase' }}>
            {b.status === 'error' ? '■ error' : '● ok'}
          </Mono>
        </div>

        {/* The session id — copyable */}
        <CopyableId id={b.session} />

        <div style={{ display: 'flex', gap: 18, alignItems: 'baseline' }}>
          <Mono color={C.dim} size={10}>{b.model}</Mono>
          <Mono size={11}>{window.fmtDur(b.durationMs)}</Mono>
          <Mono size={11}>{window.fmtCost(sessionTotal)}</Mono>
          <a href={`/sessions/${b.session}`} onClick={(ev) => ev.preventDefault()}
            style={{
              fontFamily: 'var(--font-mono)', fontSize: 10, color: C.fg,
              textDecoration: 'underline', textUnderlineOffset: 3,
              textDecorationColor: C.borderStrong, letterSpacing: '0.06em',
              textTransform: 'uppercase',
            }}>open →</a>
        </div>
      </div>

      {/* Steps table */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '1fr 70px 56px 70px 70px 70px',
        gap: 10, padding: '6px 0 6px',
        borderBottom: `1px solid ${C.borderSoft}`,
        fontFamily: 'var(--font-mono)', fontSize: 9, color: C.mfg,
        letterSpacing: '0.14em', textTransform: 'uppercase',
      }}>
        <span>step</span>
        <span style={{ textAlign: 'right' }}>dur</span>
        <span style={{ textAlign: 'right' }}>%</span>
        <span style={{ textAlign: 'right' }}>tok in</span>
        <span style={{ textAlign: 'right' }}>tok out</span>
        <span style={{ textAlign: 'right' }}>cost</span>
      </div>
      {b.steps.map((st, k) => (
        <div key={k} style={{
          display: 'grid',
          gridTemplateColumns: '1fr 70px 56px 70px 70px 70px',
          gap: 10, padding: '8px 0',
          borderBottom: `1px solid ${C.borderSoft}`,
          alignItems: 'baseline',
          color: st.status === 'error' ? C.red : C.fg,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{
              width: 6, height: 6, borderRadius: 999,
              background: st.status === 'error' ? C.red : C.green,
            }} />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5 }}>
              {st.name}{st.status === 'error' ? ' · failed' : ''}
            </span>
          </div>
          <Mono size={11} style={{ textAlign: 'right' }}>{window.fmtDur(st.durMs)}</Mono>
          <Mono size={11} color={C.dim} style={{ textAlign: 'right' }}>
            {((st.durMs / b.durationMs) * 100).toFixed(0)}%
          </Mono>
          <Mono size={11} style={{ textAlign: 'right' }} color={st.tokensIn ? C.fg : C.dim}>
            {window.fmtTok(st.tokensIn)}
          </Mono>
          <Mono size={11} style={{ textAlign: 'right' }} color={st.tokensOut ? C.fg : C.dim}>
            {window.fmtTok(st.tokensOut)}
          </Mono>
          <Mono size={11} style={{ textAlign: 'right' }} color={st.cost > 0 ? C.fg : C.dim}>
            {window.fmtCost(st.cost)}
          </Mono>
        </div>
      ))}

      {/* Session totals row */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '1fr 70px 56px 70px 70px 70px',
        gap: 10, padding: '8px 0',
        fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
        letterSpacing: '0.06em', textTransform: 'uppercase',
      }}>
        <span style={{ textAlign: 'right' }}>session totals</span>
        <Mono size={11} color={C.mfg} style={{ textAlign: 'right' }}>{window.fmtDur(b.durationMs)}</Mono>
        <Mono size={11} color={C.dim} style={{ textAlign: 'right' }}>
          {eventDurMs ? ((b.durationMs / eventDurMs) * 100).toFixed(0) : 0}%
        </Mono>
        <Mono size={11} color={C.mfg} style={{ textAlign: 'right' }}>{window.fmtTok(b.tokensIn)}</Mono>
        <Mono size={11} color={C.mfg} style={{ textAlign: 'right' }}>{window.fmtTok(b.tokensOut)}</Mono>
        <Mono size={11} color={C.mfg} style={{ textAlign: 'right' }}>{window.fmtCost(sessionTotal)}</Mono>
      </div>
    </div>
  );
}

// A click-to-copy inline ID. Shows the full id in mono, with a tiny "copy"
// affordance to its right; on click the affordance briefly says "copied".
function CopyableId({ id }) {
  const C = window.C;
  const [state, setState] = React.useState('idle');
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'baseline', gap: 8, minWidth: 0,
    }}>
      <span style={{
        fontFamily: 'var(--font-mono)', fontSize: 11, color: C.fg,
        letterSpacing: '0.01em',
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        maxWidth: '32ch',
      }}>{id}</span>
      <a href="#" onClick={(ev) => {
        ev.preventDefault();
        try { navigator.clipboard?.writeText(id); } catch {}
        setState('copied');
        setTimeout(() => setState('idle'), 900);
      }} style={{
        fontFamily: 'var(--font-mono)', fontSize: 9, color: C.dim,
        letterSpacing: '0.10em', textTransform: 'uppercase',
        textDecoration: 'underline', textUnderlineOffset: 3,
        textDecorationColor: C.borderSoft, flexShrink: 0,
      }}>{state === 'copied' ? 'copied' : 'copy'}</a>
    </span>
  );
}

// Drawer: raw payload viewer (truncated mock JSON)
function DrawerRaw({ event: e }) {
  const C = window.C;
  const raw = mockPayload(e);
  return (
    <div>
      <div style={{
        display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 10,
      }}>
        <Eyebrow>payload · {e.bytes ? e.bytes + 'B' : '—'}</Eyebrow>
        <Mono color={C.dim} size={10}>received over {e.channel.replace('_', ' ')}</Mono>
        <span style={{ marginLeft: 'auto' }} />
        <PillBtn>download</PillBtn>
        <PillBtn>open in editor</PillBtn>
      </div>
      <pre style={{
        margin: 0, padding: '14px 18px',
        background: window.__theme === 'light' ? 'oklch(0 0 0 / 0.03)' : 'oklch(1 0 0 / 0.03)',
        border: `1px solid ${C.borderSoft}`,
        fontFamily: 'var(--font-mono)', fontSize: 11, color: C.fg,
        letterSpacing: 0, lineHeight: 1.55,
        whiteSpace: 'pre-wrap', wordBreak: 'break-word',
        maxHeight: 320, overflow: 'auto',
      }}>{raw}</pre>
      <Mono color={C.dim} size={10} style={{ display: 'block', marginTop: 8 }}>
        truncated · headers + body shown · full payload {(e.tokensIn / 1000).toFixed(1)}k tokens
      </Mono>
    </div>
  );
}

function mockPayload(e) {
  // Per-channel realistic-looking JSON snippet, truncated.
  const head = `{
  "event_id": "${e.id}",
  "received_at": "${e.t}",
  "channel": "${e.channel}",
  "kind": "${e.kind}",
  "tier": "${e.tier}",`;
  let body = '';
  if (e.channel === 'telegram') body = `
  "sender": { "id": 1102392, "username": "${e.sender.match(/@(\S+)/)?.[1] || 'unknown'}" },
  "message": { "text": ${JSON.stringify((e.summary || '').replace(/^"|"$/g, ''))} }`;
  else if (e.channel === 'email') body = `
  "from": ${JSON.stringify(e.sender)},
  "subject": ${JSON.stringify(e.summary)},
  "headers": { "list-unsubscribe": "<https://...>", "received": "${e.t}" },
  "body": { "text": "...", "html": "<truncated>" }`;
  else if (e.channel === 'home_assistant') body = `
  "entity_id": "${e.sender}",
  "state": "${(e.summary || '').match(/state:\s*(\S+.*)/)?.[1] || 'on'}",
  "attributes": { "device_class": "presence", "friendly_name": "${e.senderShort}" }`;
  else if (e.channel === 'spotify') body = `
  "track": ${JSON.stringify(e.summary)},
  "duration_ms": 184320,
  "played_at": "${e.t}",
  "played_ms": 184320`;
  else if (e.channel === 'calendar') body = `
  "event": { "summary": ${JSON.stringify(e.summary)}, "start": "2026-05-17T19:30:00+01:00",
    "attendees": [{ "email": "${e.sender}" }, { "email": "self" }] }`;
  else body = `
  "summary": ${JSON.stringify(e.summary)}`;
  return head + body + '\n}';
}

// Drawer: replay history
function DrawerReplay({ event: e }) {
  const C = window.C;
  const hist = mockReplayHistory(e);
  return (
    <div>
      <div style={{
        display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 14,
      }}>
        <Eyebrow>replay · {hist.attempts} attempt{hist.attempts === 1 ? '' : 's'}</Eyebrow>
        <Mono color={C.dim} size={10}>{hist.policy}</Mono>
        <span style={{ marginLeft: 'auto' }} />
        {e.status !== 'ingested' && <PillBtn kind="commit"><ReplayIcon size={11} /> replay now</PillBtn>}
      </div>

      <div>
        <div style={{
          display: 'grid', gridTemplateColumns: '90px 90px 1fr auto',
          gap: 14, padding: '8px 0 6px',
          borderBottom: `1px solid ${C.borderSoft}`,
          fontFamily: 'var(--font-mono)', fontSize: 9, color: C.mfg,
          letterSpacing: '0.14em', textTransform: 'uppercase',
        }}>
          <span>at</span><span>by</span><span>result</span><span>cost</span>
        </div>
        {hist.runs.map((r, i) => (
          <div key={i} style={{
            display: 'grid', gridTemplateColumns: '90px 90px 1fr auto',
            gap: 14, padding: '10px 0',
            borderBottom: `1px solid ${C.borderSoft}`,
            alignItems: 'baseline',
          }}>
            <Mono size={11}>{r.at}</Mono>
            <Mono color={C.dim} size={10.5}>{r.by}</Mono>
            <div>
              <Mono size={11} color={r.result === 'ok' ? C.green : r.result === 'pending' ? C.amber : C.red}
                style={{ letterSpacing: '0.06em', textTransform: 'uppercase' }}>{r.result}</Mono>
              <Mono color={C.dim} size={10.5} style={{ display: 'block', marginTop: 3 }}>{r.detail}</Mono>
            </div>
            <Mono size={11}>{r.cost ? window.fmtCost(r.cost) : '—'}</Mono>
          </div>
        ))}
      </div>

      <div style={{
        marginTop: 14, fontFamily: 'var(--font-serif)', fontStyle: 'italic',
        fontSize: 13, color: C.mfg, lineHeight: 1.55, maxWidth: '60ch',
      }}>{hist.note}</div>
    </div>
  );
}

function mockReplayHistory(e) {
  if (e.status === 'replay_pending') {
    return {
      attempts: 3,
      policy: 'retry × 3 · backoff 2^n · then human',
      note: 'After three retries it stopped — the attached PDF is encrypted and pdf.parse can\'t decode it. Resolve by opening the message and choosing a parser, then replay.',
      runs: [
        { at: '09:31:08', by: 'auto',  result: 'error',   detail: 'pdf.parse · encrypted attachment', cost: 0.0014 },
        { at: '09:33:11', by: 'auto',  result: 'error',   detail: 'pdf.parse · encrypted attachment', cost: 0.0014 },
        { at: '09:37:14', by: 'auto',  result: 'error',   detail: 'pdf.parse · encrypted attachment', cost: 0.0014 },
        { at: '09:37:14', by: 'system',result: 'pending', detail: 'queued for human-initiated replay', cost: 0 },
      ],
    };
  }
  return {
    attempts: 1,
    policy: 'on demand only',
    note: 'Ingested cleanly on first run. Replay would re-emit the event through the dispatch pipeline; downstream side-effects are idempotent.',
    runs: [
      { at: e.t, by: 'auto', result: 'ok', detail: `${e.butlers.length} session${e.butlers.length === 1 ? '' : 's'} · ${window.fmtDur(e.durationMs)}`, cost: e.cost },
    ],
  };
}

// A compact session row for the drawer's right rail. The card acts as an
// anchor — clicking it scrolls to the matching session block on the left
// column. The "open →" link still navigates to /sessions/<id>.
function SessionIndex({ butler: b }) {
  const C = window.C;
  const scrollTo = (ev) => {
    ev.preventDefault();
    const el = document.getElementById(`session-${b.session}`);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  return (
    <a href={`#session-${b.session}`} onClick={scrollTo}
      style={{
        display: 'grid', gridTemplateColumns: '20px 1fr auto',
        gap: 10, padding: '10px 0',
        borderBottom: `1px solid ${C.borderSoft}`,
        textDecoration: 'none', color: C.fg,
      }}>
      <window.BMark name={b.name} size={14} tone="fill" />
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <span style={{ fontSize: 12.5, fontWeight: 500, letterSpacing: '-0.005em' }}>{b.name}</span>
          <Mono color={b.status === 'error' ? C.red : C.green} size={9}
            style={{ letterSpacing: '0.08em', textTransform: 'uppercase' }}>
            {b.status === 'error' ? '■' : '●'}
          </Mono>
        </div>
        <Mono color={C.dim} size={10} style={{ display: 'block', marginTop: 3 }}>
          {b.session.split('-')[0]} · {window.fmtDur(b.durationMs)} · {window.fmtCost(b.cost || 0)}
        </Mono>
      </div>
      <a href={`/sessions/${b.session}`} onClick={(ev) => ev.stopPropagation()}
        style={{
          fontFamily: 'var(--font-mono)', fontSize: 9.5, color: C.dim,
          textDecoration: 'underline', textUnderlineOffset: 3,
          textDecorationColor: C.borderSoft, letterSpacing: '0.06em',
          textTransform: 'uppercase', alignSelf: 'center',
        }}>open →</a>
    </a>
  );
}

function SessionCard({ butler: b }) {
  const C = window.C;
  return (
    <a href={`/sessions/${b.session}`} onClick={(ev) => ev.preventDefault()}
      style={{
        display: 'block', padding: '10px 12px',
        border: `1px solid ${C.border}`, borderRadius: 3,
        textDecoration: 'none', color: C.fg, background: 'transparent',
      }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <window.BMark name={b.name} size={14} tone="fill" />
        <span style={{ fontSize: 13, fontWeight: 500, letterSpacing: '-0.005em' }}>{b.name}</span>
        <span style={{
          marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: 9.5,
          color: b.status === 'error' ? C.red : C.green,
          letterSpacing: '0.06em', textTransform: 'uppercase',
        }}>{b.status}</span>
      </div>
      <div style={{
        marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 10.5, color: C.mfg,
        letterSpacing: '0.02em',
        display: 'grid', gridTemplateColumns: '1fr auto', gap: 8,
      }}>
        <span>{b.model}</span>
        <span className="tnum">{window.fmtDur(b.durationMs)}</span>
      </div>
      <div style={{
        marginTop: 4, display: 'flex', justifyContent: 'space-between',
        fontFamily: 'var(--font-mono)', fontSize: 10, color: C.dim,
      }}>
        <span>{b.session}</span>
        <span className="tnum">{window.fmtTok(b.tokensIn)} → {window.fmtTok(b.tokensOut)}</span>
      </div>
      <div style={{ marginTop: 8 }}>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 10, color: C.fg,
          textDecoration: 'underline', textUnderlineOffset: 3, textDecorationColor: C.borderStrong,
        }}>open session →</span>
      </div>
    </a>
  );
}

function KV({ label, value }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '80px 1fr', gap: 12, alignItems: 'baseline' }}>
      <Eyebrow>{label}</Eyebrow>
      <div style={{ minWidth: 0 }}>{value}</div>
    </div>
  );
}

function RollupBand({ totals, range }) {
  const C = window.C;
  const items = [
    { k: `events · ${range}`, v: totals.count },
    { k: 'accepted',          v: totals.accepted, tone: C.green },
    { k: 'filtered',          v: totals.filtered, tone: C.dim },
    { k: 'needs replay',      v: totals.failed,   tone: totals.failed ? C.amber : C.dim },
    { k: 'sessions',          v: totals.sessions },
    { k: 'tok in',            v: window.fmtTok(totals.tokensIn) },
    { k: 'tok out',           v: window.fmtTok(totals.tokensOut) },
    { k: 'cost',              v: window.fmtCost(totals.cost) },
  ];
  return (
    <div style={{
      marginTop: 32, padding: '16px 0 0',
      borderTop: `1px solid ${C.border}`,
      display: 'grid', gridTemplateColumns: 'repeat(8, 1fr)', gap: 16,
    }}>
      {items.map((it, i) => (
        <div key={i}>
          <Eyebrow>{it.k}</Eyebrow>
          <div className="tnum" style={{
            marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 18, fontWeight: 500, color: C.fg,
            letterSpacing: '-0.01em', display: 'flex', alignItems: 'center', gap: 8,
          }}>
            {it.tone && <span style={{ width: 6, height: 6, borderRadius: 999, background: it.tone }} />}
            {it.v}
          </div>
        </div>
      ))}
    </div>
  );
}

window.V1_Ledger = V1_Ledger;
