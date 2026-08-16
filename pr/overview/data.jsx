// Realistic Butlers data — flavors: relationship, health, calendar, qa, education, chronicler

const NOW = new Date('2026-05-06T14:32:00');

window.BUTLERS_DATA = {
  now: NOW,
  user: { name: 'Tze', greeting: 'Wednesday afternoon' },

  // Things requiring HUMAN action — bubble these to the top
  attention: [
    {
      id: 'reauth-gcal',
      kind: 'reauth',
      severity: 'high',
      butler: 'calendar',
      title: 'Google Calendar token expired',
      detail: 'OAuth refresh failed at 09:14. 4 scheduled syncs paused.',
      action: 'Re-authorize',
      age: '5h 18m',
    },
    {
      id: 'approval-grocery',
      kind: 'approval',
      severity: 'medium',
      butler: 'household',
      title: 'Approve grocery order ($148.20)',
      detail: 'Trader Joe\'s — 23 items, including 2 substitutions',
      action: 'Review',
      age: '42m',
    },
    {
      id: 'approval-email',
      kind: 'approval',
      severity: 'medium',
      butler: 'relationship',
      title: 'Send reply to Maya about Sunday dinner',
      detail: 'Drafted from your prior pattern (warm, brief, suggests time)',
      action: 'Send / Edit',
      age: '11m',
    },
    {
      id: 'reauth-spotify',
      kind: 'reauth',
      severity: 'low',
      butler: 'chronicler',
      title: 'Spotify scope changed',
      detail: 'New permission needed for listening-history ingestion.',
      action: 'Re-authorize',
      age: '1d 3h',
    },
  ],

  // Live butler roster
  butlers: [
    { name: 'relationship', label: 'Relationships', status: 'ok',     activity: 'idle',     loadPct: 12, sessions24h: 47, costToday: 1.84, lastRun: '6m ago' },
    { name: 'health',       label: 'Health',        status: 'ok',     activity: 'running',  loadPct: 38, sessions24h: 62, costToday: 2.41, lastRun: 'now' },
    { name: 'calendar',     label: 'Calendar',      status: 'degraded',activity: 'paused',  loadPct:  0, sessions24h: 18, costToday: 0.31, lastRun: '5h 18m' },
    { name: 'qa',           label: 'QA Staffer',    status: 'ok',     activity: 'patrol',   loadPct: 22, sessions24h: 144, costToday: 0.84, lastRun: '14m ago' },
    { name: 'memory',       label: 'Memory',        status: 'ok',     activity: 'consolidating', loadPct: 51, sessions24h: 31, costToday: 0.57, lastRun: 'now' },
    { name: 'education',    label: 'Education',     status: 'ok',     activity: 'idle',     loadPct:  4, sessions24h:  9, costToday: 0.18, lastRun: '2h ago' },
    { name: 'chronicler',   label: 'Chronicler',    status: 'ok',     activity: 'ingesting',loadPct: 28, sessions24h: 23, costToday: 0.93, lastRun: '3m ago' },
    { name: 'household',    label: 'Household',     status: 'waiting',activity: 'awaiting approval', loadPct: 0, sessions24h: 7, costToday: 0.12, lastRun: '42m ago' },
  ],

  // Top-line KPIs
  kpis: {
    sessionsToday: { value: 341, delta: '+18%', sparkline: [12,14,11,9,8,6,4,3,2,5,8,11,14,18,22,26,28,30,29,31,33,34,32,28] },
    costToday:     { value: 7.20, unit: '$', delta: '−4%',  sparkline: [0.4,0.3,0.2,0.2,0.1,0.1,0.1,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.7,0.6,0.6,0.5,0.4,0.4,0.3,0.3,0.2,0.2] },
    momentsLogged: { value: 1287, delta: '+9%', sparkline: [40,38,42,45,48,50,52,55,58,62,65,68,72,75,78,80,82,85,88,90,88,85,82,80] },
    healthyButlers:{ value: 7, total: 8 },
    pendingApprovals: { value: 2 },
    reauthNeeded:  { value: 2 },
  },

  // Narrative feed — chronological story of the morning
  feed: [
    { time: '14:28', butler: 'health',       kind: 'log',     text: 'Ingested glucose reading 94 mg/dL from Libre 3.', meta: 'measurement #4 today' },
    { time: '14:14', butler: 'qa',           kind: 'finding', text: '7 sessions completed without anomaly. Patrol clean.', meta: '14m patrol' },
    { time: '14:02', butler: 'relationship', kind: 'draft',   text: 'Drafted reply to Maya re: Sunday dinner.', meta: 'awaiting your send', cta: '/approvals' },
    { time: '13:47', butler: 'memory',       kind: 'consolidate', text: 'Promoted 4 short-term facts → mid-term tier.', meta: 'morning episode' },
    { time: '13:12', butler: 'chronicler',   kind: 'log',     text: 'Reconstructed 09:00–13:00 timeline (lunch w/ Wei at Camden).', meta: '37 events, 4 sources' },
    { time: '12:30', butler: 'household',    kind: 'awaiting',text: 'Grocery order ready for review — $148.20.', meta: 'expires 17:00', cta: '/approvals' },
    { time: '11:58', butler: 'health',       kind: 'log',     text: 'Logged 28-minute walk (1.4mi, 64 avg HR).', meta: 'auto-detected' },
    { time: '11:14', butler: 'education',    kind: 'log',     text: 'Completed Anki review — 47 cards, 92% retention.', meta: '6m session' },
    { time: '09:14', butler: 'calendar',     kind: 'error',   text: 'Google Calendar OAuth refresh failed.', meta: 'token rotation', cta: '/settings/integrations' },
    { time: '08:47', butler: 'relationship', kind: 'log',     text: 'Logged morning call with Mom (8m).',  meta: 'tier 1' },
    { time: '08:02', butler: 'chronicler',   kind: 'log',     text: 'Began listening-history ingestion (Spotify).', meta: '142 plays since Mon' },
    { time: '07:30', butler: 'qa',           kind: 'finding', text: 'Investigation #214 closed: rate-limit on Notion API.', meta: 'resolved auto' },
  ],

  // 24h session activity per butler — for stripe chart
  // Each row 24 values 0..1 (intensity)
  sessionGrid: [
    { butler: 'relationship', row: [0,0,0,0,0,0,1,2,3,2,1,1,2,3,2,1,1,2,3,2,1,1,0,0] },
    { butler: 'health',       row: [0,0,0,0,0,0,2,3,2,1,1,2,3,4,3,2,1,1,2,3,2,1,0,0] },
    { butler: 'calendar',     row: [1,1,1,0,0,0,1,2,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0] },
    { butler: 'qa',           row: [1,1,1,1,1,1,2,2,2,2,2,2,3,3,2,2,2,2,2,2,2,1,1,1] },
    { butler: 'memory',       row: [3,3,2,1,0,0,1,1,1,1,1,1,2,2,1,1,1,1,1,2,2,3,3,3] },
    { butler: 'education',    row: [0,0,0,0,0,0,0,1,1,0,0,0,0,0,0,0,1,2,1,0,0,0,0,0] },
    { butler: 'chronicler',   row: [0,0,0,0,0,0,1,2,2,1,1,1,2,3,2,1,1,2,2,2,2,1,0,0] },
    { butler: 'household',    row: [0,0,0,0,0,0,0,1,1,0,0,0,1,1,1,0,0,0,0,0,0,0,0,0] },
  ],

  // Upcoming — calendar / scheduled butler runs
  upcoming: [
    { time: '15:00', kind: 'butler',  butler: 'memory', label: 'Memory consolidation (afternoon)', dur: '~3m' },
    { time: '15:30', kind: 'event',   label: 'Mei — coffee at Old Hill', meta: 'tier 2 contact' },
    { time: '17:00', kind: 'expires', label: 'Grocery approval window closes', meta: 'auto-decline' },
    { time: '18:00', kind: 'butler',  butler: 'chronicler', label: 'Daily timeline assembly', dur: '~12m' },
    { time: '19:30', kind: 'event',   label: 'Dinner — Wei & Sarah', meta: 'tier 1' },
    { time: '22:00', kind: 'butler',  butler: 'health', label: 'Sleep prep checklist',     dur: '<1m' },
  ],

  // Recent contacts + interaction warmth
  contacts: [
    { name: 'Mom',     tier: 1, last: 'call · 6h ago', warm: 0.92 },
    { name: 'Wei',     tier: 1, last: 'lunch · 2h ago', warm: 0.88 },
    { name: 'Maya',    tier: 2, last: 'msg · 11m ago (drafted)', warm: 0.71 },
    { name: 'Sarah',   tier: 1, last: 'dinner upcoming', warm: 0.84 },
    { name: 'Mei',     tier: 2, last: 'coffee in 28m', warm: 0.65 },
    { name: 'Daniel',  tier: 3, last: 'msg · 4d ago', warm: 0.41 },
  ],

  // Health sparklines — small ambient signals
  health: {
    glucose: { value: 94, unit: 'mg/dL', trend: [102, 98, 96, 95, 99, 94] },
    steps:   { value: 6420, unit: '', trend: [400, 1200, 2400, 3100, 4800, 6420] },
    hrv:     { value: 58, unit: 'ms', trend: [54, 57, 55, 60, 62, 58] },
    sleep:   { value: '7h 12m', last: 'last night' },
  },

  // Cost breakdown for the day
  costsByButler: [
    { name: 'health', cost: 2.41 },
    { name: 'relationship', cost: 1.84 },
    { name: 'chronicler', cost: 0.93 },
    { name: 'qa', cost: 0.84 },
    { name: 'memory', cost: 0.57 },
    { name: 'calendar', cost: 0.31 },
    { name: 'education', cost: 0.18 },
    { name: 'household', cost: 0.12 },
  ],

  // Memory growth
  memory: {
    episodes: 4218, episodesDelta: '+12 today',
    facts:    18420, factsDelta: '+47 today',
    rules:    312, rulesDelta: '+1 today',
  },
};
