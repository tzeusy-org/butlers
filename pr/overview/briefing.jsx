// briefing.jsx — hybrid briefing generator (Approach C)
//
// Headline: deterministic template, picked by system state class.
// Elaboration: LLM-written from structured state, with deterministic fallback.
//
// In production: the LLM call happens on the Python backend (FastAPI)
// at GET /api/dashboard/briefing — keyed by user, cached for ~5min,
// falls back to deterministic on error. The frontend hook (useBriefing)
// just consumes the response. See backend stub below.

// ---------------------------------------------------------------------------
// 1. State classifier — deterministic
// ---------------------------------------------------------------------------

function classifyState(d) {
  const high   = d.attention.filter((a) => a.severity === 'high').length;
  const medium = d.attention.filter((a) => a.severity === 'medium').length;
  const total  = d.attention.length;
  const degraded = d.butlers.filter((b) => b.status === 'degraded' || b.status === 'error').length;
  const hour = d.now.getHours();

  let timeOfDay;
  if (hour < 5)  timeOfDay = 'late night';
  else if (hour < 12) timeOfDay = 'morning';
  else if (hour < 17) timeOfDay = 'afternoon';
  else if (hour < 21) timeOfDay = 'evening';
  else                timeOfDay = 'night';

  let urgency;
  if (high > 0)              urgency = 'urgent';
  else if (total >= 3)       urgency = 'busy';
  else if (total >= 1)       urgency = 'mild';
  else if (degraded > 0)     urgency = 'degraded-quiet';
  else                       urgency = 'quiet';

  return { timeOfDay, urgency, high, medium, total, degraded };
}

// ---------------------------------------------------------------------------
// 2. Headline template — picked by class, not generated
// ---------------------------------------------------------------------------

function headlineFor(state) {
  const greet = `Good ${state.timeOfDay}.`;
  const body = {
    urgent:          state.high > 1
      ? `${state.high} things need you now.`
      : `One thing needs you now.`,
    busy:            `Things are busy — ${state.total} items waiting.`,
    mild:            state.total > 1
      ? `Things are quiet, with ${state.total} exceptions.`
      : `Things are quiet, with one exception.`,
    'degraded-quiet':`Quiet, but ${state.degraded} butler${state.degraded > 1 ? 's are' : ' is'} degraded.`,
    quiet:           `Everything is in hand.`,
  }[state.urgency];
  return { greet, body };
}

// ---------------------------------------------------------------------------
// 3. LLM elaboration — calls window.claude.complete with structured state.
//    Falls back to a deterministic templated paragraph on error.
// ---------------------------------------------------------------------------

async function elaborateLLM(d, state) {
  const facts = {
    time: d.now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }),
    attention: d.attention.map((a) => ({
      kind: a.kind, butler: a.butler, title: a.title,
      detail: a.detail, age: a.age, severity: a.severity,
    })),
    butlers: d.butlers.map((b) => ({
      name: b.label, status: b.status, activity: b.activity, sessions: b.sessions24h,
    })),
    sessionsToday: d.kpis.sessionsToday.value,
    momentsLogged: d.kpis.momentsLogged.value,
    costToday: d.kpis.costToday.value,
  };

  const prompt = `You are writing a single short paragraph for a personal AI dashboard.

CONSTRAINTS:
- 1-3 sentences, max 50 words total.
- Past tense for events, present for state. No future tense.
- No exclamation marks. No emoji. No first person ("I"). Avoid "your".
- No hedging adverbs (currently, presently, just, simply).
- Mention the most important attention item by name and time, if any.
- If everything is quiet, write a single calm sentence noting that.
- Voice: a butler announcing, not a chatbot reporting.

STATE:
${JSON.stringify(facts, null, 2)}

Write only the paragraph. No quotes, no preamble.`;

  try {
    const text = await window.claude.complete(prompt);
    return text.trim().replace(/^["']|["']$/g, '');
  } catch (e) {
    return elaborateFallback(d, state);
  }
}

// ---------------------------------------------------------------------------
// 4. Deterministic fallback elaboration — used on LLM failure.
// ---------------------------------------------------------------------------

function elaborateFallback(d, state) {
  if (state.urgency === 'quiet') {
    return `${d.kpis.sessionsToday.value} sessions today, $${d.kpis.costToday.value.toFixed(2)} spent. Everything else, the butlers are handling.`;
  }
  const lead = d.attention[0];
  const second = d.attention[1];
  const parts = [];
  if (lead.kind === 'reauth') {
    parts.push(`${lead.butler.charAt(0).toUpperCase() + lead.butler.slice(1)} is paused — ${lead.detail.toLowerCase()}.`);
  } else {
    parts.push(`${lead.title} — waiting ${lead.age}.`);
  }
  if (second) {
    parts.push(`${second.title} since ${second.age} ago.`);
  }
  parts.push(`Everything else, the butlers are handling.`);
  return parts.join(' ');
}

// ---------------------------------------------------------------------------
// 5. Hook — useBriefing.
//    In production this would call /api/dashboard/briefing via TanStack Query;
//    here it composes the parts client-side and exposes a refresh.
// ---------------------------------------------------------------------------

function useBriefing(d, opts = {}) {
  const [state, setState] = React.useState(() => classifyState(d));
  const [headline, setHeadline] = React.useState(() => headlineFor(state));
  const [elaboration, setElaboration] = React.useState(() => elaborateFallback(d, state));
  const [status, setStatus] = React.useState('fallback'); // 'loading' | 'llm' | 'fallback'

  const refresh = React.useCallback(async () => {
    const s = classifyState(d);
    setState(s);
    setHeadline(headlineFor(s));
    if (opts.useLLM === false) {
      setElaboration(elaborateFallback(d, s));
      setStatus('fallback');
      return;
    }
    setStatus('loading');
    const text = await elaborateLLM(d, s);
    setElaboration(text);
    // crude: if it's identical to fallback, mark as such
    setStatus(text === elaborateFallback(d, s) ? 'fallback' : 'llm');
  }, [d, opts.useLLM]);

  React.useEffect(() => { refresh(); /* eslint-disable-next-line */ }, [d]);

  return { headline, elaboration, status, refresh };
}

window.useBriefing = useBriefing;
window.classifyState = classifyState;
window.headlineFor = headlineFor;
window.elaborateFallback = elaborateFallback;
