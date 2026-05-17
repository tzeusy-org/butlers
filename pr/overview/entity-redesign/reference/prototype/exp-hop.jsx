// 02 — Hop Explorer
//
// Click any entity to make it the new centre. Direct neighbours fan out
// around it, with the predicate written on the edge. A breadcrumb keeps
// the chain of hops so you can step back. The right pane is the canonical
// entity detail — never modal, always present, swaps in place.
//
// This is the direction we'd push hardest on. Hop until you're somewhere
// you didn't expect to be, then return.

function ExpHop() {
  const [centerId, setCenterId] = React.useState('me');
  const [trail, setTrail] = React.useState(['me']);
  const [hover, setHover] = React.useState(null);
  const [predFilter, setPredFilter] = React.useState(null);

  const center = ENTITY_INDEX[centerId];
  const rawNeighbours = ADJ[centerId] || [];
  const neighbours = predFilter
    ? rawNeighbours.filter((n) => n.pred === predFilter)
    : rawNeighbours;

  // Group neighbours by predicate so we can draw clean wedges per group.
  const groups = React.useMemo(() => {
    const m = new Map();
    for (const n of neighbours) {
      if (!m.has(n.pred)) m.set(n.pred, []);
      m.get(n.pred).push(n);
    }
    return [...m.entries()].map(([pred, items]) => ({ pred, items }));
  }, [neighbours]);

  // Deterministic radial layout: each predicate gets a wedge slice; nodes
  // within the wedge fan along an arc.
  const W = 540, H = 460, CX = W / 2, CY = H / 2;
  const R = 165;
  const totalGroups = Math.max(1, groups.length);
  const wedgeAngle = (Math.PI * 2) / totalGroups;

  const positioned = [];
  groups.forEach((g, gi) => {
    const center = -Math.PI / 2 + gi * wedgeAngle;
    const items = g.items;
    items.forEach((n, ni) => {
      const t = items.length === 1 ? 0 : (ni / (items.length - 1) - 0.5);
      const spread = Math.min(wedgeAngle * 0.7, items.length * 0.18);
      const a = center + t * spread;
      const r = R + (ni % 2) * 14;
      positioned.push({
        ...n,
        x: CX + Math.cos(a) * r,
        y: CY + Math.sin(a) * r,
        labelX: CX + Math.cos(center) * (R + 36),
        labelY: CY + Math.sin(center) * (R + 36),
        predLabel: PREDICATE_INDEX[g.pred]?.label || g.pred,
        groupCenterAngle: center,
        groupIndex: gi,
      });
    });
  });

  function recenter(id) {
    if (id === centerId) return;
    setCenterId(id);
    setTrail((t) => [...t, id]);
  }
  function popTo(idx) {
    setCenterId(trail[idx]);
    setTrail((t) => t.slice(0, idx + 1));
  }

  // Predicate filter set for the chips
  const predsHere = [...new Set(rawNeighbours.map((n) => n.pred))];

  // Right panel content (centre entity details)
  const inboundCount = rawNeighbours.length;

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 280px', height: '100%' }}>
      {/* Graph pane */}
      <div style={{ position: 'relative', borderRight: '1px solid var(--border)', minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        <SubpageTabs current="hop" />
        <div style={{ position: 'relative', flex: 1, minHeight: 0 }}>
        {/* Breadcrumb trail */}
        <div style={{
          position: 'absolute', top: 14, left: 16, right: 16, zIndex: 2,
          display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap',
        }}>
          <Eyebrow>trail</Eyebrow>
          {trail.map((id, i) => {
            const e = ENTITY_INDEX[id];
            return (
              <React.Fragment key={i + '-' + id}>
                {i > 0 && <span style={{ color: 'var(--dim)', fontFamily: 'var(--font-mono)', fontSize: 10 }}>›</span>}
                <button onClick={() => popTo(i)} style={{
                  background: 'transparent', border: 'none', padding: 0, cursor: 'pointer',
                  fontFamily: 'var(--font-sans)', fontSize: 11,
                  color: i === trail.length - 1 ? 'var(--fg)' : 'var(--mfg)',
                  textDecoration: i === trail.length - 1 ? 'none' : 'underline',
                  textUnderlineOffset: 3, textDecorationColor: 'var(--border-strong)',
                }}>{e ? e.name : id}</button>
              </React.Fragment>
            );
          })}
          <span style={{ flex: 1 }} />
          {trail.length > 1 && (
            <button onClick={() => { setCenterId('me'); setTrail(['me']); }} className="pill">reset</button>
          )}
        </div>

        {/* Predicate filter chips */}
        {predsHere.length > 1 && (
          <div style={{
            position: 'absolute', bottom: 14, left: 16, right: 16, zIndex: 2,
            display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap',
          }}>
            <Eyebrow>edges</Eyebrow>
            <Pill active={!predFilter} onClick={() => setPredFilter(null)}>all</Pill>
            {predsHere.map((p) => (
              <Pill key={p} active={predFilter === p}
                onClick={() => setPredFilter(predFilter === p ? null : p)}>
                {PREDICATE_INDEX[p]?.label || p}
              </Pill>
            ))}
          </div>
        )}

        {/* SVG */}
        <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: '100%', display: 'block' }}>
          {/* edges */}
          {positioned.map((n, idx) => (
            <line key={`l-${idx}-${n.pred}-${n.other}`} x1={CX} y1={CY} x2={n.x} y2={n.y}
              stroke={hover === n.other ? 'var(--fg)' : 'var(--border)'}
              strokeWidth={hover === n.other ? 1.4 : 1} />
          ))}
          {/* predicate labels on the wedge ring */}
          {groups.map((g, gi) => {
            const a = -Math.PI / 2 + gi * wedgeAngle;
            const x = CX + Math.cos(a) * (R + 50);
            const y = CY + Math.sin(a) * (R + 50);
            return (
              <text key={g.pred} x={x} y={y} fill="var(--mfg)"
                textAnchor="middle" dominantBaseline="central"
                style={{
                  fontFamily: 'var(--font-mono)', fontSize: 10,
                  textTransform: 'uppercase', letterSpacing: '0.1em',
                }}>
                {PREDICATE_INDEX[g.pred]?.label || g.pred}
                <tspan dx="6" fill="var(--dim)">·{g.items.length}</tspan>
              </text>
            );
          })}
          {/* centre node */}
          <g>
            <circle cx={CX} cy={CY} r={20} fill="var(--fg)" />
            <text x={CX} y={CY} fill="var(--bg)" textAnchor="middle" dominantBaseline="central"
              style={{ fontFamily: 'var(--font-sans)', fontSize: 11, fontWeight: 600 }}>
              {markText(center)}
            </text>
            <text x={CX} y={CY + 36} fill="var(--fg)" textAnchor="middle"
              style={{ fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500 }}>
              {center?.name}
            </text>
            <text x={CX} y={CY + 52} fill="var(--mfg)" textAnchor="middle"
              style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase' }}>
              {center?.type}
            </text>
          </g>
          {/* neighbour nodes */}
          {positioned.map((n, idx) => {
            const e = ENTITY_INDEX[n.other];
            if (!e) return null;
            const isH = hover === n.other;
            const radius = Math.max(8, Math.min(16, 6 + Math.sqrt(n.meta.weight || 1) * 0.6));
            return (
              <g key={`n-${idx}-${n.pred}-${n.other}`}
                onMouseEnter={() => setHover(n.other)}
                onMouseLeave={() => setHover(null)}
                onClick={() => recenter(n.other)}
                style={{ cursor: 'pointer' }}>
                <circle cx={n.x} cy={n.y} r={radius + 8} fill="transparent" />
                <circle cx={n.x} cy={n.y} r={radius}
                  fill={isH ? 'var(--fg)' : 'var(--bg-deep)'}
                  stroke="var(--fg)" strokeWidth={isH ? 0 : 1} />
                <text x={n.x} y={n.y} textAnchor="middle" dominantBaseline="central"
                  fill={isH ? 'var(--bg)' : 'var(--fg)'}
                  style={{ fontFamily: 'var(--font-sans)', fontSize: 9, fontWeight: 600 }}>
                  {markText(e)}
                </text>
                <text x={n.x} y={n.y + radius + 12} textAnchor="middle"
                  fill={isH ? 'var(--fg)' : 'var(--mfg)'}
                  style={{ fontFamily: 'var(--font-sans)', fontSize: 11 }}>
                  {e.name}
                </text>
              </g>
            );
          })}
        </svg>
        </div>
      </div>

      {/* Detail pane */}
      <div style={{ padding: 18, overflow: 'auto' }}>
        <Eyebrow style={{ marginBottom: 8 }}>centre</Eyebrow>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4,
        }}>
          <EntityMark entity={center} size={22} />
          <div style={{ minWidth: 0 }}>
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 17, fontWeight: 500, letterSpacing: '-0.01em' }}>
              {center?.name}
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--dim)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
              {center?.type}{center?.role === 'owner' ? ' · owner' : ''}
              {center?.tier ? ` · tier ${center.tier}` : ''}
            </div>
          </div>
        </div>

        {(center?.firstSeen || center?.lastSeen) && (
          <div style={{
            marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border-soft)',
            display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12,
          }}>
            {center?.firstSeen && (
              <div>
                <Eyebrow>first seen</Eyebrow>
                <div className="tnum mono" style={{ fontSize: 12, marginTop: 4 }}>{center.firstSeen}</div>
              </div>
            )}
            {center?.lastSeen && (
              <div>
                <Eyebrow>last seen</Eyebrow>
                <div className="tnum mono" style={{ fontSize: 12, marginTop: 4 }}>{center.lastSeen}</div>
              </div>
            )}
          </div>
        )}

        {center?.aliases?.length > 0 && (
          <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border-soft)' }}>
            <Eyebrow>aliases</Eyebrow>
            <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {center.aliases.map((a) => (
                <span key={a} className="kind-tag" style={{
                  border: '1px solid var(--border-soft)', padding: '2px 6px',
                  fontSize: 10, color: 'var(--mfg)',
                }}>{a}</span>
              ))}
            </div>
          </div>
        )}

        <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border-soft)' }}>
          <Eyebrow>relations <span style={{ color: 'var(--dim)' }}>· {inboundCount}</span></Eyebrow>
          <div style={{ marginTop: 4 }}>
            {rawNeighbours
              .slice()
              .sort((a, b) => (b.meta.weight || 0) - (a.meta.weight || 0))
              .slice(0, 8)
              .map((n, i) => {
                const e = ENTITY_INDEX[n.other];
                return (
                  <div key={i} onClick={() => recenter(n.other)} style={{
                    display: 'grid', gridTemplateColumns: 'auto 1fr auto',
                    gap: 8, padding: '8px 0',
                    borderBottom: '1px solid var(--border-soft)',
                    cursor: 'pointer', alignItems: 'center',
                  }}
                  onMouseEnter={() => setHover(n.other)}
                  onMouseLeave={() => setHover(null)}>
                    <span className="kind-tag" style={{ width: 80, fontSize: 9 }}>
                      {n.dir === 'out' ? '→' : '←'}&nbsp;{PREDICATE_INDEX[n.pred]?.label || n.pred}
                    </span>
                    <span style={{ fontSize: 12, color: 'var(--fg)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {e ? e.name : n.other}
                    </span>
                    <span className="tnum mono" style={{ fontSize: 10, color: 'var(--mfg)' }}>
                      ×{n.meta.weight || 1}
                    </span>
                  </div>
                );
              })}
          </div>
        </div>

        <Voice italic style={{ marginTop: 18, fontSize: 13, color: 'var(--mfg)' }}>
          Click any node to make it the centre.
        </Voice>
      </div>
    </div>
  );
}

// Initials for the in-svg mark (we can't easily render the EntityMark JSX
// in SVG, so do a tiny inline equivalent).
function markText(e) {
  if (!e) return '?';
  if (e.type === 'person') {
    return e.name.split(/\s+/).slice(0, 2).map((w) => w[0]).join('').toUpperCase();
  }
  return TYPES[e.type]?.glyph || '?';
}

window.ExpHop = ExpHop;
