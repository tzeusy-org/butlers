// Mock ingestion data — shared across all four direction prototypes.
//
// An EVENT is one external item the system received (one email, one HA sensor
// update, one Spotify play, one Telegram message). It may have triggered zero,
// one, or several butler SESSIONS. We carry enough detail to render flame
// graphs at multiple zoom levels.

// ─── Connectors (channels) ───────────────────────────────────────────────
window.CONNECTORS = [
  { id: 'home_assistant', label: 'Home Assistant', kind: 'webhook',  glyph: 'H' },
  { id: 'email',          label: 'Email',          kind: 'imap',     glyph: 'E' },
  { id: 'telegram',       label: 'Telegram',       kind: 'long-poll',glyph: 'T' },
  { id: 'whatsapp',       label: 'WhatsApp',       kind: 'webhook',  glyph: 'W' },
  { id: 'spotify',        label: 'Spotify',        kind: 'poll',     glyph: 'S' },
  { id: 'calendar',       label: 'Google Calendar',kind: 'webhook',  glyph: 'K' },
  { id: 'notion',         label: 'Notion',         kind: 'poll',     glyph: 'N' },
];

// ─── Butler hue map (extends the 8 categories) ───────────────────────────
window.BUTLER_HUE = {
  switchboard:  'oklch(0.795 0.155 84)',   // amber-yellow (cat-3-ish)
  lifestyle:    'oklch(0.745 0.115 215)',  // cool cyan
  chronicler:   'oklch(0.790 0.200 58)',   // warm orange (cat-8)
  relationship: 'oklch(0.680 0.195 259)',  // blue (cat-1)
  memory:       'oklch(0.640 0.220 292)',  // violet (cat-2)
  household:    'oklch(0.695 0.255 11)',   // crimson (cat-5)
  education:    'oklch(0.640 0.218 278)',  // indigo (cat-6)
  health:       'oklch(0.758 0.135 184)',  // teal (cat-4)
  qa:           'oklch(0.770 0.168 209)',  // sky (cat-7)
  calendar:     'oklch(0.810 0.185 84)',
};

function bh(name) { return window.BUTLER_HUE[name] || 'oklch(0.60 0 0)'; }
window.bh = bh;

// ─── Events ──────────────────────────────────────────────────────────────
// t = HH:MM:SS  (today). durMs = end-to-end pipeline duration (max butler end).
// butlers[*] is one session: start offset from event accept, duration, tokens, model.

window.EVENTS = [
  // — top of stream: a quiet run of HA sensors —
  { id: '019e2ed8-197e-75f5-a452-90b50c28d887', t: '11:32:54', channel: 'home_assistant',
    kind: 'state.changed', sender: 'binary_sensor.presence_sensor_fp2_8b1c',
    senderShort: 'fp2 · presence', summary: 'state: off → on',
    status: 'ingested', tier: 'default',
    tokensIn: 0, tokensOut: 0, cost: 0, durationMs: 8, butlers: [],
    bytes: 412, hopFiltered: 'preserved · presence delta',
  },
  { id: '019e2ed5-fa9b-7771-ae12-2e98aa11c001', t: '11:30:11', channel: 'spotify',
    kind: 'play.recent', sender: 'recently-played',
    senderShort: 'Spotify · Tze', summary: 'Mac Miller — Self Care',
    status: 'ingested', tier: 'default',
    tokensIn: 1842, tokensOut: 88, cost: 0.00021, durationMs: 1840,
    butlers: [
      { name: 'chronicler', session: 'c2b8118a-...-4d12', model: 'gpt-5.4-nano',
        startedAt: '11:30:11', startOffsetMs: 0, durationMs: 1840,
        tokensIn: 1842, tokensOut: 88, status: 'ok',
        steps: [
          { name: 'normalize',  durMs: 120,  status: 'ok' },
          { name: 'classify',   durMs: 1480, status: 'ok' },
          { name: 'persist',    durMs: 240,  status: 'ok' },
        ]},
    ]},
  { id: '019e2ed2-a01a-7c44-9911-12abc004ff21', t: '11:26:59', channel: 'home_assistant',
    kind: 'state.changed', sender: 'binary_sensor.lc_motion_alarm',
    senderShort: 'lc · motion', summary: 'state: cleared',
    status: 'ingested', tier: 'default',
    tokensIn: 0, tokensOut: 0, cost: 0, durationMs: 6, butlers: [],
    bytes: 387, hopFiltered: 'preserved · motion delta',
  },
  { id: '019e2ed2-991f-7c11-aa18-b3a91220e002', t: '11:26:32', channel: 'home_assistant',
    kind: 'state.changed', sender: 'binary_sensor.presence_sensor_fp2_8b1c',
    senderShort: 'fp2 · presence', summary: 'state: on → off',
    status: 'ingested', tier: 'default',
    tokensIn: 0, tokensOut: 0, cost: 0, durationMs: 9, butlers: [],
    bytes: 410,
  },

  // — telegram from a contact, triggers relationship + memory —
  { id: '019e2ecf-0a01-77f1-b819-a7e810a4f099', t: '11:18:02', channel: 'telegram',
    kind: 'message.inbound', sender: 'Wei (telegram:@weiminator)',
    senderShort: 'Wei · @weiminator', summary: '"hey are we still on for dinner sunday"',
    status: 'ingested', tier: 'priority',
    tokensIn: 6814, tokensOut: 412, cost: 0.0021, durationMs: 4180,
    butlers: [
      { name: 'switchboard', session: 'a07aff3d-...-1a91', model: 'gpt-5.4-mini',
        startedAt: '11:18:02', startOffsetMs: 0, durationMs: 1180,
        tokensIn: 1842, tokensOut: 88, status: 'ok',
        steps: [{ name: 'classify', durMs: 1060, status: 'ok' }, { name: 'route', durMs: 120, status: 'ok' }] },
      { name: 'relationship', session: '8db4c0a1-...-770e', model: 'gpt-5.4-mini',
        startedAt: '11:18:03', startOffsetMs: 1180, durationMs: 2820,
        tokensIn: 3984, tokensOut: 312, status: 'ok',
        steps: [
          { name: 'load.contact',     durMs: 220, status: 'ok' },
          { name: 'draft.reply',      durMs: 2380, status: 'ok' },
          { name: 'persist.outbox',   durMs: 220, status: 'ok' },
        ] },
      { name: 'memory', session: '4c2a9921-...-bb12', model: 'gpt-5.4-nano',
        startedAt: '11:18:06', startOffsetMs: 4000, durationMs: 180,
        tokensIn: 988, tokensOut: 12, status: 'ok',
        steps: [{ name: 'log.fact', durMs: 180, status: 'ok' }] },
    ]},

  // — whatsapp from group, filtered (high volume) —
  { id: '019e2ecc-220a-79f1-c844-8e1212450231', t: '11:14:48', channel: 'whatsapp',
    kind: 'group.message', sender: 'Climbing crew',
    senderShort: 'WA · Climbing crew', summary: '"anyone free thursday?"',
    status: 'filtered', tier: 'default',
    tokensIn: 0, tokensOut: 0, cost: 0, durationMs: 4, butlers: [],
    hopFiltered: 'rule · groups.lowsignal',
  },

  // — home assistant air monitor —
  { id: '019e2eaa-9111-7022-ab12-9991aabc1100', t: '10:43:35', channel: 'home_assistant',
    kind: 'sensor.update', sender: 'sensor.bedroom_air_monitor_voc',
    senderShort: 'bedroom · voc', summary: 'voc: 142 → 168 ppb',
    status: 'ingested', tier: 'default',
    tokensIn: 0, tokensOut: 0, cost: 0, durationMs: 11, butlers: [],
  },

  // — the big email — //
  { id: '019e2e8c-7f12-71fa-9f1d-2244aabb55cc', t: '10:10:47', channel: 'email',
    kind: 'inbound', sender: 'Chelsea with IFTTT <chelsea.c@ifttt.com>',
    senderShort: 'Chelsea / IFTTT',
    summary: 'IFTTT digest · 18 new applets you might like',
    status: 'ingested', tier: 'default',
    tokensIn: 133246, tokensOut: 1411, cost: 0.0759, durationMs: 33400,
    butlers: [
      { name: 'switchboard', session: 'a07aff3d-...-31bb', model: 'gpt-5.4-mini',
        startedAt: '10:10:48', startOffsetMs: 0, durationMs: 24200,
        tokensIn: 92964, tokensOut: 1364, status: 'ok',
        steps: [
          { name: 'mime.parse',       durMs: 380,  status: 'ok' },
          { name: 'classify',         durMs: 1240, status: 'ok' },
          { name: 'extract.applets',  durMs: 20400, status: 'ok' },
          { name: 'route',            durMs: 2180, status: 'ok' },
        ]},
      { name: 'lifestyle', session: '90aaf618-...-cdef', model: 'opencode-go/deepseek-v4-pro',
        startedAt: '10:11:09', startOffsetMs: 21000, durationMs: 12400,
        tokensIn: 40282, tokensOut: 47, status: 'ok',
        steps: [
          { name: 'parse',                 durMs: 800,   status: 'ok' },
          { name: 'extract.recommendations',durMs: 11600, status: 'ok' },
        ]},
    ]},

  // — telegram from another contact —
  { id: '019e2e80-aa19-7022-bb01-771810abc002', t: '09:54:12', channel: 'telegram',
    kind: 'message.inbound', sender: 'Mom (telegram:@meiying.parent)',
    senderShort: 'Mom · @meiying', summary: '"call me when you have a moment"',
    status: 'ingested', tier: 'priority',
    tokensIn: 4220, tokensOut: 188, cost: 0.0014, durationMs: 2100,
    butlers: [
      { name: 'switchboard', session: 'a07ff128-...-e220', model: 'gpt-5.4-mini',
        startedAt: '09:54:12', startOffsetMs: 0, durationMs: 980,
        tokensIn: 1240, tokensOut: 64, status: 'ok',
        steps: [{ name: 'classify', durMs: 880, status: 'ok' }, { name: 'route', durMs: 100, status: 'ok' }] },
      { name: 'relationship', session: '8a47c00d-...-9912', model: 'gpt-5.4-mini',
        startedAt: '09:54:13', startOffsetMs: 980, durationMs: 1120,
        tokensIn: 2980, tokensOut: 124, status: 'ok',
        steps: [
          { name: 'load.contact',  durMs: 180, status: 'ok' },
          { name: 'notify.priority', durMs: 720, status: 'ok' },
          { name: 'persist',       durMs: 220, status: 'ok' },
        ] },
    ]},

  // — email failure mid-pipeline (replay pending) —
  { id: '019e2e6e-1f11-7741-aa12-bbcc12300441', t: '09:31:07', channel: 'email',
    kind: 'inbound', sender: 'British Gas <noreply@britishgas.co.uk>',
    senderShort: 'British Gas', summary: 'Statement available · April 2026',
    status: 'replay_pending', tier: 'default',
    tokensIn: 6422, tokensOut: 0, cost: 0.0014, durationMs: 8400,
    butlers: [
      { name: 'switchboard', session: 'a07a01ff-...-44a1', model: 'gpt-5.4-mini',
        startedAt: '09:31:07', startOffsetMs: 0, durationMs: 1180,
        tokensIn: 1820, tokensOut: 88, status: 'ok',
        steps: [{ name: 'classify', durMs: 1080, status: 'ok' }, { name: 'route', durMs: 100, status: 'ok' }] },
      { name: 'household', session: '7e0caa11-...-fe19', model: 'gpt-5.4-mini',
        startedAt: '09:31:08', startOffsetMs: 1180, durationMs: 7220,
        tokensIn: 4602, tokensOut: 0, status: 'error',
        error: 'pdf.parse failed: encrypted attachment',
        steps: [
          { name: 'fetch.attachment', durMs: 1820, status: 'ok' },
          { name: 'pdf.parse',        durMs: 5400, status: 'error' },
        ]},
    ]},

  // — spotify pause —
  { id: '019e2e40-aa22-7011-9912-882211400023', t: '08:47:09', channel: 'spotify',
    kind: 'play.recent', sender: 'recently-played',
    senderShort: 'Spotify · Tze', summary: 'Jamie xx — Gosh',
    status: 'ingested', tier: 'default',
    tokensIn: 1820, tokensOut: 84, cost: 0.00021, durationMs: 1620,
    butlers: [
      { name: 'chronicler', session: 'c298810a-...-2240', model: 'gpt-5.4-nano',
        startedAt: '08:47:09', startOffsetMs: 0, durationMs: 1620,
        tokensIn: 1820, tokensOut: 84, status: 'ok',
        steps: [
          { name: 'normalize', durMs: 110, status: 'ok' },
          { name: 'classify',  durMs: 1320, status: 'ok' },
          { name: 'persist',   durMs: 190, status: 'ok' },
        ]},
    ]},

  // — calendar event invite —
  { id: '019e2e22-771a-7144-aa19-21abc1cf0099', t: '08:14:50', channel: 'calendar',
    kind: 'event.created', sender: 'maya@hello.io',
    senderShort: 'Calendar · Maya', summary: 'Sunday dinner @ 19:30 — Camden',
    status: 'ingested', tier: 'priority',
    tokensIn: 3120, tokensOut: 220, cost: 0.0018, durationMs: 3200,
    butlers: [
      { name: 'calendar', session: '7c180a02-...-9001', model: 'gpt-5.4-mini',
        startedAt: '08:14:50', startOffsetMs: 0, durationMs: 1100,
        tokensIn: 1820, tokensOut: 60, status: 'ok',
        steps: [{ name: 'parse.ics', durMs: 220, status: 'ok' }, { name: 'reconcile', durMs: 880, status: 'ok' }] },
      { name: 'relationship', session: '8c44a019-...-2010', model: 'gpt-5.4-mini',
        startedAt: '08:14:51', startOffsetMs: 1100, durationMs: 2100,
        tokensIn: 1300, tokensOut: 160, status: 'ok',
        steps: [
          { name: 'load.contact', durMs: 200, status: 'ok' },
          { name: 'log.touchpoint', durMs: 1700, status: 'ok' },
          { name: 'persist',      durMs: 200, status: 'ok' },
        ] },
    ]},

  // — notion page change —
  { id: '019e2e0b-aa10-72ff-c218-bb91200ee012', t: '07:42:18', channel: 'notion',
    kind: 'page.updated', sender: 'Workspace · Daily Log',
    senderShort: 'Notion · Daily Log', summary: 'page edited · 4 blocks changed',
    status: 'ingested', tier: 'default',
    tokensIn: 2840, tokensOut: 92, cost: 0.0009, durationMs: 2210,
    butlers: [
      { name: 'chronicler', session: 'c2f0a201-...-c411', model: 'gpt-5.4-nano',
        startedAt: '07:42:18', startOffsetMs: 0, durationMs: 2210,
        tokensIn: 2840, tokensOut: 92, status: 'ok',
        steps: [
          { name: 'diff',     durMs: 410, status: 'ok' },
          { name: 'classify', durMs: 1620, status: 'ok' },
          { name: 'persist',  durMs: 180, status: 'ok' },
        ]},
    ]},
];

// ─── Per-step token + cost distribution ─────────────────────────────────
// The mock only records duration and status per step. To support a
// drawer-level cost breakdown per step, we distribute each butler session's
// tokens proportionally to step.durMs and compute a cost from a small model
// rate table. Last step picks up the rounding remainder so per-session
// totals stay exact.
const MODEL_RATES = {
  // $ per token — order-of-magnitude figures (in / out)
  'gpt-5.4-nano':              { in: 0.00000005, out: 0.00000040 },
  'gpt-5.4-mini':              { in: 0.00000020, out: 0.00000080 },
  'gpt-5.4':                   { in: 0.00000300, out: 0.00001500 },
  'opencode-go/deepseek-v4-pro':{ in: 0.00000040, out: 0.00000160 },
};
function modelRate(name) { return MODEL_RATES[name] || MODEL_RATES['gpt-5.4-mini']; }

for (const e of window.EVENTS) {
  for (const b of e.butlers) {
    const steps = b.steps || [];
    const totalDur = steps.reduce((s, st) => s + st.durMs, 0) || b.durationMs || 1;
    let allocIn = 0, allocOut = 0;
    const r = modelRate(b.model);
    for (let i = 0; i < steps.length; i++) {
      const st = steps[i];
      const ratio = st.durMs / totalDur;
      if (i === steps.length - 1) {
        st.tokensIn  = (b.tokensIn  || 0) - allocIn;
        st.tokensOut = (b.tokensOut || 0) - allocOut;
      } else {
        st.tokensIn  = Math.round((b.tokensIn  || 0) * ratio);
        st.tokensOut = Math.round((b.tokensOut || 0) * ratio);
        allocIn  += st.tokensIn;
        allocOut += st.tokensOut;
      }
      st.cost = st.tokensIn * r.in + st.tokensOut * r.out;
    }
    // Stash the model rate so the drawer can show it.
    b.rate = r;
    b.cost = steps.reduce((s, st) => s + st.cost, 0);
  }
}

// Rollup helpers
window.totals = (events) => {
  const accepted = events.filter((e) => e.status !== 'filtered');
  const failed   = events.filter((e) => e.status === 'replay_pending' || e.status === 'error' || e.status === 'replay_failed');
  const filtered = events.filter((e) => e.status === 'filtered');
  return {
    count: events.length,
    accepted: accepted.length,
    filtered: filtered.length,
    failed: failed.length,
    tokensIn:  events.reduce((s, e) => s + (e.tokensIn  || 0), 0),
    tokensOut: events.reduce((s, e) => s + (e.tokensOut || 0), 0),
    cost:      events.reduce((s, e) => s + (e.cost      || 0), 0),
    sessions:  events.reduce((s, e) => s + (e.butlers   || []).length, 0),
  };
};

// Quick filter for connector summary
window.byConnector = (events) => {
  const map = {};
  for (const e of events) {
    if (!map[e.channel]) map[e.channel] = { id: e.channel, events: 0, sessions: 0, cost: 0, tokensIn: 0, tokensOut: 0, last: e.t, errors: 0 };
    const r = map[e.channel];
    r.events += 1;
    r.sessions += (e.butlers || []).length;
    r.cost += e.cost || 0;
    r.tokensIn += e.tokensIn || 0;
    r.tokensOut += e.tokensOut || 0;
    if (e.status === 'replay_pending' || e.status === 'error') r.errors += 1;
  }
  return Object.values(map);
};
