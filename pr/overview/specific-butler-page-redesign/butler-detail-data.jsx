// Per-butler synthetic detail data — keyed by butler.name. Includes the
// bespoke tab spec (label + renderer key) plus content for each tab.
//
// Each bespoke entry now has richer sub-fields the deeper renderers use.

const D = window.BUTLERS_DATA;

function seriesFor(name, range) {
  const seed = (name || '').charCodeAt(0) || 1;
  const len = range === '7d' ? 7 : range === '30d' ? 30 : 24;
  const out = [];
  for (let i = 0; i < len; i++) {
    out.push(Math.max(0, Math.round(20 + 30 * Math.sin((i + seed) / (len / 6)) + ((i * seed) % 11))));
  }
  return out;
}

const BUTLER_DETAILS = {
  relationship: {
    label: 'Relationships',
    description: 'Tracks people in your life, drafts replies, surfaces lapsed contacts.',
    process: { port: 8471, pid: 30148, uptime: '4d 12h' },
    config: {
      model: 'claude-haiku-4-5',
      schedule: 'on demand · poll every 6m',
      scopes: ['contacts:read', 'mail:draft', 'sms:read', 'calendar:read'],
      integrations: ['Google Contacts', 'Apple Mail', 'iMessage', 'Linear (CRM)'],
      configPath: '~/.butlers/relationship.toml',
    },
    counts: { episodes: 418, facts: 2184, entities: 146, rules: 23, deltas: ['+12','+9','+1','+1'] },
    bespoke: { tab: 'Contacts', kind: 'contacts' },
  },
  health: {
    label: 'Health',
    description: 'Ingests biometrics, logs activity, flags anomalies.',
    process: { port: 8472, pid: 30149, uptime: '12d 02h' },
    config: {
      model: 'claude-haiku-4-5',
      schedule: 'continuous · sub 30s',
      scopes: ['healthkit:read', 'libre3:read', 'fitness:read'],
      integrations: ['Apple HealthKit', 'Libre 3 CGM', 'Garmin', 'Withings'],
      configPath: '~/.butlers/health.toml',
    },
    counts: { episodes: 1284, facts: 8492, entities: 18, rules: 41, deltas: ['+47','+218','+0','+0'] },
    bespoke: { tab: 'Measurements', kind: 'measurements' },
  },
  calendar: {
    label: 'Calendar',
    description: 'Maintains schedule, drafts replies to invites, suggests reschedules.',
    process: { port: 8473, pid: null, uptime: '— · paused 5h 18m' },
    config: {
      model: 'claude-haiku-4-5',
      schedule: 'paused — auth expired',
      scopes: ['calendar:read', 'calendar:write'],
      integrations: ['Google Calendar (auth needed)', 'iCloud Calendar'],
      configPath: '~/.butlers/calendar.toml',
    },
    counts: { episodes: 384, facts: 1102, entities: 84, rules: 12, deltas: ['+0','+0','+0','+0'] },
    bespoke: { tab: 'Events', kind: 'events' },
  },
  qa: {
    label: 'QA Staffer',
    description: 'Watches the other butlers. Investigates anomalies, files reports.',
    process: { port: 8474, pid: 30151, uptime: '4d 12h' },
    config: {
      model: 'claude-sonnet-4-5',
      schedule: 'patrol every 14m',
      scopes: ['butlers:logs', 'sessions:read', 'metrics:read'],
      integrations: ['internal · butlers.local', 'Slack (alerts)'],
      configPath: '~/.butlers/qa.toml',
    },
    counts: { episodes: 824, facts: 3891, entities: 8, rules: 56, deltas: ['+18','+34','+0','+2'] },
    bespoke: { tab: 'Investigations', kind: 'investigations' },
  },
  memory: {
    label: 'Memory',
    description: 'Consolidates short-term episodes into mid- and long-term tiers.',
    process: { port: 8475, pid: 30152, uptime: '11d 04h' },
    config: {
      model: 'claude-haiku-4-5',
      schedule: 'morning + afternoon + nightly',
      scopes: ['memory:rw'],
      integrations: ['internal · memory.db'],
      configPath: '~/.butlers/memory.toml',
    },
    counts: { episodes: 4218, facts: 18420, entities: 612, rules: 312, deltas: ['+12','+47','+1','+1'] },
    bespoke: { tab: 'Consolidations', kind: 'consolidations' },
  },
  education: {
    label: 'Education',
    description: 'Spaced-repetition queue. Builds decks, schedules reviews.',
    process: { port: 8476, pid: 30153, uptime: '4d 12h' },
    config: {
      model: 'claude-haiku-4-5',
      schedule: 'on demand · review reminders',
      scopes: ['anki:rw', 'notes:read'],
      integrations: ['Anki', 'Obsidian (notes)'],
      configPath: '~/.butlers/education.toml',
    },
    counts: { episodes: 184, facts: 921, entities: 38, rules: 14, deltas: ['+1','+2','+0','+0'] },
    bespoke: { tab: 'Decks', kind: 'decks' },
  },
  chronicler: {
    label: 'Chronicler',
    description: 'Reconstructs your day. Pulls from calendar, location, listens, screen-time.',
    process: { port: 8477, pid: 30154, uptime: '4d 12h' },
    config: {
      model: 'claude-sonnet-4-5',
      schedule: 'continuous ingestion · 18:00 assembly',
      scopes: ['location:read', 'screen-time:read', 'spotify:read', 'photos:read'],
      integrations: ['Apple Location', 'Spotify (auth needed)', 'Photos', 'Screen Time'],
      configPath: '~/.butlers/chronicler.toml',
    },
    counts: { episodes: 624, facts: 4218, entities: 218, rules: 28, deltas: ['+8','+92','+4','+0'] },
    bespoke: { tab: 'Timelines', kind: 'timelines' },
  },
  household: {
    label: 'Household',
    description: 'Pantry, groceries, recurring chores. Drafts orders for approval.',
    process: { port: 8478, pid: 30155, uptime: '4d 12h' },
    config: {
      model: 'claude-haiku-4-5',
      schedule: 'inventory daily · weekly orders',
      scopes: ['traderjoes:order', 'amazon:order', 'pantry:rw'],
      integrations: ['Trader Joe\'s', 'Amazon Fresh', 'pantry.db'],
      configPath: '~/.butlers/household.toml',
    },
    counts: { episodes: 84, facts: 421, entities: 184, rules: 18, deltas: ['+1','+8','+0','+0'] },
    bespoke: { tab: 'Orders', kind: 'orders' },
  },
};

// ─── Bespoke tab content (deepened) ──────────────────────────────────────

const BESPOKE_CONTENT = {
  contacts: {
    kpis: [
      { label: 'tracked', value: '146', sub: 'tier1: 6 · tier2: 12 · tier3: 38' },
      { label: 'warmth · t1 avg', value: '0.86', sub: '+0.02 vs last week' },
      { label: 'cadence · ok', value: '52', sub: '/56 inside window' },
      { label: 'overdue', value: '4', sub: 'follow-ups needed', tone: 'amber' },
    ],
    tierDistribution: [
      { tier: 1, count: 6,  warm: 0.86 },
      { tier: 2, count: 12, warm: 0.71 },
      { tier: 3, count: 38, warm: 0.52 },
      { tier: 4, count: 90, warm: 0.34 },
    ],
    rows: D.contacts.map((c) => ({
      key: c.name, tier: c.tier, last: c.last, warm: c.warm,
    })),
    overdue: [
      { name: 'Daniel', tier: 3, owed: '4d', target: '14d cadence', last: 'msg · 4d ago' },
      { name: 'Priya',  tier: 3, owed: '38d', target: '30d cadence', last: 'msg · 38d ago' },
      { name: 'Theo',   tier: 2, owed: '6d',  target: '14d cadence', last: 'lunch · 20d ago' },
      { name: 'Renee',  tier: 3, owed: '12d', target: '30d cadence', last: 'msg · 42d ago' },
    ],
    selected: {
      name: 'Maya', tier: 2, warm: 0.71, lastFour: [
        { ts: '14:02 today', dir: 'out', text: '(drafted) Sunday brunch instead — 11am at La Bête?' },
        { ts: '11m ago',     dir: 'in',  text: 'lol need to push our Sat dinner — long week. swap?' },
        { ts: 'sat 21:14',   dir: 'out', text: 'hey! still on for 7pm at Tambo?' },
        { ts: 'thu 09:02',   dir: 'in',  text: 'just got off the train — saw the spot you mentioned' },
      ],
      facts: [
        'Prefers Sunday brunch over Saturday dinner.',
        'Allergic to shellfish.',
        'Daughter Ada · 4y · loves the aquarium.',
      ],
    },
  },

  measurements: {
    kpis: [
      { label: 'glucose', value: '94', sub: 'mg/dL · in range' },
      { label: 'hrv', value: '58', sub: 'ms · 7d avg 56' },
      { label: 'steps', value: '6.4k', sub: 'goal 8k · 80%' },
      { label: 'sleep', value: '7h12m', sub: 'last night' },
    ],
    series: [
      { name: 'glucose · mg/dL', data: [102,98,96,95,99,94,92,95,98,94,93,96,100,94], range: [70,140], anomaly: null },
      { name: 'heart rate · bpm', data: [62,64,65,68,72,71,68,64,62,65,72,75,72,68], range: [50,90], anomaly: null },
      { name: 'hrv · ms', data: [54,57,55,60,62,58,56,58,60,55,52,58,60,58], range: [40,80], anomaly: null },
      { name: 'weight · lb', data: [171,170.6,170.9,170.4,170.1,169.8,169.6], range: null, anomaly: null },
    ],
    sleep: {
      total: '7h 12m',
      stages: [
        { kind: 'awake', mins: 18,  pct: 0.04 },
        { kind: 'rem',   mins: 92,  pct: 0.21 },
        { kind: 'light', mins: 224, pct: 0.52 },
        { kind: 'deep',  mins: 98,  pct: 0.23 },
      ],
    },
    sources: [
      { name: 'Apple HealthKit', last: 'now',    samples: '4,892 today' },
      { name: 'Libre 3 CGM',     last: '6m ago', samples: '288 today' },
      { name: 'Garmin',          last: '21m ago',samples: '14 walks · 7d' },
      { name: 'Withings',        last: '8h ago', samples: 'weight · 1' },
    ],
    note: '4 readings ingested today · last 6m ago',
  },

  events: {
    kpis: [
      { label: 'today', value: '4', sub: '2 done · 2 upcoming' },
      { label: 'this week', value: '18', sub: '−3 vs last' },
      { label: 'paused syncs', value: '4', sub: 'auth expired', tone: 'amber' },
      { label: 'conflicts', value: '1', sub: 'resolve before 19:00', tone: 'amber' },
    ],
    week: [
      { day: 'Mon', date: '04', events: [{ t: 9,  dur: 1,   label: 'Standup' }] },
      { day: 'Tue', date: '05', events: [{ t: 12, dur: 1.5, label: 'Lunch · Wei' }, { t: 18, dur: 2, label: 'Class' }] },
      { day: 'Wed', date: '06', current: true, events: [{ t: 9, dur: 0.5, label: 'Standup', past: true }, { t: 12, dur: 1, label: 'Lunch · Wei', past: true }, { t: 15.5, dur: 1, label: 'Mei coffee' }, { t: 19.5, dur: 2, label: 'Dinner · W&S' }] },
      { day: 'Thu', date: '07', events: [{ t: 9, dur: 0.5, label: 'Standup' }, { t: 14, dur: 1, label: 'Dentist' }] },
      { day: 'Fri', date: '08', events: [{ t: 9, dur: 0.5, label: 'Standup' }, { t: 17, dur: 1, label: 'Demo' }] },
      { day: 'Sat', date: '09', events: [] },
      { day: 'Sun', date: '10', events: [{ t: 11, dur: 1.5, label: 'Brunch · Maya' }] },
    ],
    upcoming: [
      { time: '15:30', label: 'Mei — coffee at Old Hill', meta: 'tier 2 contact' },
      { time: '17:00', label: 'Grocery approval window closes', meta: 'auto-decline' },
      { time: '19:30', label: 'Dinner — Wei & Sarah', meta: 'tier 1' },
      { time: 'tomorrow 09:00', label: 'Standup — engineering', meta: 'recurring' },
      { time: 'tomorrow 14:00', label: 'Dentist · Dr. Lin', meta: '6mo cleaning' },
    ],
    drafts: [
      { ts: '11m ago', who: 'Maya', subject: 'Re: Sunday brunch?', preview: 'Sunday brunch works — 11am at La Bête?', state: 'awaiting your send' },
      { ts: '2h ago',  who: 'Aniket', subject: 'Re: Friday demo prep', preview: 'I can join at 16:30 if that works.', state: 'awaiting your send' },
    ],
    conflicts: [
      { ts: 'Wed 19:30', label: 'Dinner · Wei & Sarah overlaps with "Class · advanced korean"', resolve: 'auto-suggest reschedule class to Thu' },
    ],
    sources: [
      { name: 'Google Calendar', state: 'paused — auth expired', tone: 'amber' },
      { name: 'iCloud Calendar', state: 'live · 0 changes',      tone: 'green' },
    ],
  },

  investigations: {
    kpis: [
      { label: 'open', value: '1', sub: 'low severity' },
      { label: 'closed · 24h', value: '8', sub: '7 auto, 1 escalated' },
      { label: 'patrols · 24h', value: '102', sub: '14m cadence' },
      { label: 'mttr · 24h', value: '38m', sub: '−12m vs 7d', tone: 'green' },
    ],
    patrolStripe: [1,1,1,1,1,1,2,2,2,2,2,2,3,3,2,2,2,2,2,2,2,1,1,1],
    investigations: [
      { id: '#218', sev: 'low',    title: 'Spotify scope drift detected',     butler: 'chronicler', age: '1d 3h',  state: 'open · awaiting reauth' },
      { id: '#217', sev: 'low',    title: 'Calendar oauth refresh failed',    butler: 'calendar',   age: '5h 18m', state: 'escalated → user' },
      { id: '#216', sev: 'medium', title: 'Memory consolidation 14% slower',  butler: 'memory',     age: '2h 11m', state: 'closed · within bounds' },
      { id: '#215', sev: 'low',    title: 'Health duplicate measurement',     butler: 'health',     age: '4h 28m', state: 'closed · auto-dedup' },
      { id: '#214', sev: 'low',    title: 'Notion API rate-limit',            butler: 'chronicler', age: '7h 02m', state: 'closed · backoff applied' },
    ],
    selected: {
      id: '#218', sev: 'low', butler: 'chronicler',
      title: 'Spotify scope drift detected',
      hypothesis: 'New permission needed for listening-history ingestion. Spotify rotated oauth scope for /me/player/recently-played on 2026-05-05.',
      timeline: [
        { ts: '14:32', what: 'qa.alert raised — chronicler ingestion stalled' },
        { ts: '14:30', what: 'chronicler ingest.spotify failed × 4 (scope_mismatch)' },
        { ts: '14:14', what: 'qa.patrol confirmed pattern' },
        { ts: '14:14', what: 'investigation #218 opened — awaiting reauth' },
      ],
      evidence: [
        '4 consecutive 401 responses on /me/player/recently-played',
        'oauth introspection: scope set unchanged since 2025-12',
        'Spotify changelog: scope rename effective 2026-05-05 09:00 UTC',
      ],
      next: 'Surface re-authorize CTA on /settings/integrations.',
    },
  },

  consolidations: {
    kpis: [
      { label: 'short-term', value: '184', sub: 'pending consolidation' },
      { label: 'mid-term', value: '4,218', sub: '+12 today' },
      { label: 'long-term', value: '13,212', sub: '+0 today' },
      { label: 'dropped · 7d', value: '142', sub: 'duplicates / stale' },
    ],
    tiers: [
      { name: 'short', count: 184,    cap: 500, color: 'fg' },
      { name: 'mid',   count: 4218,   cap: 8000, color: 'mfg' },
      { name: 'long',  count: 13212,  cap: 20000, color: 'dim' },
    ],
    runs: [
      { time: '13:47', kind: 'morning', moved: '4 → mid', dropped: 1, dur: '2.1s', tokens: '8.4k' },
      { time: '11:30', kind: 'patrol',  moved: '0 → mid', dropped: 0, dur: '0.4s', tokens: '0.2k' },
      { time: '08:00', kind: 'wakeup',  moved: '7 → mid', dropped: 3, dur: '3.4s', tokens: '12.1k' },
      { time: '02:00', kind: 'nightly', moved: '24 → mid · 8 → long', dropped: 5, dur: '14.2s', tokens: '52.3k' },
    ],
    facts: [
      { kind: 'fact',   text: 'Maya prefers Sunday brunch over Saturday dinner.', tier: 'mid', butler: 'relationship' },
      { kind: 'entity', text: 'Camden — recurring lunch venue with Wei.',         tier: 'mid', butler: 'chronicler' },
      { kind: 'rule',   text: 'Reply within 6h to tier-1 contacts on weekdays.',  tier: 'long',butler: 'relationship' },
      { kind: 'fact',   text: "Sarah's daughter starts school 2026-09-04.",       tier: 'long',butler: 'relationship' },
      { kind: 'fact',   text: 'HRV 58ms (above 7d avg 56).',                      tier: 'short',butler: 'health' },
      { kind: 'rule',   text: 'Calendar sync paused — Google OAuth expired.',     tier: 'short',butler: 'calendar' },
      { kind: 'fact',   text: 'Spotify scope drift detected 2026-05-05.',         tier: 'short',butler: 'qa' },
      { kind: 'fact',   text: 'Pantry low: eggs, oats, oat milk, bananas.',       tier: 'short',butler: 'household' },
    ],
  },

  decks: {
    kpis: [
      { label: 'cards · all', value: '1,284', sub: 'across 6 decks' },
      { label: 'due today', value: '47', sub: '92% retention' },
      { label: 'streak', value: '38', sub: 'days · best 64' },
      { label: 'time · 7d', value: '38m', sub: '~5m / day' },
    ],
    decks: [
      { name: 'Spanish · core',         due: 18, total: 412, retention: 0.94 },
      { name: 'ML · concepts',          due: 12, total: 184, retention: 0.91 },
      { name: 'Distributed systems',    due: 6,  total: 92,  retention: 0.96 },
      { name: 'Korean · 한글',           due: 8,  total: 218, retention: 0.88 },
      { name: 'Cooking · techniques',   due: 3,  total: 84,  retention: 0.97 },
      { name: 'Chess · openings',       due: 0,  total: 294, retention: 0.93 },
    ],
    queue: [
      { deck: 'Spanish · core',     front: 'el embotellamiento',   hint: 'noun', last: '12d ago · 2/3' },
      { deck: 'ML · concepts',      front: 'KL divergence',        hint: 'one-line def',  last: '4d ago · 4/5' },
      { deck: 'Korean · 한글',       front: '벌써',                  hint: 'adverb', last: '21d ago · 1/4' },
      { deck: 'Spanish · core',     front: 'aprovechar (de)',      hint: 'verb',   last: '8d ago · 3/4' },
      { deck: 'Distributed systems',front: 'CAP theorem · partition tolerance', hint: 'one-line', last: '14d ago · 5/5' },
    ],
    retention30d: [0.91,0.92,0.90,0.92,0.93,0.91,0.92,0.93,0.94,0.92,0.91,0.93,0.94,0.92,0.93,0.94,0.91,0.93,0.92,0.94,0.93,0.92,0.91,0.93,0.94,0.92,0.93,0.94,0.92,0.92],
    streakWeeks: [1,1,1,1,1,1,1, 1,1,1,1,1,1,1, 1,1,1,1,1,1,1, 1,1,1,1,1,1,1, 1,1,1,1,1,1,1, 1,1],
  },

  timelines: {
    kpis: [
      { label: 'today · events', value: '37', sub: '4 sources' },
      { label: 'sources · live', value: '3', sub: '1 paused', tone: 'amber' },
      { label: 'gap · longest', value: '47m', sub: '12:23 → 13:10' },
      { label: 'next assembly', value: '18:00', sub: '~12m run' },
    ],
    todays: [
      { t: '07:30', label: 'Wake', src: 'sleep+motion', kind: 'sleep' },
      { t: '08:02', label: 'Spotify · morning playlist', src: 'spotify', kind: 'media' },
      { t: '08:45', label: 'Call · Mom (8m)', src: 'phone', kind: 'social' },
      { t: '09:14', label: 'Office · arrived', src: 'location', kind: 'place' },
      { t: '11:14', label: 'Anki review · 47 cards', src: 'butlers', kind: 'work' },
      { t: '12:02', label: 'Lunch · Camden with Wei', src: 'calendar+location', kind: 'social' },
      { t: '12:23', label: 'GAP · 47m unaccounted', src: '—', kind: 'gap' },
      { t: '13:10', label: 'Walk · 28m, 1.4mi', src: 'motion', kind: 'walk' },
      { t: '13:47', label: 'Glucose 94 mg/dL', src: 'libre3', kind: 'biom' },
      { t: '14:14', label: 'QA patrol clean', src: 'butlers', kind: 'work' },
    ],
    sources: [
      { name: 'calendar',  count: 7,  state: 'live' },
      { name: 'location',  count: 12, state: 'live' },
      { name: 'spotify',   count: 142, state: 'paused', tone: 'amber' },
      { name: 'photos',    count: 4,  state: 'live' },
      { name: 'butlers',   count: 8,  state: 'live' },
    ],
  },

  orders: {
    kpis: [
      { label: 'pending', value: '1', sub: '$148.20 · expires 17:00', tone: 'amber' },
      { label: 'this month', value: '$612', sub: '−8% vs avg' },
      { label: 'pantry · low', value: '6', sub: 'eggs, oats, …' },
      { label: 'recurring', value: '4', sub: 'next: thu 09:00' },
    ],
    pending: {
      id: 'TJ-2026-0142', vendor: "Trader Joe's", total: '$148.20', items: 23,
      reason: 'Pantry shortfall + recurring weekly order',
      lines: [
        { qty: 2, name: 'Eggs · pasture-raised dozen', price: '$10.98', sub: false },
        { qty: 4, name: 'Oat milk · half-gallon',      price: '$15.96', sub: false },
        { qty: 1, name: 'Steel-cut oats · 24oz',       price: '$4.99',  sub: 'subbed: rolled oats not in stock' },
        { qty: 6, name: 'Bananas',                     price: '$2.94',  sub: false },
        { qty: 2, name: 'Lemons · bag',                price: '$3.98',  sub: false },
        { qty: 1, name: 'Garlic · 3-pack',             price: '$2.49',  sub: false },
        { qty: 1, name: '+ 17 more line items',        price: '$106.86', sub: false },
      ],
      window: 'expires 17:00 today · auto-decline at deadline',
    },
    pantry: [
      { item: 'Eggs',     onHand: 2,  par: 12, runout: 'tomorrow' },
      { item: 'Oats',     onHand: 0,  par: 1,  runout: 'now' },
      { item: 'Oat milk', onHand: 1,  par: 4,  runout: '2 days' },
      { item: 'Bananas',  onHand: 0,  par: 6,  runout: 'now' },
      { item: 'Lemons',   onHand: 0,  par: 4,  runout: 'now' },
      { item: 'Garlic',   onHand: 1,  par: 3,  runout: '4 days' },
    ],
    history: [
      { id: 'TJ-2026-0141', vendor: "Trader Joe's", total: '$94.18',  items: 14, state: 'delivered · 3d ago' },
      { id: 'AF-2026-0089', vendor: 'Amazon Fresh', total: '$42.50',  items: 8,  state: 'delivered · 5d ago' },
      { id: 'TJ-2026-0140', vendor: "Trader Joe's", total: '$118.32', items: 19, state: 'delivered · 10d ago' },
      { id: 'AF-2026-0088', vendor: 'Amazon Fresh', total: '$28.40',  items: 4,  state: 'delivered · 12d ago' },
    ],
    vendors: [
      { name: "Trader Joe's", spend: 412, share: 0.67 },
      { name: 'Amazon Fresh', spend: 142, share: 0.23 },
      { name: 'Local mkt',    spend: 58,  share: 0.10 },
    ],
  },
};

window.BUTLER_DETAILS = BUTLER_DETAILS;
window.BESPOKE_CONTENT = BESPOKE_CONTENT;
window.seriesFor = seriesFor;
