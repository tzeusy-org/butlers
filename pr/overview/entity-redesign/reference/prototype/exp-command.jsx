// 05 — Command-bar finder
//
// Press `/` anywhere in the app. A single rule-row panel takes over the
// page. Type — fuzzy-matches names, aliases, and predicates. Each result
// is a single rule-row carrying the entity, the matched fact, and an
// "↗ hop into" handle that pivots the rest of the UI without leaving the
// finder open.
//
// The keyboard story is the design. No icons. Mono captions tell you what
// keys do what.

function ExpCommand() {
  const [q, setQ] = React.useState('');
  const [cursor, setCursor] = React.useState(0);
  const inputRef = React.useRef(null);

  React.useEffect(() => { inputRef.current?.focus(); }, []);

  const results = React.useMemo(() => {
    const needle = q.trim().toLowerCase();
    const scored = [];
    for (const e of ENTITIES) {
      if (e.id === 'me') continue;
      const hayBits = [e.name, ...(e.aliases || [])].map((s) => s.toLowerCase());
      let score = 0, matchedOn = null;
      if (!needle) {
        // No query — show by weight.
        const w = (ADJ.me.find((n) => n.other === e.id)?.meta.weight) || 0;
        score = w;
        matchedOn = 'pinned';
      } else {
        for (const h of hayBits) {
          if (h.startsWith(needle)) { score = Math.max(score, 100); matchedOn = h; break; }
          if (h.includes(needle))   { score = Math.max(score, 50);  matchedOn = h; }
        }
        // also let predicate text match (e.g. 'vendor' → purchased-from)
        const adj = ADJ[e.id] || [];
        for (const n of adj) {
          const lbl = (PREDICATE_INDEX[n.pred]?.label || '').toLowerCase();
          if (lbl.includes(needle)) { score = Math.max(score, 30); matchedOn = lbl + ' relation'; }
        }
      }
      if (score > 0) scored.push({ e, score, matchedOn });
    }
    scored.sort((a, b) => b.score - a.score);
    return scored.slice(0, 8);
  }, [q]);

  React.useEffect(() => { setCursor(0); }, [q]);

  const sel = results[cursor];
  const sideAdj = sel ? (ADJ[sel.e.id] || []).slice().sort((a, b) => (b.meta.weight || 0) - (a.meta.weight || 0)).slice(0, 5) : [];

  function onKey(e) {
    if (e.key === 'ArrowDown') { setCursor((c) => Math.min(results.length - 1, c + 1)); e.preventDefault(); }
    if (e.key === 'ArrowUp')   { setCursor((c) => Math.max(0, c - 1)); e.preventDefault(); }
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', height: '100%' }}
         onKeyDown={onKey} tabIndex={0}>
      {/* LEFT — finder */}
      <div style={{ padding: '24px 24px 0', display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--border)' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <Eyebrow>finder · press / anywhere in butlers</Eyebrow>
          <span className="mono" style={{ fontSize: 10, color: 'var(--dim)', letterSpacing: '0.08em' }}>esc to close</span>
        </div>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '14px 0', borderBottom: '1px solid var(--border)',
        }}>
          <span className="mono" style={{ fontSize: 14, color: 'var(--mfg)' }}>/</span>
          <input ref={inputRef}
            value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="who knows ravi · cph trip · vendors I haven’t seen"
            style={{
              flex: 1, background: 'transparent', border: 'none', outline: 'none',
              color: 'var(--fg)',
              fontFamily: 'var(--font-sans)', fontSize: 22, fontWeight: 400,
              letterSpacing: '-0.015em',
            }} />
          <span className="mono" style={{ fontSize: 10, color: 'var(--dim)' }}>↑↓ to step · ↵ to open · ⇥ to hop</span>
        </div>

        <div style={{ overflow: 'auto', flex: 1 }}>
          {results.length === 0 && (
            <Voice italic style={{ padding: '40px 0', color: 'var(--mfg)' }}>
              Nothing matches.
            </Voice>
          )}
          {results.map(({ e, matchedOn }, i) => {
            const isC = i === cursor;
            const w = (ADJ.me.find((n) => n.other === e.id)?.meta.weight) || 0;
            const myEdge = (ADJ.me.find((n) => n.other === e.id));
            return (
              <div key={e.id}
                onMouseEnter={() => setCursor(i)}
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'auto 1fr auto auto',
                  gap: 14, alignItems: 'center',
                  padding: '12px 0',
                  borderBottom: '1px solid var(--border-soft)',
                  background: isC ? 'oklch(1 0 0 / 0.04)' : 'transparent',
                  cursor: 'pointer',
                  paddingLeft: isC ? 12 : 0,
                  borderLeft: isC ? '2px solid var(--fg)' : '2px solid transparent',
                  transition: 'padding-left 80ms linear',
                }}>
                <EntityMark entity={e} size={20} tone={isC ? 'fill' : 'neutral'} />
                <div style={{ minWidth: 0 }}>
                  <div style={{
                    fontSize: 15, color: 'var(--fg)', fontWeight: isC ? 500 : 400,
                    letterSpacing: '-0.01em', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>{e.name}</div>
                  <div className="mono" style={{ fontSize: 10, color: 'var(--dim)', textTransform: 'uppercase', letterSpacing: '0.08em', marginTop: 2 }}>
                    {e.type}
                    {e.tier ? ` · t${e.tier}` : ''}
                    {myEdge ? ` · ${PREDICATE_INDEX[myEdge.pred]?.label || myEdge.pred}` : ''}
                    {!q && ' · pinned'}
                    {q && matchedOn !== 'pinned' && ` · matched "${matchedOn}"`}
                  </div>
                </div>
                <span className="tnum mono" style={{ fontSize: 11, color: 'var(--mfg)' }}>{w ? `×${w}` : ''}</span>
                <span className="mono" style={{
                  fontSize: 10, color: isC ? 'var(--fg)' : 'var(--mfg)',
                  textTransform: 'uppercase', letterSpacing: '0.08em',
                  opacity: isC ? 1 : 0,
                }}>↗ hop</span>
              </div>
            );
          })}
        </div>

        {/* keyboard footer */}
        <div style={{
          padding: '10px 0', borderTop: '1px solid var(--border)',
          display: 'flex', gap: 18, fontFamily: 'var(--font-mono)', fontSize: 10,
          color: 'var(--mfg)', letterSpacing: '0.06em', textTransform: 'uppercase',
        }}>
          <span>· tier <KbMono>t</KbMono></span>
          <span>· type <KbMono>p / o / l</KbMono></span>
          <span>· last <KbMono>r</KbMono></span>
          <span>· merge <KbMono>m</KbMono></span>
          <span>· forget <KbMono>⇧⌫</KbMono></span>
          <span style={{ flex: 1 }} />
          <span>{results.length} of {ENTITIES.length - 1}</span>
        </div>
      </div>

      {/* RIGHT — live preview of selected, with adj relations */}
      <div style={{ padding: 24, overflow: 'auto' }}>
        {sel && (
          <>
            <Eyebrow style={{ marginBottom: 10 }}>preview · {sel.e.id}</Eyebrow>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
              <EntityMark entity={sel.e} size={32} tone="fill" />
              <div>
                <div style={{ fontSize: 20, fontWeight: 500, letterSpacing: '-0.015em' }}>{sel.e.name}</div>
                <div className="mono" style={{ fontSize: 10, color: 'var(--dim)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
                  {sel.e.type}
                  {sel.e.tier ? ` · tier ${sel.e.tier}` : ''}
                  {sel.e.aliases?.length ? ` · ${sel.e.aliases.length} alias${sel.e.aliases.length === 1 ? '' : 'es'}` : ''}
                </div>
              </div>
            </div>

            <Voice italic style={{ fontSize: 14, color: 'var(--mfg)', marginBottom: 18 }}>
              {storyFor(sel.e)}
            </Voice>

            <div style={{ borderTop: '1px solid var(--border)' }}>
              <div style={{ padding: '12px 0 4px' }}>
                <Eyebrow>relations</Eyebrow>
              </div>
              {sideAdj.map((n, i) => {
                const e = ENTITY_INDEX[n.other];
                return (
                  <div key={i} style={{
                    display: 'grid', gridTemplateColumns: '100px 1fr auto',
                    gap: 10, padding: '8px 0',
                    borderBottom: '1px solid var(--border-soft)',
                  }}>
                    <span className="kind-tag" style={{ fontSize: 9 }}>{PREDICATE_INDEX[n.pred]?.label}</span>
                    <span style={{ fontSize: 12 }}>{e?.name}</span>
                    <span className="tnum mono" style={{ fontSize: 10, color: 'var(--mfg)' }}>×{n.meta.weight || 1}</span>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function KbMono({ children }) {
  return (
    <span style={{
      border: '1px solid var(--border-strong)', padding: '1px 4px',
      borderRadius: 2, color: 'var(--fg)', marginLeft: 4,
    }}>{children}</span>
  );
}

function storyFor(e) {
  if (e.role === 'owner') return 'The root of every chain. You.';
  if (e.state === 'unidentified') return 'Seen but not yet matched. A merge would attach this to a known contact.';
  if (e.type === 'person' && e.tier === 1) return 'Among the closest. Daily-cadence relations.';
  if (e.type === 'person' && e.tier === 2) return 'Close circle. Touches weekly to monthly.';
  if (e.type === 'person') return `Tier ${e.tier} contact. Periodic touch.`;
  if (e.type === 'organization' && e.category === 'employer') return 'Current employer. Many co-attended events here.';
  if (e.type === 'organization' && e.category === 'subscription') return 'Recurring subscription. Monthly debit.';
  if (e.type === 'organization') return 'Vendor. Weight scales with receipts.';
  if (e.type === 'place') return 'A place you’ve been recorded at.';
  if (e.type === 'group') return 'A bag of entities. Membership is just mentioned-in.';
  return '—';
}

window.ExpCommand = ExpCommand;
