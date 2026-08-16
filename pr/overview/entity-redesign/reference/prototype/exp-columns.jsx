// 03 — Miller columns
//
// Drill the graph left-to-right. Each column is a list of entities you can
// step into; selecting one opens the next column to its right with that
// entity's relations, grouped by predicate. No node-link drawing — pure
// type and rules, hop by reading and clicking.
//
// Pattern lifted from Finder. The win: you can keep state stable as you
// step deeper. The trail is the columns themselves.

function ExpColumns() {
  // path is a list of {id, pred?}, where pred is the predicate you took to
  // get here from the previous column.
  const [path, setPath] = React.useState([{ id: 'me' }]);

  // For each path slot, the next column is its outgoing relations,
  // optionally narrowed to one predicate.
  function columnFor(slot, depth) {
    const adj = ADJ[slot.id] || [];
    // Pick top relations by weight, grouped by predicate.
    const groups = {};
    for (const n of adj) {
      if (!groups[n.pred]) groups[n.pred] = [];
      groups[n.pred].push(n);
    }
    Object.values(groups).forEach((arr) => arr.sort((a, b) => (b.meta.weight || 0) - (a.meta.weight || 0)));
    const orderedPreds = Object.keys(groups).sort((a, b) =>
      Math.max(...groups[b].map((n) => n.meta.weight || 0)) -
      Math.max(...groups[a].map((n) => n.meta.weight || 0))
    );
    return { groups, orderedPreds, entity: ENTITY_INDEX[slot.id] };
  }

  function push(idx, neighbourId) {
    const newPath = path.slice(0, idx + 1);
    newPath.push({ id: neighbourId });
    setPath(newPath);
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <SubpageTabs current="columns" />
      <div style={{ display: 'flex', flex: 1, overflow: 'auto', minHeight: 0 }}>
      {path.map((slot, depth) => {
        const col = columnFor(slot, depth);
        const e = col.entity;
        const selectedNext = path[depth + 1];
        const isLast = depth === path.length - 1;
        return (
          <div key={depth} style={{
            width: 280, flexShrink: 0,
            borderRight: '1px solid var(--border)',
            display: 'flex', flexDirection: 'column',
          }}>
            {/* Column header */}
            <div style={{
              padding: '12px 14px',
              borderBottom: '1px solid var(--border)',
              background: depth === 0 ? 'var(--bg-elev)' : 'transparent',
            }}>
              <Eyebrow>{depth === 0 ? 'owner' : `hop ${depth}`}</Eyebrow>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}>
                <EntityMark entity={e} size={18} tone={depth === 0 ? 'fill' : 'neutral'} />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 14, fontWeight: 500, letterSpacing: '-0.01em' }}>{e?.name}</div>
                  <div className="mono" style={{ fontSize: 9, color: 'var(--dim)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
                    {e?.type}
                    {e?.tier ? ` · tier ${e.tier}` : ''}
                    {e?.state === 'unidentified' ? ' · unidentified' : ''}
                  </div>
                </div>
              </div>
            </div>

            {/* Relations grouped by predicate */}
            <div style={{ flex: 1, overflow: 'auto' }}>
              {col.orderedPreds.length === 0 && (
                <Voice italic style={{ padding: 18, fontSize: 13, color: 'var(--mfg)' }}>
                  Nothing further.
                </Voice>
              )}
              {col.orderedPreds.map((pred) => {
                const items = col.groups[pred];
                return (
                  <div key={pred}>
                    <div style={{
                      padding: '10px 14px 6px',
                      display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
                      borderBottom: '1px solid var(--border-soft)',
                    }}>
                      <Eyebrow>{PREDICATE_INDEX[pred]?.label || pred}</Eyebrow>
                      <span className="mono tnum" style={{ fontSize: 9, color: 'var(--dim)' }}>{items.length}</span>
                    </div>
                    {items.slice(0, 6).map((n, i) => {
                      const ne = ENTITY_INDEX[n.other];
                      if (!ne) return null;
                      const active = selectedNext && selectedNext.id === ne.id;
                      return (
                        <div key={i}
                          onClick={() => push(depth, ne.id)}
                          style={{
                            padding: '8px 14px',
                            display: 'grid',
                            gridTemplateColumns: 'auto 1fr auto',
                            gap: 10, alignItems: 'center',
                            background: active ? 'oklch(1 0 0 / 0.06)' : 'transparent',
                            cursor: 'pointer',
                            borderBottom: '1px solid var(--border-soft)',
                          }}
                          onMouseEnter={(e2) => { if (!active) e2.currentTarget.style.background = 'oklch(1 0 0 / 0.03)'; }}
                          onMouseLeave={(e2) => { if (!active) e2.currentTarget.style.background = 'transparent'; }}>
                          <EntityMark entity={ne} size={16} />
                          <div style={{ minWidth: 0 }}>
                            <div style={{ fontSize: 13, color: 'var(--fg)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {ne.name}
                            </div>
                            {ne.tier && (
                              <div className="mono" style={{ fontSize: 9, color: 'var(--dim)' }}>
                                t{ne.tier}
                              </div>
                            )}
                          </div>
                          <div style={{
                            display: 'flex', alignItems: 'center', gap: 4,
                            color: active ? 'var(--fg)' : 'var(--mfg)',
                          }}>
                            <span className="tnum mono" style={{ fontSize: 10 }}>×{n.meta.weight || 1}</span>
                            <span className="mono" style={{ fontSize: 10 }}>›</span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                );
              })}
            </div>

            {isLast && (
              <div style={{
                padding: '10px 14px', borderTop: '1px solid var(--border)',
                fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--dim)',
                textTransform: 'uppercase', letterSpacing: '0.1em',
              }}>step deeper →</div>
            )}
          </div>
        );
      })}
      </div>
    </div>
  );
}

window.ExpColumns = ExpColumns;
