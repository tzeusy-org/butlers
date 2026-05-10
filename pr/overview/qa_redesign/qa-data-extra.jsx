// Extended QA case data — adds claim-anchored blurbs, reasoning traces,
// counter-evidence, inline diffs, and why-this-fix lines.
// Loaded after qa-data.jsx; mutates QA_CASES in place.

(function () {
  const byId = Object.fromEntries(window.QA_CASES.map((c) => [c.id, c]));

  // Helper: split a blurb into segments. Each segment is either plain text or
  // an anchored span carrying a claim id; renderer turns the latter into a
  // numbered superscript that highlights matching evidence on hover.
  // Format: ['plain text ', {claim: 'c1', text: 'anchored phrase'}, ' more text']

  // ─── #218 · Spotify scope rename ────────────────────────────────────────
  Object.assign(byId['#218'], {
    blurbSegments: [
      { claim: 'c1', text: 'Spotify rotated the OAuth scope name for /me/player/recently-played at 09:00 UTC yesterday.' },
      ' ',
      { claim: 'c2', text: "Chronicler's ingest call now returns 401." },
      ' The runtime fix is mechanical — update the requested scope string and re-prompt the user — but the actual reauth needs a human, so the PR adds the new scope plus a graceful error path that points at /settings/integrations.',
    ],
    claims: {
      c1: { evidenceIds: ['e1'], note: 'Confirmed via Spotify changelog 2026-05-05.' },
      c2: { evidenceIds: ['e1', 'e2', 'e3'], note: 'Failure streak of 4 across 18m.' },
    },
    evidence: [
      { id: 'e1', ts: '14:30:11', lvl: 'ERROR', butler: 'chronicler', msg: 'spotify.ingest 401 scope_mismatch /me/player/recently-played' },
      { id: 'e2', ts: '14:28:02', lvl: 'ERROR', butler: 'chronicler', msg: 'spotify.ingest 401 scope_mismatch /me/player/recently-played' },
      { id: 'e3', ts: '14:14:11', lvl: 'WARN',  butler: 'qa',         msg: 'patrol.flag chronicler.ingest.spotify failure_streak=4' },
    ],
    reasoning: [
      { ts: '14:14', step: 'flagged',       text: 'patrol cycle 217 · failure_streak crossed 4',                                detail: 'chronicler.ingest.spotify · severity heuristic · low (recoverable upstream)' },
      { ts: '14:15', step: 'sampled',       text: 'pulled last 50 chronicler logs',                                              detail: 'grep level=ERROR · 14 matches · all scope_mismatch' },
      { ts: '14:15', step: 'sampled',       text: 'read butlers/chronicler/config.toml',                                          detail: 'extracted SPOTIFY_SCOPES list · 4 entries' },
      { ts: '14:16', step: 'cross-checked', text: 'fetched Spotify dev portal scope reference',                                   detail: 'cache hit · changelog 2026-05-05 confirms rename' },
      { ts: '14:16', step: 'considered',    text: 'hypothesis · token expiry',                                                    detail: 'rejected — refresh succeeded 13:58' },
      { ts: '14:16', step: 'considered',    text: 'hypothesis · upstream outage',                                                 detail: 'rejected — /me/player and /me/top-artists returning 200' },
      { ts: '14:17', step: 'concluded',     text: 'scope name drifted',                                                            detail: 'confidence 0.91 · mechanical rename + reauth prompt' },
      { ts: '14:18', step: 'drafted',       text: 'PR #1284 · qa/spotify-scope-rename',                                            detail: '+18 / −6 · 2 files touched' },
      { ts: '14:18', step: 'wait',          text: 'CI · 4 checks pending',                                                         detail: 'lint · types · integration · butler-smoke' },
      { ts: '14:32', step: 'tick',          text: 'patrol cycle 218 · case still open',                                            detail: 'awaiting reauth — surfaced on /overview attention' },
    ],
    counterEvidence: [
      { hypothesis: 'Token expiry', verdict: 'rejected', reason: 'refresh call succeeded at 13:58' },
      { hypothesis: 'Spotify-wide outage', verdict: 'rejected', reason: '/me/player and /me/top-artists returning 200' },
      { hypothesis: 'Network egress block', verdict: 'rejected', reason: 'other ingest endpoints clean' },
    ],
    whyThisFix: 'Renames the scope string in one place, then surfaces the reauth on /settings/integrations so the human action is unblocked.',
    diff: [
      { kind: 'meta', text: 'butlers/chronicler/ingest/spotify.py' },
      { kind: ' ', text: '  SPOTIFY_SCOPES = [' },
      { kind: '-', text: '      "user-read-recently-played",' },
      { kind: '+', text: '      "user-recently-played",  // renamed 2026-05-05' },
      { kind: ' ', text: '      "user-read-currently-playing",' },
      { kind: ' ', text: '  ]' },
      { kind: ' ', text: '' },
      { kind: ' ', text: '  except SpotifyAuthError as e:' },
      { kind: '-', text: '      log.error("spotify.ingest", exc=e)' },
      { kind: '-', text: '      raise' },
      { kind: '+', text: '      if e.code == "scope_mismatch":' },
      { kind: '+', text: '          await reauth_prompt("/settings/integrations")' },
      { kind: '+', text: '      raise' },
    ],
  });

  // ─── #217 · Memory cache bound ──────────────────────────────────────────
  Object.assign(byId['#217'], {
    blurbSegments: [
      { claim: 'c1', text: "Memory's morning consolidation has crept from 1.8s to 2.1s over the last week." },
      ' The cause is ',
      { claim: 'c2', text: 'the short-tier cache exceeding the L1 bound (default 128) — eviction is now happening every run.' },
      ' Not a regression, just growth. Patched the bound to 256 and added a runtime metric so we notice earlier next time.',
    ],
    claims: {
      c1: { evidenceIds: ['e1'], note: '7d trailing average baseline.' },
      c2: { evidenceIds: ['e2'], note: 'short_tier=184 vs capacity=128.' },
    },
    evidence: [
      { id: 'e1', ts: '13:47:19', lvl: 'WARN', butler: 'memory', msg: 'consolidate.morning 2.14s (+0.31 vs 7d avg)' },
      { id: 'e2', ts: '11:30:11', lvl: 'WARN', butler: 'memory', msg: 'cache.evict short_tier=184 capacity=128' },
    ],
    reasoning: [
      { ts: '12:11', step: 'flagged',    text: 'consolidate latency exceeded 2σ above 7d average',                detail: '2.14s vs 1.83s baseline' },
      { ts: '12:50', step: 'sampled',    text: 'pulled cache.evict counts across 7d',                              detail: 'steady climb 38 → 142 evictions / run' },
      { ts: '12:54', step: 'sampled',    text: 'read butlers/memory/cache.py',                                     detail: 'SHORT_TIER_BOUND = 128' },
      { ts: '12:55', step: 'considered', text: 'hypothesis · GC pressure',                                          detail: 'rejected — heap utilization stable at 62%' },
      { ts: '12:57', step: 'considered', text: 'hypothesis · disk I/O contention',                                  detail: 'rejected — iostat clean during runs' },
      { ts: '13:00', step: 'concluded',  text: 'cache bound below working set',                                     detail: 'confidence 0.94 · raise + add eviction metric' },
      { ts: '13:02', step: 'drafted',    text: 'PR #1283 · qa/memory-cache-bound',                                  detail: '+42 / −14 · 3 files touched' },
      { ts: '13:38', step: 'merged',     text: 'CI green · auto-merged',                                            detail: 'next patrol clean · case closed' },
    ],
    counterEvidence: [
      { hypothesis: 'GC pressure', verdict: 'rejected', reason: 'heap utilization stable at 62%' },
      { hypothesis: 'Disk I/O contention', verdict: 'rejected', reason: 'iostat clean during runs' },
    ],
    whyThisFix: 'Raises the bound past the observed 7d high-water mark and adds eviction telemetry so the next drift is caught proactively.',
    diff: [
      { kind: 'meta', text: 'butlers/memory/cache.py' },
      { kind: '-', text: '  SHORT_TIER_BOUND = 128' },
      { kind: '+', text: '  SHORT_TIER_BOUND = 256  // 7d high-water 184; bump w/ headroom' },
      { kind: ' ', text: '' },
      { kind: 'meta', text: 'butlers/memory/metrics.py' },
      { kind: '+', text: '  EVICT_COUNT = Counter("memory_cache_evict_total", ["tier"])' },
    ],
  });

  // ─── #216 · Health dedup ────────────────────────────────────────────────
  Object.assign(byId['#216'], {
    blurbSegments: [
      { claim: 'c1', text: 'When the user has Libre 3 connected and Apple Health is also pulling from Libre via HealthKit, glucose readings get written twice with the same timestamp.' },
      ' ',
      { claim: 'c2', text: 'The dedup ran on (source, ts) but Libre is two different source ids.' },
      ' Fix is one line in the dedup key.',
    ],
    claims: {
      c1: { evidenceIds: ['e1', 'e2'], note: 'Both timestamps appear in conflict logs.' },
      c2: { evidenceIds: ['e1'], note: 'Sources: libre3, healthkit:libre3.' },
    },
    evidence: [
      { id: 'e1', ts: '10:28:14', lvl: 'WARN', butler: 'health', msg: 'ingest.dedup conflict glucose@14:14 sources=[libre3, healthkit:libre3]' },
      { id: 'e2', ts: '10:28:13', lvl: 'WARN', butler: 'health', msg: 'ingest.dedup conflict glucose@14:00 sources=[libre3, healthkit:libre3]' },
    ],
    reasoning: [
      { ts: '10:28', step: 'flagged',       text: 'dedup conflicts on identical timestamps',                detail: 'health.ingest.glucose · 2 conflicts in 1s' },
      { ts: '10:30', step: 'sampled',       text: 'inspected ingest.dedup key',                              detail: 'key = (sample.source, sample.ts)' },
      { ts: '10:31', step: 'cross-checked', text: 'compared source ids vs canonical-source map',             detail: 'libre3 ≠ healthkit:libre3 · both resolve to libre3' },
      { ts: '10:33', step: 'considered',    text: 'hypothesis · clock drift',                                detail: 'rejected — timestamps identical to the second' },
      { ts: '10:38', step: 'concluded',     text: 'one-line key fix · canonicalize source',                  detail: 'confidence 0.97' },
      { ts: '10:42', step: 'drafted',       text: 'PR #1282 · qa/health-dedup-canonical',                    detail: '+7 / −2 · 1 file touched' },
      { ts: '11:01', step: 'merged',        text: 'CI green · auto-merged',                                  detail: 'next patrol · 0 conflicts · case closed' },
    ],
    counterEvidence: [
      { hypothesis: 'Clock drift', verdict: 'rejected', reason: 'timestamps identical to the second' },
    ],
    whyThisFix: 'Resolves both source ids to a canonical key before dedup, so the conflict goes away without losing either source as a fallback.',
    diff: [
      { kind: 'meta', text: 'butlers/health/ingest/dedup.py' },
      { kind: '-', text: '  key = (sample.source, sample.ts)' },
      { kind: '+', text: '  key = (canonicalize(sample.source), sample.ts)' },
    ],
  });

  // ─── #215 · TJ backoff ──────────────────────────────────────────────────
  Object.assign(byId['#215'], {
    blurbSegments: [
      { claim: 'c1', text: "TJ's order endpoint is rate-limiting between 16:00 and 17:00 daily." },
      ' ',
      { claim: 'c2', text: 'Household was failing the order rather than backing off.' },
      ' PR adds an exponential-backoff retry (4 tries, 250ms→2s) and shifts the daily order window earlier when the upstream is hot.',
    ],
    claims: {
      c1: { evidenceIds: ['e1', 'e2'], note: '503s clustered in 16:00 window 7/7 days.' },
      c2: { evidenceIds: ['e1'], note: 'No retry policy in traderjoes.order.' },
    },
    evidence: [
      { id: 'e1', ts: '16:48:02', lvl: 'ERROR', butler: 'household', msg: 'traderjoes.order 503 service_unavailable' },
      { id: 'e2', ts: '16:48:01', lvl: 'ERROR', butler: 'household', msg: 'traderjoes.order 503 service_unavailable' },
    ],
    reasoning: [
      { ts: '16:48', step: 'flagged',       text: '503 streak on traderjoes.order',                          detail: 'household · 5 consecutive failures' },
      { ts: '16:50', step: 'sampled',       text: 'pulled 7d response codes for traderjoes.order',           detail: '503s cluster 16:00–17:00 every weekday' },
      { ts: '16:55', step: 'cross-checked', text: 'inspected retry policy in butlers/household/order',         detail: 'no retry policy · single attempt' },
      { ts: '17:02', step: 'considered',    text: 'hypothesis · auth misconfig',                              detail: 'rejected — 200s outside 16:00 window' },
      { ts: '17:08', step: 'concluded',     text: 'transient peak rate-limit · backoff + shift schedule',     detail: 'confidence 0.89' },
      { ts: '17:11', step: 'drafted',       text: 'PR #1281 · qa/household-tj-backoff',                       detail: '+64 / −18 · 2 files touched' },
      { ts: '17:14', step: 'wait',          text: 'CI green · awaiting your review',                          detail: 'no auto-merge — schedule change touches user-facing window' },
      { ts: '08:42', step: 'merged',        text: 'CI green · merged',                                       detail: 'first run on 14:00 schedule clean' },
    ],
    counterEvidence: [
      { hypothesis: 'Auth misconfig', verdict: 'rejected', reason: '200s outside 16:00 window' },
    ],
    whyThisFix: 'Backoff handles the transient peak; schedule shift moves us out of the contention window for the common case.',
    diff: [
      { kind: 'meta', text: 'butlers/household/order/traderjoes.py' },
      { kind: '+', text: '  @retry(tries=4, backoff=exponential(250, max=2000))' },
      { kind: ' ', text: '  async def submit_order(cart):' },
      { kind: 'meta', text: 'butlers/household/scheduler.py' },
      { kind: '-', text: '  TJ_WINDOW = "16:00"' },
      { kind: '+', text: '  TJ_WINDOW = "14:00"  // pre-peak' },
    ],
  });

  // ─── #214 · Notion throttle ─────────────────────────────────────────────
  Object.assign(byId['#214'], {
    blurbSegments: [
      { claim: 'c1', text: "Notion's 3 req/s rate-limit was hit during the morning timeline assembly when 14 pages were touched in under a second." },
      ' Added token-bucket backoff and a small batching layer. Investigation auto-closed once the next patrol came back clean.',
    ],
    claims: {
      c1: { evidenceIds: ['e1'], note: 'retry_after=2 in 429 response.' },
    },
    evidence: [
      { id: 'e1', ts: '07:30:11', lvl: 'WARN', butler: 'chronicler', msg: 'notion.fetch 429 rate_limit retry_after=2' },
    ],
    reasoning: [
      { ts: '07:30', step: 'flagged',    text: '429 burst from notion.fetch',                                  detail: 'chronicler · 14 pages in <1s · retry_after=2' },
      { ts: '07:34', step: 'sampled',    text: 'inspected morning timeline assembly',                          detail: 'no rate-limiting layer · raw burst' },
      { ts: '07:42', step: 'concluded',  text: 'add token-bucket throttle + small batching layer',             detail: 'confidence 0.95 · trivially mechanical' },
      { ts: '07:48', step: 'drafted',    text: 'PR #1280 · qa/chronicler-notion-throttle',                      detail: '+38 / −4 · 1 file touched' },
      { ts: '08:11', step: 'merged',     text: 'CI green · auto-merged',                                       detail: 'next patrol clean · 0 retries' },
    ],
    counterEvidence: [],
    whyThisFix: 'Token bucket smooths the burst; batching reduces total call count by ~40%.',
    diff: [
      { kind: 'meta', text: 'butlers/chronicler/sources/notion.py' },
      { kind: '+', text: '  bucket = TokenBucket(rate=3, capacity=3)' },
      { kind: '+', text: '  await bucket.acquire()' },
    ],
  });

  // ─── #213 · Calendar reauth surface ─────────────────────────────────────
  Object.assign(byId['#213'], {
    blurbSegments: [
      { claim: 'c1', text: 'Refresh token was revoked, likely from a Google security audit.' },
      ' The runtime can\'t fix this — the user has to re-grant. The QA filed a PR adding ',
      { claim: 'c2', text: 'clearer surfacing in the UI (so the user sees this on /overview, not just on /settings)' },
      ' and tightened the error path so the calendar process doesn\'t spin.',
    ],
    claims: {
      c1: { evidenceIds: ['e1'], note: 'invalid_grant returned by Google.' },
      c2: { evidenceIds: ['e2'], note: 'sync.pause is silent on /overview today.' },
    },
    evidence: [
      { id: 'e1', ts: '09:14:21', lvl: 'ERROR', butler: 'calendar', msg: 'oauth.refresh google.calendar invalid_grant' },
      { id: 'e2', ts: '09:14:00', lvl: 'WARN',  butler: 'calendar', msg: 'sync.pause google.calendar token_rotation' },
    ],
    reasoning: [
      { ts: '09:14', step: 'flagged',       text: 'invalid_grant on google.calendar oauth.refresh',           detail: 'severity heuristic · high (sync paused)' },
      { ts: '09:16', step: 'sampled',       text: 'inspected oauth flow in butlers/calendar/oauth.py',          detail: 'no terminal state · loops on refresh every 60s' },
      { ts: '09:17', step: 'cross-checked', text: 'tested other Google OAuth scopes in workspace',              detail: 'gmail · drive refreshing fine · isolated to calendar' },
      { ts: '09:18', step: 'considered',    text: 'hypothesis · auto-rotate',                                  detail: 'rejected — not possible without user reauth' },
      { ts: '09:20', step: 'considered',    text: 'hypothesis · clock skew',                                   detail: 'rejected — NTP drift < 200ms' },
      { ts: '09:20', step: 'considered',    text: 'hypothesis · client-secret rotation',                       detail: 'rejected — other scopes refreshing fine' },
      { ts: '09:22', step: 'concluded',     text: 'escalate to user · improve UI surfacing in meantime',       detail: 'confidence 1.00 on diagnosis · cannot self-heal' },
      { ts: '09:38', step: 'drafted',       text: 'PR #1279 · qa/calendar-reauth-surface',                      detail: '+52 / −11 · 2 files touched' },
      { ts: '09:42', step: 'wait',          text: 'CI green · awaiting your review',                          detail: 'touches frontend Attention.tsx — no auto-merge' },
      { ts: '14:32', step: 'tick',          text: 'patrol cycle 218 · case still escalated',                   detail: 'sync paused · 5h 18m elapsed' },
    ],
    counterEvidence: [
      { hypothesis: 'Clock skew', verdict: 'rejected', reason: 'NTP drift < 200ms' },
      { hypothesis: 'Client-secret rotation', verdict: 'rejected', reason: 'other Google OAuth scopes refreshing fine' },
    ],
    whyThisFix: 'Cannot self-heal a revoked grant; this PR makes the human action clearer (Overview attention card) and prevents the runtime from spinning on a hopeless retry.',
    diff: [
      { kind: 'meta', text: 'butlers/calendar/oauth.py' },
      { kind: ' ', text: '  except RefreshError as e:' },
      { kind: '-', text: '      await asyncio.sleep(60); raise' },
      { kind: '+', text: '      if e.code == "invalid_grant":' },
      { kind: '+', text: '          await escalate_to_user(reason="reauth_required")' },
      { kind: '+', text: '          return BACKOFF_TERMINAL' },
      { kind: 'meta', text: 'frontend/src/overview/Attention.tsx' },
      { kind: '+', text: '  if (butler.needsReauth) cards.push(<ReauthCard butler={butler} />)' },
    ],
  });
})();
