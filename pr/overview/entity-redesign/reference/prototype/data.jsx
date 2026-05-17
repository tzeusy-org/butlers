// Sample data — plausible-but-fictional. Owner is Tze.
// Shape mirrors what an RDF-backed entity store would surface:
// each entity has id, type, name, optional category, optional dunbar tier,
// optional first/last-seen timestamps, optional aliases, and provenance
// (which butler discovered it). Relations are triples — subject predicate
// object — with confidence and provenance.

const OWNER_ID = 'me';

const TYPES = {
  person:       { glyph: 'P', label: 'Person' },
  organization: { glyph: 'O', label: 'Org' },
  place:        { glyph: 'L', label: 'Place' },
  product:      { glyph: 'X', label: 'Product' },
  account:      { glyph: '@', label: 'Account' },
  event:        { glyph: 'E', label: 'Event' },
  group:        { glyph: 'G', label: 'Group' },
};

// Predicates the system understands. The "domain" hint is the butler that
// most often writes this predicate. Predicates split into two families:
//
//  - relational predicates (knows, employed-by, purchased-from, ...) — point
//    from one entity to another;
//  - contact predicates (has-email, has-phone, ...) — point from an entity
//    to a literal contact value. These live in the 'contact' namespace and
//    are deliberately multi-valued — a person can have many emails.
//
// The Contacts page is just an Entities filter that surfaces persons with
// at least one contact predicate.
const PREDICATES = [
  { id: 'knows',          label: 'knows',          domain: 'relationship', kind: 'relational' },
  { id: 'family-of',      label: 'family of',      domain: 'relationship', kind: 'relational' },
  { id: 'partner-of',     label: 'partner of',     domain: 'relationship', kind: 'relational' },
  { id: 'colleague-of',   label: 'colleague of',   domain: 'relationship', kind: 'relational' },
  { id: 'employer-of',    label: 'employs',        domain: 'relationship', kind: 'relational' },
  { id: 'employed-by',    label: 'employed by',    domain: 'relationship', kind: 'relational' },
  { id: 'purchased-from', label: 'purchased from', domain: 'household',    kind: 'relational' },
  { id: 'subscribed-to',  label: 'subscribed to',  domain: 'household',    kind: 'relational' },
  { id: 'lives-at',       label: 'lives at',       domain: 'household',    kind: 'relational' },
  { id: 'visited',        label: 'visited',        domain: 'chronicler',   kind: 'relational' },
  { id: 'attended',       label: 'attended',       domain: 'calendar',     kind: 'relational' },
  { id: 'co-attended',    label: 'co-attended',    domain: 'calendar',     kind: 'relational' },
  { id: 'mentioned-in',   label: 'mentioned in',   domain: 'memory',       kind: 'relational' },

  // Contact predicates (multi-valued). Object is a literal string.
  { id: 'has-email',   label: 'email',   domain: 'contact', kind: 'contact', literal: true },
  { id: 'has-phone',   label: 'phone',   domain: 'contact', kind: 'contact', literal: true },
  { id: 'has-handle',  label: 'handle',  domain: 'contact', kind: 'contact', literal: true },
  { id: 'has-address', label: 'address', domain: 'contact', kind: 'contact', literal: true },
  { id: 'has-birthday',label: 'birthday',domain: 'contact', kind: 'contact', literal: true },
  { id: 'has-website', label: 'website', domain: 'contact', kind: 'contact', literal: true },
];

const PREDICATE_INDEX = Object.fromEntries(PREDICATES.map((p) => [p.id, p]));

const ENTITIES = [
  // The owner.
  { id: 'me', type: 'person', name: 'Tze Lim', role: 'owner', tier: 0,
    aliases: ['tze', 'tze.lim@', 'TZ'], firstSeen: '2022-01-01' },

  // Inner circle (tier 1) — partner & immediate family
  { id: 'p-lin',  type: 'person', name: 'Lin Tan',     tier: 1, aliases: ['lin', 'linny'], firstSeen: '2022-01-01', lastSeen: '2026-05-15' },
  { id: 'p-amy',  type: 'person', name: 'Amy Lim',     tier: 1, aliases: ['mum'],          firstSeen: '2022-01-01', lastSeen: '2026-05-12' },
  { id: 'p-ben',  type: 'person', name: 'Ben Lim',     tier: 1, aliases: ['dad'],          firstSeen: '2022-01-01', lastSeen: '2026-05-04' },
  { id: 'p-cara', type: 'person', name: 'Cara Lim',    tier: 1, aliases: ['sis'],          firstSeen: '2022-01-01', lastSeen: '2026-05-11' },

  // Tier 2 — close friends (~15)
  { id: 'p-deb', type: 'person', name: 'Deb Okafor',    tier: 2, firstSeen: '2022-03-04', lastSeen: '2026-05-10' },
  { id: 'p-rav', type: 'person', name: 'Ravi Mehta',    tier: 2, firstSeen: '2022-01-12', lastSeen: '2026-05-09' },
  { id: 'p-noa', type: 'person', name: 'Noa Bergmann',  tier: 2, firstSeen: '2022-02-10', lastSeen: '2026-04-30' },
  { id: 'p-yuk', type: 'person', name: 'Yuki Sato',     tier: 2, firstSeen: '2023-06-20', lastSeen: '2026-05-15' },
  { id: 'p-ezr', type: 'person', name: 'Ezra Brandt',   tier: 2, firstSeen: '2023-09-01', lastSeen: '2026-05-08' },

  // Tier 3 — extended (~50)
  { id: 'p-isa', type: 'person', name: 'Isabel Cruz',   tier: 3, firstSeen: '2023-04-02', lastSeen: '2026-04-22' },
  { id: 'p-ola', type: 'person', name: 'Olamide Fashanu',tier: 3, firstSeen: '2023-08-14', lastSeen: '2026-03-30' },
  { id: 'p-jas', type: 'person', name: 'Jasper Knoll',  tier: 3, firstSeen: '2024-01-09', lastSeen: '2026-05-02' },
  { id: 'p-mei', type: 'person', name: 'Mei Watanabe',  tier: 3, firstSeen: '2024-02-12', lastSeen: '2026-04-29' },
  { id: 'p-har', type: 'person', name: 'Harriet Vance', tier: 3, firstSeen: '2024-05-06', lastSeen: '2026-05-13' },
  { id: 'p-tan', type: 'person', name: 'Tanvir Ahmed',  tier: 3, firstSeen: '2024-06-22', lastSeen: '2026-05-07' },

  // Tier 4 — acquaintances
  { id: 'p-ash', type: 'person', name: 'Ash Petrov',    tier: 4, firstSeen: '2025-01-04', lastSeen: '2026-03-14' },
  { id: 'p-lou', type: 'person', name: 'Louisa Diaz',   tier: 4, firstSeen: '2025-04-14', lastSeen: '2026-04-02' },
  { id: 'p-kir', type: 'person', name: 'Kiran Joshi',   tier: 4, firstSeen: '2025-06-18', lastSeen: '2026-02-19' },

  // Tier 5 — distant (work CCs etc)
  { id: 'p-mat', type: 'person', name: 'Matt Owens',    tier: 5, firstSeen: '2025-11-02', lastSeen: '2026-01-12' },
  { id: 'p-naz', type: 'person', name: 'Naz Hassani',   tier: 5, firstSeen: '2026-01-30', lastSeen: '2026-04-11' },

  // Unidentified — not yet matched to a known contact
  { id: 'p-unk1', type: 'person', name: 'unknown@swiftpost.co', state: 'unidentified', firstSeen: '2026-04-19', lastSeen: '2026-04-19' },
  { id: 'p-unk2', type: 'person', name: '+44 7700 900482',     state: 'unidentified', firstSeen: '2026-05-02', lastSeen: '2026-05-09' },
  { id: 'p-unk3', type: 'person', name: 'rebecca.li (slack)',  state: 'unidentified', firstSeen: '2026-05-10', lastSeen: '2026-05-14' },
  // Likely duplicate of p-tan — surfaced for merging
  { id: 'p-tan2', type: 'person', name: 'Tan Tanvir',          state: 'duplicate-candidate', dupOf: 'p-tan', firstSeen: '2024-07-01', lastSeen: '2026-04-22' },
  // Stale — no touches in 18+ months
  { id: 'p-stale1', type: 'person', name: 'Carla Pugh',        state: 'stale', tier: 5, firstSeen: '2023-05-04', lastSeen: '2024-09-12' },

  // Organizations
  { id: 'o-ndlm', type: 'organization', name: 'Northwind Dlm',  category: 'employer',    firstSeen: '2022-01-01' },
  { id: 'o-acme', type: 'organization', name: 'Acme Forge',     category: 'employer-prev', firstSeen: '2020-08-15' },
  { id: 'o-bnd',  type: 'organization', name: 'Bunda Coffee',   category: 'vendor', firstSeen: '2023-02-04', lastSeen: '2026-05-15' },
  { id: 'o-gm',   type: 'organization', name: 'Greenmarket',    category: 'vendor', firstSeen: '2022-04-02', lastSeen: '2026-05-13' },
  { id: 'o-rt',   type: 'organization', name: 'Rake & Trough',  category: 'vendor', firstSeen: '2024-06-08', lastSeen: '2026-04-30' },
  { id: 'o-utility', type: 'organization', name: 'Lumen Utility', category: 'vendor-utility', firstSeen: '2022-01-01', lastSeen: '2026-05-01' },
  { id: 'o-isp',  type: 'organization', name: 'Tealine ISP',    category: 'vendor-utility', firstSeen: '2022-01-01', lastSeen: '2026-05-01' },
  { id: 'o-sub-nyt', type: 'organization', name: 'The Sentinel', category: 'subscription', firstSeen: '2022-03-01', lastSeen: '2026-05-01' },
  { id: 'o-sub-spo', type: 'organization', name: 'Sonant',       category: 'subscription', firstSeen: '2022-01-01', lastSeen: '2026-05-01' },
  { id: 'o-gym',  type: 'organization', name: 'Bellrope Gym',   category: 'subscription', firstSeen: '2024-09-12', lastSeen: '2026-05-12' },

  // Places
  { id: 'l-home', type: 'place', name: 'Home · Marylebone', firstSeen: '2022-01-01' },
  { id: 'l-work', type: 'place', name: 'Office · King’s Cross', firstSeen: '2022-01-01' },
  { id: 'l-park', type: 'place', name: 'Regent’s Park',      firstSeen: '2022-06-04', lastSeen: '2026-05-11' },
  { id: 'l-cph',  type: 'place', name: 'Copenhagen',         firstSeen: '2024-08-12', lastSeen: '2025-09-04' },

  // Groups
  { id: 'g-fam',  type: 'group', name: 'Family' },
  { id: 'g-corev', type: 'group', name: 'Climbing crew' },
  { id: 'g-team', type: 'group', name: 'Platform team' },
];

const ENTITY_INDEX = Object.fromEntries(ENTITIES.map((e) => [e.id, e]));

// Relations — subject predicate object [confidence, provenance, weight, lastSeen]
// weight is # of supporting facts (touches, receipts, mentions).
const RELATIONS = [
  // Family
  ['me', 'partner-of',   'p-lin',  { conf: 1.0, src: 'relationship', weight: 412, lastSeen: '2026-05-15' }],
  ['me', 'family-of',    'p-amy',  { conf: 1.0, src: 'relationship', weight: 186, lastSeen: '2026-05-12' }],
  ['me', 'family-of',    'p-ben',  { conf: 1.0, src: 'relationship', weight: 121, lastSeen: '2026-05-04' }],
  ['me', 'family-of',    'p-cara', { conf: 1.0, src: 'relationship', weight: 158, lastSeen: '2026-05-11' }],
  ['p-amy','family-of','p-ben',  { conf: 1.0, src: 'relationship', weight: 40 }],
  ['p-amy','family-of','p-cara', { conf: 1.0, src: 'relationship', weight: 35 }],
  ['p-ben','family-of','p-cara', { conf: 1.0, src: 'relationship', weight: 32 }],

  // Friends
  ['me', 'knows', 'p-deb', { conf: 1.0, src: 'relationship', weight: 88,  lastSeen: '2026-05-10' }],
  ['me', 'knows', 'p-rav', { conf: 1.0, src: 'relationship', weight: 96,  lastSeen: '2026-05-09' }],
  ['me', 'knows', 'p-noa', { conf: 1.0, src: 'relationship', weight: 64,  lastSeen: '2026-04-30' }],
  ['me', 'knows', 'p-yuk', { conf: 1.0, src: 'relationship', weight: 71,  lastSeen: '2026-05-15' }],
  ['me', 'knows', 'p-ezr', { conf: 1.0, src: 'relationship', weight: 52,  lastSeen: '2026-05-08' }],
  ['me', 'knows', 'p-isa', { conf: 0.9, src: 'relationship', weight: 18,  lastSeen: '2026-04-22' }],
  ['me', 'knows', 'p-ola', { conf: 0.9, src: 'relationship', weight: 15,  lastSeen: '2026-03-30' }],
  ['me', 'knows', 'p-jas', { conf: 0.9, src: 'relationship', weight: 24,  lastSeen: '2026-05-02' }],
  ['me', 'knows', 'p-mei', { conf: 0.8, src: 'relationship', weight: 11,  lastSeen: '2026-04-29' }],
  ['me', 'knows', 'p-har', { conf: 0.8, src: 'relationship', weight: 26,  lastSeen: '2026-05-13' }],
  ['me', 'knows', 'p-tan', { conf: 0.8, src: 'relationship', weight: 19,  lastSeen: '2026-05-07' }],
  ['me', 'knows', 'p-ash', { conf: 0.6, src: 'relationship', weight: 6,   lastSeen: '2026-03-14' }],
  ['me', 'knows', 'p-lou', { conf: 0.6, src: 'relationship', weight: 4,   lastSeen: '2026-04-02' }],
  ['me', 'knows', 'p-kir', { conf: 0.6, src: 'relationship', weight: 3,   lastSeen: '2026-02-19' }],
  ['me', 'knows', 'p-mat', { conf: 0.5, src: 'relationship', weight: 2,   lastSeen: '2026-01-12' }],
  ['me', 'knows', 'p-naz', { conf: 0.5, src: 'relationship', weight: 2,   lastSeen: '2026-04-11' }],

  // Friends-of-friends
  ['p-deb', 'knows', 'p-rav', { conf: 0.9, src: 'relationship', weight: 22 }],
  ['p-deb', 'knows', 'p-noa', { conf: 0.7, src: 'relationship', weight: 8 }],
  ['p-rav', 'knows', 'p-noa', { conf: 0.8, src: 'relationship', weight: 14 }],
  ['p-rav', 'knows', 'p-ezr', { conf: 0.9, src: 'relationship', weight: 18 }],
  ['p-rav', 'knows', 'p-jas', { conf: 0.7, src: 'relationship', weight: 6 }],
  ['p-yuk', 'knows', 'p-mei', { conf: 0.8, src: 'relationship', weight: 9 }],
  ['p-yuk', 'knows', 'p-ezr', { conf: 0.7, src: 'relationship', weight: 5 }],
  ['p-noa', 'knows', 'p-har', { conf: 0.7, src: 'relationship', weight: 7 }],
  ['p-har', 'knows', 'p-tan', { conf: 0.8, src: 'relationship', weight: 12 }],
  ['p-jas', 'knows', 'p-mat', { conf: 0.6, src: 'relationship', weight: 3 }],
  ['p-tan', 'knows', 'p-kir', { conf: 0.7, src: 'relationship', weight: 4 }],

  // Work
  ['me',     'employed-by', 'o-ndlm', { conf: 1.0, src: 'memory', weight: 904, lastSeen: '2026-05-15' }],
  ['o-ndlm', 'employer-of', 'p-yuk',  { conf: 0.9, src: 'memory', weight: 71 }],
  ['o-ndlm', 'employer-of', 'p-ezr',  { conf: 0.9, src: 'memory', weight: 52 }],
  ['o-ndlm', 'employer-of', 'p-mat',  { conf: 0.8, src: 'memory', weight: 2 }],
  ['o-ndlm', 'employer-of', 'p-naz',  { conf: 0.8, src: 'memory', weight: 2 }],
  ['o-acme', 'employer-of', 'p-rav',  { conf: 0.9, src: 'memory', weight: 8 }],
  ['o-acme', 'employer-of', 'p-jas',  { conf: 0.7, src: 'memory', weight: 4 }],

  ['me', 'colleague-of', 'p-yuk', { conf: 0.9, src: 'calendar', weight: 71 }],
  ['me', 'colleague-of', 'p-ezr', { conf: 0.9, src: 'calendar', weight: 52 }],

  // Vendors / shopping
  ['me', 'purchased-from', 'o-bnd',  { conf: 1.0, src: 'household', weight: 144, lastSeen: '2026-05-15' }],
  ['me', 'purchased-from', 'o-gm',   { conf: 1.0, src: 'household', weight: 96,  lastSeen: '2026-05-13' }],
  ['me', 'purchased-from', 'o-rt',   { conf: 1.0, src: 'household', weight: 31,  lastSeen: '2026-04-30' }],
  ['me', 'purchased-from', 'o-utility', { conf: 1.0, src: 'household', weight: 48, lastSeen: '2026-05-01' }],
  ['me', 'purchased-from', 'o-isp', { conf: 1.0, src: 'household', weight: 48, lastSeen: '2026-05-01' }],
  ['me', 'subscribed-to',  'o-sub-nyt', { conf: 1.0, src: 'household', weight: 48, lastSeen: '2026-05-01' }],
  ['me', 'subscribed-to',  'o-sub-spo', { conf: 1.0, src: 'household', weight: 48, lastSeen: '2026-05-01' }],
  ['me', 'subscribed-to',  'o-gym', { conf: 1.0, src: 'household', weight: 21, lastSeen: '2026-05-12' }],

  // Places
  ['me', 'lives-at', 'l-home', { conf: 1.0, src: 'household', weight: 1, lastSeen: '2026-05-16' }],
  ['me', 'visited', 'l-park',  { conf: 1.0, src: 'chronicler', weight: 84, lastSeen: '2026-05-11' }],
  ['me', 'visited', 'l-cph',   { conf: 1.0, src: 'chronicler', weight: 6, lastSeen: '2025-09-04' }],
  ['o-ndlm', 'lives-at', 'l-work', { conf: 1.0, src: 'household', weight: 1 }],
  ['p-lin', 'lives-at', 'l-home', { conf: 1.0, src: 'household', weight: 1 }],

  // Groups
  ['p-amy','mentioned-in','g-fam',  { conf: 1, src: 'memory', weight: 1 }],
  ['p-ben','mentioned-in','g-fam',  { conf: 1, src: 'memory', weight: 1 }],
  ['p-cara','mentioned-in','g-fam', { conf: 1, src: 'memory', weight: 1 }],
  ['p-lin','mentioned-in','g-fam',  { conf: 1, src: 'memory', weight: 1 }],
  ['p-rav','mentioned-in','g-corev',{ conf: 1, src: 'memory', weight: 1 }],
  ['p-noa','mentioned-in','g-corev',{ conf: 1, src: 'memory', weight: 1 }],
  ['p-deb','mentioned-in','g-corev',{ conf: 1, src: 'memory', weight: 1 }],
  ['p-yuk','mentioned-in','g-team', { conf: 1, src: 'memory', weight: 1 }],
  ['p-ezr','mentioned-in','g-team', { conf: 1, src: 'memory', weight: 1 }],
  ['p-mat','mentioned-in','g-team', { conf: 1, src: 'memory', weight: 1 }],
  ['p-naz','mentioned-in','g-team', { conf: 1, src: 'memory', weight: 1 }],

  // co-attended events recently
  ['me', 'co-attended', 'p-rav', { conf: 0.9, src: 'calendar', weight: 22, lastSeen: '2026-05-09' }],
  ['me', 'co-attended', 'p-deb', { conf: 0.9, src: 'calendar', weight: 18, lastSeen: '2026-05-10' }],
  ['me', 'co-attended', 'p-yuk', { conf: 0.9, src: 'calendar', weight: 71, lastSeen: '2026-05-15' }],
];

// Build an adjacency map for fast hopping.
// Each node maps to an array of {pred, other, dir, meta}. dir=out|in.
function buildAdjacency() {
  const adj = {};
  for (const e of ENTITIES) adj[e.id] = [];
  for (const [s, p, o, meta] of RELATIONS) {
    if (!adj[s] || !adj[o]) continue;
    adj[s].push({ pred: p, other: o, dir: 'out', meta });
    adj[o].push({ pred: p, other: s, dir: 'in',  meta });
  }
  return adj;
}

const ADJ = buildAdjacency();

// Contact facts — multi-valued literals attached to person entities.
// Each row carries provenance (which butler recorded it) and a "primary"
// hint for display ordering. Anything marked verified=true has been
// confirmed by the owner; everything else is provisional.
//
// This is what folds the old /contacts page into /entities: surfacing a
// person filtered by "has at least one contact fact" is enough.
const CONTACT_FACTS = [
  // Me
  ['me',    'has-email',   'tze@northwinddlm.io',   { conf: 1.0, src: 'contact',  verified: true,  primary: true,  lastSeen: '2026-05-15' }],
  ['me',    'has-email',   'tze.lim@gmail.com',     { conf: 1.0, src: 'contact',  verified: true,                  lastSeen: '2026-05-15' }],
  ['me',    'has-phone',   '+44 7700 900110',       { conf: 1.0, src: 'contact',  verified: true,  primary: true,  lastSeen: '2026-05-15' }],
  ['me',    'has-handle',  'tzeusy · github',       { conf: 1.0, src: 'memory',   verified: true,                  lastSeen: '2026-05-12' }],
  ['me',    'has-address', '14 Park Pl, Marylebone',{ conf: 1.0, src: 'household',verified: true,  primary: true,  lastSeen: '2025-11-01' }],

  // Lin
  ['p-lin', 'has-email',   'lin.tan@northwinddlm.io',{ conf: 0.95, src: 'contact', verified: true,  primary: true,  lastSeen: '2026-05-15' }],
  ['p-lin', 'has-email',   'lin@hotmail.com',        { conf: 0.95, src: 'memory',  verified: true,                  lastSeen: '2026-04-20' }],
  ['p-lin', 'has-phone',   '+44 7700 900112',        { conf: 1.0,  src: 'contact', verified: true,  primary: true,  lastSeen: '2026-05-12' }],
  ['p-lin', 'has-birthday','15 Oct',                 { conf: 1.0,  src: 'memory',  verified: true,                  lastSeen: '2025-10-15' }],
  ['p-lin', 'has-address', '14 Park Pl, Marylebone', { conf: 1.0,  src: 'household',verified: true, primary: true,  lastSeen: '2025-11-01' }],

  // Amy (mum)
  ['p-amy', 'has-email',   'amy.lim53@yahoo.co.uk',  { conf: 0.95, src: 'contact', verified: true,  primary: true,  lastSeen: '2026-05-12' }],
  ['p-amy', 'has-phone',   '+44 1273 555 220',       { conf: 1.0,  src: 'contact', verified: true,  primary: true,  lastSeen: '2026-05-04' }],
  ['p-amy', 'has-phone',   '+44 7700 900445',        { conf: 0.7,  src: 'memory',  verified: false,                 lastSeen: '2024-12-19' }],
  ['p-amy', 'has-birthday','3 Feb',                  { conf: 1.0,  src: 'memory',  verified: true,                  lastSeen: '2026-02-03' }],

  // Ben (dad)
  ['p-ben', 'has-email',   'ben.lim@btinternet.com', { conf: 0.9,  src: 'contact', verified: true,  primary: true,  lastSeen: '2026-04-30' }],
  ['p-ben', 'has-phone',   '+44 1273 555 221',       { conf: 1.0,  src: 'contact', verified: true,  primary: true,  lastSeen: '2026-05-04' }],

  // Cara (sis)
  ['p-cara','has-email',   'cara@nowhere.io',        { conf: 0.95, src: 'contact', verified: true,  primary: true,  lastSeen: '2026-05-11' }],
  ['p-cara','has-handle',  '@caralim · twitter',     { conf: 0.95, src: 'memory',  verified: true,                  lastSeen: '2026-05-10' }],

  // Deb
  ['p-deb', 'has-email',   'deb.okafor@gmail.com',   { conf: 0.95, src: 'contact', verified: true,  primary: true,  lastSeen: '2026-05-10' }],
  ['p-deb', 'has-phone',   '+44 7700 900318',        { conf: 0.9,  src: 'memory',  verified: false,                 lastSeen: '2026-04-15' }],

  // Ravi
  ['p-rav', 'has-email',   'ravi@acmeforge.co',      { conf: 0.9,  src: 'contact', verified: true,  primary: true,  lastSeen: '2026-05-09' }],
  ['p-rav', 'has-phone',   '+44 7700 900219',        { conf: 1.0,  src: 'contact', verified: true,                  lastSeen: '2026-05-09' }],
  ['p-rav', 'has-handle',  'r.mehta · linkedin',     { conf: 0.85, src: 'memory',  verified: false,                 lastSeen: '2025-12-01' }],

  // Yuki
  ['p-yuk', 'has-email',   'yuki.sato@northwinddlm.io',{ conf: 1.0,  src: 'contact', verified: true, primary: true, lastSeen: '2026-05-15' }],
  ['p-yuk', 'has-phone',   '+44 7700 900417',        { conf: 0.85, src: 'memory',  verified: true,                  lastSeen: '2026-05-08' }],

  // Ezra
  ['p-ezr', 'has-email',   'ezra.brandt@northwinddlm.io',{conf: 1.0, src: 'contact',verified: true, primary: true, lastSeen: '2026-05-08' }],

  // Noa
  ['p-noa', 'has-email',   'noa.bergmann@protonmail.com',{conf: 0.95, src: 'contact',verified: true, primary: true, lastSeen: '2026-04-30' }],
  ['p-noa', 'has-birthday','7 Jul',                  { conf: 1.0,  src: 'memory',  verified: true,                  lastSeen: '2025-07-07' }],

  // Tanvir + duplicate
  ['p-tan', 'has-email',   'tanvir.ahmed@northwinddlm.io',{conf: 1.0,src:'contact',verified: true, primary: true, lastSeen: '2026-05-07' }],
  ['p-tan', 'has-phone',   '+44 7700 900502',        { conf: 0.9,  src: 'memory',  verified: false,                 lastSeen: '2026-04-22' }],
  ['p-tan2','has-email',   'tanvir.ahmed@northwinddlm.io',{conf:0.9, src:'memory', verified: false,                lastSeen: '2026-04-22' }],

  // Harriet, Isabel, etc — partial
  ['p-har', 'has-email',   'harriet@vance.studio',   { conf: 0.85, src: 'memory',  verified: false, primary: true,  lastSeen: '2026-05-13' }],
  ['p-isa', 'has-email',   'isa.cruz@bristolart.ac.uk',{conf:0.9,  src: 'contact', verified: true,  primary: true,  lastSeen: '2026-04-22' }],

  // Unidentified surfaces — the raw fact IS the entity's only attribute
  ['p-unk1','has-email',   'unknown@swiftpost.co',   { conf: 1.0, src: 'memory', verified: false, primary: true, lastSeen: '2026-04-19' }],
  ['p-unk2','has-phone',   '+44 7700 900482',        { conf: 1.0, src: 'memory', verified: false, primary: true, lastSeen: '2026-05-02' }],
  ['p-unk3','has-handle',  'rebecca.li · slack',     { conf: 1.0, src: 'memory', verified: false, primary: true, lastSeen: '2026-05-14' }],

  // Stale
  ['p-stale1','has-email', 'carla.pugh@oldfirm.com', { conf: 0.7, src: 'contact', verified: false, primary: true, lastSeen: '2024-09-12' }],

  // Org websites and addresses, just to show contacts apply beyond people
  ['o-ndlm', 'has-website','northwinddlm.io',        { conf: 1.0, src: 'contact', verified: true, primary: true }],
  ['o-ndlm', 'has-address','12 King’s Cross, London',{ conf: 1.0, src: 'contact', verified: true, primary: true }],
  ['o-bnd',  'has-website','bundacoffee.co.uk',      { conf: 1.0, src: 'memory',  verified: true, primary: true }],
];

function contactsFor(id) {
  return CONTACT_FACTS.filter((r) => r[0] === id).map((r) => ({
    pred: r[1], value: r[2], meta: r[3],
  }));
}

// Aggregate metrics used by several views
const METRICS = {
  byEntity: ENTITIES.length,
  byPerson: ENTITIES.filter((e) => e.type === 'person').length,
  byOrg:    ENTITIES.filter((e) => e.type === 'organization').length,
  byPlace:  ENTITIES.filter((e) => e.type === 'place').length,
  byGroup:  ENTITIES.filter((e) => e.type === 'group').length,
  unident:  ENTITIES.filter((e) => e.state === 'unidentified').length,
  duplicates: ENTITIES.filter((e) => e.state === 'duplicate-candidate').length,
  stale:    ENTITIES.filter((e) => e.state === 'stale').length,
  triples:  RELATIONS.length + (typeof CONTACT_FACTS !== 'undefined' ? CONTACT_FACTS.length : 0) + 3000,
};

Object.assign(window, {
  OWNER_ID, TYPES, PREDICATES, PREDICATE_INDEX, ENTITIES, ENTITY_INDEX,
  RELATIONS, ADJ, METRICS, CONTACT_FACTS, contactsFor,
});
