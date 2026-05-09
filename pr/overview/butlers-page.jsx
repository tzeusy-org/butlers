// /butlers/ — the staff index. Same editorial language as the Overview:
// eyebrow → display headline → serif voice paragraph → KPI strip → roster.
// Right column is a quiet index: spend, next runs, "why this shape".
//
// Hue still appears only on the letter-mark. State color (red/amber) only
// when state demands. Density via rule-separated rows, not cards.

const ROLE = {
  relationship: 'Tracks contacts, drafts replies, watches the warmth between you and the people you care about.',
  health:       'Logs measurements, movement, sleep — the body, quietly accumulating signal.',
  calendar:     'Mirrors your calendar; schedules the rest of the staff around it.',
  qa:           'Patrols every other butler\u2019s output. Investigates anomalies before you see them.',
  memory:       'Consolidates the morning\u2019s observations into mid- and long-term memory.',
  education:    'Reviews, schedules, and prompts. Knows what you\u2019re studying and when.',
  chronicler:   'Reconstructs the day from devices, location, calendar, music. Writes the record.',
  household:    'Orders, schedules, restocks. Asks before spending.',
};

const ACTIVE_VERBS = new Set(['running', 'patrol', 'consolidating', 'ingesting']);
const NUM_WORDS = { 0:'No', 1:'One', 2:'Two', 3:'Three', 4:'Four', 5:'Five', 6:'Six', 7:'Seven', 8:'Eight', 9:'Nine' };
const asWord = (n) => NUM_WORDS[n] || String(n);

function activityColor(b) {
  if (b.status === 'degraded' || b.activity === 'paused') return C.red;
  if (b.activity === 'awaiting approval' || b.status === 'waiting') return C.amber;
  if (ACTIVE_VERBS.has(b.activity)) return C.green;
  return C.dim;
}

function composeHeadline(d) {
  const total = d.butlers.length;
  const paused = d.butlers.filter((b) => b.activity === 'paused' || b.status === 'degraded').length;
  const waiting = d.butlers.filter((b) => b.status === 'waiting' || b.activity === 'awaiting approval').length;
  const active  = d.butlers.filter((b) => ACTIVE_VERBS.has(b.activity)).length;

  if (paused === 0 && waiting === 0) {
    return { greet: `${asWord(total)} butlers,`, body: 'all at posts.' };
  }
  if (paused === 1 && waiting === 0) {
    return { greet: `${asWord(total)} butlers,`, body: 'one paused.' };
  }
  if (paused === 0 && waiting >= 1) {
    return { greet: `${asWord(total)} butlers,`, body: `${asWord(waiting).toLowerCase()} awaiting you.` };
  }
  return { greet: `${asWord(total)} butlers,`, body: `${asWord(paused).toLowerCase()} paused, ${asWord(waiting).toLowerCase()} awaiting.` };
}

function composeVoice(d) {
  const paused = d.butlers.filter((b) => b.activity === 'paused' || b.status === 'degraded');
  const waiting = d.butlers.filter((b) => b.activity === 'awaiting approval' || b.status === 'waiting');
  const active  = d.butlers.filter((b) => ACTIVE_VERBS.has(b.activity));

  const sentences = [];
  if (paused.length) {
    const p = paused[0];
    sentences.push(`${cap(p.label)} has been paused for ${p.lastRun.replace(' ago', '')} \u2014 token rotation failed and the rest of the calendar work is on hold until you re-authorize.`);
  }
  if (active.length) {
    const verb = (a) => ({
      running: 'running',
      patrol: 'on patrol',
      consolidating: 'consolidating',
      ingesting: 'ingesting',
    }[a] || a);
    const phrases = active.slice(0, 3).map((b) => `${cap(b.label)} is ${verb(b.activity)}`);
    const tail = active.length > 3 ? `, and ${active.length - 3} more in motion` : '';
    sentences.push(joinList(phrases) + tail + '.');
  }
  if (waiting.length) {
    sentences.push(`${cap(waiting[0].label)} is waiting on a decision from you.`);
  }
  if (!paused.length && !waiting.length) {
    sentences.push('Nothing requires your attention.');
  }
  return sentences.join(' ');
}

function cap(s) { return s ? s[0].toUpperCase() + s.slice(1) : s; }
function joinList(arr) {
  if (arr.length <= 1) return arr.join('');
  if (arr.length === 2) return `${arr[0]} and ${arr[1]}`;
  return `${arr.slice(0, -1).join(', ')}, and ${arr[arr.length - 1]}`;
}

// 24-cell stripe — compact, no labels. Tone follows live theme.
function MiniStripe({ row, cellW = 5, cellH = 18, gap = 2 }) {
  const isDark = window.__theme !== 'light';
  return (
    <div style={{ display: 'flex', alignItems: 'center' }}>
      {row.map((v, i) => {
        const intensity = Math.min(1, v / 4);
        const empty  = isDark ? 'oklch(1 0 0 / 0.05)' : 'oklch(0 0 0 / 0.05)';
        const filled = isDark
          ? `oklch(0.985 0 0 / ${0.18 + intensity * 0.55})`
          : `oklch(0.18 0 0 / ${0.20 + intensity * 0.55})`;
        return (
          <div key={i} style={{
            width: cellW, height: cellH, marginRight: i < row.length - 1 ? gap : 0,
            background: v === 0 ? empty : filled,
            borderRadius: 1,
          }} />
        );
      })}
    </div>
  );
}

function ButlerRow({ b, stripe, last }) {
  const isActive = ACTIVE_VERBS.has(b.activity);
  const isPaused = b.status === 'degraded' || b.activity === 'paused';
  const isWaiting = b.status === 'waiting' || b.activity === 'awaiting approval';
  const tone = isActive ? 'fill' : 'neutral';
  const isDark = window.__theme !== 'light';
  const ringBg = isDark ? 'oklch(0.145 0 0)' : 'oklch(0.985 0.003 85)';

  const [hover, setHover] = React.useState(false);

  return (
    <a href={`/butlers/${b.name}`}
       onMouseEnter={() => setHover(true)}
       onMouseLeave={() => setHover(false)}
       style={{
         display: 'grid',
         gridTemplateColumns: '40px 1fr auto',
         gap: 24, padding: '22px 0',
         borderBottom: last ? 'none' : `1px solid ${C.borderSoft}`,
         color: C.fg, textDecoration: 'none', alignItems: 'start',
         background: hover ? (isDark ? 'oklch(1 0 0 / 0.025)' : 'oklch(0 0 0 / 0.02)') : 'transparent',
         marginInline: hover ? -16 : 0,
         paddingInline: hover ? 16 : 0,
         transition: 'margin-inline 120ms ease, padding-inline 120ms ease, background 120ms ease',
       }}>
      {/* Letter mark + status pip */}
      <div style={{ position: 'relative', marginTop: 2 }}>
        <ButlerMark name={b.name} size={36} tone={tone} />
        {(isPaused || isWaiting) && (
          <span style={{
            position: 'absolute', top: -2, right: -2,
            width: 10, height: 10, borderRadius: 999,
            background: isPaused ? C.red : C.amber,
            border: `2px solid ${ringBg}`,
          }} />
        )}
      </div>

      {/* Title + serif role + state line */}
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, flexWrap: 'wrap' }}>
          <span style={{
            fontSize: 19, fontWeight: 500, letterSpacing: '-0.015em',
            textTransform: 'capitalize', whiteSpace: 'nowrap',
          }}>{b.label}</span>
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: 10,
            textTransform: 'uppercase', letterSpacing: '0.14em',
            color: activityColor(b),
          }}>{b.activity}</span>
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: 10, color: C.dim,
            letterSpacing: '0.06em',
          }}>· last run {b.lastRun}</span>
        </div>
        <div style={{
          fontFamily: 'var(--font-serif)', fontSize: 14, color: C.mfg,
          marginTop: 8, lineHeight: 1.55, maxWidth: '52ch',
        }}>
          {ROLE[b.name] || ''}
        </div>
      </div>

      {/* Right meta cluster: stripe (above) + numbers + arrow */}
      <div style={{
        display: 'flex', flexDirection: 'column', alignItems: 'flex-end',
        gap: 10, minWidth: 168, marginTop: 4,
      }}>
        <MiniStripe row={stripe} />
        <div style={{
          display: 'flex', alignItems: 'baseline', gap: 16,
          fontFamily: 'var(--font-mono)', fontSize: 11,
        }}>
          <span className="tnum" style={{ color: C.fg, fontWeight: 500 }}>
            {b.sessions24h}
            <span style={{ color: C.dim, marginLeft: 4 }}>sess</span>
          </span>
          <span className="tnum" style={{ color: C.mfg }}>
            ${b.costToday.toFixed(2)}
          </span>
          <span style={{
            color: C.fg, textDecoration: 'underline',
            textUnderlineOffset: 4, textDecorationColor: C.borderStrong,
            fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500,
            opacity: hover ? 1 : 0.65,
          }}>open →</span>
        </div>
      </div>
    </a>
  );
}

// ─── Right column: Spend, Next runs, "why this shape" ──────────────────────

function SpendList({ costs, total }) {
  const max = Math.max(...costs.map((c) => c.cost));
  const isDark = window.__theme !== 'light';
  const trackBg = isDark ? 'oklch(1 0 0 / 0.04)' : 'oklch(0 0 0 / 0.04)';
  return (
    <div>
      {costs.map((c, i) => (
        <div key={c.name} style={{
          display: 'grid', gridTemplateColumns: '20px 1fr 60px 56px',
          gap: 10, padding: '8px 0',
          borderBottom: i < costs.length - 1 ? `1px solid ${C.borderSoft}` : 'none',
          alignItems: 'center',
        }}>
          <ButlerMark name={c.name} size={14} tone="neutral" />
          <span style={{ fontSize: 12, textTransform: 'capitalize', color: C.fg }}>{c.name}</span>
          <div style={{
            height: 6, background: trackBg, borderRadius: 1, overflow: 'hidden',
          }}>
            <div style={{
              width: `${(c.cost / max) * 100}%`, height: '100%',
              background: C.fg, opacity: 0.7,
            }} />
          </div>
          <span className="tnum" style={{
            fontFamily: 'var(--font-mono)', fontSize: 11, color: C.mfg, textAlign: 'right',
          }}>${c.cost.toFixed(2)}</span>
        </div>
      ))}
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
        paddingTop: 12, marginTop: 4, borderTop: `1px solid ${C.border}`,
        fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
        textTransform: 'uppercase', letterSpacing: '0.08em',
      }}>
        <span>total today</span>
        <span className="tnum" style={{ color: C.fg, fontSize: 11, fontWeight: 500 }}>
          ${total.toFixed(2)}
        </span>
      </div>
    </div>
  );
}

function NextRuns({ upcoming }) {
  const runs = upcoming.filter((u) => u.kind === 'butler').slice(0, 5);
  if (!runs.length) {
    return (
      <div style={{
        padding: '16px 0', color: C.dim, fontSize: 13,
        fontFamily: 'var(--font-serif)', fontStyle: 'italic',
      }}>Nothing scheduled.</div>
    );
  }
  return (
    <div>
      {runs.map((u, i) => (
        <div key={i} style={{
          display: 'grid', gridTemplateColumns: '46px 18px 1fr auto',
          gap: 10, padding: '10px 0',
          borderBottom: i < runs.length - 1 ? `1px solid ${C.borderSoft}` : 'none',
          alignItems: 'center',
        }}>
          <span className="tnum" style={{
            fontFamily: 'var(--font-mono)', fontSize: 11, color: C.dim,
          }}>{u.time}</span>
          <ButlerMark name={u.butler} size={14} tone="neutral" />
          <span style={{ fontSize: 13, color: C.fg }}>{u.label}</span>
          <span className="tnum" style={{
            fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
          }}>{u.dur}</span>
        </div>
      ))}
    </div>
  );
}

function ButlersIndex({ data }) {
  const d = data;
  const stripeMap = Object.fromEntries(d.sessionGrid.map((s) => [s.butler, s.row]));
  const headline = composeHeadline(d);
  const voice = composeVoice(d);
  const totalCost = d.costsByButler.reduce((s, c) => s + c.cost, 0);
  const totalSessions = d.butlers.reduce((s, b) => s + b.sessions24h, 0);
  const healthy = d.butlers.filter((b) => b.status === 'ok').length;
  const total = d.butlers.length;
  const awaiting = d.butlers.filter((b) => b.status === 'waiting' || b.activity === 'awaiting approval').length;

  const dateStr = `${d.now.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })} · ${d.now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })}`;

  return (
    <div style={{ background: C.bg, color: C.fg, fontFamily: 'var(--font-sans)' }}>
      <div style={{
        display: 'grid', gridTemplateColumns: '1.4fr 1fr',
        gap: 56, padding: '48px 56px',
        maxWidth: 1280, margin: '0 auto',
      }}>
        {/* LEFT — narrative + roster */}
        <div>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 12,
            fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
            textTransform: 'uppercase', letterSpacing: '0.14em', marginBottom: 16,
          }}>
            <span>Butlers · Roster · {dateStr}</span>
            <span style={{ flex: 1 }} />
            <RosterPill total={total} healthy={healthy} />
          </div>

          <h1 style={{
            fontFamily: 'var(--font-sans)', fontWeight: 500,
            fontSize: 44, lineHeight: 1.08, letterSpacing: '-0.025em',
            margin: 0, marginBottom: 18, maxWidth: '14ch',
          }}>
            <span style={{ color: C.mfg }}>{headline.greet}</span><br />
            {headline.body}
          </h1>

          <div style={{
            fontFamily: 'var(--font-serif)', fontSize: 16, lineHeight: 1.6,
            color: C.mfg, maxWidth: '50ch', marginBottom: 36,
          }}>
            {voice}
          </div>

          {/* KPI strip */}
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)',
            borderTop: `1px solid ${C.border}`,
            borderBottom: `1px solid ${C.border}`,
            marginBottom: 36,
          }}>
            {[
              { label: 'in service', value: `${healthy}/${total}`, sub: 'healthy' },
              { label: 'sessions · 24h', value: totalSessions.toLocaleString(), sub: 'across staff' },
              { label: 'spend · today', value: '$' + totalCost.toFixed(2), sub: '−4% vs avg' },
              { label: 'awaiting you', value: awaiting, sub: awaiting === 1 ? 'one item' : `${awaiting} items` },
            ].map((k, i) => (
              <div key={i} style={{
                padding: '20px 0',
                borderRight: i < 3 ? `1px solid ${C.border}` : 'none',
                paddingLeft: i === 0 ? 0 : 20,
              }}>
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
                  textTransform: 'uppercase', letterSpacing: '0.14em', marginBottom: 6,
                }}>{k.label}</div>
                <div className="tnum" style={{
                  fontSize: 32, fontWeight: 500, letterSpacing: '-0.03em', lineHeight: 1,
                }}>{k.value}</div>
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 10, color: C.dim, marginTop: 6,
                  textTransform: 'lowercase',
                }}>{k.sub}</div>
              </div>
            ))}
          </div>

          {/* Roster */}
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
            textTransform: 'uppercase', letterSpacing: '0.14em',
            paddingBottom: 8, borderBottom: `1px solid ${C.border}`,
            display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
          }}>
            <span>The staff</span>
            <span style={{ color: C.dim, letterSpacing: '0.08em' }}>
              {total} butler{total === 1 ? '' : 's'} · sorted by today’s volume
            </span>
          </div>
          <div>
            {[...d.butlers]
              .sort((a, b) => b.sessions24h - a.sessions24h)
              .map((b, i, arr) => (
                <ButlerRow key={b.name} b={b}
                  stripe={stripeMap[b.name] || Array(24).fill(0)}
                  last={i === arr.length - 1} />
              ))}
          </div>

          {/* Closing serif gloss — calm-when-empty */}
          <div style={{
            marginTop: 48, paddingTop: 20, borderTop: `1px solid ${C.borderSoft}`,
            fontFamily: 'var(--font-serif)', fontStyle: 'italic',
            fontSize: 13, color: C.dim, lineHeight: 1.6, maxWidth: '50ch',
          }}>
            Each butler runs as its own service on its own port. They don’t
            talk to each other directly — the staffer routes between them and
            the QA staffer reads everyone’s logs.
          </div>
        </div>

        {/* RIGHT — quiet index */}
        <div style={{ paddingTop: 8 }}>
          <window.Section title="Spend today">
            <SpendList costs={d.costsByButler} total={totalCost} />
          </window.Section>

          <window.Section title="Next butler runs">
            <NextRuns upcoming={d.upcoming} />
          </window.Section>

          <window.Section title="Composition">
            <CompositionTable butlers={d.butlers} />
          </window.Section>

          <div style={{
            fontFamily: 'var(--font-serif)', fontSize: 14, lineHeight: 1.65,
            color: C.mfg, marginTop: 28,
          }}>
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: 10, color: C.dim,
              textTransform: 'uppercase', letterSpacing: '0.14em', marginBottom: 12,
            }}>Why this shape</div>
            <p style={{ margin: 0, marginBottom: 12 }}>
              Every domain that earns a butler gets one — no more, no fewer.
              A second relationship butler would compete with the first and
              produce drift; a single all-purpose butler would never be small
              enough to debug.
            </p>
            <p style={{ margin: 0, color: C.dim, fontStyle: 'italic' }}>
              The staffers (QA, memory, household orchestration) are different
              — they exist to support the butlers, not to handle a domain.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

function CompositionTable({ butlers }) {
  // Group by activity; show counts in a quiet table.
  const groups = {};
  butlers.forEach((b) => {
    let key = b.activity;
    if (b.status === 'degraded') key = 'paused';
    if (b.status === 'waiting') key = 'awaiting approval';
    groups[key] = (groups[key] || 0) + 1;
  });
  const order = ['running', 'patrol', 'consolidating', 'ingesting', 'idle', 'paused', 'awaiting approval'];
  const entries = order.filter((k) => groups[k]).map((k) => [k, groups[k]]);
  return (
    <div>
      {entries.map(([k, n], i) => {
        const tone = k === 'paused' ? C.red
                   : k === 'awaiting approval' ? C.amber
                   : ACTIVE_VERBS.has(k) ? C.green
                   : C.dim;
        return (
          <div key={k} style={{
            display: 'grid', gridTemplateColumns: '8px 1fr auto',
            gap: 10, padding: '8px 0', alignItems: 'center',
            borderBottom: i < entries.length - 1 ? `1px solid ${C.borderSoft}` : 'none',
          }}>
            <span style={{ width: 6, height: 6, borderRadius: 999, background: tone, display: 'inline-block' }} />
            <span style={{ fontSize: 13, color: C.fg, textTransform: 'capitalize' }}>{k}</span>
            <span className="tnum" style={{
              fontFamily: 'var(--font-mono)', fontSize: 11, color: C.mfg,
            }}>{n}</span>
          </div>
        );
      })}
    </div>
  );
}

function RosterPill({ total, healthy }) {
  const allHealthy = healthy === total;
  const tone = allHealthy ? C.green : C.amber;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 6,
      border: `1px solid ${C.border}`, padding: '3px 8px', borderRadius: 3,
      fontFamily: 'var(--font-mono)', fontSize: 9, color: C.mfg,
      textTransform: 'uppercase', letterSpacing: '0.08em',
    }}>
      <span style={{ width: 6, height: 6, borderRadius: 999, background: tone }} />
      {healthy}/{total} reporting
    </span>
  );
}

window.ButlersIndex = ButlersIndex;
