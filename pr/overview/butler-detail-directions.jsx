// /butlers/{butler} — operational detail. Status-board aesthetic extended
// to one butler. Three directions explored side-by-side.
//
// Subject: relationships. Base set of tabs is shared; bespoke tab(s) get
// hooked in per butler — here, "Contacts" for relationships.

const D = window.BUTLERS_DATA;
const ACTIVE = new Set(['running', 'patrol', 'consolidating', 'ingesting']);

const REL = D.butlers.find((b) => b.name === 'relationship');
const REL_STRIPE = (D.sessionGrid.find((s) => s.butler === 'relationship') || {}).row || Array(24).fill(0);
const REL_FEED = D.feed.filter((f) => f.butler === 'relationship');
const REL_PENDING = D.attention.filter((a) => a.butler === 'relationship');

// Synthesized 7d / 30d series — quiet, plausible
const SERIES_7D  = [38, 41, 44, 39, 47, 52, 47];
const SERIES_30D = [22,28,31,29,34,38,40,36,42,45,41,38,44,47,52,49,46,50,55,53,48,51,56,52,49,53,57,54,50,47];

// Config (mocked but plausible)
const CONFIG = {
  port: 8471,
  pid: 30148,
  uptime: '4d 12h',
  schedule: 'on demand · poll every 6m',
  model: 'claude-haiku-4-5',
  tokensIn:  '128.4k', tokensOut: '38.2k', costPer: '$0.039',
  scopes: ['contacts:read', 'mail:draft', 'sms:read', 'calendar:read'],
  integrations: ['Google Contacts', 'Apple Mail', 'iMessage', 'Linear (CRM)'],
  configPath: '~/.butlers/relationship.toml',
};

const MEMORY_FACTS = [
  { ts: '14:02', kind: 'fact',    text: 'Maya prefers Sunday brunch over Saturday dinner.' },
  { ts: '13:11', kind: 'entity',  text: 'Wei → confirmed as tier-1 (lunch cadence ≤ 14d).' },
  { ts: '11:40', kind: 'rule',    text: 'Reply within 6h to tier-1 contacts on weekdays.' },
  { ts: '09:30', kind: 'fact',    text: "Sarah's daughter starts school 2026-09-04." },
  { ts: '08:48', kind: 'fact',    text: 'Mom called 8m, mood: warm. No follow-ups needed.' },
];

const CONTACTS = D.contacts;

// ─── Shared chrome bits ──────────────────────────────────────────────────

const isDarkOk = () => window.__theme !== 'light';
const P = {
  bg: 'oklch(0.145 0 0)',
  surface: 'oklch(0.165 0 0)',
  deep: 'oklch(0.115 0 0)',
  fg: 'oklch(0.985 0 0)',
  mfg: 'oklch(0.708 0 0)',
  dim: 'oklch(0.55 0 0)',
  border: 'oklch(1 0 0 / 0.10)',
  borderSoft: 'oklch(1 0 0 / 0.06)',
  borderStrong: 'oklch(1 0 0 / 0.18)',
  red: 'oklch(0.685 0.250 29.2)',
  amber: 'oklch(0.810 0.185 84.0)',
  green: 'oklch(0.790 0.195 148.2)',
};

function Eyebrow({ children, sub }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'baseline', gap: 10,
      fontFamily: 'var(--font-mono)', fontSize: 9, color: P.mfg,
      textTransform: 'uppercase', letterSpacing: '0.14em',
      paddingBottom: 6, borderBottom: `1px solid ${P.border}`, marginBottom: 10,
    }}>
      <span>{children}</span>
      {sub && <span style={{ color: P.dim, letterSpacing: '0.06em', textTransform: 'none' }}>{sub}</span>}
    </div>
  );
}

function MonoLabel({ children, color = P.mfg }) {
  return (
    <span style={{
      fontFamily: 'var(--font-mono)', fontSize: 9, color,
      textTransform: 'uppercase', letterSpacing: '0.14em',
    }}>{children}</span>
  );
}

function Hero({ b, action = 'patrolling drafts queue', P: PP = P }) {
  const isActive = ACTIVE.has(b.activity);
  const tone = b.status === 'degraded' ? PP.red
              : b.activity === 'awaiting approval' ? PP.amber
              : isActive ? PP.green : PP.dim;
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: 'auto 1fr auto',
      gap: 18, alignItems: 'center',
      padding: '14px 0', borderBottom: `1px solid ${PP.border}`,
    }}>
      <ButlerMark name={b.name} size={36} tone={isActive ? 'fill' : 'neutral'} />
      <div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: 9, color: PP.dim,
            textTransform: 'uppercase', letterSpacing: '0.14em',
          }}>butler · /butlers/{b.name}</span>
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: 9, color: tone,
            textTransform: 'uppercase', letterSpacing: '0.14em',
          }}>● {b.activity}</span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: PP.dim }}>
            pid 30148 · port 8471 · uptime 4d 12h
          </span>
        </div>
        <div style={{
          fontSize: 22, fontWeight: 500, letterSpacing: '-0.02em',
          textTransform: 'capitalize', marginTop: 4,
        }}>{b.label}<span style={{ color: PP.dim, fontWeight: 400, marginLeft: 10, fontSize: 14 }}>· {action}</span></div>
      </div>
      <ActionBar P={PP} />
    </div>
  );
}

function ActionBar({ P: PP = P }) {
  const btn = {
    background: 'transparent', color: PP.fg,
    border: `1px solid ${PP.border}`, padding: '5px 10px',
    fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em',
    textTransform: 'uppercase', cursor: 'pointer', borderRadius: 3,
  };
  return (
    <div style={{ display: 'flex', gap: 6 }}>
      <button style={btn}>force run</button>
      <button style={btn}>logs</button>
      <button style={btn}>config</button>
      <button style={btn}>prompt</button>
      <button style={{ ...btn, background: PP.fg, color: PP.bg, borderColor: PP.fg }}>pause</button>
    </div>
  );
}

// 24-cell stripe used in panels
function Stripe24({ row, height = 16 }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 1, height }}>
      {row.map((v, k) => (
        <div key={k} style={{
          flex: 1,
          height: v === 0 ? 2 : 2 + (v / 4) * (height - 2),
          background: v === 0 ? 'oklch(1 0 0 / 0.05)' : `oklch(0.985 0 0 / ${0.18 + (v / 4) * 0.55})`,
          borderRadius: 1,
        }} />
      ))}
    </div>
  );
}

// Simple bar/line chart for n-day series
function BarSeries({ data, height = 56, accent = false }) {
  const max = Math.max(...data) || 1;
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height }}>
      {data.map((v, k) => (
        <div key={k} style={{
          flex: 1,
          height: 2 + (v / max) * (height - 2),
          background: accent ? P.amber : `oklch(0.985 0 0 / ${0.20 + (v / max) * 0.55})`,
          borderRadius: 1,
        }} />
      ))}
    </div>
  );
}

function RangeToggle({ value, onChange, P: PP = P }) {
  const opts = ['24h', '7d', '30d'];
  return (
    <div style={{ display: 'flex', gap: 0, border: `1px solid ${PP.border}`, borderRadius: 3 }}>
      {opts.map((o, i) => (
        <button key={o} onClick={() => onChange(o)} style={{
          background: value === o ? PP.fg : 'transparent',
          color: value === o ? PP.bg : PP.fg,
          border: 'none', borderLeft: i ? `1px solid ${PP.border}` : 'none',
          padding: '4px 10px', cursor: 'pointer',
          fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em',
        }}>{o}</button>
      ))}
    </div>
  );
}

// Single KPI cell used in strips
function KPI({ label, value, sub }) {
  return (
    <div style={{ padding: '14px 16px' }}>
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: 9, color: P.mfg,
        textTransform: 'uppercase', letterSpacing: '0.14em', marginBottom: 6,
      }}>{label}</div>
      <div className="tnum" style={{
        fontSize: 22, fontWeight: 500, letterSpacing: '-0.025em', lineHeight: 1,
      }}>{value}</div>
      {sub && <div style={{
        fontFamily: 'var(--font-mono)', fontSize: 9, color: P.dim, marginTop: 4,
      }}>{sub}</div>}
    </div>
  );
}

function Panel({ title, sub, children, span = 1, height, fill }) {
  return (
    <div style={{
      gridColumn: `span ${span}`,
      borderRight: `1px solid ${P.border}`, borderBottom: `1px solid ${P.border}`,
      padding: '14px 16px', minHeight: height || 'auto',
      display: 'flex', flexDirection: 'column', gap: 10,
      background: fill ? 'oklch(1 0 0 / 0.012)' : 'transparent',
    }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <MonoLabel>{title}</MonoLabel>
        {sub && <MonoLabel color={P.dim}>{sub}</MonoLabel>}
      </div>
      <div style={{ flex: 1, minHeight: 0 }}>{children}</div>
    </div>
  );
}

const TABS_BASE = ['Overview', 'Activity', 'Logs', 'Approvals', 'Spend', 'Config', 'Memory'];
const TABS_REL = [...TABS_BASE, 'Contacts'];   // bespoke tab — relationships only

function Tabs({ tabs, value, onChange, P: PP = P }) {
  return (
    <div style={{
      display: 'flex', gap: 0,
      borderBottom: `1px solid ${PP.border}`,
    }}>
      {tabs.map((t) => {
        const active = t === value;
        const isBespoke = t === 'Contacts';
        return (
          <button key={t} onClick={() => onChange(t)} style={{
            background: 'transparent', color: active ? PP.fg : PP.mfg,
            border: 'none', borderBottom: active ? `2px solid ${PP.fg}` : '2px solid transparent',
            padding: '10px 14px', cursor: 'pointer',
            fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.10em',
            textTransform: 'uppercase', position: 'relative',
            marginBottom: -1,
          }}>
            {t}
            {isBespoke && (
              <span style={{
                position: 'absolute', top: 6, right: 4,
                width: 4, height: 4, borderRadius: 999,
                background: 'var(--category-1)',
              }} />
            )}
          </button>
        );
      })}
    </div>
  );
}

// ─── Variant 1 · Tabbed dashboard ────────────────────────────────────────
// Datadog-ish. Hero on top, action bar, tabs, then a panel grid keyed to
// the active tab. Default tab = Overview.

function VariantTabbed() {
  const [tab, setTab] = React.useState('Overview');
  const [range, setRange] = React.useState('24h');
  return (
    <div style={{
      width: '100%', height: '100%', background: P.bg, color: P.fg,
      fontFamily: 'var(--font-sans)', padding: '20px 24px',
      display: 'flex', flexDirection: 'column', boxSizing: 'border-box', overflow: 'hidden',
    }}>
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: 9, color: P.dim,
        textTransform: 'uppercase', letterSpacing: '0.14em', marginBottom: 8,
      }}>Butlers / Relationships</div>

      <Hero b={REL} />

      <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 8 }}>
        <Tabs tabs={TABS_REL} value={tab} onChange={setTab} />
        <span style={{ flex: 1 }} />
        <RangeToggle value={range} onChange={setRange} />
      </div>

      {/* Panel grid */}
      <div style={{
        flex: 1, marginTop: 16, minHeight: 0, overflow: 'hidden',
        display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)',
        gridTemplateRows: 'auto auto 1fr',
        borderTop: `1px solid ${P.border}`, borderLeft: `1px solid ${P.border}`,
      }}>
        {tab === 'Overview' && <OverviewPanels range={range} />}
        {tab === 'Activity' && <ActivityPanels range={range} />}
        {tab === 'Logs' && <LogsPanels />}
        {tab === 'Approvals' && <ApprovalsPanels />}
        {tab === 'Spend' && <SpendPanels />}
        {tab === 'Config' && <ConfigPanels />}
        {tab === 'Memory' && <MemoryPanels />}
        {tab === 'Contacts' && <ContactsPanels />}
      </div>
    </div>
  );
}

function OverviewPanels({ range }) {
  const series = range === '7d' ? SERIES_7D : range === '30d' ? SERIES_30D : REL_STRIPE;
  return (
    <>
      <Panel title="status">
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <span style={{ width: 8, height: 8, background: P.green, borderRadius: 999 }} />
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 14, color: P.fg }}>OK · idle</span>
        </div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: P.dim, marginTop: 4 }}>
          last run 6m ago · next on demand
        </div>
      </Panel>
      <Panel title="sessions" sub={range}>
        <div className="tnum" style={{ fontSize: 26, fontWeight: 500, letterSpacing: '-0.025em' }}>{REL.sessions24h}</div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: P.dim, marginTop: 2 }}>+18% vs prior</div>
      </Panel>
      <Panel title="spend" sub={range}>
        <div className="tnum" style={{ fontSize: 26, fontWeight: 500, letterSpacing: '-0.025em' }}>${REL.costToday.toFixed(2)}</div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: P.dim, marginTop: 2 }}>$0.039 / session</div>
      </Panel>
      <Panel title="awaiting" sub="approvals">
        <div className="tnum" style={{ fontSize: 26, fontWeight: 500, letterSpacing: '-0.025em', color: P.amber }}>{REL_PENDING.length}</div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: P.dim, marginTop: 2 }}>oldest: 11m</div>
      </Panel>

      <Panel title="activity" sub={range} span={2}>
        {range === '24h' ? <Stripe24 row={REL_STRIPE} height={56} /> : <BarSeries data={series} height={56} />}
        <div style={{
          display: 'flex', justifyContent: 'space-between',
          fontFamily: 'var(--font-mono)', fontSize: 9, color: P.dim, marginTop: 6,
        }}>
          <span>{range === '24h' ? '00:00' : range === '7d' ? '7d ago' : '30d ago'}</span>
          <span>now</span>
        </div>
      </Panel>
      <Panel title="recent" sub={`${REL_FEED.length} events`} span={2}>
        <div style={{ overflowY: 'auto' }}>
          {REL_FEED.slice(0, 4).map((e, i) => (
            <div key={i} style={{
              display: 'grid', gridTemplateColumns: '46px 1fr auto',
              gap: 10, padding: '6px 0',
              borderBottom: i < 3 ? `1px solid ${P.borderSoft}` : 'none',
            }}>
              <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: P.dim }}>{e.time}</span>
              <span style={{ fontSize: 12 }}>{e.text}</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: P.mfg, letterSpacing: '0.08em', textTransform: 'uppercase' }}>{e.kind}</span>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="awaiting your action" span={2}>
        {REL_PENDING.map((a, i) => (
          <div key={i} style={{
            display: 'grid', gridTemplateColumns: '8px 1fr auto', gap: 10,
            padding: '8px 0',
            borderBottom: i < REL_PENDING.length - 1 ? `1px solid ${P.borderSoft}` : 'none',
            alignItems: 'baseline',
          }}>
            <span style={{ width: 6, height: 6, background: a.severity === 'high' ? P.red : P.amber, borderRadius: 1 }} />
            <span style={{ fontSize: 12 }}>{a.title} <span style={{ color: P.dim }}>· {a.age}</span></span>
            <a href="#" style={{ color: P.fg, textDecoration: 'underline', textUnderlineOffset: 3, textDecorationColor: P.borderStrong, fontSize: 12 }}>{a.action} →</a>
          </div>
        ))}
      </Panel>
      <Panel title="config" sub={CONFIG.configPath} span={2}>
        <div style={{
          display: 'grid', gridTemplateColumns: '90px 1fr', rowGap: 4, columnGap: 12,
          fontFamily: 'var(--font-mono)', fontSize: 11,
        }}>
          <span style={{ color: P.mfg }}>model</span><span>{CONFIG.model}</span>
          <span style={{ color: P.mfg }}>schedule</span><span>{CONFIG.schedule}</span>
          <span style={{ color: P.mfg }}>scopes</span><span>{CONFIG.scopes.join(', ')}</span>
          <span style={{ color: P.mfg }}>integrations</span><span>{CONFIG.integrations.join(', ')}</span>
        </div>
      </Panel>
    </>
  );
}

function ActivityPanels({ range }) {
  const series = range === '7d' ? SERIES_7D : range === '30d' ? SERIES_30D : REL_STRIPE;
  return (
    <>
      <Panel title="sessions" sub={range} span={4} height={140}>
        {range === '24h' ? <Stripe24 row={REL_STRIPE} height={92} /> : <BarSeries data={series} height={92} />}
      </Panel>
      <Panel title="latency · p50 / p95" sub="ms" span={2} height={120}><BarSeries data={[120,110,140,100,98,130,115,108]} height={70} /></Panel>
      <Panel title="errors" sub="count/h" span={2} height={120}><BarSeries data={[0,0,1,0,0,0,0,0]} height={70} /></Panel>
      <Panel title="kind breakdown" sub="24h" span={4}>
        <div style={{ display: 'flex', gap: 18, alignItems: 'baseline', flexWrap: 'wrap' }}>
          {[
            { k: 'log', n: 18 }, { k: 'draft', n: 4 }, { k: 'consolidate', n: 12 },
            { k: 'investigate', n: 7 }, { k: 'idle', n: 6 },
          ].map((x) => (
            <div key={x.k}>
              <MonoLabel>{x.k}</MonoLabel>
              <div className="tnum" style={{ fontSize: 18, fontWeight: 500 }}>{x.n}</div>
            </div>
          ))}
        </div>
      </Panel>
    </>
  );
}

function LogsPanels() {
  const lines = [
    '14:28:02 INFO  scheduler.tick — no new contacts',
    '14:27:58 INFO  draft.compose maya/sunday-dinner — model=haiku-4-5 in=412 out=128 latency=0.91s',
    '14:27:54 INFO  draft.queue maya/sunday-dinner pending_review',
    '14:14:11 DEBUG patrol.scan contacts.tier1 — 18 ok',
    '14:02:03 INFO  reply.draft.start maya — last_msg_age=11m',
    '13:47:19 INFO  warmth.recompute — 6 contacts updated',
    '13:46:51 DEBUG memory.write fact=maya.prefers_brunch',
    '13:11:02 INFO  tier.confirm wei → 1',
    '11:40:00 INFO  rule.add reply<6h tier1 weekday',
    '09:30:11 INFO  fact.add sarah.daughter.school 2026-09-04',
    '08:48:30 INFO  call.log mom 8m mood=warm',
    '08:02:11 INFO  scheduler.tick — sleep mode end',
  ];
  return (
    <>
      <Panel title="raw log" sub="tail -f · auto-scroll" span={4} height="100%">
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 11, color: P.fg,
          lineHeight: 1.5, overflowY: 'auto', height: '100%',
        }}>
          {lines.map((l, i) => {
            const [ts, lvl, ...rest] = l.split(' ');
            const lvlColor = lvl === 'ERROR' ? P.red : lvl === 'WARN' ? P.amber : lvl === 'DEBUG' ? P.dim : P.green;
            return (
              <div key={i} style={{ display: 'grid', gridTemplateColumns: '78px 56px 1fr', gap: 10 }}>
                <span style={{ color: P.dim }}>{ts}</span>
                <span style={{ color: lvlColor }}>{lvl}</span>
                <span style={{ color: P.fg }}>{rest.join(' ')}</span>
              </div>
            );
          })}
        </div>
      </Panel>
    </>
  );
}

function ApprovalsPanels() {
  const items = [
    { sev: 'medium', title: 'Send reply to Maya about Sunday dinner', age: '11m', action: 'Send / Edit', detail: 'Drafted from your prior pattern (warm, brief, suggests time)' },
    { sev: 'low', title: 'Confirm new contact: Marcus from Wei\u2019s lunch', age: '2h', action: 'Confirm', detail: 'Detected from 3 calendar mentions and an iMessage thread' },
    { sev: 'low', title: 'Suggested reply to Daniel (4d silence)', age: '4h', action: 'Review', detail: 'Tier-3 nudge cadence' },
  ];
  return (
    <>
      <Panel title="awaiting your action" sub={`${items.length} pending`} span={4} height="100%">
        {items.map((a, i) => (
          <div key={i} style={{
            display: 'grid', gridTemplateColumns: '8px 1fr auto', gap: 14,
            padding: '14px 0',
            borderBottom: i < items.length - 1 ? `1px solid ${P.borderSoft}` : 'none',
            alignItems: 'baseline',
          }}>
            <span style={{ width: 6, height: 6, background: a.sev === 'high' ? P.red : a.sev === 'medium' ? P.amber : P.dim, borderRadius: 1 }} />
            <div>
              <div style={{ fontSize: 14, fontWeight: 500 }}>{a.title}</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: P.dim, marginTop: 4 }}>{a.detail} · {a.age}</div>
            </div>
            <a href="#" style={{ color: P.fg, textDecoration: 'underline', textUnderlineOffset: 4, textDecorationColor: P.borderStrong, fontSize: 13 }}>{a.action} →</a>
          </div>
        ))}
      </Panel>
    </>
  );
}

function SpendPanels() {
  return (
    <>
      <Panel title="spend · today" span={2}><div className="tnum" style={{ fontSize: 28, fontWeight: 500, letterSpacing: '-0.025em' }}>${REL.costToday.toFixed(2)}</div><MonoLabel color={P.dim}>−4% vs avg</MonoLabel></Panel>
      <Panel title="spend · 30d" span={2}><div className="tnum" style={{ fontSize: 28, fontWeight: 500, letterSpacing: '-0.025em' }}>$54.18</div><MonoLabel color={P.dim}>$1.81 / day</MonoLabel></Panel>
      <Panel title="spend · 30d" sub="trend" span={4} height={140}><BarSeries data={SERIES_30D.map((x) => x * 0.04)} height={92} /></Panel>
      <Panel title="cost / session" span={2}>
        <div className="tnum" style={{ fontSize: 22, fontWeight: 500 }}>$0.039</div>
        <MonoLabel color={P.dim}>haiku-4-5 · 128.4k in / 38.2k out</MonoLabel>
      </Panel>
      <Panel title="model" span={2}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13 }}>{CONFIG.model}</div>
        <MonoLabel color={P.dim}>fallback: claude-sonnet-4-5</MonoLabel>
      </Panel>
    </>
  );
}

function ConfigPanels() {
  return (
    <>
      <Panel title="process" span={2}>
        <KV k="port" v={CONFIG.port} />
        <KV k="pid" v={CONFIG.pid} />
        <KV k="uptime" v={CONFIG.uptime} />
        <KV k="config" v={CONFIG.configPath} mono />
      </Panel>
      <Panel title="schedule" span={2}>
        <KV k="cadence" v={CONFIG.schedule} />
        <KV k="last run" v="6m ago" />
        <KV k="next run" v="on demand" />
      </Panel>
      <Panel title="scopes" span={2}>
        {CONFIG.scopes.map((s) => (
          <div key={s} style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: P.fg, padding: '2px 0' }}>● {s}</div>
        ))}
      </Panel>
      <Panel title="integrations" span={2}>
        {CONFIG.integrations.map((s) => (
          <div key={s} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', borderBottom: `1px solid ${P.borderSoft}` }}>
            <span style={{ fontSize: 12 }}>{s}</span>
            <MonoLabel color={P.green}>● connected</MonoLabel>
          </div>
        ))}
      </Panel>
    </>
  );
}

function MemoryPanels() {
  return (
    <>
      <Panel title="episodes"><div className="tnum" style={{ fontSize: 22, fontWeight: 500 }}>418</div><MonoLabel color={P.dim}>+12 today</MonoLabel></Panel>
      <Panel title="facts"><div className="tnum" style={{ fontSize: 22, fontWeight: 500 }}>2,184</div><MonoLabel color={P.dim}>+9 today</MonoLabel></Panel>
      <Panel title="entities"><div className="tnum" style={{ fontSize: 22, fontWeight: 500 }}>146</div><MonoLabel color={P.dim}>+1 today</MonoLabel></Panel>
      <Panel title="rules"><div className="tnum" style={{ fontSize: 22, fontWeight: 500 }}>23</div><MonoLabel color={P.dim}>+1 today</MonoLabel></Panel>
      <Panel title="recent writes" span={4}>
        {MEMORY_FACTS.map((m, i) => (
          <div key={i} style={{
            display: 'grid', gridTemplateColumns: '50px 80px 1fr',
            gap: 12, padding: '8px 0',
            borderBottom: i < MEMORY_FACTS.length - 1 ? `1px solid ${P.borderSoft}` : 'none',
          }}>
            <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: P.dim }}>{m.ts}</span>
            <MonoLabel>{m.kind}</MonoLabel>
            <span style={{ fontSize: 13 }}>{m.text}</span>
          </div>
        ))}
      </Panel>
    </>
  );
}

function ContactsPanels() {
  return (
    <>
      <Panel title="contacts · tracked" span={2}><div className="tnum" style={{ fontSize: 24, fontWeight: 500 }}>146</div><MonoLabel color={P.dim}>tier1: 6 · tier2: 12 · tier3: 38</MonoLabel></Panel>
      <Panel title="warmth · avg tier1" span={2}><div className="tnum" style={{ fontSize: 24, fontWeight: 500 }}>0.86</div><MonoLabel color={P.dim}>+0.02 vs last week</MonoLabel></Panel>
      <Panel title="watchlist · tier1+2" span={4} height="100%">
        {CONTACTS.map((c, i) => (
          <div key={c.name} style={{
            display: 'grid', gridTemplateColumns: '20px 100px 1fr 80px 60px', gap: 12, padding: '8px 0',
            borderBottom: i < CONTACTS.length - 1 ? `1px solid ${P.borderSoft}` : 'none',
            alignItems: 'center',
          }}>
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: 9, color: P.dim,
              border: `1px solid ${P.border}`, borderRadius: 2, padding: '1px 4px', textAlign: 'center',
            }}>T{c.tier}</span>
            <span style={{ fontSize: 13 }}>{c.name}</span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: P.mfg }}>{c.last}</span>
            <div style={{ height: 4, background: 'oklch(1 0 0 / 0.06)', borderRadius: 1 }}>
              <div style={{ width: `${c.warm * 100}%`, height: '100%', background: P.fg, opacity: 0.7 }} />
            </div>
            <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: P.fg, textAlign: 'right' }}>{c.warm.toFixed(2)}</span>
          </div>
        ))}
      </Panel>
    </>
  );
}

function KV({ k, v, mono }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '90px 1fr', gap: 12, padding: '3px 0',
      fontFamily: 'var(--font-mono)', fontSize: 11,
    }}>
      <span style={{ color: P.mfg }}>{k}</span>
      <span style={{ color: P.fg, fontFamily: mono ? 'var(--font-mono)' : 'inherit' }}>{v}</span>
    </div>
  );
}

// ─── Variant 2 · Single-screen ops console ───────────────────────────────
// No tabs. All panels visible at once on one dashboard, like a NOC for one
// butler. Uses span= to vary panel sizes — bigger panels for activity, log,
// approvals; smaller for KPIs and config.

function VariantConsole() {
  return (
    <div style={{
      width: '100%', height: '100%', background: P.bg, color: P.fg,
      fontFamily: 'var(--font-sans)', padding: '20px 24px',
      display: 'flex', flexDirection: 'column', boxSizing: 'border-box', overflow: 'hidden',
    }}>
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: 9, color: P.dim,
        textTransform: 'uppercase', letterSpacing: '0.14em', marginBottom: 8,
      }}>Butlers / Relationships · console</div>
      <Hero b={REL} />

      <div style={{
        flex: 1, marginTop: 16, minHeight: 0, overflow: 'hidden',
        display: 'grid',
        gridTemplateColumns: 'repeat(6, 1fr)',
        gridTemplateRows: 'auto auto 1fr 1fr',
        borderTop: `1px solid ${P.border}`, borderLeft: `1px solid ${P.border}`,
      }}>
        {/* KPI row */}
        <Panel title="status"><div style={{ display: 'flex', alignItems: 'center', gap: 8 }}><span style={{ width: 8, height: 8, background: P.green, borderRadius: 999 }} /><span style={{ fontFamily: 'var(--font-mono)', fontSize: 13 }}>OK</span></div><MonoLabel color={P.dim}>idle · 6m ago</MonoLabel></Panel>
        <Panel title="sessions · 24h"><div className="tnum" style={{ fontSize: 22, fontWeight: 500 }}>{REL.sessions24h}</div><MonoLabel color={P.dim}>+18%</MonoLabel></Panel>
        <Panel title="spend · today"><div className="tnum" style={{ fontSize: 22, fontWeight: 500 }}>${REL.costToday.toFixed(2)}</div><MonoLabel color={P.dim}>−4%</MonoLabel></Panel>
        <Panel title="latency · p95"><div className="tnum" style={{ fontSize: 22, fontWeight: 500 }}>1.2s</div><MonoLabel color={P.dim}>haiku-4-5</MonoLabel></Panel>
        <Panel title="errors · 24h"><div className="tnum" style={{ fontSize: 22, fontWeight: 500 }}>0</div><MonoLabel color={P.dim}>last: never</MonoLabel></Panel>
        <Panel title="awaiting"><div className="tnum" style={{ fontSize: 22, fontWeight: 500, color: P.amber }}>{REL_PENDING.length}</div><MonoLabel color={P.dim}>oldest 11m</MonoLabel></Panel>

        {/* Activity row */}
        <Panel title="activity · 24h" span={4} height={120}><Stripe24 row={REL_STRIPE} height={68} /></Panel>
        <Panel title="spend · 7d" span={2} height={120}><BarSeries data={SERIES_7D.map((x) => x * 0.04)} height={68} /></Panel>

        {/* Lower row: log + approvals */}
        <Panel title="raw log · tail" sub="auto-scroll" span={3}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, lineHeight: 1.5, overflowY: 'auto', height: '100%', maxHeight: 200 }}>
            {[
              ['14:28', 'INFO', 'scheduler.tick — no new contacts'],
              ['14:27', 'INFO', 'draft.compose maya/sunday-dinner'],
              ['14:14', 'DEBG', 'patrol.scan tier1 — 18 ok'],
              ['14:02', 'INFO', 'reply.draft.start maya'],
              ['13:47', 'INFO', 'warmth.recompute — 6 updated'],
              ['13:11', 'INFO', 'tier.confirm wei → 1'],
            ].map(([t, lvl, msg], i) => (
              <div key={i} style={{ display: 'grid', gridTemplateColumns: '46px 44px 1fr', gap: 8 }}>
                <span style={{ color: P.dim }}>{t}</span>
                <span style={{ color: lvl === 'DEBG' ? P.dim : P.green }}>{lvl}</span>
                <span>{msg}</span>
              </div>
            ))}
          </div>
        </Panel>
        <Panel title="awaiting your action" span={3}>
          {REL_PENDING.map((a, i) => (
            <div key={i} style={{
              display: 'grid', gridTemplateColumns: '8px 1fr auto', gap: 10,
              padding: '6px 0',
              borderBottom: i < REL_PENDING.length - 1 ? `1px solid ${P.borderSoft}` : 'none',
              alignItems: 'baseline',
            }}>
              <span style={{ width: 6, height: 6, background: a.severity === 'high' ? P.red : P.amber, borderRadius: 1 }} />
              <span style={{ fontSize: 12 }}>{a.title}</span>
              <a href="#" style={{ color: P.fg, textDecoration: 'underline', textUnderlineOffset: 3, textDecorationColor: P.borderStrong, fontSize: 12 }}>{a.action} →</a>
            </div>
          ))}
        </Panel>

        {/* Bottom row: config + memory + bespoke */}
        <Panel title="config" span={2}>
          <KV k="model" v={CONFIG.model} />
          <KV k="schedule" v="poll 6m" />
          <KV k="port" v={CONFIG.port} />
          <KV k="scopes" v={`${CONFIG.scopes.length} grants`} />
        </Panel>
        <Panel title="memory" span={2}>
          <KV k="episodes" v="418 (+12)" />
          <KV k="facts" v="2,184 (+9)" />
          <KV k="entities" v="146 (+1)" />
          <KV k="rules" v="23 (+1)" />
        </Panel>
        <Panel title="contacts · bespoke" sub="relationships only" span={2}>
          <div style={{ display: 'flex', gap: 14, marginBottom: 6 }}>
            <div><MonoLabel>tracked</MonoLabel><div className="tnum" style={{ fontSize: 18 }}>146</div></div>
            <div><MonoLabel>warmth</MonoLabel><div className="tnum" style={{ fontSize: 18 }}>0.86</div></div>
          </div>
          <MonoLabel color={P.dim}>top: mom · wei · sarah</MonoLabel>
        </Panel>
      </div>
    </div>
  );
}

// ─── Variant 3 · Sub-nav rail (left) + main view ────────────────────────
// Tab equivalent moved to a vertical rail on the left. Main pane is a
// single focus surface. Reads as "this butler is a process; pick a facet".

function VariantRail() {
  const [section, setSection] = React.useState('Overview');
  const sections = TABS_REL;
  return (
    <div style={{
      width: '100%', height: '100%', background: P.bg, color: P.fg,
      fontFamily: 'var(--font-sans)', display: 'grid',
      gridTemplateColumns: '180px 1fr', boxSizing: 'border-box', overflow: 'hidden',
    }}>
      {/* Rail */}
      <div style={{
        background: P.deep, borderRight: `1px solid ${P.border}`,
        padding: '20px 0', display: 'flex', flexDirection: 'column',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '0 18px 16px', borderBottom: `1px solid ${P.borderSoft}` }}>
          <ButlerMark name={REL.name} size={28} tone="fill" />
          <div>
            <div style={{ fontSize: 13, fontWeight: 500, textTransform: 'capitalize' }}>{REL.label}</div>
            <MonoLabel color={P.green}>● {REL.activity}</MonoLabel>
          </div>
        </div>
        <div style={{ padding: '6px 0', flex: 1, overflowY: 'auto' }}>
          {sections.map((s) => {
            const active = s === section;
            const isBespoke = s === 'Contacts';
            return (
              <button key={s} onClick={() => setSection(s)} style={{
                display: 'block', width: '100%', textAlign: 'left',
                background: active ? 'oklch(1 0 0 / 0.06)' : 'transparent',
                color: active ? P.fg : P.mfg,
                border: 'none', borderLeft: active ? `2px solid ${P.fg}` : '2px solid transparent',
                padding: '8px 18px', cursor: 'pointer',
                fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.10em',
                textTransform: 'uppercase',
              }}>
                {s}
                {isBespoke && (
                  <span style={{
                    display: 'inline-block', marginLeft: 8,
                    width: 5, height: 5, borderRadius: 999, background: 'var(--category-1)',
                    verticalAlign: 'middle',
                  }} />
                )}
              </button>
            );
          })}
        </div>
        <div style={{ padding: '12px 18px', borderTop: `1px solid ${P.borderSoft}` }}>
          <ActionBar P={P} />
        </div>
      </div>

      {/* Main */}
      <div style={{ padding: '20px 24px', overflowY: 'auto', minHeight: 0 }}>
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 9, color: P.dim,
          textTransform: 'uppercase', letterSpacing: '0.14em', marginBottom: 8,
        }}>/butlers/{REL.name} · {section.toLowerCase()}</div>
        <div style={{
          fontSize: 22, fontWeight: 500, letterSpacing: '-0.02em', marginBottom: 18,
        }}>{section}</div>

        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)',
          borderTop: `1px solid ${P.border}`, borderLeft: `1px solid ${P.border}`,
        }}>
          {section === 'Overview' && <OverviewPanels range="24h" />}
          {section === 'Activity' && <ActivityPanels range="24h" />}
          {section === 'Logs' && <LogsPanels />}
          {section === 'Approvals' && <ApprovalsPanels />}
          {section === 'Spend' && <SpendPanels />}
          {section === 'Config' && <ConfigPanels />}
          {section === 'Memory' && <MemoryPanels />}
          {section === 'Contacts' && <ContactsPanels />}
        </div>
      </div>
    </div>
  );
}

window.BUTLER_DETAIL_VARIANTS = { VariantTabbed, VariantConsole, VariantRail };
