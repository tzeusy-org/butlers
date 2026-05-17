// 04 — Concentration
//
// Where you concentrate, by category. A bipartite reading: you on the
// left, organizations on the right, with weight bars between. Tabs flip
// the predicate — vendors (purchased-from), employers (employer-of /
// employed-by), subscriptions (subscribed-to), households (lives-at).
// Sorted by weight; reads like a balance sheet.
//
// Key insight: the same shape generalises to people-ish queries too
// (who I co-attend with most), but anchoring on orgs first builds the
// "shopping concentration" view the prompt asked about.

function ExpConcentration() {
  const TABS = [
    { id: 'purchased-from', label: 'vendors',       unit: '↦' },
    { id: 'subscribed-to',  label: 'subscriptions', unit: '↦' },
    { id: 'co-attended',    label: 'co-attended',   unit: '×' },
    { id: 'colleague-of',   label: 'colleagues',    unit: '×' },
  ];
  const [tab, setTab] = React.useState('purchased-from');

  const rows = React.useMemo(() => {
    const out = [];
    for (const r of RELATIONS) {
      const [s, p, o, meta] = r;
      if (s !== 'me' || p !== tab) continue;
      const e = ENTITY_INDEX[o];
      if (!e) continue;
      out.push({ e, weight: meta.weight || 0, lastSeen: meta.lastSeen });
    }
    out.sort((a, b) => b.weight - a.weight);
    return out;
  }, [tab]);

  const total = rows.reduce((a, r) => a + r.weight, 0);
  const top = rows[0]?.weight || 1;

  // Concentration index — share of top 3.
  const top3 = rows.slice(0, 3).reduce((a, r) => a + r.weight, 0);
  const concentration = total > 0 ? Math.round((top3 / total) * 100) : 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <SubpageTabs current="concentration" />
      <div style={{ padding: 24, flex: 1, minHeight: 0, boxSizing: 'border-box', display: 'flex', flexDirection: 'column' }}>
      {/* Tab strip */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 }}>
        <div style={{ display: 'flex', gap: 6 }}>
          {TABS.map((t) => (
            <Pill key={t.id} active={tab === t.id} onClick={() => setTab(t.id)} count={
              RELATIONS.filter(([s, p]) => s === 'me' && p === t.id).length
            }>{t.label}</Pill>
          ))}
        </div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 14 }}>
          <Eyebrow>top 3 share</Eyebrow>
          <span className="tnum" style={{ fontFamily: 'var(--font-sans)', fontSize: 22, fontWeight: 500, letterSpacing: '-0.02em' }}>{concentration}%</span>
        </div>
      </div>

      {/* Header rule */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '140px 1fr 80px 60px',
        borderTop: '1px solid var(--border)',
        borderBottom: '1px solid var(--border-soft)',
        padding: '8px 0',
      }}>
        <Eyebrow>org</Eyebrow>
        <Eyebrow>share</Eyebrow>
        <Eyebrow style={{ textAlign: 'right' }}>touches</Eyebrow>
        <Eyebrow style={{ textAlign: 'right' }}>last</Eyebrow>
      </div>

      {/* Rows */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        {rows.length === 0 && (
          <Voice italic style={{ padding: 32, fontSize: 14, color: 'var(--mfg)' }}>
            Nothing here.
          </Voice>
        )}
        {rows.map(({ e, weight, lastSeen }, i) => {
          const pct = total > 0 ? (weight / total) * 100 : 0;
          const barW = (weight / top) * 100;
          return (
            <div key={e.id} style={{
              display: 'grid',
              gridTemplateColumns: '140px 1fr 80px 60px',
              gap: 16, alignItems: 'center',
              padding: '12px 0',
              borderBottom: '1px solid var(--border-soft)',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                <EntityMark entity={e} size={16} />
                <div style={{ minWidth: 0, overflow: 'hidden' }}>
                  <div style={{ fontSize: 13, color: 'var(--fg)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {e.name}
                  </div>
                </div>
              </div>
              <div style={{ position: 'relative', height: 18 }}>
                <div style={{
                  position: 'absolute', left: 0, top: 6, bottom: 6,
                  width: `${barW}%`, background: 'var(--fg)', opacity: 0.92,
                }} />
                <div className="mono tnum" style={{
                  position: 'absolute', left: `calc(${barW}% + 8px)`, top: 0,
                  fontSize: 10, color: 'var(--mfg)', lineHeight: '18px',
                }}>
                  {pct.toFixed(1)}%
                </div>
              </div>
              <div className="mono tnum" style={{ fontSize: 11, color: 'var(--mfg)', textAlign: 'right' }}>×{weight}</div>
              <div className="mono tnum" style={{ fontSize: 10, color: 'var(--dim)', textAlign: 'right' }}>
                {lastSeen ? lastSeen.slice(5) : '—'}
              </div>
            </div>
          );
        })}
      </div>

      <div style={{
        marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border)',
        display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 18,
      }}>
        <div>
          <Eyebrow>total touches</Eyebrow>
          <div className="tnum" style={{ fontSize: 18, fontWeight: 500, marginTop: 4 }}>{total}</div>
        </div>
        <div>
          <Eyebrow>orgs</Eyebrow>
          <div className="tnum" style={{ fontSize: 18, fontWeight: 500, marginTop: 4 }}>{rows.length}</div>
        </div>
        <div>
          <Eyebrow>top</Eyebrow>
          <div style={{ fontSize: 13, marginTop: 4 }}>{rows[0]?.e.name || '—'}</div>
        </div>
        <div>
          <Eyebrow>tail (&lt;1%)</Eyebrow>
          <div className="tnum" style={{ fontSize: 18, fontWeight: 500, marginTop: 4 }}>
            {rows.filter((r) => total > 0 && r.weight / total < 0.01).length}
          </div>
        </div>
      </div>
    </div>
    </div>
  );
}

window.ExpConcentration = ExpConcentration;
