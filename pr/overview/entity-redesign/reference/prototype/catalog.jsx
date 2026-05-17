// Catalog — what the entity system already supports.
// Read as a memo: each row is one capability, with a one-line gloss and a
// pointer back to where it lives in the codebase.

const CATALOG = [
  {
    group: 'Model',
    items: [
      { name: 'Triple-store core (RDF)',
        gloss: 'Every fact about an entity is a subject–predicate–object triple. Memory and entities share the same backing store.',
        where: 'butlers backend · ontology.ttl' },
      { name: 'Owner entity',
        gloss: 'A single distinguished entity for "you" — every relation is reachable in finite hops from here.',
        where: '--role-owner badge · EntityDetailPage' },
      { name: 'Typed entities',
        gloss: 'Person, organization, place, product, account, event, group. Type drives the glyph in the mark, not the hue.',
        where: 'index.css · EntityMark' },
      { name: 'Aliases',
        gloss: 'Many surface forms collapse onto one entity — "mum", "Amy Lim", "amylim@" all resolve.',
        where: 'identity-resolver · EntitiesPage' },
      { name: 'Contact predicates (multi-valued)',
        gloss: 'has-email, has-phone, has-handle, has-address, has-birthday, has-website. Each is multi-valued and carries provenance + verified state. The old /contacts page is just an /entities filter over these.',
        where: 'CONTACT_FACTS · contact namespace' },
      { name: 'Roles',
        gloss: 'A small set of capability badges that color contact handling — owner, admin.',
        where: '--role-owner / --role-admin in index.css' },
      { name: 'Confidence',
        gloss: 'Each triple carries a confidence score; the UI surfaces low-confidence facts as candidates, never as truth.',
        where: 'relation.conf · approvals queue' },
      { name: 'Provenance',
        gloss: 'Every fact remembers which butler wrote it and from which source — receipt, email thread, calendar invite.',
        where: 'relation.src · timeline' },
      { name: 'First-seen / last-seen',
        gloss: 'Two timestamps per entity and per relation, indexed for time-series queries.',
        where: 'entity.firstSeen / lastSeen' },
      { name: 'Unidentified state',
        gloss: 'Pending entities that haven\u2019t been matched to a known contact, surfaced for the user to merge or dismiss.',
        where: '--state-unidentified · #ea580c' },
      { name: 'Duplicate candidate',
        gloss: 'Two entities flagged as the same thing by identity-resolver — shared email, shared phone, near-duplicate name. Surfaced in the queue with a one-click merge.',
        where: 'identity-resolver · needs-you queue' },
      { name: 'Stale state',
        gloss: 'An entity with no touches for an extended period — candidate for archive. Stays in the graph but drops from default lists.',
        where: 'stale state · needs-you queue' },
    ],
  },
  {
    group: 'Surfaces',
    items: [
      { name: 'Entities list (/entities)',
        gloss: 'Flat, filterable index of every known entity, with type, tier, and last activity. The canonical entry point; bulk actions live in the gutter.',
        where: 'EntitiesPage.tsx' },
      { name: 'Sub-routes under /entities',
        gloss: 'The list is the default; sibling routes /entities/hop, /entities/columns, /entities/concentration, /entities/social-map share the page chrome but render a different navigation surface.',
        where: 'router.tsx · new IA below' },
      { name: 'Entity detail (/entities/:id)',
        gloss: 'Unified per-entity page — used to live under /butlers/relationship; folded back into one canonical view.',
        where: 'EntityDetailPage.tsx (RelationshipEntityRedirect)' },
      { name: 'Social Map (/entities/social-map)',
        gloss: 'Dunbar-style concentric tiers around the owner; tier badges colored 1–5 in --tier tokens.',
        where: 'SocialMapPage.tsx · dunbarTierBadgeStyle' },
      { name: 'Per-activity zoom-in',
        gloss: 'An entity\u2019s page can drill into a single activity (a thread, a trip, a transaction) without losing the entity frame.',
        where: 'activity sub-route in EntityDetailPage' },
    ],
  },
  {
    group: 'Inference',
    items: [
      { name: 'Dunbar tier classifier',
        gloss: 'Bins people into five tiers (inner / close / extended / acq / distant) using touch counts, recency, and explicit signals.',
        where: 'tier-1..5 tokens · social map' },
      { name: 'Identity resolution',
        gloss: 'Aliases, addresses, and phone numbers fold onto one entity; the un-resolved leftovers become "unidentified".',
        where: 'identity-resolver · ingestion butler' },
      { name: 'Predicate suggestion',
        gloss: 'When a new fact is seen, the system proposes the relation it thinks it is — typed in the QA approvals queue.',
        where: 'qa-cd-proposals.jsx' },
      { name: 'Cross-butler weight',
        gloss: 'Multiple butlers can write evidence for the same triple; weight accumulates as a single integer touch-count.',
        where: 'relation.weight · backend-sketch.jsx' },
    ],
  },
  {
    group: 'Time-series',
    items: [
      { name: 'Touch-count over time',
        gloss: 'Per-relation, daily touch buckets — drives "closer / further" deltas on the social map.',
        where: 'household + calendar butlers' },
      { name: 'Spend over time',
        gloss: 'For purchased-from edges, an attached money series, queryable per vendor or rolled up to a class.',
        where: 'household butler · receipts' },
      { name: 'Co-attendance series',
        gloss: 'Calendar events emit co-attended triples; the series powers "who you saw this month".',
        where: 'calendar butler · chronicler' },
      { name: 'Mention drift',
        gloss: 'Memory tracks how often an entity is named in your notes vs in messages; drift surfaces as a signal.',
        where: 'memory butler · mentioned-in' },
    ],
  },
  {
    group: 'Curation',
    items: [
      { name: 'Approve / reject proposals',
        gloss: 'New triples land in the QA approvals queue with one commit button per row.',
        where: 'Approvals page · pill 11px commit' },
      { name: 'Merge entities',
        gloss: 'Two entities can be folded into one; aliases and relations migrate atomically.',
        where: 'EntitiesPage merge action' },
      { name: 'Promote / demote',
        gloss: 'Manual Dunbar overrides — bump a person up a tier or move a vendor into "household-utility".',
        where: 'EntityDetailPage actions' },
      { name: 'Forget',
        gloss: 'A hard delete that also tombstones the source provenance so it cannot be re-derived from the same input.',
        where: 'audit-log · forget action' },
    ],
  },
];

function CatalogTable() {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '180px 1fr',
      gap: 0,
      borderTop: '1px solid var(--border)',
    }}>
      {CATALOG.map((group, gi) => (
        <React.Fragment key={group.group}>
          <div style={{
            padding: '20px 16px 20px 0',
            borderBottom: '1px solid var(--border)',
            borderRight: '1px solid var(--border)',
            background: 'transparent',
          }}>
            <Eyebrow>{`0${gi + 1} · ` + group.group.toLowerCase()}</Eyebrow>
            <div style={{
              marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--dim)',
            }}>{group.items.length} capabilities</div>
          </div>
          <div style={{ borderBottom: '1px solid var(--border)' }}>
            {group.items.map((item) => (
              <div key={item.name} style={{
                display: 'grid',
                gridTemplateColumns: '220px 1fr',
                gap: 18, padding: '12px 0 12px 18px',
                borderBottom: '1px solid var(--border-soft)',
                alignItems: 'baseline',
              }}>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--fg)', fontWeight: 500 }}>
                  {item.name}
                </div>
                <div>
                  <div style={{ fontFamily: 'var(--font-serif)', fontSize: 14, lineHeight: 1.5, color: 'var(--fg)', maxWidth: '70ch' }}>
                    {item.gloss}
                  </div>
                  <div style={{ marginTop: 4, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--dim)' }}>
                    {item.where}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </React.Fragment>
      ))}
    </div>
  );
}

// Summary KPI strip — five cells: entities, people, orgs, places, triples.
// Plain Dispatch style — mono eyebrows + sans 32px tnum numbers, hairline-divided.
function CatalogKpis() {
  const cells = [
    { label: 'entities',     value: METRICS.byEntity,  meta: 'across 7 types' },
    { label: 'people',       value: METRICS.byPerson,  meta: 'tier 1 – 5' },
    { label: 'organizations',value: METRICS.byOrg,     meta: 'vendor · employer · subscription' },
    { label: 'triples',      value: METRICS.triples,   meta: 'across 19 predicates' },
    { label: 'contact facts',value: CONTACT_FACTS.length, meta: '6 contact predicates · multi-valued' },
    { label: 'needs you',    value: METRICS.unident + METRICS.duplicates + METRICS.stale,
      meta: `${METRICS.unident} unident · ${METRICS.duplicates} dupe · ${METRICS.stale} stale`,
      flag: (METRICS.unident + METRICS.duplicates) > 0 ? 'amber' : null },
  ];
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(6, 1fr)',
      borderTop: '1px solid var(--border)',
      borderBottom: '1px solid var(--border)',
    }}>
      {cells.map((c, i) => (
        <div key={c.label} style={{
          padding: '18px 16px',
          borderRight: i < cells.length - 1 ? '1px solid var(--border)' : 'none',
        }}>
          <Eyebrow>{c.label}</Eyebrow>
          <div className="tnum" style={{
            fontFamily: 'var(--font-sans)', fontSize: 32, fontWeight: 500,
            letterSpacing: '-0.03em', color: c.flag === 'amber' ? 'var(--amber)' : 'var(--fg)',
            marginTop: 6, marginBottom: 4, lineHeight: 1,
          }}>{c.value}</div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--dim)',
          }}>{c.meta}</div>
        </div>
      ))}
    </div>
  );
}

Object.assign(window, { CATALOG, CatalogTable, CatalogKpis });
