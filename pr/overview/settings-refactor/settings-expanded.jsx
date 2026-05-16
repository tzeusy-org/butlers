// settings-expanded.jsx
//
// Five expanded-card views, each accessible from the Console.
// Each view is rich enough to be a route in its own right
// (e.g. /settings/butlers, /settings/spend, /settings/approvals).
//
// Conventions per view:
//   1. breadcrumb · butlers › settings › <view>
//   2. page header (h1 + status)
//   3. KPI strip
//   4. one or two "distinctive" sections that introduce real product
//      depth — fallback chains, forecast curves, retention policies,
//      quiet-hours editors, audit reels.
//   5. ApiWireFooter — the FastAPI surface that would power this view.
//
// Atoms imported from window.* (exposed by settings-redesign.jsx) so
// the visual vocabulary stays unified.

const Cs = window.C;
const Eyebrow      = window.S_Eyebrow;
const Mono         = window.S_Mono;
const Pill         = window.S_Pill;
const EditValue    = window.S_EditValue;
const Toggle       = window.S_Toggle;
const FakeRail     = window.S_FakeRail;
const KpiStrip     = window.S_KpiStrip;
const MiniSpark    = window.S_MiniSpark;
const CapacityBar  = window.S_CapacityBar;
const ConfigLine   = window.S_ConfigLine;
const ConfigLineMini = window.S_ConfigLineMini;
const AttentionStrip = window.S_AttentionStrip;
const ATTN_BG      = window.S_ATTN_BG;
const BUTLERS      = window.S_BUTLERS;
const BIG_MODELS   = window.S_BIG_MODELS;
const PERMS        = window.S_PERMS;
const hasPerm      = window.S_hasPerm;
const STATE_COLOR  = window.S_STATE_COLOR;
const linkS        = window.S_linkS;

// ─── Shared expanded-view chrome ─────────────────────────────────────

function ApiBadge({ method, path, hover }) {
  const colorMap = {
    GET: Cs.green, POST: Cs.amber, PUT: Cs.amber, PATCH: Cs.amber, DELETE: Cs.red, WS: Cs.fg,
  };
  const c = colorMap[method] || Cs.mfg;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'baseline', gap: 8,
      fontFamily: 'var(--font-mono)', fontSize: 10,
      padding: '3px 8px',
      border: `1px solid ${Cs.border}`, borderRadius: 2,
    }}>
      <span style={{ color: c, fontWeight: 500, letterSpacing: '0.04em' }}>{method}</span>
      <span style={{ color: Cs.mfg }}>{path}</span>
      {hover && <span style={{ color: Cs.dim, fontStyle: 'italic' }}>· {hover}</span>}
    </span>
  );
}

function ApiWireFooter({ endpoints, note }) {
  return (
    <div style={{
      borderTop: `1px solid ${Cs.border}`,
      background: Cs.bgDeep,
      padding: '14px 28px',
      display: 'grid', gridTemplateColumns: '160px 1fr', gap: 24, alignItems: 'flex-start',
    }}>
      <div>
        <Mono color={Cs.dim} size={9} track="0.14em">wired to · fastapi</Mono>
        {note && (
          <div style={{
            marginTop: 6, fontFamily: 'var(--font-serif)', fontSize: 11.5,
            color: Cs.dim, fontStyle: 'italic', lineHeight: 1.4, maxWidth: 140,
          }}>{note}</div>
        )}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {endpoints.map((e, i) => <ApiBadge key={i} {...e} />)}
      </div>
    </div>
  );
}

function ExpandHeader({ eyebrow, title, sub, status, parentRoute = 'settings' }) {
  return (
    <>
      {/* breadcrumb */}
      <div style={{
        padding: '14px 28px', borderBottom: `1px solid ${Cs.border}`,
        display: 'flex', alignItems: 'baseline', gap: 12,
        fontFamily: 'var(--font-mono)', fontSize: 10, color: Cs.dim,
        textTransform: 'uppercase', letterSpacing: '0.14em',
      }}>
        <span>butlers</span><span>›</span>
        {parentRoute && (
          <React.Fragment>
            <a style={{ color: Cs.mfg, textDecoration: 'none' }}>{parentRoute}</a><span>›</span>
          </React.Fragment>
        )}
        <span style={{ color: Cs.fg }}>{title.toLowerCase()}</span>
        <span style={{ marginLeft: 'auto', color: Cs.mfg, letterSpacing: '0.06em', textTransform: 'none' }}>{sub}</span>
      </div>
      {/* page header */}
      <div style={{
        padding: '22px 28px 18px',
        borderBottom: `1px solid ${Cs.border}`,
        display: 'grid', gridTemplateColumns: '1fr auto', gap: 24, alignItems: 'baseline',
      }}>
        <div>
          <Eyebrow>{eyebrow}</Eyebrow>
          <h1 style={{
            margin: '8px 0 0', fontSize: 30, fontWeight: 500,
            letterSpacing: '-0.025em', lineHeight: 1.05,
          }}>{title}</h1>
        </div>
        {status && <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>{status}</div>}
      </div>
    </>
  );
}

function Section({ n, title, hint, right, children }) {
  return (
    <section style={{ padding: '24px 28px', borderBottom: `1px solid ${Cs.border}` }}>
      <div style={{
        display: 'grid', gridTemplateColumns: '36px 1fr auto', gap: 14, alignItems: 'baseline',
        marginBottom: 16,
      }}>
        <Mono color={Cs.dim} size={11} track="0.06em">§{String(n).padStart(2,'0')}</Mono>
        <div>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 500, letterSpacing: '-0.015em' }}>{title}</h2>
          {hint && <Mono color={Cs.dim} size={10} upper={false} track="0.04em" style={{ marginTop: 2 }}>{hint}</Mono>}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════════════
// 1 · Butlers Control Room
// ═══════════════════════════════════════════════════════════════════

const BUTLER_DETAIL = {
  qa: {
    activity: 'patrol', activityTone: 'green',
    sessions24h: 142, spend7d: 184.60, lastRun: '2m 14s ago', queueDepth: 0,
    primaryModel: 'claude-sonnet-4-5', fallback: ['claude-haiku-4-5', 'claude-opus-4-5'],
    schedule: 'patrol 14m', ceiling: 8.00, autosend: false,
    prompt: `You are the QA Butler. Patrol the house's running services every fourteen minutes. When something is amiss, gather evidence first — sample logs, cross-check metrics, consider what's normal. Compose a short dossier: a serif-prose diagnosis pinned to specific log lines, a proposed fix as a draft PR, and a one-line "why this fix" gloss. Never speak in alarm. Never invent failures. If nothing's wrong, say so.`,
    promptVersion: 7, promptUpdated: '14 May · 02:11',
    tools: [
      { name: 'log.tail',          desc: 'tail any butler log stream',                allowed: true,  scope: 'all butlers' },
      { name: 'metrics.read',      desc: 'read prometheus metrics',                   allowed: true,  scope: 'all metrics' },
      { name: 'github.draft_pr',   desc: 'open a draft PR for a proposed fix',        allowed: true,  scope: 'butlerhouse/* repos' },
      { name: 'github.merge',      desc: 'merge a PR (subject to auto-merge policy)', allowed: true,  scope: 'severity ≤ medium' },
      { name: 'butlers.pause',     desc: 'pause a misbehaving butler',                allowed: true,  scope: 'all butlers' },
      { name: 'shell.exec',        desc: 'run shell commands on the host',            allowed: false, scope: '—' },
      { name: 'audit.write',       desc: 'append to the system audit log',            allowed: true,  scope: 'qa.* events' },
    ],
    memory: { read: ['short','mid','long'], write: [], namespace: 'qa.cases', drops: 0 },
    activity24h: [0,0,0,1,0,0,1,2,1,3,4,5,3,4,6,5,4,3,4,3,2,3,4,2],
    recentRuns: [
      { ts: '16:42', kind: 'patrol', note: 'no anomalies · 14 services healthy' },
      { ts: '16:28', kind: 'case',   note: 'opened qa-2026-0512-a · drafted PR #142' },
      { ts: '16:14', kind: 'patrol', note: 'no anomalies' },
      { ts: '16:00', kind: 'patrol', note: 'no anomalies' },
      { ts: '15:46', kind: 'merge',  note: 'merged PR #141 (qa-2026-0511-c)' },
    ],
  },
  calendar: {
    activity: 'paused · auth', activityTone: 'red',
    sessions24h: 0, spend7d: 0.40, lastRun: '4h 12m ago', queueDepth: 6,
    primaryModel: 'claude-haiku-4-5', fallback: [],
    schedule: 'paused · auth', ceiling: 2.00, autosend: true,
    prompt: `You are the Calendar Butler. Mirror the household's calendar(s) faithfully. Detect conflicts before they happen. Compose a brief two-line summary each morning for the Overview. Never invent meetings; never auto-decline.`,
    promptVersion: 3, promptUpdated: '02 Apr · 11:30',
    tools: [
      { name: 'google.calendar.read',  desc: 'read events from the Lim Residence calendar', allowed: true,  scope: '6 calendars' },
      { name: 'google.calendar.write', desc: 'create/edit events',                          allowed: false, scope: '—' },
      { name: 'memory.write',          desc: 'persist events to mid-term',                  allowed: true,  scope: 'calendar.events' },
    ],
    memory: { read: ['short','mid'], write: ['mid'], namespace: 'calendar.events', drops: 4 },
    activity24h: [4,3,2,1,0,0,0,1,3,5,6,5,4,5,4,0,0,0,0,0,0,0,0,0],
    recentRuns: [
      { ts: '12:30', kind: 'error',  note: 'token refresh failed · 401 from oauth.google.com' },
      { ts: '12:30', kind: 'pause',  note: 'auto-paused after 3 consecutive auth failures' },
      { ts: '12:14', kind: 'mirror', note: '14 events synced · 0 conflicts' },
      { ts: '11:46', kind: 'mirror', note: '14 events synced · 0 conflicts' },
    ],
  },
};

function ButlerListItem({ b, selected, onSelect }) {
  const detail = BUTLER_DETAIL[b.name] || {};
  const tone = detail.activityTone;
  const color = tone === 'red' ? Cs.red : tone === 'amber' ? Cs.amber : tone === 'green' ? Cs.green : Cs.dim;
  const tint = tone === 'red' ? ATTN_BG.red : tone === 'amber' ? ATTN_BG.amber : null;
  return (
    <button
      onClick={() => onSelect(b.name)}
      style={{
        all: 'unset', cursor: 'pointer', display: 'block', width: '100%',
        padding: '12px 14px',
        background: selected ? 'oklch(1 0 0 / 0.04)' : tint || 'transparent',
        borderLeft: `2px solid ${selected ? Cs.fg : tone ? color : 'transparent'}`,
        borderBottom: `1px solid ${Cs.borderSoft}`,
      }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <window.ButlerMark name={b.name} size={20} tone={b.enabled ? 'fill' : 'neutral'} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13.5, color: Cs.fg, textTransform: 'capitalize' }}>{b.name}</div>
          <Mono color={color} size={9} track="0.10em">● {detail.activity || 'idle'}</Mono>
        </div>
      </div>
      <div style={{ marginTop: 6, display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <Mono color={Cs.dim} size={9} upper={false} track="0.04em">{b.model}</Mono>
        <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: Cs.mfg }}>
          {detail.sessions24h ?? '—'} sess
        </span>
      </div>
    </button>
  );
}

function FallbackChain({ primary, fallback }) {
  const chain = [primary, ...fallback];
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      {chain.map((m, i) => (
        <React.Fragment key={m + i}>
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: 11,
            padding: '4px 8px', border: `1px solid ${i === 0 ? Cs.borderStrong : Cs.border}`,
            borderRadius: 2,
            color: i === 0 ? Cs.fg : Cs.mfg,
            background: i === 0 ? 'oklch(1 0 0 / 0.03)' : 'transparent',
          }}>
            {i === 0 && <Mono color={Cs.green} size={8.5} track="0.10em">primary · </Mono>}
            {i > 0 && <Mono color={Cs.dim} size={8.5} track="0.10em">fb{i} · </Mono>}
            {m}
          </span>
          {i < chain.length - 1 && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: Cs.dim }}>→</span>}
        </React.Fragment>
      ))}
      <a style={{ ...linkS, fontSize: 11, marginLeft: 6 }}>+ add fallback</a>
    </div>
  );
}

function ToolRow({ t, last }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '160px 1fr 1fr 40px',
      gap: 14, padding: '10px 0', alignItems: 'center',
      borderBottom: last ? 'none' : `1px solid ${Cs.borderSoft}`,
    }}>
      <Mono color={Cs.fg} size={11} upper={false} track="0.02em">{t.name}</Mono>
      <span style={{ fontSize: 12, color: Cs.mfg }}>{t.desc}</span>
      <Mono color={t.allowed ? Cs.mfg : Cs.dim} size={10} upper={false}>{t.scope}</Mono>
      <span style={{ display: 'flex', justifyContent: 'flex-end' }}><Toggle on={t.allowed} /></span>
    </div>
  );
}

function StripeChart({ data, color }) {
  return (
    <div style={{ display: 'flex', gap: 2, height: 26 }}>
      {data.map((v, i) => {
        const on = Math.min(1, v / 6);
        const filled = v === 0
          ? 'oklch(1 0 0 / 0.05)'
          : `oklch(0.985 0 0 / ${0.18 + on * 0.55})`;
        return (
          <div key={i} style={{
            flex: 1, background: filled, borderRadius: 1,
            borderBottom: v > 0 ? `2px solid ${color || Cs.fg}` : 'none',
            opacity: v > 0 ? 0.9 : 1,
          }} title={`${String(i).padStart(2,'0')}:00 · ${v} sessions`} />
        );
      })}
    </div>
  );
}

function ButlersExpanded() {
  const [selectedName, setSelectedName] = React.useState('qa');
  const b = BUTLERS.find((x) => x.name === selectedName);
  const d = BUTLER_DETAIL[selectedName] || {};
  const attn = d.activityTone === 'red';

  return (
    <div style={{ height: '100%', background: Cs.bg, color: Cs.fg, display: 'flex', fontFamily: 'var(--font-sans)' }}>
      <FakeRail />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <ExpandHeader
          eyebrow="settings · §2 · butlers"
          title="The staff in full"
          sub={`${BUTLERS.length} butlers · 1 paused · refreshes every 5s`}
          status={[
            <Pill key="ok" tone="ok">7 staffed</Pill>,
            <Pill key="a"  tone="amber">1 paused</Pill>,
          ]}
        />

        {/* Body — list + detail */}
        <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '280px 1fr', minHeight: 0 }}>
          {/* List */}
          <div style={{ borderRight: `1px solid ${Cs.border}`, overflow: 'auto' }}>
            <div style={{ padding: '14px 16px 10px', borderBottom: `1px solid ${Cs.border}` }}>
              <Mono color={Cs.dim} size={9} track="0.14em">all 8 · sorted by activity</Mono>
            </div>
            {BUTLERS.map((bb) => (
              <ButlerListItem key={bb.name} b={bb} selected={bb.name === selectedName} onSelect={setSelectedName} />
            ))}
            <div style={{ padding: '14px 16px' }}>
              <a style={{ ...linkS, fontSize: 11 }}>+ commission new butler</a>
            </div>
          </div>

          {/* Detail */}
          <div style={{ overflow: 'auto', background: attn ? ATTN_BG.red : 'transparent', position: 'relative' }}>
            {attn && <span style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 2, background: Cs.red, zIndex: 1 }} />}

            {/* Hero */}
            <div style={{
              padding: '24px 32px 18px', borderBottom: `1px solid ${Cs.border}`,
              display: 'grid', gridTemplateColumns: '64px 1fr auto', gap: 20, alignItems: 'center',
            }}>
              <window.ButlerMark name={b.name} size={56} tone="fill" />
              <div>
                <Eyebrow>{b.name} · port :847{b.name.charCodeAt(0) % 10}</Eyebrow>
                <h2 style={{
                  margin: '6px 0 4px', fontSize: 32, fontWeight: 500,
                  letterSpacing: '-0.025em', textTransform: 'capitalize',
                }}>{b.name}</h2>
                <Mono color={d.activityTone === 'red' ? Cs.red : d.activityTone === 'green' ? Cs.green : Cs.mfg} size={11} track="0.14em">● {d.activity}</Mono>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'flex-end' }}>
                <div style={{ display: 'flex', gap: 6 }}>
                  <Toggle on={b.enabled} label={b.enabled ? 'enabled' : 'disabled'} />
                </div>
                <a style={{
                  fontFamily: 'var(--font-mono)', fontSize: 10.5, color: Cs.red,
                  textDecoration: 'underline', textDecorationColor: Cs.red,
                  textUnderlineOffset: 4, letterSpacing: '0.04em', cursor: 'pointer',
                }}>kill switch · 30s grace →</a>
              </div>
            </div>

            {/* KPI strip */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', borderBottom: `1px solid ${Cs.border}` }}>
              {[
                { l: 'sessions · 24h', v: d.sessions24h, sub: 'avg 6/h' },
                { l: 'spend · 7d',     v: '$' + (d.spend7d || 0).toFixed(2), sub: 'mtd cap $8/d' },
                { l: 'last run',       v: d.lastRun, sub: d.queueDepth ? `${d.queueDepth} queued` : 'queue empty' },
                { l: 'failures · 7d',  v: 2, sub: '0.8% of runs' },
                { l: 'prompt · v',     v: d.promptVersion, sub: d.promptUpdated },
              ].map((k, i) => (
                <div key={i} style={{
                  padding: '14px 18px', borderRight: i < 4 ? `1px solid ${Cs.border}` : 'none',
                }}>
                  <Mono color={Cs.mfg} size={9} track="0.14em">{k.l}</Mono>
                  <div className="tnum" style={{
                    marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 18,
                    fontWeight: 500, color: Cs.fg, letterSpacing: '-0.02em',
                  }}>{k.v}</div>
                  <Mono color={Cs.dim} size={9} upper={false} track="0.04em">{k.sub}</Mono>
                </div>
              ))}
            </div>

            {/* Section 1 — Identity & routing */}
            <Section n={1} title="Identity &amp; routing" hint="model, fallback chain, schedule, ceilings">
              <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 32 }}>
                <div>
                  <Mono color={Cs.mfg} size={9} track="0.14em">model · fallback chain</Mono>
                  <div style={{ marginTop: 8 }}>
                    <FallbackChain primary={d.primaryModel} fallback={d.fallback} />
                  </div>
                  <div style={{
                    marginTop: 12, fontFamily: 'var(--font-serif)', fontSize: 12.5,
                    color: Cs.dim, fontStyle: 'italic', lineHeight: 1.55, maxWidth: '52ch',
                  }}>
                    On primary failure, the runtime tries each fallback in order with a 2s
                    timeout. After three exhausted attempts the butler pauses and an
                    approval is opened.
                  </div>
                </div>
                <div style={{ display: 'grid', gap: 0 }}>
                  <ConfigLineMini label="Schedule"   value={<EditValue mono={false}>{d.schedule}</EditValue>} />
                  <ConfigLineMini label="$/day ceiling" value={<EditValue>${(d.ceiling || 0).toFixed(2)}</EditValue>} />
                  <ConfigLineMini label="Approvals"  value={<EditValue mono={false}>{d.autosend ? 'auto · skip when low-stakes' : 'ask · all requests'}</EditValue>} />
                  <ConfigLineMini label="Timeout"    value={<EditValue>30s</EditValue>} />
                  <ConfigLineMini label="Concurrency" value={<EditValue>1</EditValue>} />
                </div>
              </div>
            </Section>

            {/* Section 2 — System prompt */}
            <Section
              n={2}
              title="System prompt"
              hint={`version ${d.promptVersion} · updated ${d.promptUpdated}`}
              right={<div style={{ display: 'flex', gap: 12 }}>
                <a style={{ ...linkS, fontSize: 11 }}>history · 7 versions →</a>
                <a style={{ ...linkS, fontSize: 11 }}>diff vs v{d.promptVersion - 1} →</a>
              </div>}
            >
              <div style={{
                padding: '14px 18px',
                border: `1px solid ${Cs.border}`,
                background: 'oklch(1 0 0 / 0.02)',
                fontFamily: 'var(--font-serif)', fontSize: 14,
                color: Cs.fg, lineHeight: 1.65, maxWidth: '72ch',
              }}>
                {d.prompt}
              </div>
              <div style={{
                marginTop: 10, display: 'flex', alignItems: 'center', gap: 14,
                fontFamily: 'var(--font-mono)', fontSize: 10, color: Cs.dim,
              }}>
                <span>tokens · 142</span>
                <span>·</span>
                <span>last edit · Tze</span>
                <span style={{ flex: 1 }} />
                <a style={{ ...linkS, fontSize: 11 }}>edit prompt →</a>
                <a style={{ ...linkS, fontSize: 11 }}>test against fixture set →</a>
              </div>
            </Section>

            {/* Section 3 — Tools & integrations */}
            <Section
              n={3}
              title="Tools &amp; integrations"
              hint={`${(d.tools || []).filter((t) => t.allowed).length}/${(d.tools || []).length} allowed · expand a row to scope further`}
              right={<a style={{ ...linkS, fontSize: 11 }}>+ grant tool →</a>}
            >
              <div style={{
                display: 'grid', gridTemplateColumns: '160px 1fr 1fr 40px',
                gap: 14, padding: '6px 0',
                borderBottom: `1px solid ${Cs.border}`,
              }}>
                <Mono>tool</Mono><Mono>description</Mono><Mono>scope</Mono><Mono>on</Mono>
              </div>
              {(d.tools || []).map((t, i, arr) => (
                <ToolRow key={t.name} t={t} last={i === arr.length - 1} />
              ))}
            </Section>

            {/* Section 4 — Memory access */}
            <Section
              n={4}
              title="Memory access"
              hint="which tiers this butler may read, write, and which namespace it owns"
            >
              <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: 32 }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', border: `1px solid ${Cs.border}` }}>
                  {['short','mid','long'].map((t, i) => {
                    const r = d.memory.read.includes(t);
                    const w = d.memory.write.includes(t);
                    return (
                      <div key={t} style={{
                        padding: '14px 16px',
                        borderRight: i < 2 ? `1px solid ${Cs.border}` : 'none',
                      }}>
                        <Mono color={Cs.dim} size={9} track="0.14em">{t}-term</Mono>
                        <div style={{ marginTop: 8, display: 'flex', gap: 6 }}>
                          <span style={{
                            display: 'inline-flex', alignItems: 'center', gap: 4,
                            fontFamily: 'var(--font-mono)', fontSize: 10,
                            color: r ? Cs.green : Cs.dim,
                            border: `1px solid ${r ? Cs.green : Cs.border}`,
                            padding: '1px 6px', borderRadius: 2, letterSpacing: '0.04em',
                          }}>{r ? '●' : '○'} read</span>
                          <span style={{
                            display: 'inline-flex', alignItems: 'center', gap: 4,
                            fontFamily: 'var(--font-mono)', fontSize: 10,
                            color: w ? Cs.green : Cs.dim,
                            border: `1px solid ${w ? Cs.green : Cs.border}`,
                            padding: '1px 6px', borderRadius: 2, letterSpacing: '0.04em',
                          }}>{w ? '●' : '○'} write</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div style={{ display: 'grid', gap: 0 }}>
                  <ConfigLineMini label="Namespace · owned" value={<EditValue>{d.memory.namespace}</EditValue>} />
                  <ConfigLineMini label="Drops · 7d"        value={<span style={{ color: d.memory.drops > 0 ? Cs.amber : Cs.dim }}>{d.memory.drops}</span>} />
                  <ConfigLineMini label="Embed model"       value={<EditValue mono={false}>text-embedding-3-large</EditValue>} />
                </div>
              </div>
            </Section>

            {/* Section 5 — Activity */}
            <Section
              n={5}
              title="Activity · last 24 hours"
              hint="hour-buckets · click a column to inspect"
              right={<a style={{ ...linkS, fontSize: 11 }}>open audit log →</a>}
            >
              <StripeChart data={d.activity24h || Array(24).fill(0)} />
              <div style={{
                marginTop: 8, display: 'flex', justifyContent: 'space-between',
                fontFamily: 'var(--font-mono)', fontSize: 9, color: Cs.dim, letterSpacing: '0.10em',
              }}>
                <span>00</span><span>06</span><span>12</span><span>18</span><span>now</span>
              </div>

              <div style={{ marginTop: 18 }}>
                <Mono color={Cs.mfg} size={9} track="0.14em">recent runs</Mono>
                <div style={{ marginTop: 8 }}>
                  {(d.recentRuns || []).map((r, i, arr) => {
                    const kindColor = { error: Cs.red, pause: Cs.red, merge: Cs.green, case: Cs.amber, patrol: Cs.fg, mirror: Cs.fg }[r.kind] || Cs.fg;
                    return (
                      <div key={i} style={{
                        display: 'grid', gridTemplateColumns: '54px 70px 1fr',
                        gap: 14, padding: '7px 0', alignItems: 'baseline',
                        borderBottom: i < arr.length - 1 ? `1px solid ${Cs.borderSoft}` : 'none',
                        fontFamily: 'var(--font-mono)', fontSize: 11,
                      }}>
                        <span style={{ color: Cs.dim }}>{r.ts}</span>
                        <span style={{ color: kindColor, letterSpacing: '0.04em' }}>{r.kind}</span>
                        <span style={{ color: Cs.fg }}>{r.note}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </Section>

            <ApiWireFooter
              note="Every change is appended to the audit log before commit."
              endpoints={[
                { method: 'GET',  path: '/api/butlers' },
                { method: 'GET',  path: `/api/butlers/${selectedName}` },
                { method: 'PUT',  path: `/api/butlers/${selectedName}/config` },
                { method: 'PUT',  path: `/api/butlers/${selectedName}/prompt` },
                { method: 'GET',  path: `/api/butlers/${selectedName}/prompt/history` },
                { method: 'POST', path: `/api/butlers/${selectedName}/pause`, hover: '30s grace' },
                { method: 'POST', path: `/api/butlers/${selectedName}/resume` },
                { method: 'GET',  path: `/api/butlers/${selectedName}/activity?since=24h` },
                { method: 'WS',   path: `/api/butlers/${selectedName}/stream` },
              ]}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// 2 · Spend Dashboard
// ═══════════════════════════════════════════════════════════════════

function ForecastChart() {
  // 30 days, day 16 is "today". Actual data 0..15, projected 16..29.
  const actual = [18, 22, 16, 24, 28, 22, 26, 38, 32, 28, 34, 30, 36, 42, 38, 44];
  const proj   = [42, 40, 44, 38, 42, 46, 40, 44, 48, 42, 46, 50, 44, 48];
  const all = [...actual, ...proj];
  const ceiling = 60;
  const max = Math.max(ceiling, ...all);
  const W = 1000, H = 180;
  const stepX = W / (all.length - 1);
  const pt = (i, v) => `${i * stepX},${H - (v / max) * (H - 16) - 4}`;
  const actualPts = actual.map((v, i) => pt(i, v)).join(' ');
  const projPts = [
    pt(actual.length - 1, actual[actual.length - 1]),
    ...proj.map((v, i) => pt(actual.length + i, v)),
  ].join(' ');
  const todayX = (actual.length - 1) * stepX;
  const ceilY = H - (ceiling / max) * (H - 16) - 4;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 200 }}>
      {/* ceiling line */}
      <line x1="0" y1={ceilY} x2={W} y2={ceilY} stroke={Cs.amber} strokeWidth="1" strokeDasharray="4 4" opacity="0.6" />
      <text x={W - 10} y={ceilY - 6} textAnchor="end" fontFamily="var(--font-mono)" fontSize="9" fill={Cs.amber} letterSpacing="0.10em">CEILING · $1,200</text>
      {/* projected (dashed) */}
      <polyline points={projPts} fill="none" stroke={Cs.fg} strokeWidth="1.5" strokeDasharray="3 4" opacity="0.6" />
      {/* actual (solid) */}
      <polyline points={actualPts} fill="none" stroke={Cs.fg} strokeWidth="1.75" />
      {/* fill under actual */}
      <polygon points={`0,${H} ${actualPts} ${todayX},${H}`} fill={Cs.fg} opacity="0.06" />
      {/* today marker */}
      <line x1={todayX} y1="0" x2={todayX} y2={H} stroke={Cs.fg} strokeWidth="1" opacity="0.4" />
      <text x={todayX + 6} y="14" fontFamily="var(--font-mono)" fontSize="9" fill={Cs.fg} letterSpacing="0.10em">TODAY · DAY 16</text>
      {/* projected landing */}
      <text x={W - 10} y={H - 8} textAnchor="end" fontFamily="var(--font-mono)" fontSize="9" fill={Cs.mfg} letterSpacing="0.06em">PROJECTED LAND · $1,180</text>
    </svg>
  );
}

function BreakdownBars({ rows, fmt = (v) => '$' + v.toFixed(2) }) {
  const max = Math.max(...rows.map((r) => r.value));
  return (
    <div style={{ display: 'grid', gap: 6 }}>
      {rows.map((r) => (
        <div key={r.label} style={{
          display: 'grid', gridTemplateColumns: '14px 100px 1fr 60px',
          gap: 8, alignItems: 'center',
        }}>
          {r.mark || <span />}
          <Mono color={Cs.mfg} size={10} upper={false} track="0.02em">{r.label}</Mono>
          <div style={{ height: 6, background: 'oklch(1 0 0 / 0.04)', borderRadius: 1 }}>
            <div style={{
              width: `${(r.value / max) * 100}%`, height: '100%',
              background: r.color || Cs.fg, opacity: 0.7, borderRadius: 1,
            }} />
          </div>
          <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: Cs.fg, textAlign: 'right' }}>{fmt(r.value)}</span>
        </div>
      ))}
    </div>
  );
}

function RuleRow({ rule, last }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '20px 1fr 100px 80px',
      gap: 12, padding: '12px 0', alignItems: 'baseline',
      borderBottom: last ? 'none' : `1px solid ${Cs.borderSoft}`,
    }}>
      <Mono color={Cs.dim} size={10} track="0.04em" upper={false}>if</Mono>
      <div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: Cs.fg, lineHeight: 1.5 }}>
          <span style={{ color: Cs.mfg }}>{rule.subject}</span>
          <span style={{ color: Cs.fg }}> {rule.cond} </span>
          <span style={{ color: Cs.amber }}>{rule.threshold}</span>
          <span style={{ color: Cs.mfg }}>, route to </span>
          <span style={{ color: Cs.fg }}>{rule.route}</span>
        </div>
        <Mono color={Cs.dim} size={9.5} upper={false} track="0.04em">{rule.note}</Mono>
      </div>
      <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: Cs.fg }}>{rule.savings7d}</span>
      <Toggle on={rule.on} label={rule.on ? 'on' : 'off'} />
    </div>
  );
}

function SpendDashboard() {
  const period = '30d';
  const byButler = [
    { label: 'qa',           value: 184.60, color: Cs.fg, mark: <window.ButlerMark name="qa"           size={10} tone="neutral" /> },
    { label: 'relationship', value: 120.40, color: Cs.fg, mark: <window.ButlerMark name="relationship" size={10} tone="neutral" /> },
    { label: 'chronicler',   value:  84.10, color: Cs.fg, mark: <window.ButlerMark name="chronicler"   size={10} tone="neutral" /> },
    { label: 'health',       value:  42.20, color: Cs.fg, mark: <window.ButlerMark name="health"       size={10} tone="neutral" /> },
    { label: 'household',    value:  31.80, color: Cs.fg, mark: <window.ButlerMark name="household"    size={10} tone="neutral" /> },
    { label: 'education',    value:  22.40, color: Cs.fg, mark: <window.ButlerMark name="education"    size={10} tone="neutral" /> },
    { label: 'memory',       value:  16.70, color: Cs.fg, mark: <window.ButlerMark name="memory"       size={10} tone="neutral" /> },
    { label: 'calendar',     value:  10.20, color: Cs.fg, mark: <window.ButlerMark name="calendar"     size={10} tone="neutral" /> },
  ];
  const byModel = [
    { label: 'sonnet-4-5',    value: 268.70 },
    { label: 'haiku-4-5',     value: 332.10 },
    { label: 'opus-4-5',      value:   3.80 },
    { label: 'whisper-1',     value:   2.10 },
    { label: 'embedding-3-l', value:   6.40 },
    { label: 'gpt-5.1-mini',  value:   0.40 },
  ];
  const byFeature = [
    { label: 'qa patrols',     value: 184.60 },
    { label: 'briefings',      value: 142.80 },
    { label: 'consolidations', value:  98.40 },
    { label: 'on-demand',      value:  86.20 },
    { label: 'chronicling',    value:  74.80 },
    { label: 'approvals',      value:  25.60 },
  ];
  const rules = [
    { subject: 'completion ≤ 500 tokens', cond: '&&', threshold: 'temp ≤ 0.3', route: 'haiku-4-5', note: 'Skip sonnet for routine answers.',           savings7d: '$48.20', on: true },
    { subject: 'butler · qa',             cond: 'patrol confidence ≥', threshold: '0.8',          route: 'haiku-4-5', note: 'Only escalate uncertain cases.',              savings7d: '$32.40', on: true },
    { subject: 'briefing · cached',       cond: 'age ≤',               threshold: '5m',           route: 'cache',     note: 'Don\'t recompose on every refresh.',          savings7d: '$18.60', on: true },
    { subject: 'butler · chronicler',     cond: 'word count ≥',        threshold: '800',          route: 'sonnet-4-5', note: 'Long-form needs the better writer.',         savings7d: '—',     on: true },
    { subject: 'monthly spend ≥',         cond: '',                    threshold: '90% ceiling',  route: 'haiku-4-5 only', note: 'Auto-downshift near ceiling.',           savings7d: '—',     on: false },
  ];

  return (
    <div style={{ height: '100%', background: Cs.bg, color: Cs.fg, display: 'flex', fontFamily: 'var(--font-sans)' }}>
      <FakeRail />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <ExpandHeader
          eyebrow="settings · §2 · spend"
          title="What it costs to staff a household"
          sub="mtd $612.40 · cap $1,200 · projected $1,180"
          status={[
            <Pill key="ok" tone="ok">51% of cap</Pill>,
            <Pill key="proj">projected under · 98%</Pill>,
          ]}
        />

        {/* Period tabs */}
        <div style={{
          display: 'flex', gap: 0, borderBottom: `1px solid ${Cs.border}`,
          padding: '0 28px',
        }}>
          {['24h', '7d', '30d · mtd', '90d', 'ytd', 'all time'].map((t) => {
            const active = t === '30d · mtd';
            return (
              <div key={t} style={{
                padding: '12px 18px',
                fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.06em',
                color: active ? Cs.fg : Cs.mfg,
                borderBottom: active ? `2px solid ${Cs.fg}` : '2px solid transparent',
                marginBottom: -1, cursor: 'pointer',
              }}>{t}</div>
            );
          })}
          <span style={{ flex: 1 }} />
          <div style={{ padding: '12px 0', display: 'flex', alignItems: 'center', gap: 14 }}>
            <Mono color={Cs.dim} size={9.5}>refresh · live</Mono>
            <a style={{ ...linkS, fontSize: 11 }}>export CSV →</a>
          </div>
        </div>

        {/* Headline + forecast */}
        <Section n={1} title="Burn-rate forecast" hint="actual to today · projected to month end · ceiling line at $1,200">
          <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: 32, alignItems: 'flex-start' }}>
            <div>
              <ForecastChart />
            </div>
            <div style={{ display: 'grid', gap: 18 }}>
              <div>
                <Mono color={Cs.dim} size={9} track="0.14em">month-to-date</Mono>
                <div style={{ display: 'baseline', marginTop: 8 }}>
                  <span className="tnum" style={{
                    fontFamily: 'var(--font-mono)', fontSize: 38, fontWeight: 500,
                    color: Cs.fg, letterSpacing: '-0.03em',
                  }}>$612.40</span>
                  <span style={{ marginLeft: 10, color: Cs.dim, fontFamily: 'var(--font-mono)', fontSize: 12 }}>/ $1,200 mtd</span>
                </div>
                <CapacityBar pct={51} />
                <Mono color={Cs.dim} size={9.5} upper={false} track="0.04em" style={{ marginTop: 6 }}>day 16 of 30 · 53% of month elapsed</Mono>
              </div>
              <div style={{
                padding: '12px 14px', borderLeft: `2px solid ${Cs.green}`,
                background: 'oklch(0.79 0.195 148 / 0.06)',
              }}>
                <Mono color={Cs.green} size={9} track="0.14em">forecast</Mono>
                <div style={{ marginTop: 6, fontFamily: 'var(--font-serif)', fontSize: 14, color: Cs.fg, lineHeight: 1.55 }}>
                  At current trajectory the month lands at <span style={{ color: Cs.fg, fontFamily: 'var(--font-mono)' }}>$1,180</span> — twenty under ceiling. No automated downshift will trigger.
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div>
                  <Mono color={Cs.dim} size={9} track="0.14em">avg · day</Mono>
                  <div className="tnum" style={{ marginTop: 4, fontFamily: 'var(--font-mono)', fontSize: 18, color: Cs.fg, fontWeight: 500 }}>$38.27</div>
                </div>
                <div>
                  <Mono color={Cs.dim} size={9} track="0.14em">peak · day</Mono>
                  <div className="tnum" style={{ marginTop: 4, fontFamily: 'var(--font-mono)', fontSize: 18, color: Cs.fg, fontWeight: 500 }}>$48.20</div>
                </div>
              </div>
            </div>
          </div>
        </Section>

        {/* Breakdown 3-up */}
        <Section n={2} title="Where the money went" hint="three slices of the same $612.40 · 30 days">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 32 }}>
            <div>
              <Mono color={Cs.mfg} size={9} track="0.14em">by butler · 8</Mono>
              <div style={{ marginTop: 10 }}><BreakdownBars rows={byButler} /></div>
            </div>
            <div>
              <Mono color={Cs.mfg} size={9} track="0.14em">by model · 6 active</Mono>
              <div style={{ marginTop: 10 }}><BreakdownBars rows={byModel} /></div>
            </div>
            <div>
              <Mono color={Cs.mfg} size={9} track="0.14em">by feature · 6</Mono>
              <div style={{ marginTop: 10 }}><BreakdownBars rows={byFeature} /></div>
            </div>
          </div>
        </Section>

        {/* Cost-routing rules */}
        <Section
          n={3}
          title="Cost-routing rules"
          hint="declarative routing · evaluated before each LLM call · top-to-bottom"
          right={<a style={{ ...linkS, fontSize: 11 }}>+ add rule →</a>}
        >
          <div style={{
            display: 'grid', gridTemplateColumns: '20px 1fr 100px 80px', gap: 12,
            padding: '6px 0', borderBottom: `1px solid ${Cs.border}`,
          }}>
            <Mono>·</Mono><Mono>rule</Mono><Mono>saved · 7d</Mono><Mono>on</Mono>
          </div>
          {rules.map((r, i) => <RuleRow key={i} rule={r} last={i === rules.length - 1} />)}
        </Section>

        {/* Alerts & ceilings */}
        <Section n={4} title="Alerts &amp; ceilings" hint="what trips a warning, what halts spending">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 32 }}>
            <div>
              <Mono color={Cs.mfg} size={9} track="0.14em">ceilings</Mono>
              <div style={{ marginTop: 6 }}>
                <ConfigLine label="Monthly system ceiling"  helper="All butlers pause if exceeded." value={<EditValue>$1,200</EditValue>} />
                <ConfigLine label="Per-butler ceilings"     helper="Configured under §2 Butlers."   value={<a style={linkS}>see Butlers ↗</a>} mono={false} />
                <ConfigLine label="Action on breach"        helper="What happens at hard ceiling."  value={<EditValue mono={false}>pause &amp; notify</EditValue>} last />
              </div>
            </div>
            <div>
              <Mono color={Cs.mfg} size={9} track="0.14em">alerts</Mono>
              <div style={{ marginTop: 6 }}>
                <ConfigLine label="Daily soft warning"      helper="Notification only · no pause." value={<EditValue>$45</EditValue>} />
                <ConfigLine label="Anomaly detection"       helper="Pause if 3σ over 24h baseline." value={<Toggle on label="on" />} mono={false} />
                <ConfigLine label="Forecast warning"        helper="When projection crosses ceiling." value={<EditValue mono={false}>3 days ahead</EditValue>} last />
              </div>
            </div>
          </div>
        </Section>

        <ApiWireFooter
          note="All spend events stream over WS so the chart never lies about lag."
          endpoints={[
            { method: 'GET',  path: '/api/spend?period=30d' },
            { method: 'GET',  path: '/api/spend/breakdown?by=butler,model,feature' },
            { method: 'GET',  path: '/api/spend/forecast' },
            { method: 'GET',  path: '/api/spend/rules' },
            { method: 'POST', path: '/api/spend/rules' },
            { method: 'PUT',  path: '/api/spend/rules/{id}' },
            { method: 'PUT',  path: '/api/spend/ceiling' },
            { method: 'WS',   path: '/api/spend/stream' },
          ]}
        />
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// 3 · Memory Ops Center
// ═══════════════════════════════════════════════════════════════════

function TierFlowCell({ label, used, cap, kindCounts, drops, color }) {
  const pct = Math.round((used / cap) * 100);
  return (
    <div style={{
      flex: 1, padding: '18px 20px',
      border: `1px solid ${Cs.border}`, position: 'relative',
    }}>
      <Mono color={Cs.mfg} size={9} track="0.14em">{label}-term</Mono>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 8 }}>
        <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 24, fontWeight: 500, color: Cs.fg, letterSpacing: '-0.025em' }}>{used.toLocaleString()}</span>
        <Mono color={Cs.dim} size={11} upper={false} track="0.04em">/ {cap.toLocaleString()}</Mono>
      </div>
      <div style={{ marginTop: 8 }}><CapacityBar pct={pct} color={color || Cs.fg} /></div>
      <div style={{ marginTop: 4 }}><Mono color={Cs.dim} size={9.5} upper={false} track="0.04em">{pct}% capacity</Mono></div>
      <div style={{ marginTop: 14, display: 'grid', gap: 4 }}>
        {kindCounts.map((k) => (
          <div key={k.kind} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
            <Mono color={Cs.mfg} size={10} upper={false} track="0.02em">{k.kind}</Mono>
            <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: Cs.fg }}>{k.n.toLocaleString()}</span>
          </div>
        ))}
      </div>
      <div style={{
        marginTop: 14, paddingTop: 10,
        borderTop: `1px solid ${Cs.borderSoft}`,
        display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
      }}>
        <Mono color={Cs.dim} size={9} track="0.14em">drops · 7d</Mono>
        <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: drops > 50 ? Cs.amber : Cs.mfg }}>{drops}</span>
      </div>
    </div>
  );
}

function FlowArrow({ label, sub }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, padding: '0 8px' }}>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 22, color: Cs.dim }}>→</span>
      <Mono color={Cs.fg} size={10} track="0.10em">{label}</Mono>
      <Mono color={Cs.dim} size={9} upper={false} track="0.04em">{sub}</Mono>
    </div>
  );
}

function RetentionRow({ r, last }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '130px 1fr 90px 90px 110px 80px',
      gap: 14, padding: '12px 0', alignItems: 'baseline',
      borderBottom: last ? 'none' : `1px solid ${Cs.borderSoft}`,
    }}>
      <Mono color={Cs.fg} size={11} upper={false} track="0.02em">{r.kind}</Mono>
      <span style={{ fontSize: 12, color: Cs.mfg }}>{r.desc}</span>
      <EditValue mono>{r.ttl}</EditValue>
      <EditValue mono>{r.maxCount}</EditValue>
      <EditValue mono={false} size={11}>{r.dropPolicy}</EditValue>
      <EditValue mono>{r.promote}</EditValue>
    </div>
  );
}

function MemoryExpanded() {
  const compaction = [
    { ts: '14 May 02:04', moved: 423, drops: 12, dur: '2m 18s', note: 'nightly · default' },
    { ts: '13 May 02:03', moved: 391, drops:  8, dur: '2m 02s', note: 'nightly · default' },
    { ts: '12 May 14:48', moved:  84, drops:  0, dur:    '38s', note: 'on-demand · post-trip' },
    { ts: '12 May 02:04', moved: 412, drops: 14, dur: '2m 22s', note: 'nightly · default' },
    { ts: '11 May 02:03', moved: 380, drops:  6, dur: '1m 58s', note: 'nightly · default' },
  ];
  const retentions = [
    { kind: 'event',       desc: 'discrete things that happened',     ttl: '∞',    maxCount: '20,000', dropPolicy: 'oldest', promote: 'always' },
    { kind: 'fact',        desc: 'reusable assertions about entities',ttl: '∞',    maxCount: '8,000',  dropPolicy: 'low-recall', promote: 'always' },
    { kind: 'preference',  desc: 'expressed taste / habits',          ttl: '∞',    maxCount: '500',    dropPolicy: 'lowest-confidence', promote: 'always' },
    { kind: 'summary',     desc: 'butler-written précis',             ttl: '180d', maxCount: '500',    dropPolicy: 'oldest', promote: 'manual' },
    { kind: 'transcript',  desc: 'raw transcripts of voice notes',    ttl: '30d',  maxCount: '200',    dropPolicy: 'oldest', promote: 'never · ephemeral' },
    { kind: 'embedding',   desc: 'vector index for retrieval',        ttl: '∞',    maxCount: '—',      dropPolicy: 'mirror parent', promote: 'derived' },
  ];
  const searchResults = [
    { kind: 'fact',      ts: '4d',  score: 0.91, body: 'Mei drinks no caffeine after 14:00 · noted by relationship butler after the Tuesday call' },
    { kind: 'event',     ts: '7d',  score: 0.88, body: 'Anniversary dinner at Burnt Ends · 8 PM Thursday · Mei picked the wine' },
    { kind: 'preference',ts: '21d', score: 0.84, body: 'Prefers mineral water · still · over sparkling' },
    { kind: 'summary',   ts: '14d', score: 0.79, body: 'May week 2 · chronicler · quiet week, two dinners, one trip to the clinic' },
  ];

  return (
    <div style={{ height: '100%', background: Cs.bg, color: Cs.fg, display: 'flex', fontFamily: 'var(--font-sans)' }}>
      <FakeRail />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <ExpandHeader
          eyebrow="settings · §4 · memory"
          title="What the staff remembers"
          sub="14,231 objects · 50% capacity · nightly compaction 02:00"
          status={[
            <Pill key="ok"  tone="ok">94.2% hit rate · 7d</Pill>,
            <Pill key="dim">compaction in 9h 18m</Pill>,
          ]}
        />

        {/* Tier flow */}
        <Section n={1} title="Tier flow" hint="objects move short → mid → long during compaction · click a kind to filter">
          <div style={{ display: 'flex', alignItems: 'stretch', gap: 0 }}>
            <TierFlowCell label="short" used={312} cap={500} drops={142} kindCounts={[
              { kind: 'event',      n: 184 },
              { kind: 'transcript', n:  46 },
              { kind: 'fact',       n:  62 },
              { kind: 'other',      n:  20 },
            ]} />
            <FlowArrow label="promote" sub="morning · 02:00" />
            <TierFlowCell label="mid" used={5840} cap={8000} drops={23} kindCounts={[
              { kind: 'event',       n: 3120 },
              { kind: 'fact',        n: 1820 },
              { kind: 'preference',  n:  340 },
              { kind: 'summary',     n:  120 },
              { kind: 'other',       n:  440 },
            ]} />
            <FlowArrow label="promote" sub="nightly · vetted" />
            <TierFlowCell label="long" used={8079} cap={20000} drops={4} kindCounts={[
              { kind: 'event',       n: 4620 },
              { kind: 'fact',        n: 2410 },
              { kind: 'preference',  n:  148 },
              { kind: 'summary',     n:  712 },
              { kind: 'other',       n:  189 },
            ]} />
          </div>
          <div style={{ marginTop: 18, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 32 }}>
            <ConfigLineMini label="Drop policy"           value={<EditValue mono={false}>oldest · low-recall</EditValue>} />
            <ConfigLineMini label="Compaction window"     value={<EditValue>02:00 → 04:00</EditValue>} />
            <ConfigLineMini label="Vetting requires LLM"  value={<Toggle on label="sonnet-4-5" />} />
          </div>
        </Section>

        {/* Retention policies */}
        <Section
          n={2}
          title="Retention policies"
          hint="per-kind rules · how long to keep · when to drop · whether to promote"
          right={<a style={{ ...linkS, fontSize: 11 }}>+ add kind →</a>}
        >
          <div style={{
            display: 'grid', gridTemplateColumns: '130px 1fr 90px 90px 110px 80px',
            gap: 14, padding: '6px 0', borderBottom: `1px solid ${Cs.border}`,
          }}>
            <Mono>kind</Mono><Mono>description</Mono><Mono>ttl</Mono><Mono>max count</Mono><Mono>drop · when full</Mono><Mono>promote</Mono>
          </div>
          {retentions.map((r, i) => <RetentionRow key={r.kind} r={r} last={i === retentions.length - 1} />)}
        </Section>

        {/* Compaction log + Memory inspect */}
        <Section n={3} title="Compaction log &amp; memory inspect" hint="recent compactions · search across all tiers">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.2fr', gap: 32 }}>
            <div>
              <Mono color={Cs.mfg} size={9} track="0.14em">recent compactions · last 5</Mono>
              <div style={{ marginTop: 10 }}>
                {compaction.map((c, i) => (
                  <div key={i} style={{
                    display: 'grid', gridTemplateColumns: '100px 70px 60px 60px 1fr',
                    gap: 12, padding: '10px 0', alignItems: 'baseline',
                    fontFamily: 'var(--font-mono)', fontSize: 10.5,
                    borderBottom: i < compaction.length - 1 ? `1px solid ${Cs.borderSoft}` : 'none',
                  }}>
                    <span style={{ color: Cs.dim }}>{c.ts}</span>
                    <span style={{ color: Cs.fg }}>{c.moved} moved</span>
                    <span style={{ color: c.drops > 10 ? Cs.amber : Cs.dim }}>{c.drops} drops</span>
                    <span style={{ color: Cs.mfg }}>{c.dur}</span>
                    <span style={{ color: Cs.dim, fontStyle: 'italic' }}>{c.note}</span>
                  </div>
                ))}
              </div>
            </div>

            <div>
              <Mono color={Cs.mfg} size={9} track="0.14em">inspect · full-text + vector search</Mono>
              <div style={{
                marginTop: 10, padding: '10px 14px',
                border: `1px solid ${Cs.borderStrong}`, borderRadius: 2,
                display: 'flex', alignItems: 'center', gap: 10,
              }}>
                <Mono color={Cs.dim} size={11}>⌕</Mono>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: Cs.fg }}>mei · caffeine</span>
                <span style={{ flex: 1 }} />
                <Mono color={Cs.dim} size={9.5} upper={false} track="0.04em">14,231 indexed · ⌘K</Mono>
              </div>
              <div style={{ marginTop: 8 }}>
                {searchResults.map((r, i) => (
                  <div key={i} style={{
                    display: 'grid', gridTemplateColumns: '90px 50px 1fr 40px',
                    gap: 10, padding: '8px 0', alignItems: 'baseline',
                    borderBottom: i < searchResults.length - 1 ? `1px solid ${Cs.borderSoft}` : 'none',
                  }}>
                    <Mono color={Cs.mfg} size={10} upper={false}>{r.kind} · {r.ts}</Mono>
                    <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: Cs.green }}>{r.score.toFixed(2)}</span>
                    <span style={{ fontFamily: 'var(--font-serif)', fontSize: 13, color: Cs.fg, lineHeight: 1.5 }}>{r.body}</span>
                    <a style={{ ...linkS, fontSize: 10 }}>open →</a>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </Section>

        {/* Embed + danger */}
        <Section n={4} title="Embedding &amp; danger" hint="how memory is indexed · what wipes look like">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 32 }}>
            <div>
              <Mono color={Cs.mfg} size={9} track="0.14em">embedding</Mono>
              <div style={{ marginTop: 6 }}>
                <ConfigLine label="Embed model"        helper="Used for vector index." value={<EditValue>text-embedding-3-large</EditValue>} />
                <ConfigLine label="Dimensions"         value={<EditValue>3,072</EditValue>} />
                <ConfigLine label="Chunk size"         helper="Tokens per indexed chunk." value={<EditValue>320</EditValue>} />
                <ConfigLine label="Recency boost"      helper="Recent memories rank higher." value={<EditValue mono={false}>moderate · half-life 30d</EditValue>} last />
              </div>
            </div>
            <div>
              <Mono color={Cs.mfg} size={9} track="0.14em">danger zone</Mono>
              <div style={{ marginTop: 6 }}>
                <ConfigLine label="Re-embed all"     helper="Re-vectorize against current embed model. ~12 min." value={<a style={{ ...linkS, color: Cs.amber, textDecorationColor: Cs.amber }}>begin →</a>} mono={false} />
                <ConfigLine label="Drop tier · short" helper="Empties short-term only. 7-day cool-down before refill." value={<a style={{ ...linkS, color: Cs.amber, textDecorationColor: Cs.amber }}>drop short-term →</a>} mono={false} />
                <ConfigLine label="Drop tier · mid"   helper="Empties mid-term. Cannot be undone." value={<a style={{ ...linkS, color: Cs.amber, textDecorationColor: Cs.amber }}>drop mid-term →</a>} mono={false} />
                <ConfigLine label="Wipe everything"   helper="Removes all memory across all tiers. Requires phrase." value={<a style={{ ...linkS, color: Cs.red, textDecorationColor: Cs.red }}>requires phrase →</a>} mono={false} last />
              </div>
            </div>
          </div>
        </Section>

        <ApiWireFooter
          note="Memory writes are append-only; compaction is what consolidates."
          endpoints={[
            { method: 'GET',    path: '/api/memory/tiers' },
            { method: 'GET',    path: '/api/memory/policies' },
            { method: 'PUT',    path: '/api/memory/policies/{kind}' },
            { method: 'GET',    path: '/api/memory/compactions?limit=50' },
            { method: 'POST',   path: '/api/memory/compact', hover: 'on demand' },
            { method: 'GET',    path: '/api/memory/search?q=&tier=' },
            { method: 'POST',   path: '/api/memory/reembed' },
            { method: 'DELETE', path: '/api/memory/{tier}', hover: 'destructive · phrase' },
          ]}
        />
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// 4 · Approvals Inbox + Policy
// ═══════════════════════════════════════════════════════════════════

const APPROVALS = [
  {
    id: 'apv-2026-0514-002', butler: 'household',
    title: 'Order Acqua Panna · 12-pack · $42.80',
    ts: '2h 14m ago', expires: '21h 46m',
    why: `Stock down to 2 bottles · last reorder 4 weeks ago · supplier rate unchanged from last cycle.`,
    evidence: [
      'pantry.bottles.water = 2 (threshold: 4)',
      'pantry.bottles.water.last_purchase = 17 Apr · 12 units',
      'supplier.rate.acqua_panna.12pk = $42.80 (no change since 03 Apr)',
    ],
  },
  {
    id: 'apv-2026-0514-001', butler: 'relationship',
    title: 'Draft to Mei · "thank-you for the cocktail"',
    ts: '4h 02m ago', expires: '19h 58m',
    why: `Mei mentioned bringing a cocktail to your father last week — drafted a brief thank-you. Auto-send is disabled for this butler.`,
    evidence: [
      'event.gift_received = "negroni-no-bitter" from Mei · 09 May',
      'sentiment.gift.tone = warm',
      'policy.relationship.autosend = false',
    ],
  },
];

const RECENT_DECISIONS = [
  { ts: '13:42', butler: 'qa',           title: 'merge PR #142 · fix flake in chronicler.summary_test',     decision: 'approved', by: 'Tze',   ms: '12s' },
  { ts: '11:18', butler: 'household',    title: 'reorder coffee · Aerolatte beans · $28.40',                decision: 'approved', by: 'Tze',   ms: '4s' },
  { ts: '09:02', butler: 'qa',           title: 'merge PR #141 · trim verbose log at chronicler.startup',  decision: 'auto',     by: 'policy', ms: '—' },
  { ts: 'Y 21:14', butler: 'relationship', title: 'draft to Dad · "see you Friday"',                        decision: 'edited',   by: 'Tze',   ms: '38s' },
  { ts: 'Y 18:48', butler: 'health',     title: 'set 22:30 wind-down · phone do-not-disturb',               decision: 'denied',   by: 'Tze',   ms: '6s' },
  { ts: 'Y 16:02', butler: 'household',  title: 'pre-order birthday cake · Tiong Bahru bakery',             decision: 'expired',  by: '—',     ms: '24h' },
];

function ApprovalRow({ a }) {
  return (
    <div style={{
      padding: '16px 18px', border: `1px solid ${Cs.amber}`, borderLeft: `2px solid ${Cs.amber}`,
      background: ATTN_BG.amber, marginBottom: 12,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <window.ButlerMark name={a.butler} size={16} tone="fill" />
        <span style={{ fontSize: 14, color: Cs.fg, fontWeight: 500 }}>{a.title}</span>
        <span style={{ flex: 1 }} />
        <Mono color={Cs.amber} size={9.5} track="0.10em">expires {a.expires}</Mono>
      </div>
      <Mono color={Cs.dim} size={9.5} upper={false} track="0.04em" style={{ marginTop: 4 }}>
        {a.butler} · {a.id} · waiting {a.ts}
      </Mono>
      <div style={{
        marginTop: 12, padding: '10px 12px',
        background: 'oklch(1 0 0 / 0.02)', border: `1px solid ${Cs.borderSoft}`,
      }}>
        <Mono color={Cs.mfg} size={9} track="0.14em">why</Mono>
        <p style={{
          margin: '4px 0 0',
          fontFamily: 'var(--font-serif)', fontSize: 13, color: Cs.fg, lineHeight: 1.55,
        }}>{a.why}</p>
        <Mono color={Cs.dim} size={9} track="0.14em" style={{ marginTop: 10 }}>evidence</Mono>
        <div style={{ marginTop: 4, fontFamily: 'var(--font-mono)', fontSize: 10.5, color: Cs.mfg, lineHeight: 1.6 }}>
          {a.evidence.map((e, i) => <div key={i}>· {e}</div>)}
        </div>
      </div>
      <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
        <button style={{
          all: 'unset', cursor: 'pointer',
          padding: '6px 14px', background: Cs.fg, color: Cs.bg,
          fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.06em',
          textTransform: 'uppercase', borderRadius: 2,
        }}>approve</button>
        <button style={{
          all: 'unset', cursor: 'pointer',
          padding: '6px 14px', border: `1px solid ${Cs.borderStrong}`,
          color: Cs.fg, fontFamily: 'var(--font-mono)', fontSize: 11,
          letterSpacing: '0.06em', textTransform: 'uppercase', borderRadius: 2,
        }}>edit &amp; approve</button>
        <button style={{
          all: 'unset', cursor: 'pointer',
          padding: '6px 14px', border: `1px solid ${Cs.borderStrong}`,
          color: Cs.fg, fontFamily: 'var(--font-mono)', fontSize: 11,
          letterSpacing: '0.06em', textTransform: 'uppercase', borderRadius: 2,
        }}>deny</button>
        <span style={{ flex: 1 }} />
        <Mono color={Cs.dim} size={10} upper={false} track="0.04em">defer 4h ↗</Mono>
      </div>
    </div>
  );
}

function QuietHoursStrip() {
  // 24 cells, quiet from 22:00 → 07:00 inclusive
  const hours = Array.from({ length: 24 }, (_, i) => i);
  const isQuiet = (h) => h >= 22 || h < 7;
  return (
    <div>
      <div style={{ display: 'flex', gap: 2, height: 28 }}>
        {hours.map((h) => {
          const quiet = isQuiet(h);
          return (
            <div key={h} style={{
              flex: 1,
              background: quiet ? 'oklch(1 0 0 / 0.10)' : 'oklch(1 0 0 / 0.03)',
              borderRadius: 1,
              borderTop: quiet ? `2px solid ${Cs.fg}` : 'none',
              opacity: quiet ? 0.85 : 0.5,
              cursor: 'pointer',
            }} />
          );
        })}
      </div>
      <div style={{
        marginTop: 6, display: 'flex', justifyContent: 'space-between',
        fontFamily: 'var(--font-mono)', fontSize: 9, color: Cs.dim, letterSpacing: '0.10em',
      }}>
        <span>00</span><span>06</span><span>12</span><span>18</span><span>24</span>
      </div>
      <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 16 }}>
        <ConfigLineMini label="Start" value={<EditValue>22:00</EditValue>} />
        <ConfigLineMini label="End"   value={<EditValue>07:00</EditValue>} />
      </div>
    </div>
  );
}

function ApprovalsPage() {
  return (
    <div style={{ height: '100%', background: Cs.bg, color: Cs.fg, display: 'flex', fontFamily: 'var(--font-sans)' }}>
      <FakeRail />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <ExpandHeader
          parentRoute={null}
          eyebrow="approvals"
          title="What needs your eyes"
          sub="2 waiting · 6 decided today · auto-approved 4 of those"
          status={[
            <Pill key="w" tone="amber">2 waiting</Pill>,
            <Pill key="a" tone="ok">avg response · 18s</Pill>,
          ]}
        />

        <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1.4fr 1fr', minHeight: 0 }}>
          {/* Left: Inbox */}
          <div style={{ overflow: 'auto', borderRight: `1px solid ${Cs.border}`, padding: '24px 28px' }}>
            <Mono color={Cs.mfg} size={9} track="0.14em">awaiting you · 2</Mono>
            <div style={{ marginTop: 12 }}>
              {APPROVALS.map((a) => <ApprovalRow key={a.id} a={a} />)}
            </div>

            <div style={{ marginTop: 24 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
                <Mono color={Cs.mfg} size={9} track="0.14em">recent decisions · today &amp; yesterday</Mono>
                <a style={{ ...linkS, fontSize: 11 }}>full history →</a>
              </div>
              <div style={{ marginTop: 10 }}>
                <div style={{
                  display: 'grid', gridTemplateColumns: '52px 14px 1fr 80px 70px 50px',
                  gap: 10, padding: '6px 0', borderBottom: `1px solid ${Cs.border}`,
                }}>
                  <Mono>when</Mono><Mono>·</Mono><Mono>request</Mono><Mono>decision</Mono><Mono>by</Mono><Mono>ms</Mono>
                </div>
                {RECENT_DECISIONS.map((d, i) => {
                  const decColor = {
                    approved: Cs.green, auto: Cs.green, edited: Cs.fg,
                    denied: Cs.red, expired: Cs.dim,
                  }[d.decision] || Cs.mfg;
                  return (
                    <div key={i} style={{
                      display: 'grid', gridTemplateColumns: '52px 14px 1fr 80px 70px 50px',
                      gap: 10, padding: '8px 0', alignItems: 'center',
                      borderBottom: i < RECENT_DECISIONS.length - 1 ? `1px solid ${Cs.borderSoft}` : 'none',
                    }}>
                      <Mono color={Cs.dim} size={10}>{d.ts}</Mono>
                      <window.ButlerMark name={d.butler} size={12} tone="neutral" />
                      <span style={{ fontSize: 12, color: Cs.fg, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{d.title}</span>
                      <Mono color={decColor} size={10} track="0.06em">{d.decision}</Mono>
                      <Mono color={Cs.dim} size={10} upper={false}>{d.by}</Mono>
                      <Mono color={Cs.dim} size={10}>{d.ms}</Mono>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          {/* Right: Policy */}
          <div style={{ overflow: 'auto', padding: '24px 28px' }}>
            <Mono color={Cs.mfg} size={9} track="0.14em">policy</Mono>
            <h2 style={{ margin: '4px 0 18px', fontSize: 20, fontWeight: 500, letterSpacing: '-0.02em' }}>How the system asks</h2>

            <div style={{ marginBottom: 24 }}>
              <Mono color={Cs.dim} size={9} track="0.14em">quiet hours</Mono>
              <div style={{ marginTop: 10 }}><QuietHoursStrip /></div>
              <Mono color={Cs.dim} size={9.5} upper={false} track="0.04em" style={{ marginTop: 10 }}>
                During quiet hours, notifications are queued. Anything red still wakes you.
              </Mono>
            </div>

            <div style={{ marginBottom: 24 }}>
              <Mono color={Cs.dim} size={9} track="0.14em">auto-decisions</Mono>
              <div style={{ marginTop: 6 }}>
                <ConfigLineMini label="QA · merge PRs"     value={<EditValue mono={false}>low &amp; medium severity</EditValue>} />
                <ConfigLineMini label="Household · reorder" value={<EditValue mono={false}>under $30 · regular SKUs</EditValue>} />
                <ConfigLineMini label="Memory · drop"      value={<EditValue mono={false}>nightly compaction only</EditValue>} />
                <ConfigLineMini label="Relationship · send" value={<EditValue mono={false}>never · always ask</EditValue>} />
              </div>
            </div>

            <div style={{ marginBottom: 24 }}>
              <Mono color={Cs.dim} size={9} track="0.14em">notification channels</Mono>
              <div style={{ marginTop: 8, display: 'grid', gap: 6 }}>
                {[
                  { ch: 'desktop',  on: true,  sub: 'macOS · banner' },
                  { ch: 'telegram', on: true,  sub: '@tze · 9472 chat' },
                  { ch: 'email',    on: false, sub: 'tze@residence.lim · digest' },
                  { ch: 'sms',      on: false, sub: '—' },
                ].map((c) => (
                  <div key={c.ch} style={{
                    display: 'grid', gridTemplateColumns: '90px 1fr auto',
                    gap: 12, padding: '6px 0', alignItems: 'baseline',
                    borderBottom: `1px solid ${Cs.borderSoft}`,
                  }}>
                    <Mono color={Cs.fg} size={11} upper={false} track="0.02em">{c.ch}</Mono>
                    <Mono color={Cs.dim} size={10} upper={false} track="0.04em">{c.sub}</Mono>
                    <Toggle on={c.on} />
                  </div>
                ))}
              </div>
            </div>

            <div>
              <Mono color={Cs.dim} size={9} track="0.14em">timing</Mono>
              <div style={{ marginTop: 6 }}>
                <ConfigLineMini label="Default expiry"  value={<EditValue>24h</EditValue>} />
                <ConfigLineMini label="Re-auth grace"   value={<EditValue>15m</EditValue>} />
                <ConfigLineMini label="Reminder cadence" value={<EditValue mono={false}>none · single notify</EditValue>} />
              </div>
            </div>
          </div>
        </div>

        <ApiWireFooter
          note="Approvals are append-only; a denial is a decision, not a delete."
          endpoints={[
            { method: 'GET',  path: '/api/approvals?state=waiting' },
            { method: 'GET',  path: '/api/approvals/{id}' },
            { method: 'POST', path: '/api/approvals/{id}/approve' },
            { method: 'POST', path: '/api/approvals/{id}/deny' },
            { method: 'POST', path: '/api/approvals/{id}/defer', hover: '+N hours' },
            { method: 'GET',  path: '/api/approvals/history?since=' },
            { method: 'GET',  path: '/api/approvals/policy' },
            { method: 'PUT',  path: '/api/approvals/policy' },
            { method: 'WS',   path: '/api/approvals/stream' },
          ]}
        />
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// 5 · Permissions & Data
// ═══════════════════════════════════════════════════════════════════

const AUDIT = [
  { ts: '16:42:14', actor: 'qa',          action: 'merged_pr',      target: 'butlerhouse/chronicler#142', note: 'auto-merge · severity low' },
  { ts: '16:28:02', actor: 'qa',          action: 'opened_case',    target: 'qa-2026-0514-a',             note: 'detected log volume spike in chronicler' },
  { ts: '14:38:51', actor: 'household',   action: 'opened_approval',target: 'apv-2026-0514-002',          note: 'order Acqua Panna 12-pack' },
  { ts: '14:02:14', actor: 'butlerhouse-warm', action: 'auth_failure', target: 'inference.warm.local',    note: '401 from upstream · third in 24h' },
  { ts: '12:30:08', actor: 'calendar',    action: 'auto_pause',     target: 'self',                        note: '3 consecutive token refresh failures' },
  { ts: '12:30:00', actor: 'calendar',    action: 'auth_failure',   target: 'google.calendar',             note: 'token refresh failed · 401' },
  { ts: '12:28:14', actor: 'Tze',         action: 'edit_config',    target: 'butlers/qa/prompt',           note: 'v6 → v7 · added "no celebration" line' },
  { ts: '11:18:42', actor: 'household',   action: 'opened_approval',target: 'apv-2026-0514-000',          note: 'reorder coffee · Aerolatte $28.40' },
  { ts: '11:18:54', actor: 'Tze',         action: 'approved',       target: 'apv-2026-0514-000',          note: 'response 12s' },
  { ts: '09:14:02', actor: 'memory',      action: 'compaction',     target: 'short → mid',                 note: '391 moved · 8 drops · 2m 02s' },
  { ts: '09:02:08', actor: 'qa',          action: 'merged_pr',      target: 'butlerhouse/chronicler#141',  note: 'auto-merge · trimmed verbose log' },
  { ts: '08:42:14', actor: 'briefing',    action: 'rendered',       target: 'overview',                    note: 'llm · 142 tokens · 1.4s' },
  { ts: '07:00:00', actor: 'system',      action: 'quiet_hours_end',target: 'self',                        note: 'queued notifications · 3 flushed' },
  { ts: 'Y 22:00',  actor: 'system',      action: 'quiet_hours_start',target: 'self',                      note: '—' },
  { ts: 'Y 21:46',  actor: 'chronicler',  action: 'consolidate',    target: 'memory/day/2026-05-13',       note: 'sonnet-4-5 · 4,210 tokens' },
];

function AuditRow({ e, last }) {
  const actionColor = {
    auto_pause: Cs.red, auth_failure: Cs.red,
    auto_merge: Cs.green, merged_pr: Cs.green, approved: Cs.green,
    opened_approval: Cs.amber, opened_case: Cs.amber,
    edit_config: Cs.fg, compaction: Cs.fg, rendered: Cs.dim, consolidate: Cs.fg,
    quiet_hours_start: Cs.dim, quiet_hours_end: Cs.dim,
  }[e.action] || Cs.fg;
  const isButler = BUTLERS.some((b) => b.name === e.actor);
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '70px 16px 100px 120px 1fr',
      gap: 12, padding: '8px 0', alignItems: 'baseline',
      fontFamily: 'var(--font-mono)', fontSize: 10.5,
      borderBottom: last ? 'none' : `1px solid ${Cs.borderSoft}`,
    }}>
      <span style={{ color: Cs.dim }}>{e.ts}</span>
      {isButler
        ? <window.ButlerMark name={e.actor} size={12} tone="neutral" />
        : <span style={{
            width: 12, height: 12, borderRadius: 2,
            border: `1px solid ${Cs.border}`,
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 8, color: Cs.mfg,
          }}>{e.actor[0]}</span>}
      <span style={{ color: actionColor, letterSpacing: '0.04em' }}>{e.action}</span>
      <span style={{ color: Cs.mfg, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{e.target}</span>
      <span style={{ color: Cs.dim, fontStyle: 'italic' }}>{e.note}</span>
    </div>
  );
}

function BigPermissionsMatrix() {
  const butlers = BUTLERS.map((b) => b.name);
  return (
    <div style={{ border: `1px solid ${Cs.border}` }}>
      <div style={{
        display: 'grid', gridTemplateColumns: `260px repeat(${butlers.length}, 1fr)`,
        background: Cs.bgDeep, borderBottom: `1px solid ${Cs.border}`,
      }}>
        <div style={{ padding: '14px 18px' }}><Mono color={Cs.mfg}>permission</Mono></div>
        {butlers.map((b) => (
          <div key={b} style={{
            padding: '14px 8px', display: 'flex', flexDirection: 'column',
            alignItems: 'center', gap: 6, borderLeft: `1px solid ${Cs.borderSoft}`,
          }}>
            <window.ButlerMark name={b} size={22} tone="fill" />
            <Mono color={Cs.mfg} size={9.5} upper={false} track="0.04em">{b.slice(0, 5)}</Mono>
          </div>
        ))}
      </div>
      {PERMS.map((p, i) => (
        <div key={p.id} style={{
          display: 'grid', gridTemplateColumns: `260px repeat(${butlers.length}, 1fr)`,
          borderBottom: i < PERMS.length - 1 ? `1px solid ${Cs.borderSoft}` : 'none',
        }}>
          <div style={{ padding: '16px 18px', display: 'grid', gap: 3 }}>
            <span style={{ fontSize: 13.5, color: Cs.fg }}>{p.label}</span>
            <Mono color={Cs.dim} size={10} upper={false} track="0.04em">{p.id}</Mono>
          </div>
          {butlers.map((b) => (
            <div key={b} style={{
              padding: '16px 8px', display: 'flex', alignItems: 'center', justifyContent: 'center',
              borderLeft: `1px solid ${Cs.borderSoft}`, cursor: 'pointer',
            }}>
              {hasPerm(b, p.id) ? (
                <span style={{ width: 11, height: 11, borderRadius: 2, background: Cs.fg, opacity: 0.85 }} />
              ) : (
                <span style={{ width: 11, height: 11, borderRadius: 2, border: `1px solid ${Cs.borderSoft}` }} />
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function DataExpanded() {
  const webhooks = [
    { url: 'https://hooks.slack.com/T0…/B0…/xY9P', events: 'approval.*, attention.*',  on: true,  last: '2h' },
    { url: 'pipedream://workflow/p-tz9-briefing',  events: 'briefing.rendered',         on: true,  last: '12m' },
    { url: 'localhost:9099/ingest',                events: 'qa.*, butlers.lifecycle',   on: true,  last: '4m' },
    { url: 'discord://webhook/butlerhouse-watch',  events: 'attention.red',             on: false, last: '—' },
  ];
  return (
    <div style={{ height: '100%', background: Cs.bg, color: Cs.fg, display: 'flex', fontFamily: 'var(--font-sans)' }}>
      <FakeRail />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <ExpandHeader
          eyebrow="settings · §3 · permissions &amp; data"
          title="What each butler may do, and what becomes of it"
          sub="56 grants · 14,231 audit entries · 142 MB archive"
          status={[
            <Pill key="ok" tone="ok">all grants audited</Pill>,
            <Pill key="ar">encrypted at rest</Pill>,
          ]}
        />

        {/* Section 1 — Full matrix */}
        <Section
          n={1}
          title="Permissions matrix"
          hint="what each butler is allowed to do at the system level · click a cell to grant or revoke"
          right={<div style={{ display: 'flex', gap: 12 }}>
            <Mono color={Cs.dim} size={10}>● granted · □ denied</Mono>
            <a style={{ ...linkS, fontSize: 11 }}>+ define permission →</a>
          </div>}
        >
          <BigPermissionsMatrix />
          <Mono color={Cs.dim} size={9.5} upper={false} track="0.04em" style={{ marginTop: 12 }}>
            Each cell-change is appended to the audit log with the actor's identity and a reason field. Permissions changes never bypass the audit.
          </Mono>
        </Section>

        {/* Section 2 — Audit log + Data ops */}
        <Section
          n={2}
          title="Audit log &amp; data operations"
          hint="every decision, configuration change, and butler action since 02 Feb 2026"
        >
          <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 32 }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
                <Mono color={Cs.mfg} size={9} track="0.14em">most recent · 15 of 14,231</Mono>
                <a style={{ ...linkS, fontSize: 11 }}>open /audit · full log →</a>
              </div>
              <div style={{ marginTop: 10 }}>
                <div style={{
                  display: 'grid', gridTemplateColumns: '70px 16px 100px 120px 1fr',
                  gap: 12, padding: '6px 0', borderBottom: `1px solid ${Cs.border}`,
                }}>
                  <Mono>when</Mono><Mono>·</Mono><Mono>action</Mono><Mono>target</Mono><Mono>note</Mono>
                </div>
                {AUDIT.map((e, i) => <AuditRow key={i} e={e} last={i === AUDIT.length - 1} />)}
              </div>
            </div>

            <div>
              <Mono color={Cs.mfg} size={9} track="0.14em">data operations</Mono>
              <div style={{ marginTop: 8 }}>
                <ConfigLine label="Export full archive"  helper="Memory, audit, configuration · encrypted zip." value={<a style={linkS}>download · 142 MB →</a>} mono={false} />
                <ConfigLine label="Scheduled exports"    helper="Auto-export to cloud each Sunday." value={<EditValue mono={false}>off</EditValue>} />
                <ConfigLine label="Encryption at rest"   helper="All tiers encrypted with household key." value={<Pill tone="ok">aes-256 · key in keychain</Pill>} mono={false} />
                <ConfigLine label="Retention"            helper="How long the audit log is kept." value={<EditValue mono={false}>indefinite · no expiry</EditValue>} />
                <ConfigLine label="Reset memory · tier"  helper="Drops one tier · 7-day cool-down before refill." value={<a style={{ ...linkS, color: Cs.amber, textDecorationColor: Cs.amber }}>destructive →</a>} mono={false} />
                <ConfigLine label="Wipe system"          helper="Removes all configuration and memory." value={<a style={{ ...linkS, color: Cs.red, textDecorationColor: Cs.red }}>requires phrase →</a>} mono={false} last />
              </div>
            </div>
          </div>
        </Section>

        {/* Section 3 — Webhooks */}
        <Section
          n={3}
          title="Outbound webhooks"
          hint="systems to notify when something happens · idempotent · retried with backoff"
          right={<a style={{ ...linkS, fontSize: 11 }}>+ add webhook →</a>}
        >
          <div style={{
            display: 'grid', gridTemplateColumns: '1.6fr 1.2fr 80px 60px 60px',
            gap: 14, padding: '6px 0', borderBottom: `1px solid ${Cs.border}`,
          }}>
            <Mono>endpoint</Mono><Mono>events · subscribed</Mono><Mono>last delivery</Mono><Mono>on</Mono><Mono>·</Mono>
          </div>
          {webhooks.map((w, i) => (
            <div key={i} style={{
              display: 'grid', gridTemplateColumns: '1.6fr 1.2fr 80px 60px 60px',
              gap: 14, padding: '12px 0', alignItems: 'center',
              borderBottom: i < webhooks.length - 1 ? `1px solid ${Cs.borderSoft}` : 'none',
            }}>
              <Mono color={Cs.fg} size={11} upper={false} track="0.02em">{w.url}</Mono>
              <Mono color={Cs.mfg} size={10.5} upper={false} track="0.02em">{w.events}</Mono>
              <Mono color={Cs.dim} size={10} upper={false} track="0.04em">{w.last}</Mono>
              <Toggle on={w.on} />
              <a style={{ ...linkS, fontSize: 10.5 }}>logs →</a>
            </div>
          ))}
        </Section>

        <ApiWireFooter
          note="Audit is append-only; permissions changes must include a reason field."
          endpoints={[
            { method: 'GET',  path: '/api/permissions' },
            { method: 'PUT',  path: '/api/permissions/{butler}/{perm}' },
            { method: 'GET',  path: '/api/audit?since=&actor=&action=' },
            { method: 'GET',  path: '/api/audit/{id}' },
            { method: 'POST', path: '/api/data/export', hover: 'encrypted zip' },
            { method: 'GET',  path: '/api/webhooks' },
            { method: 'POST', path: '/api/webhooks' },
            { method: 'POST', path: '/api/webhooks/{id}/test' },
            { method: 'DELETE', path: '/api/data/wipe', hover: 'requires phrase' },
          ]}
        />
      </div>
    </div>
  );
}

window.SETTINGS_EXPANDED = {
  ButlersExpanded, SpendDashboard, MemoryExpanded, ApprovalsPage, DataExpanded,
};
