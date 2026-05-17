// 04 — Entity detail
//
// Two redesign proposals for /entities/:id. Both share the same Dispatch
// vocabulary; they differ in how much weight they give to curation vs
// narrative.
//
// Assumed prior art (since I'm working from the design system, not from
// the live page):
//
//   - the entity name in a heading
//   - a couple of badges (role, tier, perhaps unidentified)
//   - a list of relations (mostly outbound) with weights and last-seen
//   - an activity log (folded back in from the relationship butler view)
//   - some curation actions, probably in a kebab menu
//
// Both proposals below take the same content and ask: where does it
// belong? Variant A treats the page as a dispatch — the system writes
// you a sheet of paper about this entity. Variant B treats it as a
// workbench — you came here to fix something.

// ---------------------------------------------------------------------------
// Shared bits used by both detail variants.

const FAKE_ACTIVITY = (e) => {
  // Synthesize a per-entity activity log so each renders feels distinct.
  const rng = mulberry32([...e.id].reduce((a, c) => a + c.charCodeAt(0), 0));
  const today = new Date('2026-05-16');
  const items = [];
  const kinds = ['receipt', 'thread', 'event', 'note', 'call'];
  let cursor = 0;
  for (let i = 0; i < 8; i++) {
    cursor += Math.floor(rng() * 6) + 1;
    const dt = new Date(today.getTime() - cursor * 86400000);
    items.push({
      ts: dt.toISOString().slice(0, 10),
      kind: kinds[Math.floor(rng() * kinds.length)],
      summary: activityLine(e, rng),
      via: ['memory', 'household', 'calendar', 'chronicler'][Math.floor(rng() * 4)],
    });
  }
  return items;
};

function activityLine(e, rng) {
  if (e.type === 'organization' && e.category === 'vendor') {
    return ['£4.20 · oat flat white', '£18.40 · groceries', '£6.10 · pastry', '£23.00 · weekly box'][Math.floor(rng() * 4)];
  }
  if (e.type === 'person') {
    return [
      'co-attended “Platform standup”',
      'mentioned in thread w/ Lin',
      '15-min call · evening',
      'message · “see you Sunday?”',
      'photo shared · park',
    ][Math.floor(rng() * 5)];
  }
  return 'recorded activity';
}

function mulberry32(seed) {
  let t = seed | 0;
  return function () {
    t = (t + 0x6D2B79F5) | 0;
    let r = Math.imul(t ^ (t >>> 15), 1 | t);
    r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r;
    return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
  };
}

function ActivitySpark({ days = 90, entityId }) {
  // Synthesise a 90-day touch series.
  const rng = mulberry32([...entityId].reduce((a, c) => a + c.charCodeAt(0), 7));
  const data = Array.from({ length: days }, () => (rng() < 0.55 ? 0 : Math.floor(rng() * 4) + 1));
  const max = Math.max(1, ...data);
  const W = 360, H = 56;
  const step = W / days;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: H, display: 'block' }}>
      {data.map((v, i) => (
        <rect key={i} x={i * step} y={H - (v / max) * (H - 6)} width={Math.max(1, step - 0.8)}
              height={(v / max) * (H - 6)} fill={v === 0 ? 'oklch(1 0 0 / 0.04)' : 'var(--fg)'} opacity={v === 0 ? 1 : 0.92} />
      ))}
    </svg>
  );
}

function gloss(e) {
  if (e?.role === 'owner') return 'The root of the graph. You.';
  if (e?.state === 'unidentified') return 'Seen but not yet matched. Merge into a known contact or promote to a new person.';
  if (e?.tier === 1) return 'Among the closest. Daily-cadence relations and shared address.';
  if (e?.tier === 2) return 'Close circle. Weekly to monthly touches across calendar and threads.';
  if (e?.tier === 3) return 'Extended network. Periodic touches; mostly social.';
  if (e?.tier === 4) return 'Acquaintance. Stable but quiet.';
  if (e?.tier === 5) return 'Distant. Surfaced mostly through cc lines.';
  if (e?.type === 'organization' && e?.category === 'employer') return 'Current employer; the most-touched org in the graph.';
  if (e?.type === 'organization' && e?.category === 'vendor') return 'Regular vendor. Weight comes from receipts and the household butler.';
  if (e?.type === 'place') return 'A geographic anchor. Visits accumulate over time.';
  return '—';
}

// ---------------------------------------------------------------------------
// Variant A — Editorial
//
// Two columns: narrative (left) and index (right). Reads as a single
// dispatch about this entity. The curation actions live in a quiet rail at
// the very bottom of the right column — they're the last thing on the
// page, not the first. The system trusts you to scroll.

function DetailEditorial({ entityId = 'p-lin' }) {
  const [eid, setEid] = React.useState(entityId);
  const e = ENTITY_INDEX[eid];
  const contacts = contactsFor(eid);
  const adj = (ADJ[eid] || []).slice().sort((a, b) => (b.meta.weight || 0) - (a.meta.weight || 0));
  const activity = React.useMemo(() => FAKE_ACTIVITY(e), [eid]);

  // Group contacts by predicate.
  const cByPred = {};
  for (const c of contacts) (cByPred[c.pred] ||= []).push(c);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <SubpageTabs current="detail-stub" />
      <BreadcrumbStrip eid={eid} onJump={setEid} />

      <div style={{ overflow: 'auto', flex: 1 }}>
        <div style={{ padding: '36px 48px 48px', maxWidth: 1100, margin: '0 auto' }}>
          {/* Hero */}
          <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 56, alignItems: 'baseline' }}>
            <div>
              <Eyebrow>person · entity · {eid}</Eyebrow>
              <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginTop: 18 }}>
                <EntityMark entity={e} size={40} tone="fill" />
                <Display size={44} style={{ maxWidth: '20ch', margin: 0 }}>{e?.name}</Display>
              </div>
              <Voice style={{ marginTop: 22, color: 'var(--fg)' }}>
                {gloss(e)}
              </Voice>
              <div style={{ display: 'flex', gap: 12, marginTop: 20, alignItems: 'center', flexWrap: 'wrap' }}>
                {e?.tier && <TierBadge tier={e.tier} />}
                {e?.role === 'owner' && <TierBadge tier={0} />}
                {e?.state === 'unidentified' && <StatePill kind="unidentified">unidentified</StatePill>}
                <span className="mono" style={{ fontSize: 10, color: 'var(--dim)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
                  first seen · <span className="tnum">{e?.firstSeen || '—'}</span>
                  &nbsp;·&nbsp;last seen · <span className="tnum">{e?.lastSeen || '—'}</span>
                </span>
              </div>
            </div>

            <div style={{ borderLeft: '1px solid var(--border)', paddingLeft: 28 }}>
              <Eyebrow>last 90 days · touches</Eyebrow>
              <div style={{ marginTop: 10 }}>
                <ActivitySpark entityId={eid} />
              </div>
              <div style={{ marginTop: 10, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--dim)', display: 'flex', justifyContent: 'space-between' }}>
                <span>−90d</span><span>today</span>
              </div>
            </div>
          </div>

          {/* Two-column body */}
          <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 56, marginTop: 48 }}>
            {/* LEFT — narrative */}
            <div>
              <Eyebrow>relations · {adj.length}</Eyebrow>
              <div style={{ borderTop: '1px solid var(--border)', marginTop: 10 }}>
                {adj.slice(0, 8).map((n, i) => {
                  const ne = ENTITY_INDEX[n.other];
                  return (
                    <div key={i} style={{
                      display: 'grid',
                      gridTemplateColumns: '120px 1fr 60px 60px',
                      gap: 14, padding: '12px 0', alignItems: 'baseline',
                      borderBottom: '1px solid var(--border-soft)',
                    }}>
                      <span className="kind-tag" style={{ fontSize: 10 }}>
                        {n.dir === 'out' ? '→ ' : '← '}{PREDICATE_INDEX[n.pred]?.label}
                      </span>
                      <span style={{ fontSize: 14 }}>
                        <a className="deep" href="#" onClick={(e2) => { e2.preventDefault(); setEid(n.other); }}>
                          {ne?.name}
                        </a>
                        {ne?.tier && <TierBadge tier={ne.tier} />}
                      </span>
                      <span className="tnum mono" style={{ fontSize: 11, color: 'var(--mfg)', textAlign: 'right' }}>×{n.meta.weight || 1}</span>
                      <span className="mono" style={{ fontSize: 10, color: 'var(--dim)', textAlign: 'right' }}>
                        {n.meta.src || ''}
                      </span>
                    </div>
                  );
                })}
                {adj.length === 0 && (
                  <Voice italic style={{ padding: 24, color: 'var(--mfg)' }}>No relations yet.</Voice>
                )}
              </div>

              <Eyebrow style={{ marginTop: 36 }}>recent activity</Eyebrow>
              <div style={{ borderTop: '1px solid var(--border)', marginTop: 10 }}>
                {activity.map((a, i) => (
                  <div key={i} style={{
                    display: 'grid',
                    gridTemplateColumns: '70px 80px 1fr 70px',
                    gap: 14, padding: '10px 0', alignItems: 'baseline',
                    borderBottom: '1px solid var(--border-soft)',
                  }}>
                    <span className="mono tnum" style={{ fontSize: 11, color: 'var(--mfg)' }}>{a.ts.slice(5)}</span>
                    <span className="kind-tag" style={{ fontSize: 9 }}>{a.kind}</span>
                    <span style={{ fontSize: 13, color: 'var(--fg)' }}>{a.summary}</span>
                    <span className="mono" style={{ fontSize: 9, color: 'var(--dim)', textAlign: 'right', textTransform: 'uppercase' }}>via {a.via}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* RIGHT — index */}
            <div>
              <Eyebrow>contacts · {contacts.length}</Eyebrow>
              <div style={{ borderTop: '1px solid var(--border)', marginTop: 10 }}>
                {Object.entries(cByPred).map(([pred, items]) => (
                  <div key={pred} style={{ padding: '12px 0', borderBottom: '1px solid var(--border-soft)' }}>
                    <Eyebrow>{PREDICATE_INDEX[pred]?.label || pred}</Eyebrow>
                    {items.map((c, i) => (
                      <div key={i} style={{
                        display: 'grid', gridTemplateColumns: '1fr auto auto',
                        gap: 8, padding: '6px 0', alignItems: 'baseline',
                      }}>
                        <span style={{
                          fontFamily: c.pred === 'has-email' || c.pred === 'has-website' ? 'var(--font-mono)' : 'var(--font-sans)',
                          fontSize: c.pred === 'has-email' || c.pred === 'has-website' ? 12 : 13,
                          color: 'var(--fg)', wordBreak: 'break-all',
                        }}>{c.value}</span>
                        {c.meta.primary && (
                          <span className="mono" style={{ fontSize: 9, color: 'var(--mfg)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>primary</span>
                        )}
                        {!c.meta.verified && (
                          <span style={{ color: 'var(--amber)', fontSize: 10 }} title="unverified">·</span>
                        )}
                      </div>
                    ))}
                  </div>
                ))}
                {contacts.length === 0 && (
                  <Voice italic style={{ padding: 18, fontSize: 13, color: 'var(--mfg)' }}>None yet.</Voice>
                )}
              </div>

              {e?.aliases?.length > 0 && (
                <>
                  <Eyebrow style={{ marginTop: 24 }}>aliases · {e.aliases.length}</Eyebrow>
                  <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {e.aliases.map((a) => (
                      <span key={a} className="kind-tag" style={{
                        border: '1px solid var(--border-soft)', padding: '2px 6px', fontSize: 10,
                      }}>{a}</span>
                    ))}
                  </div>
                </>
              )}

              <Eyebrow style={{ marginTop: 24 }}>provenance</Eyebrow>
              <div style={{ borderTop: '1px solid var(--border-soft)', marginTop: 8 }}>
                {Object.entries(provenanceCounts(eid)).map(([butler, n]) => (
                  <div key={butler} style={{
                    display: 'grid', gridTemplateColumns: '1fr auto',
                    padding: '6px 0', borderBottom: '1px solid var(--border-soft)',
                    fontFamily: 'var(--font-mono)', fontSize: 11,
                  }}>
                    <span style={{ color: 'var(--mfg)' }}>{butler}</span>
                    <span className="tnum" style={{ color: 'var(--fg)' }}>{n}</span>
                  </div>
                ))}
              </div>

              {/* Curation rail — quiet, at the bottom. */}
              <div style={{ marginTop: 36, padding: '20px 0', borderTop: '1px solid var(--border)', borderBottom: '1px solid var(--border)' }}>
                <Eyebrow>curation</Eyebrow>
                <div style={{ marginTop: 12, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                  <CurationLink>merge into…</CurationLink>
                  <CurationLink>promote tier</CurationLink>
                  <CurationLink>demote tier</CurationLink>
                  <CurationLink>archive</CurationLink>
                  <CurationLink danger>forget</CurationLink>
                  <CurationLink>edit aliases</CurationLink>
                </div>
                <Voice italic style={{ marginTop: 12, fontSize: 12, color: 'var(--mfg)' }}>
                  Forgetting also tombstones the source. Aliases stay.
                </Voice>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function provenanceCounts(eid) {
  const out = {};
  for (const r of RELATIONS) {
    if (r[0] === eid || r[2] === eid) {
      const b = r[3]?.src || 'unknown';
      out[b] = (out[b] || 0) + 1;
    }
  }
  for (const c of CONTACT_FACTS) {
    if (c[0] === eid) {
      const b = c[3]?.src || 'unknown';
      out[b] = (out[b] || 0) + 1;
    }
  }
  return out;
}

function CurationLink({ children, danger }) {
  return (
    <a href="#" onClick={(e) => e.preventDefault()} style={{
      display: 'flex', alignItems: 'center', gap: 6,
      fontFamily: 'var(--font-sans)', fontSize: 13,
      color: danger ? 'var(--red)' : 'var(--fg)',
      textDecoration: 'underline', textUnderlineOffset: 4,
      textDecorationColor: 'var(--border-strong)',
    }}>{children}<span style={{ marginLeft: 'auto', opacity: 0.6 }}>→</span></a>
  );
}

function BreadcrumbStrip({ eid, onJump }) {
  const e = ENTITY_INDEX[eid];
  return (
    <div style={{
      padding: '10px 20px', display: 'flex', alignItems: 'center', gap: 8,
      borderBottom: '1px solid var(--border)',
      fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--mfg)',
      textTransform: 'uppercase', letterSpacing: '0.08em',
    }}>
      <a className="dlink" href="#" onClick={(ev) => { ev.preventDefault(); onJump('me'); }}
         style={{ color: 'inherit', textDecoration: 'underline', textUnderlineOffset: 3, textDecorationColor: 'var(--border-strong)' }}>
        /entities
      </a>
      <span style={{ color: 'var(--dim)' }}>›</span>
      <span style={{ color: 'var(--fg)' }}>{e?.name || eid}</span>
      <span style={{ flex: 1 }} />
      <span>prev k · next j · close esc</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Variant B — Workbench
//
// Power-user view. Three columns. The middle is the entity proper; the
// rails are the things you came here to do: hop into related entities
// (left) and run curation actions (right). Aimed at high-volume sessions:
// merging dozens of duplicates after an import, fixing a noisy butler,
// promoting a batch of unidentified emails.

function DetailWorkbench({ entityId = 'p-tan' }) {
  const [eid, setEid] = React.useState(entityId);
  const e = ENTITY_INDEX[eid];
  const contacts = contactsFor(eid);
  const adj = (ADJ[eid] || []).slice().sort((a, b) => (b.meta.weight || 0) - (a.meta.weight || 0));

  // Suggested merge target (for duplicate-candidate entities)
  const merge = e?.dupOf ? ENTITY_INDEX[e.dupOf] : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <SubpageTabs current="detail-stub" />
      <BreadcrumbStrip eid={eid} onJump={setEid} />

      <div style={{ display: 'grid', gridTemplateColumns: '240px 1fr 280px', flex: 1, minHeight: 0 }}>
        {/* LEFT rail — related entities, recent hops */}
        <div style={{ padding: '18px 16px', borderRight: '1px solid var(--border)', overflow: 'auto' }}>
          <Eyebrow>top relations</Eyebrow>
          <div style={{ marginTop: 8 }}>
            {adj.slice(0, 5).map((n, i) => {
              const ne = ENTITY_INDEX[n.other];
              return (
                <div key={i} onClick={() => setEid(n.other)} style={{
                  display: 'grid', gridTemplateColumns: 'auto 1fr auto',
                  gap: 8, padding: '8px 0', alignItems: 'center',
                  borderBottom: '1px solid var(--border-soft)', cursor: 'pointer',
                }}>
                  <EntityMark entity={ne} size={16} />
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 12, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{ne?.name}</div>
                    <div className="mono" style={{ fontSize: 9, color: 'var(--dim)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                      {PREDICATE_INDEX[n.pred]?.label}
                    </div>
                  </div>
                  <span className="tnum mono" style={{ fontSize: 10, color: 'var(--mfg)' }}>×{n.meta.weight || 1}</span>
                </div>
              );
            })}
          </div>

          <Eyebrow style={{ marginTop: 22 }}>introduced via</Eyebrow>
          <Voice italic style={{ marginTop: 8, fontSize: 12, color: 'var(--mfg)' }}>
            First-seen on a thread w/ Lin · 2024-06-22
          </Voice>

          <Eyebrow style={{ marginTop: 22 }}>shares emails w/</Eyebrow>
          {merge ? (
            <div style={{ padding: '8px 0', borderBottom: '1px solid var(--border-soft)', marginTop: 6 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <EntityMark entity={merge} size={16} />
                <span style={{ fontSize: 12 }}>{merge.name}</span>
              </div>
              <button onClick={() => setEid(merge.id)} className="dlink" style={{
                marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 10,
                textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--amber)',
              }}>likely the same person →</button>
            </div>
          ) : (
            <Voice italic style={{ marginTop: 8, fontSize: 12, color: 'var(--mfg)' }}>—</Voice>
          )}
        </div>

        {/* MIDDLE — entity proper */}
        <div style={{ padding: '24px 28px', overflow: 'auto' }}>
          <Eyebrow>entity · {eid}</Eyebrow>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 12 }}>
            <EntityMark entity={e} size={28} tone="fill" />
            <Display size={32} style={{ maxWidth: '24ch', margin: 0 }}>{e?.name}</Display>
            {e?.state === 'duplicate-candidate' && <StatePill kind="duplicate">duplicate candidate</StatePill>}
            {e?.state === 'unidentified' && <StatePill kind="unidentified">unidentified</StatePill>}
          </div>
          <Voice italic style={{ marginTop: 10, fontSize: 14, color: 'var(--mfg)' }}>{gloss(e)}</Voice>

          {/* KPI strip */}
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)',
            borderTop: '1px solid var(--border)', borderBottom: '1px solid var(--border)',
            marginTop: 24,
          }}>
            <Kpi label="relations"  value={adj.length} />
            <Kpi label="touch · 90d" value={spentLastN(eid, 90)} />
            <Kpi label="butlers"    value={Object.keys(provenanceCounts(eid)).length} meta="contributors" />
            <Kpi label="contacts"   value={contacts.length} meta={`${contacts.filter((c) => !c.meta.verified).length} unverified`} />
          </div>

          {/* Raw RDF view — the workbench's signature: shows the actual triples */}
          <Eyebrow style={{ marginTop: 28 }}>triples</Eyebrow>
          <div style={{
            marginTop: 8, fontFamily: 'var(--font-mono)', fontSize: 11,
            color: 'var(--fg)', lineHeight: 1.6,
          }}>
            {adj.slice(0, 5).map((n, i) => (
              <div key={i} style={{ padding: '4px 0', borderBottom: '1px solid var(--border-soft)' }}>
                <span style={{ color: 'var(--dim)' }}>:{eid}</span>
                {' '}
                <span style={{ color: 'var(--amber)' }}>:{n.pred}</span>
                {' '}
                <span style={{ color: 'var(--fg)' }}>:{n.other}</span>
                {' '}
                <span style={{ color: 'var(--mfg)' }}>· ×{n.meta.weight || 1} · {n.meta.src}</span>
                {' '}
                <span style={{ color: 'var(--dim)' }}>·</span>
              </div>
            ))}
            {contacts.slice(0, 4).map((c, i) => (
              <div key={'c-' + i} style={{ padding: '4px 0', borderBottom: '1px solid var(--border-soft)' }}>
                <span style={{ color: 'var(--dim)' }}>:{eid}</span>
                {' '}
                <span style={{ color: 'var(--blue)' }}>contact:{c.pred.replace('has-', '')}</span>
                {' '}
                <span style={{ color: 'var(--fg)' }}>"{c.value}"</span>
                {' '}
                <span style={{ color: 'var(--mfg)' }}>· conf {c.meta.conf}</span>
                {c.meta.verified === false && <span style={{ color: 'var(--amber)' }}> · unverified</span>}
              </div>
            ))}
          </div>
        </div>

        {/* RIGHT rail — curation actions */}
        <div style={{ padding: '18px 16px', borderLeft: '1px solid var(--border)', overflow: 'auto' }}>
          <Eyebrow>actions</Eyebrow>
          {e?.state === 'duplicate-candidate' && (
            <div style={{ marginTop: 10, padding: 12, border: '1px solid var(--amber)', borderRadius: 2 }}>
              <Eyebrow style={{ color: 'var(--amber)' }}>likely duplicate</Eyebrow>
              <Voice italic style={{ marginTop: 6, fontSize: 12, color: 'var(--mfg)' }}>
                Same email + employer as {merge?.name}.
              </Voice>
              <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
                <CommitBtn>merge into {merge?.name}</CommitBtn>
                <button className="pill" style={{ padding: '4px 10px', justifyContent: 'center' }}>keep both</button>
              </div>
            </div>
          )}
          <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 4 }}>
            <ActBtn>merge…</ActBtn>
            <ActBtn>promote tier</ActBtn>
            <ActBtn>demote tier</ActBtn>
            <ActBtn>edit aliases</ActBtn>
            <ActBtn>edit contacts</ActBtn>
            <ActBtn>archive</ActBtn>
            <ActBtn danger>forget</ActBtn>
          </div>

          <Eyebrow style={{ marginTop: 24 }}>confidence</Eyebrow>
          <div style={{ marginTop: 8 }}>
            {[
              ['name resolution', 1.0],
              ['identity merge',  e?.state === 'duplicate-candidate' ? 0.74 : 0.96],
              ['contact has-email', 0.95],
              ['tier classification', 0.86],
            ].map(([lbl, v], i) => (
              <div key={i} style={{ marginBottom: 8 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--mfg)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{lbl}</span>
                  <span className="mono tnum" style={{ fontSize: 10, color: v < 0.85 ? 'var(--amber)' : 'var(--fg)' }}>{v.toFixed(2)}</span>
                </div>
                <div style={{ height: 4, background: 'oklch(1 0 0 / 0.06)', marginTop: 4 }}>
                  <div style={{ height: '100%', width: `${v * 100}%`, background: v < 0.85 ? 'var(--amber)' : 'var(--fg)' }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function Kpi({ label, value, meta }) {
  return (
    <div style={{ padding: '14px 18px', borderRight: '1px solid var(--border)' }}>
      <Eyebrow>{label}</Eyebrow>
      <div className="tnum" style={{
        fontFamily: 'var(--font-sans)', fontSize: 26, fontWeight: 500, letterSpacing: '-0.025em',
        marginTop: 4, lineHeight: 1,
      }}>{value}</div>
      {meta && <div className="mono" style={{ fontSize: 10, color: 'var(--dim)', marginTop: 4 }}>{meta}</div>}
    </div>
  );
}

function ActBtn({ children, danger }) {
  return (
    <button style={{
      background: 'transparent', border: 'none', padding: '6px 0',
      textAlign: 'left', borderBottom: '1px solid var(--border-soft)',
      fontFamily: 'var(--font-sans)', fontSize: 13,
      color: danger ? 'var(--red)' : 'var(--fg)',
      cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    }}>
      <span>{children}</span>
      <span style={{ color: 'var(--mfg)' }}>→</span>
    </button>
  );
}

function spentLastN(eid, n) {
  const cutoff = new Date('2026-05-16').getTime() - n * 86400000;
  let count = 0;
  for (const r of RELATIONS) {
    if ((r[0] !== eid && r[2] !== eid)) continue;
    const ls = r[3]?.lastSeen;
    if (!ls) continue;
    if (new Date(ls).getTime() >= cutoff) count += r[3].weight || 1;
  }
  return count;
}

Object.assign(window, { DetailEditorial, DetailWorkbench });
