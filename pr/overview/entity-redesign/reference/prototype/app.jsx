// Top-level composition. Reorganised after round 1 feedback:
//
//   1. Index, Hop, Columns, Concentration become sub-routes of /entities
//      sharing one page chrome.
//   2. The Index is the actual landing — curation actions (promote,
//      archive, delete, merge) live here, plus the contacts fold-in.
//   3. The Entity detail page (/entities/:id) gets a deep dive with two
//      proposed variants.
//   4. Orbit + Finder remain as appendix sketches — useful affordances
//      to harvest but not the primary IA.

function NowLine() {
  const now = new Date('2026-05-16T14:21');
  const t = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  const d = now.toLocaleDateString([], { weekday: 'long', month: 'short', day: 'numeric', year: 'numeric' });
  return (
    <div className="eyebrow">overview · {d} · {t} · entities</div>
  );
}

function HeaderHero() {
  return (
    <div style={{
      paddingTop: 56, paddingBottom: 24,
      display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 56, alignItems: 'baseline',
    }}>
      <div>
        <NowLine />
        <Display size={56} style={{ marginTop: 18, maxWidth: '18ch' }}>
          A house full of names, in order of intimacy.
        </Display>
        <Voice style={{ marginTop: 22 }}>
          Entities and memory are the two long-running stores in the Butlers
          house. Where memory holds the facts a butler heard, entities hold
          the people and things those facts attach to. Each one is a node
          in an RDF graph rooted at the owner. This dispatch is a working
          document in three movements: a catalog of what the entity store
          already does, a re-cut information architecture for{' '}
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 14 }}>/entities</span>,
          and a deep dive into the per-entity page.
        </Voice>
      </div>
      <div style={{ borderLeft: '1px solid var(--border)', paddingLeft: 28 }}>
        <Eyebrow>thesis</Eyebrow>
        <Voice italic style={{ marginTop: 10, fontSize: 14, color: 'var(--mfg)' }}>
          The list is the home. Everything graph-like is an alternate view
          of the same list, never a separate product.
        </Voice>
        <div style={{ marginTop: 22, paddingTop: 14, borderTop: '1px solid var(--border)' }}>
          <Eyebrow>contents</Eyebrow>
          <ol style={{ margin: '10px 0 0', padding: 0, listStyle: 'none' }}>
            {[
              ['I',   'What the entity store already does'],
              ['II',  'IA · the /entities route family'],
              ['00',  '— Index · tabular landing + curation'],
              ['01',  '— Hop · re-centre on anything'],
              ['02',  '— Columns · drill predicate-by-predicate'],
              ['03',  '— Concentration · the balance-sheet view'],
              ['III', 'Entity detail · deep dive'],
              ['A',   '— Editorial · the dispatch'],
              ['B',   '— Workbench · the power-user view'],
              ['IV',  'Appendix · sketches we kept'],
            ].map(([n, t], i) => (
              <li key={i} style={{
                display: 'grid', gridTemplateColumns: '40px 1fr', gap: 10,
                padding: '6px 0', borderBottom: '1px solid var(--border-soft)',
                fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--mfg)',
              }}>
                <span className="tnum" style={{ color: n.length > 2 ? 'var(--dim)' : 'var(--fg)' }}>{n}</span>
                <span style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--fg)' }}>{t}</span>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}

// IA panel — explains how routes nest before the artboards arrive.
function IAPanel() {
  const rows = [
    ['/entities',                 'Tabular index. Default. Curation queue in the right rail.', 'Index'],
    ['/entities/hop',             'Re-centre on any node; predicate-grouped fan-out.',         'Hop'],
    ['/entities/columns',         'Finder-style drill, predicate-by-predicate.',               'Columns'],
    ['/entities/concentration',   'Bipartite balance-sheet for predicate weight.',             'Concentration'],
    ['/entities/social-map',      'Existing Dunbar map. Kept.',                                'Social map'],
    ['/entities/:id',             'Per-entity page. See §III for the redesign.',               'Detail'],
    ['/contacts → /entities?has=contact', 'Folded in. Contact facts are predicates on entities.', 'Removed'],
  ];
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '300px 1fr 130px',
      borderTop: '1px solid var(--border)',
    }}>
      {rows.map(([route, gloss, view], i) => (
        <React.Fragment key={i}>
          <div style={{
            padding: '12px 0', borderBottom: '1px solid var(--border-soft)',
            fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--fg)',
          }}>{route}</div>
          <div style={{
            padding: '12px 18px', borderBottom: '1px solid var(--border-soft)',
            fontFamily: 'var(--font-serif)', fontSize: 14, lineHeight: 1.5, color: 'var(--fg)',
          }}>{gloss}</div>
          <div style={{
            padding: '12px 0', borderBottom: '1px solid var(--border-soft)',
            fontFamily: 'var(--font-mono)', fontSize: 10, color: view === 'Removed' ? 'var(--amber)' : 'var(--mfg)',
            textTransform: 'uppercase', letterSpacing: '0.08em', textAlign: 'right',
          }}>{view}</div>
        </React.Fragment>
      ))}
    </div>
  );
}

function Closing() {
  return (
    <section style={{ padding: '48px 0 80px', borderTop: '1px solid var(--border)' }}>
      <Eyebrow style={{ marginBottom: 18 }}>v · next</Eyebrow>
      <Title size={28} style={{ marginBottom: 18, maxWidth: '26ch' }}>
        Where to push, and what to leave alone.
      </Title>
      <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 56 }}>
        <Voice>
          The Index is the build. Everything else is a route under it. The
          right rail's queue does most of the system's quiet work — promote,
          merge, archive, forget — and is the only place state colour is
          ever allowed to appear on this page.
          <br /><br />
          Of the alternate views, Hop is the one to wire into production
          first. Columns and Concentration follow naturally once Hop's
          centre-and-neighbours primitive is solid. Orbit and Finder remain
          in the appendix — pieces to harvest, not pages to ship.
        </Voice>
        <div>
          <Eyebrow>cautions</Eyebrow>
          <ul style={{ margin: '10px 0 0', padding: 0, listStyle: 'none', fontFamily: 'var(--font-serif)', fontSize: 15, lineHeight: 1.55 }}>
            {[
              'The Index must stay scannable above all. Filters compose, but no filter is on by default.',
              'Contact predicates are multi-valued. Never collapse them to "the email" — show the count when there is more than one.',
              'Provenance and confidence belong in the gutter on the detail page. Never in the headline.',
              'Forgetting is a first-class action. Surface it on every entity, with a serif gloss explaining what stays and what tombstones.',
              'Dunbar tiers are a useful axis, not a verdict. Manual overrides must always feel cheap.',
              'No node-link diagram should be the default page — graphs are for inspection, not navigation.',
            ].map((s, i) => (
              <li key={i} style={{ padding: '8px 0', borderBottom: '1px solid var(--border-soft)' }}>{s}</li>
            ))}
          </ul>
        </div>
      </div>
      <Voice italic style={{ marginTop: 40, fontSize: 13, color: 'var(--mfg)' }}>
        End of dispatch.
      </Voice>
    </section>
  );
}

function App() {
  return (
    <main style={{
      maxWidth: 1280, margin: '0 auto', padding: '0 56px 80px',
      color: 'var(--fg)',
    }}>
      <HeaderHero />

      <Section eyebrow="i · catalog"
               title="What the entity store already does"
               lede={<>
                 The list below is the working capability set of the entity
                 system today, refreshed after round 1 — contact predicates
                 and duplicate / stale states now feature explicitly. The
                 design exercise that follows is allowed to assume all of
                 this exists.
               </>}>
        <CatalogKpis />
        <div style={{ height: 24 }} />
        <CatalogTable />
      </Section>

      <Section eyebrow="ii · information architecture"
               title="One landing, four siblings, one fold-in."
               lede={<>
                 The Index is the only canonical landing for{' '}
                 <span className="mono" style={{ fontSize: 14 }}>/entities</span>.
                 Hop, Columns, Concentration, and the existing Social Map are
                 sibling sub-routes that share the page chrome. The old{' '}
                 <span className="mono" style={{ fontSize: 14 }}>/contacts</span>{' '}
                 page is folded in — contact information is predicates on a
                 person, not a separate noun.
               </>}>
        <IAPanel />

        {/* The Index — direction 00 */}
        <Artboard idx={0} name="index · /entities · the home"
          height={760}
          hypothesis={<>The tabular landing. Filters by type and by state across the top; the full list reads as rule-rows in the middle; the right rail is the curation queue where unidentified, duplicate-candidate, and stale entities surface with one-click commits. Bulk select promotes the row gutter to an action bar.</>}>
          <ExpIndex />
        </Artboard>

        <Artboard idx={1} name="hop · /entities/hop · re-centre on anything"
          height={760}
          hypothesis={<>Hover and click any node to make it the new centre; predicate-grouped neighbours fan out around it. The breadcrumb makes the chain of hops visible so you can step back to where you were. This is the alternate view to build first.</>}>
          <ExpHop />
        </Artboard>

        <Artboard idx={2} name="columns · /entities/columns · drill predicate-by-predicate"
          height={620}
          hypothesis={<>A Finder-style cascade. Each column is one entity's outgoing relations, grouped by predicate. Reads like a directory; the trail is the columns themselves; nothing animates.</>}>
          <ExpColumns />
        </Artboard>

        <Artboard idx={3} name="concentration · /entities/concentration · the balance-sheet view"
          height={680}
          hypothesis={<>Pick a predicate (purchased-from, subscribed-to, co-attended) and read the world as a list of weights. Top-three-share is the headline number. Answers <em>where am I concentrated</em> without a graph drawing.</>}>
          <ExpConcentration />
        </Artboard>
      </Section>

      <Section eyebrow="iii · entity detail"
               title="What sits behind /entities/:id"
               lede={<>
                 Both proposals share Dispatch vocabulary and the same
                 content — hero, relations, contact facts, activity,
                 provenance, curation. They differ in priority. Editorial
                 reads as a sheet of paper about a person; Workbench reads
                 as a console for fixing one. I'd ship Editorial as the
                 default and offer Workbench as a power-user toggle.
                 <br /><br />
                 <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--mfg)' }}>
                   Note: working from the design system, not the live page.
                   Anywhere these designs depart from what's there today, it's an
                   intentional proposal — happy to reconcile against a screenshot.
                 </span>
               </>}>
        <Artboard idx={'A'} name="detail · editorial · the dispatch"
          height={920}
          hypothesis={<>Two-column editorial. Left is the narrative — hero, relations, recent activity. Right is the index — contacts grouped by predicate (multi-valued visible), aliases, provenance, and a quiet curation rail at the bottom. The page can be read top to bottom and put down.</>}>
          <DetailEditorial entityId="p-lin" />
        </Artboard>

        <Artboard idx={'B'} name="detail · workbench · the console"
          height={780}
          hypothesis={<>Three columns. Left rail: related entities and "introduced via" suggestions. Middle: hero + KPIs + raw triples (the workbench's signature — actual RDF visible). Right rail: curation actions plus a confidence inspector. Best for high-volume curation sessions after an import.</>}>
          <DetailWorkbench entityId="p-tan2" />
        </Artboard>
      </Section>

      <Section eyebrow="iv · appendix"
               title="Sketches we kept."
               lede={<>
                 Two sketches from round 1 that didn't make the primary IA
                 but are worth harvesting. Orbit is a one-image overview;
                 Finder is the keyboard handle that should attach to every
                 page in the system, not just Entities.
               </>}>
        <Artboard idx={'i'} name="orbit · sketch"
          height={620}
          hypothesis={<>One image of the whole circle. Recency becomes angle, tier becomes radius. Not a default page; possibly a third "view mode" toggle on the Index, or the empty-state of /entities/social-map.</>}>
          <ExpOrbit />
        </Artboard>

        <Artboard idx={'ii'} name="finder · sketch (app-wide)"
          height={580}
          hypothesis={<>Press <span className="mono" style={{ fontSize: 12 }}>/</span> anywhere in Butlers. This isn't an Entities page — it's an app-wide command surface that resolves to an entity (or an Approval, a Rule, an Episode). Build the entity case first because it's the richest.</>}>
          <ExpCommand />
        </Artboard>
      </Section>

      <Closing />
    </main>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
