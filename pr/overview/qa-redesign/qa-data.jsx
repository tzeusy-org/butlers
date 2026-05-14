// Synthetic data for the QA staffer redesign.
// QA tails butler logs, opens investigations, files PRs against butler runtime.

const QA_NOW = new Date('2026-05-06T14:32:00');

// PR lifecycle: drafted → open → merged → closed (or rejected)
// Each case has: id, severity, butler, headline, detected, blurb (LLM diagnosis+plan),
// hypothesis (technical), evidence (log lines), pr (status, files, additions/deletions, link), state
const QA_CASES = [
  {
    id: '#218',
    sev: 'low',
    butler: 'chronicler',
    headline: 'Spotify ingestion failing — scope rotated upstream',
    detected: '14:14',
    state: 'open · awaiting reauth',
    age: '18m',
    blurb: 'Spotify rotated the OAuth scope name for /me/player/recently-played at 09:00 UTC yesterday. Chronicler\'s ingest call now returns 401. The runtime fix is mechanical — update the requested scope string and re-prompt the user — but the actual reauth needs a human, so the PR adds the new scope plus a graceful error path that points at /settings/integrations.',
    hypothesis: 'Hard-coded scope string drifted from Spotify\'s 2026-05-05 rename.',
    evidence: [
      { ts: '14:30:11', lvl: 'ERROR', butler: 'chronicler', msg: 'spotify.ingest 401 scope_mismatch /me/player/recently-played' },
      { ts: '14:28:02', lvl: 'ERROR', butler: 'chronicler', msg: 'spotify.ingest 401 scope_mismatch /me/player/recently-played' },
      { ts: '14:14:11', lvl: 'WARN',  butler: 'qa',         msg: 'patrol.flag chronicler.ingest.spotify failure_streak=4' },
    ],
    pr: {
      id: '#1284', state: 'open', branch: 'qa/spotify-scope-rename',
      title: 'chronicler: rename spotify scope user-read-recently-played → user-recently-played',
      files: ['butlers/chronicler/ingest/spotify.py', 'butlers/chronicler/config.toml'],
      additions: 18, deletions: 6, ci: 'passing',
      reviewers: ['Tze (you)'], opened: '14:18',
      url: 'https://github.com/butlers-local/butlers/pull/1284',
    },
  },
  {
    id: '#217',
    sev: 'medium',
    butler: 'memory',
    headline: 'Consolidation job 14% slower over 7d — within tolerance, watching',
    detected: '12:11',
    state: 'closed · within bounds',
    age: '2h 21m',
    blurb: 'Memory\'s morning consolidation has crept from 1.8s to 2.1s over the last week. The cause is the short-tier cache exceeding the L1 bound (default 128) — eviction is now happening every run. Not a regression, just growth. Patched the bound to 256 and added a runtime metric so we notice earlier next time.',
    hypothesis: 'Short-tier cache eviction every run after it crossed L1 bound.',
    evidence: [
      { ts: '13:47:19', lvl: 'WARN', butler: 'memory', msg: 'consolidate.morning 2.14s (+0.31 vs 7d avg)' },
      { ts: '11:30:11', lvl: 'WARN', butler: 'memory', msg: 'cache.evict short_tier=184 capacity=128' },
    ],
    pr: {
      id: '#1283', state: 'merged', branch: 'qa/memory-cache-bound',
      title: 'memory: raise short-tier cache bound 128 → 256, expose evict_count metric',
      files: ['butlers/memory/consolidate.py', 'butlers/memory/cache.py', 'butlers/memory/metrics.py'],
      additions: 42, deletions: 14, ci: 'passing',
      reviewers: ['Tze (you)'], opened: '13:02', merged: '13:38',
      url: 'https://github.com/butlers-local/butlers/pull/1283',
    },
  },
  {
    id: '#216',
    sev: 'low',
    butler: 'health',
    headline: 'Duplicate glucose samples — Libre 3 + HealthKit double-write',
    detected: '10:28',
    state: 'merged · auto-resolved',
    age: '4h 04m',
    blurb: 'When the user has Libre 3 connected and Apple Health is also pulling from Libre via HealthKit, glucose readings get written twice with the same timestamp. The dedup ran on (source, ts) but Libre is two different source ids. Fix is one line in the dedup key.',
    hypothesis: 'Dedup key omits canonical-source resolution.',
    evidence: [
      { ts: '10:28:14', lvl: 'WARN', butler: 'health', msg: 'ingest.dedup conflict glucose@14:14 sources=[libre3, healthkit:libre3]' },
      { ts: '10:28:13', lvl: 'WARN', butler: 'health', msg: 'ingest.dedup conflict glucose@14:00 sources=[libre3, healthkit:libre3]' },
    ],
    pr: {
      id: '#1282', state: 'merged', branch: 'qa/health-dedup-canonical',
      title: 'health: canonicalize libre3-via-healthkit source in dedup key',
      files: ['butlers/health/ingest/dedup.py'],
      additions: 7, deletions: 2, ci: 'passing',
      reviewers: ['Tze (you)'], opened: '10:42', merged: '11:01',
      url: 'https://github.com/butlers-local/butlers/pull/1282',
    },
  },
  {
    id: '#215',
    sev: 'medium',
    butler: 'household',
    headline: 'Trader Joe\'s order endpoint returning 503 in afternoons',
    detected: 'yesterday 16:48',
    state: 'merged · backoff applied',
    age: '21h',
    blurb: 'TJ\'s order endpoint is rate-limiting between 16:00 and 17:00 daily. Household was failing the order rather than backing off. PR adds an exponential-backoff retry (4 tries, 250ms→2s) and shifts the daily order window earlier when the upstream is hot.',
    hypothesis: 'Upstream rate-limit at peak; no retry policy.',
    evidence: [
      { ts: '16:48:02', lvl: 'ERROR', butler: 'household', msg: 'traderjoes.order 503 service_unavailable' },
      { ts: '16:48:01', lvl: 'ERROR', butler: 'household', msg: 'traderjoes.order 503 service_unavailable' },
    ],
    pr: {
      id: '#1281', state: 'merged', branch: 'qa/household-tj-backoff',
      title: 'household: exponential backoff for traderjoes.order; shift schedule to 14:00',
      files: ['butlers/household/order/traderjoes.py', 'butlers/household/scheduler.py'],
      additions: 64, deletions: 18, ci: 'passing',
      reviewers: ['Tze (you)'], opened: '17:11', merged: '08:42',
      url: 'https://github.com/butlers-local/butlers/pull/1281',
    },
  },
  {
    id: '#214',
    sev: 'low',
    butler: 'chronicler',
    headline: 'Notion API rate-limit during morning ingestion',
    detected: '07:30',
    state: 'merged · backoff applied',
    age: '7h 02m',
    blurb: 'Notion\'s 3 req/s rate-limit was hit during the morning timeline assembly when 14 pages were touched in under a second. Added token-bucket backoff and a small batching layer. Investigation auto-closed once the next patrol came back clean.',
    hypothesis: 'Burst calls > 3 req/s ceiling.',
    evidence: [
      { ts: '07:30:11', lvl: 'WARN', butler: 'chronicler', msg: 'notion.fetch 429 rate_limit retry_after=2' },
    ],
    pr: {
      id: '#1280', state: 'merged', branch: 'qa/chronicler-notion-throttle',
      title: 'chronicler: token-bucket throttle for notion.fetch',
      files: ['butlers/chronicler/sources/notion.py'],
      additions: 38, deletions: 4, ci: 'passing',
      reviewers: ['Tze (you)'], opened: '07:48', merged: '08:11',
      url: 'https://github.com/butlers-local/butlers/pull/1280',
    },
  },
  {
    id: '#213',
    sev: 'high',
    butler: 'calendar',
    headline: 'Google Calendar OAuth refresh failing — invalid_grant',
    detected: '09:14',
    state: 'escalated · needs human',
    age: '5h 18m',
    blurb: 'Refresh token was revoked, likely from a Google security audit. The runtime can\'t fix this — the user has to re-grant. The QA filed a PR adding clearer surfacing in the UI (so the user sees this on /overview, not just on /settings) and tightened the error path so the calendar process doesn\'t spin.',
    hypothesis: 'Revoked refresh token; out-of-band cause.',
    evidence: [
      { ts: '09:14:21', lvl: 'ERROR', butler: 'calendar', msg: 'oauth.refresh google.calendar invalid_grant' },
      { ts: '09:14:00', lvl: 'WARN',  butler: 'calendar', msg: 'sync.pause google.calendar token_rotation' },
    ],
    pr: {
      id: '#1279', state: 'open', branch: 'qa/calendar-reauth-surface',
      title: 'calendar: surface reauth on /overview attention; bound retry loop',
      files: ['butlers/calendar/oauth.py', 'frontend/src/overview/Attention.tsx'],
      additions: 52, deletions: 11, ci: 'passing',
      reviewers: ['Tze (you)'], opened: '09:38',
      url: 'https://github.com/butlers-local/butlers/pull/1279',
    },
  },
];

// Live tail (WARN+ only — INFO/DEBUG hidden by default per the spec)
const QA_TAIL = [
  { ts: '14:32:01', lvl: 'WARN',  butler: 'qa',         msg: 'patrol.tick · case #218 still open · 18m' },
  { ts: '14:30:11', lvl: 'ERROR', butler: 'chronicler', msg: 'spotify.ingest 401 scope_mismatch /me/player/recently-played' },
  { ts: '14:28:02', lvl: 'ERROR', butler: 'chronicler', msg: 'spotify.ingest 401 scope_mismatch /me/player/recently-played' },
  { ts: '14:14:11', lvl: 'WARN',  butler: 'qa',         msg: 'patrol.flag chronicler.ingest.spotify failure_streak=4' },
  { ts: '14:14:08', lvl: 'WARN',  butler: 'qa',         msg: 'investigation.open #218 chronicler' },
  { ts: '13:47:19', lvl: 'WARN',  butler: 'memory',     msg: 'consolidate.morning 2.14s (+0.31 vs 7d avg)' },
  { ts: '13:38:02', lvl: 'WARN',  butler: 'qa',         msg: 'pr.merged #1283 memory/cache-bound' },
  { ts: '13:02:11', lvl: 'WARN',  butler: 'qa',         msg: 'pr.draft #1283 memory/cache-bound' },
  { ts: '11:30:11', lvl: 'WARN',  butler: 'memory',     msg: 'cache.evict short_tier=184 capacity=128' },
  { ts: '11:01:48', lvl: 'WARN',  butler: 'qa',         msg: 'pr.merged #1282 health/dedup-canonical' },
  { ts: '10:28:14', lvl: 'WARN',  butler: 'health',     msg: 'ingest.dedup conflict glucose@14:14 sources=[libre3, healthkit:libre3]' },
  { ts: '09:38:02', lvl: 'WARN',  butler: 'qa',         msg: 'pr.draft #1279 calendar/reauth-surface' },
  { ts: '09:14:21', lvl: 'ERROR', butler: 'calendar',   msg: 'oauth.refresh google.calendar invalid_grant' },
  { ts: '09:14:00', lvl: 'WARN',  butler: 'calendar',   msg: 'sync.pause google.calendar token_rotation' },
  { ts: '08:42:11', lvl: 'WARN',  butler: 'qa',         msg: 'pr.merged #1281 household/tj-backoff' },
  { ts: '08:11:02', lvl: 'WARN',  butler: 'qa',         msg: 'pr.merged #1280 chronicler/notion-throttle' },
  { ts: '07:30:11', lvl: 'WARN',  butler: 'chronicler', msg: 'notion.fetch 429 rate_limit retry_after=2' },
];

// Headline KPIs — "usefulness" surface
const QA_KPIS = {
  prsLanded:    { value: 4,  range: '24h', sub: '17 this week', delta: '+2 vs prior 24h' },
  mttr:         { value: 38, unit: 'm',    range: '24h', sub: '−12m vs 7d',  delta: 'falling' },
  selfResolved: { value: 86, unit: '%',    range: '7d',  sub: '14% escalated to you', delta: '+4pp vs prior week' },
  hoursSaved:   { value: 6.4, unit: 'h',   range: '7d',  sub: 'modeled · 22m / case avg', delta: '+0.8h' },
};

// Patrol cadence — anomalies-per-patrol bucketed hourly across 24h
const QA_PATROL_24H = [1,1,1,1,1,1,2,2,2,3,2,2,3,4,2,2,2,2,2,2,2,1,1,1];

// PR throughput — 7d, daily counts of merged
const QA_PR_7D = [3, 2, 4, 1, 5, 2, 4]; // mon..sun

// Butler-level coverage — how many cases / merged PRs each butler has had attended to (7d)
const QA_BY_BUTLER_7D = [
  { butler: 'chronicler',   cases: 6, merged: 5 },
  { butler: 'memory',       cases: 4, merged: 4 },
  { butler: 'health',       cases: 3, merged: 3 },
  { butler: 'household',    cases: 2, merged: 2 },
  { butler: 'calendar',     cases: 1, merged: 0 },
  { butler: 'relationship', cases: 1, merged: 1 },
  { butler: 'education',    cases: 0, merged: 0 },
];

window.QA_NOW = QA_NOW;
window.QA_CASES = QA_CASES;
window.QA_TAIL = QA_TAIL;
window.QA_KPIS = QA_KPIS;
window.QA_PATROL_24H = QA_PATROL_24H;
window.QA_PR_7D = QA_PR_7D;
window.QA_BY_BUTLER_7D = QA_BY_BUTLER_7D;
