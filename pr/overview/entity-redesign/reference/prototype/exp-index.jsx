// 00 — Index
//
// The /entities landing page. A tabular, scan-heavy surface whose job is
// curation: promote provisional entities, archive stale ones, forget the
// wrong ones, merge duplicates. The four jobs map to four commit
// buttons; everything else is a rule-list.
//
// Two surfaces stacked vertically:
//   (a) the "queue" — anything in a state that requires owner attention
//       (unidentified, duplicate-candidate, stale). Each row has a primary
//       commit button. This is the only place state colour is used.
//   (b) the index — the full table, filterable by type and predicate. Bulk
//       actions live in the gutter when at least one row is selected.

function ExpIndex() {
  const [typeFilter, setTypeFilter] = React.useState('all');
  const [stateFilter, setStateFilter] = React.useState(null); // null | unidentified | duplicate | stale
  const [q, setQ] = React.useState('');
  const [selected, setSelected] = React.useState(new Set());

  const queue = React.useMemo(() => {
    return ENTITIES.filter((e) =>
      e.state === 'unidentified' ||
      e.state === 'duplicate-candidate' ||
      e.state === 'stale'
    );
  }, []);

  const rows = React.useMemo(() => {
    const needle = q.trim().toLowerCase();
    let r = ENTITIES.slice();
    if (typeFilter !== 'all') r = r.filter((e) => e.type === typeFilter);
    if (stateFilter === 'unidentified') r = r.filter((e) => e.state === 'unidentified');
    if (stateFilter === 'duplicate')    r = r.filter((e) => e.state === 'duplicate-candidate');
    if (stateFilter === 'stale')        r = r.filter((e) => e.state === 'stale');
    if (stateFilter === 'unconfirmed')  r = r.filter((e) =>
      (contactsFor(e.id).some((c) => c.meta.verified === false))
    );
    if (needle) {
      r = r.filter((e) =>
        e.name.toLowerCase().includes(needle) ||
        (e.aliases || []).some((a) => a.toLowerCase().includes(needle))
      );
    }
    r.sort((a, b) => {
      // owner first, then by last activity desc
      if (a.role === 'owner') return -1;
      if (b.role === 'owner') return 1;
      const al = a.lastSeen || a.firstSeen || '';
      const bl = b.lastSeen || b.firstSeen || '';
      return bl.localeCompare(al);
    });
    return r;
  }, [typeFilter, stateFilter, q]);

  function toggle(id) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id); else next.add(id);
    setSelected(next);
  }
  function clearSel() { setSelected(new Set()); }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 300px', height: '100%' }}>
      {/* LEFT — toolbar + queue + index */}
      <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, borderRight: '1px solid var(--border)' }}>
        {/* sub-page tabs (route IA) */}
        <SubpageTabs current="index" />

        {/* Toolbar */}
        <div style={{
          padding: '16px 20px', display: 'flex', alignItems: 'center', gap: 12,
          borderBottom: '1px solid var(--border)',
        }}>
          <input value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="filter · name · alias"
            style={{
              flex: 1, minWidth: 0, background: 'transparent', border: 'none', outline: 'none',
              color: 'var(--fg)', fontFamily: 'var(--font-sans)', fontSize: 14,
              borderBottom: '1px solid var(--border-strong)', paddingBottom: 6,
            }} />
          <div style={{ display: 'flex', gap: 4 }}>
            {['all', 'person', 'organization', 'place', 'group'].map((t) => (
              <Pill key={t} active={typeFilter === t} onClick={() => setTypeFilter(t)}>
                {t === 'all' ? 'all' : t.slice(0, t === 'organization' ? 3 : 6)}
              </Pill>
            ))}
          </div>
          <div style={{ width: 1, height: 18, background: 'var(--border)' }} />
          <div style={{ display: 'flex', gap: 4 }}>
            {[
              ['unconfirmed', METRICS.unident + METRICS.duplicates, 'amber'],
              ['stale',       METRICS.stale, null],
            ].map(([id, n, tone]) => (
              <Pill key={id} active={stateFilter === id} onClick={() => setStateFilter(stateFilter === id ? null : id)}
                    count={n}>{id}</Pill>
            ))}
          </div>
        </div>

        {/* Bulk action gutter (only when selection > 0) */}
        {selected.size > 0 && (
          <div style={{
            padding: '10px 20px',
            background: 'oklch(1 0 0 / 0.04)',
            borderBottom: '1px solid var(--border)',
            display: 'flex', alignItems: 'center', gap: 16,
            fontFamily: 'var(--font-mono)', fontSize: 10,
            textTransform: 'uppercase', letterSpacing: '0.08em',
            color: 'var(--mfg)',
          }}>
            <span className="tnum" style={{ color: 'var(--fg)' }}>{selected.size} selected</span>
            <button className="dlink">archive</button>
            <button className="dlink">merge…</button>
            <button className="dlink" style={{ color: 'var(--red)' }}>forget</button>
            <span style={{ flex: 1 }} />
            <button className="dlink" onClick={clearSel}>clear</button>
          </div>
        )}

        {/* Table header */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: '32px 22px 1fr 90px 70px 110px 26px',
          gap: 14, padding: '8px 20px',
          borderBottom: '1px solid var(--border-soft)',
        }}>
          <span />
          <span />
          <Eyebrow>name</Eyebrow>
          <Eyebrow>type</Eyebrow>
          <Eyebrow>tier</Eyebrow>
          <Eyebrow style={{ textAlign: 'right' }}>last seen</Eyebrow>
          <span />
        </div>

        {/* Rows */}
        <div style={{ flex: 1, overflow: 'auto' }}>
          {rows.map((e) => {
            const isSel = selected.has(e.id);
            const isOwner = e.role === 'owner';
            const contacts = contactsFor(e.id);
            const last = e.lastSeen || e.firstSeen;
            const lastDays = last ? Math.round((new Date('2026-05-16').getTime() - new Date(last).getTime()) / 86400000) : null;
            return (
              <div key={e.id} style={{
                display: 'grid',
                gridTemplateColumns: '32px 22px 1fr 90px 70px 110px 26px',
                gap: 14, padding: '10px 20px', alignItems: 'center',
                borderBottom: '1px solid var(--border-soft)',
                background: isSel ? 'oklch(1 0 0 / 0.04)' : 'transparent',
                cursor: 'pointer',
              }}
              onMouseEnter={(ev) => { if (!isSel) ev.currentTarget.style.background = 'oklch(1 0 0 / 0.025)'; }}
              onMouseLeave={(ev) => { if (!isSel) ev.currentTarget.style.background = 'transparent'; }}>
                <div onClick={() => !isOwner && toggle(e.id)} style={{ display: 'flex', justifyContent: 'center' }}>
                  {!isOwner && <Tick checked={isSel} />}
                </div>
                <EntityMark entity={e} size={18} />
                <div style={{ minWidth: 0, display: 'flex', alignItems: 'baseline', gap: 8 }}>
                  <span style={{
                    fontSize: 13, color: 'var(--fg)', fontWeight: 500,
                    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                  }}>{e.name}</span>
                  {isOwner && <TierBadge tier={0} />}
                  {e.state === 'unidentified' && <StatePill kind="unidentified">unidentified</StatePill>}
                  {e.state === 'duplicate-candidate' && (
                    <StatePill kind="duplicate">
                      likely dupe of {ENTITY_INDEX[e.dupOf]?.name || ''}
                    </StatePill>
                  )}
                  {e.state === 'stale' && <StatePill kind="stale">stale</StatePill>}
                  {contacts.some((c) => !c.meta.verified) && (
                    <StatePill kind="unverified">·</StatePill>
                  )}
                  {e.aliases?.length > 0 && !isOwner && (
                    <span className="mono" style={{ fontSize: 10, color: 'var(--dim)' }}>
                      +{e.aliases.length} alias{e.aliases.length === 1 ? '' : 'es'}
                    </span>
                  )}
                </div>
                <span className="mono" style={{ fontSize: 10, color: 'var(--mfg)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{e.type}</span>
                <div>{e.tier ? <TierBadge tier={e.tier} /> : <span style={{ color: 'var(--dim)' }}>—</span>}</div>
                <span className="mono tnum" style={{ fontSize: 11, color: 'var(--mfg)', textAlign: 'right' }}>
                  {lastDays != null
                    ? (lastDays < 1 ? 'today' : lastDays < 30 ? `${lastDays}d` : lastDays < 365 ? `${Math.round(lastDays / 30)}mo` : `${Math.round(lastDays / 365)}y`)
                    : '—'}
                </span>
                <span style={{ color: 'var(--dim)', textAlign: 'right' }}>›</span>
              </div>
            );
          })}
        </div>

        {/* Footer */}
        <div style={{
          padding: '10px 20px', borderTop: '1px solid var(--border)',
          fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--mfg)',
          letterSpacing: '0.06em', textTransform: 'uppercase',
          display: 'flex', gap: 18,
        }}>
          <span className="tnum">{rows.length} of {ENTITIES.length}</span>
          <span style={{ color: 'var(--dim)' }}>·</span>
          <span>select · click row gutter</span>
          <span style={{ flex: 1 }} />
          <span>n · new · ⌘k · finder · ↑↓ to step</span>
        </div>
      </div>

      {/* RIGHT — Needs you queue */}
      <div style={{ padding: '0 16px', overflow: 'auto' }}>
        <div style={{ padding: '14px 4px', borderBottom: '1px solid var(--border)' }}>
          <Eyebrow>needs you · {queue.length}</Eyebrow>
          <Voice italic style={{ marginTop: 6, fontSize: 12, color: 'var(--mfg)' }}>
            Surfaces awaiting a verdict.
          </Voice>
        </div>
        {queue.map((e) => (
          <QueueCard key={e.id} entity={e} />
        ))}
        {queue.length === 0 && (
          <Voice italic style={{ padding: 24, color: 'var(--mfg)' }}>Nothing waiting.</Voice>
        )}
      </div>

      <style>{`
        button.dlink { background:transparent; border:0; padding:0; color: inherit;
          font:inherit; cursor:pointer; text-decoration: underline;
          text-underline-offset: 3px; text-decoration-color: var(--border-strong);
          letter-spacing: inherit; }
        button.dlink:hover { color: var(--fg); text-decoration-color: var(--fg); }
      `}</style>
    </div>
  );
}

function Tick({ checked }) {
  return (
    <span style={{
      width: 14, height: 14, border: `1px solid ${checked ? 'var(--fg)' : 'var(--border-strong)'}`,
      borderRadius: 2, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      background: checked ? 'var(--fg)' : 'transparent',
      color: 'var(--bg)', fontSize: 10, fontWeight: 700,
      transition: 'background 80ms linear, border-color 80ms linear',
    }}>{checked ? '✓' : ''}</span>
  );
}

function StatePill({ kind, children }) {
  const tone = kind === 'duplicate' ? 'var(--amber)'
    : kind === 'unidentified' ? 'var(--amber)'
    : kind === 'stale' ? 'var(--mfg)'
    : kind === 'unverified' ? 'var(--amber)'
    : 'var(--mfg)';
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '1px 5px', border: `1px solid ${tone}`, color: tone,
      borderRadius: 2,
      fontFamily: 'var(--font-mono)', fontSize: 9,
      textTransform: 'uppercase', letterSpacing: '0.08em', lineHeight: 1.4,
    }}>{children}</span>
  );
}

function QueueCard({ entity: e }) {
  const k = e.state;
  const isUn = k === 'unidentified';
  const isDup = k === 'duplicate-candidate';
  const isStale = k === 'stale';
  const dupOf = isDup ? ENTITY_INDEX[e.dupOf] : null;
  const c = isUn ? contactsFor(e.id)[0] : null;

  return (
    <div style={{ padding: '14px 4px', borderBottom: '1px solid var(--border-soft)' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 8 }}>
        <Eyebrow style={{ color: 'var(--amber)' }}>
          {isUn && 'unidentified'}
          {isDup && 'duplicate candidate'}
          {isStale && 'stale'}
        </Eyebrow>
        <span className="mono" style={{ fontSize: 10, color: 'var(--dim)' }}>
          {e.lastSeen || e.firstSeen}
        </span>
      </div>

      {isUn && (
        <>
          <div style={{ fontSize: 13, color: 'var(--fg)', wordBreak: 'break-all' }}>{e.name}</div>
          <Voice italic style={{ marginTop: 8, fontSize: 12, color: 'var(--mfg)' }}>
            Seen on a memory thread; no contact match yet.
          </Voice>
          <div style={{ display: 'flex', gap: 8, marginTop: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <CommitBtn>merge into…</CommitBtn>
            <button className="pill" style={{ padding: '3px 8px' }}>new person</button>
            <button className="pill" style={{ padding: '3px 8px' }}>dismiss</button>
          </div>
        </>
      )}
      {isDup && (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <EntityMark entity={e} size={16} />
            <span style={{ fontSize: 13 }}>{e.name}</span>
            <span className="mono" style={{ fontSize: 10, color: 'var(--mfg)' }}>≈</span>
            <EntityMark entity={dupOf} size={16} />
            <span style={{ fontSize: 13 }}>{dupOf?.name}</span>
          </div>
          <Voice italic style={{ marginTop: 8, fontSize: 12, color: 'var(--mfg)' }}>
            Shared email · same employer.
          </Voice>
          <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
            <CommitBtn>merge</CommitBtn>
            <button className="pill" style={{ padding: '3px 8px' }}>keep both</button>
          </div>
        </>
      )}
      {isStale && (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <EntityMark entity={e} size={16} />
            <span style={{ fontSize: 13 }}>{e.name}</span>
          </div>
          <Voice italic style={{ marginTop: 8, fontSize: 12, color: 'var(--mfg)' }}>
            No touch in {Math.round((new Date('2026-05-16').getTime() - new Date(e.lastSeen).getTime()) / (86400000 * 30))} months.
          </Voice>
          <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
            <CommitBtn>archive</CommitBtn>
            <button className="pill" style={{ padding: '3px 8px' }}>keep</button>
          </div>
        </>
      )}
    </div>
  );
}

function CommitBtn({ children, danger }) {
  return (
    <button style={{
      background: danger ? 'var(--red)' : 'var(--fg)',
      color: danger ? '#fff' : 'var(--bg)',
      border: '1px solid transparent',
      borderRadius: 3, padding: '4px 10px',
      fontFamily: 'var(--font-mono)', fontSize: 11,
      textTransform: 'uppercase', letterSpacing: '0.06em',
      cursor: 'pointer', lineHeight: 1,
    }}>{children}</button>
  );
}

// Sub-page tab strip — shows the four routes /entities, /entities/hop,
// /entities/columns, /entities/concentration as a horizontal nav under the
// page header. Lives inside every exploration that's part of the IA.
function SubpageTabs({ current = 'index' }) {
  const tabs = [
    { id: 'index',         label: 'Index',          route: '/entities' },
    { id: 'hop',           label: 'Hop',            route: '/entities/hop' },
    { id: 'columns',       label: 'Columns',        route: '/entities/columns' },
    { id: 'concentration', label: 'Concentration',  route: '/entities/concentration' },
    { id: 'social-map',    label: 'Social map',     route: '/entities/social-map' },
  ];
  return (
    <div style={{
      display: 'flex', alignItems: 'baseline', gap: 18,
      padding: '14px 20px 12px', borderBottom: '1px solid var(--border)',
    }}>
      {tabs.map((t) => (
        <a key={t.id} href="#" onClick={(e) => e.preventDefault()} style={{
          fontFamily: 'var(--font-sans)', fontSize: 13,
          color: current === t.id ? 'var(--fg)' : 'var(--mfg)',
          textDecoration: 'none',
          paddingBottom: 8, marginBottom: -13,
          borderBottom: current === t.id ? '2px solid var(--fg)' : '2px solid transparent',
          fontWeight: current === t.id ? 500 : 400,
        }}>{t.label}</a>
      ))}
      <span style={{ flex: 1 }} />
      <span className="mono" style={{ fontSize: 10, color: 'var(--dim)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
        /entities
      </span>
    </div>
  );
}

Object.assign(window, { ExpIndex, SubpageTabs, CommitBtn, StatePill, Tick });
