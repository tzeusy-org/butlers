// /butlers/{butler} — real page. Tabbed dashboard, modular by butler.
// Reads butler from URL hash (#relationship, #health, …), defaults to relationship.
//
// Shell: sidebar (re-uses Sidebar.jsx) + main with hero + tabs + panel grid.
// Tabs: base set (Overview, Activity, Logs, Approvals, Spend, Config, Memory)
// + 1 bespoke tab per butler (defined in butler-detail-data.jsx).

const D = window.BUTLERS_DATA;
const ACTIVE = new Set(['running', 'patrol', 'consolidating', 'ingesting']);
const BASE_TABS = ['Overview', 'Activity', 'Logs', 'Approvals', 'Spend', 'Config', 'Memory'];

function useButlerKey() {
  const init = () => (window.location.hash || '#relationship').replace('#','') || 'relationship';
  const [key, setKey] = React.useState(init);
  React.useEffect(() => {
    const onHash = () => setKey(init());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  return [key, (k) => { window.location.hash = k; }];
}

// ─── Atoms ───────────────────────────────────────────────────────────────

function MonoLabel({ children, color }) {
  return <span style={{
    fontFamily: 'var(--font-mono)', fontSize: 9, color: color || C.mfg,
    textTransform: 'uppercase', letterSpacing: '0.14em',
  }}>{children}</span>;
}

function Panel({ title, sub, children, span = 1, height, scroll, accent }) {
  return (
    <div style={{
      gridColumn: `span ${span}`,
      borderRight: `1px solid ${C.border}`, borderBottom: `1px solid ${C.border}`,
      padding: '14px 16px', minHeight: height || 'auto',
      display: 'flex', flexDirection: 'column', gap: 10,
      background: accent ? 'oklch(1 0 0 / 0.012)' : 'transparent',
      overflow: scroll ? 'hidden' : 'visible',
    }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <MonoLabel>{title}</MonoLabel>
        {sub && <MonoLabel color={C.dim}>{sub}</MonoLabel>}
      </div>
      <div style={{ flex: 1, minHeight: 0, overflow: scroll ? 'auto' : 'visible' }}>
        {children}
      </div>
    </div>
  );
}

function KPI({ label, value, sub, tone, big }) {
  const colorMap = { amber: C.amber, red: C.red, green: C.green };
  return (
    <div>
      <MonoLabel>{label}</MonoLabel>
      <div className="tnum" style={{
        fontSize: big ? 28 : 22, fontWeight: 500, letterSpacing: '-0.025em',
        lineHeight: 1, marginTop: 6,
        color: colorMap[tone] || C.fg,
      }}>{value}</div>
      {sub && <div style={{
        fontFamily: 'var(--font-mono)', fontSize: 9, color: C.dim, marginTop: 4,
      }}>{sub}</div>}
    </div>
  );
}

function KV({ k, v, mono }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '110px 1fr', gap: 12, padding: '4px 0',
      fontFamily: 'var(--font-mono)', fontSize: 11,
      borderBottom: `1px solid ${C.borderSoft}`,
    }}>
      <span style={{ color: C.mfg }}>{k}</span>
      <span style={{ color: C.fg, fontFamily: mono ? 'var(--font-mono)' : 'inherit' }}>{v}</span>
    </div>
  );
}

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

function BarSeries({ data, height = 56, color }) {
  const max = Math.max(...data, 1);
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height }}>
      {data.map((v, k) => (
        <div key={k} style={{
          flex: 1,
          height: 2 + (v / max) * (height - 2),
          background: color || `oklch(0.985 0 0 / ${0.20 + (v / max) * 0.55})`,
          borderRadius: 1,
        }} />
      ))}
    </div>
  );
}

function LineSeries({ data, height = 56, color }) {
  if (!data || !data.length) return null;
  const min = Math.min(...data), max = Math.max(...data), range = max - min || 1;
  const w = 100;
  const step = w / (data.length - 1);
  const pts = data.map((v, i) => `${i * step},${height - ((v - min) / range) * (height - 4) - 2}`).join(' ');
  return (
    <svg width="100%" height={height} viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" style={{ display: 'block' }}>
      <polyline points={pts} fill="none" stroke={color || C.fg} strokeWidth="0.8" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

function HourAxis() {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between',
      fontFamily: 'var(--font-mono)', fontSize: 9, color: C.dim, marginTop: 6,
    }}>
      {['00','03','06','09','12','15','18','21','now'].map((t,i) => <span key={i}>{t}</span>)}
    </div>
  );
}

function RangeToggle({ value, onChange }) {
  return (
    <div style={{ display: 'flex', border: `1px solid ${C.border}`, borderRadius: 3 }}>
      {['24h','7d','30d'].map((o, i) => (
        <button key={o} onClick={() => onChange(o)} style={{
          background: value === o ? C.fg : 'transparent',
          color: value === o ? C.bg : C.fg,
          border: 'none', borderLeft: i ? `1px solid ${C.border}` : 'none',
          padding: '4px 10px', cursor: 'pointer',
          fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em',
        }}>{o}</button>
      ))}
    </div>
  );
}

function ButlerSwitcher({ activeKey, onChange }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      {D.butlers.map((b) => {
        const active = b.name === activeKey;
        return (
          <button key={b.name} onClick={() => onChange(b.name)} title={b.label} style={{
            display: 'flex', alignItems: 'center', gap: 6,
            background: active ? 'oklch(1 0 0 / 0.06)' : 'transparent',
            border: `1px solid ${active ? C.borderStrong : 'transparent'}`,
            color: C.fg, padding: '4px 8px', borderRadius: 3, cursor: 'pointer',
          }}>
            <ButlerMark name={b.name} size={14} tone={active ? 'fill' : 'neutral'} />
            <span style={{
              fontSize: 11, color: active ? C.fg : C.mfg, textTransform: 'capitalize',
              letterSpacing: '-0.005em',
            }}>{b.label}</span>
            {b.status !== 'ok' && <StatusDot status={b.status} size={5} />}
          </button>
        );
      })}
    </div>
  );
}

function ActionBar({ butler, onPause, paused }) {
  const btn = {
    background: 'transparent', color: C.fg,
    border: `1px solid ${C.border}`, padding: '5px 10px',
    fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em',
    textTransform: 'uppercase', cursor: 'pointer', borderRadius: 3,
  };
  return (
    <div style={{ display: 'flex', gap: 6 }}>
      <button style={btn}>force run</button>
      <button style={btn}>logs</button>
      <button style={btn}>config</button>
      <button style={btn}>prompt</button>
      <button onClick={onPause} style={{
        ...btn,
        background: paused ? C.amber : C.fg,
        color: paused ? '#000' : C.bg, borderColor: paused ? C.amber : C.fg,
      }}>{paused ? 'resume' : 'pause'}</button>
    </div>
  );
}

function Tabs({ tabs, value, onChange, bespokeTab }) {
  return (
    <div style={{ display: 'flex', borderBottom: `1px solid ${C.border}`, flex: 1 }}>
      {tabs.map((t) => {
        const active = t === value;
        const isBespoke = t === bespokeTab;
        return (
          <button key={t} onClick={() => onChange(t)} style={{
            background: 'transparent', color: active ? C.fg : C.mfg,
            border: 'none',
            borderBottom: active ? `2px solid ${C.fg}` : '2px solid transparent',
            padding: '10px 14px', cursor: 'pointer',
            fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.10em',
            textTransform: 'uppercase', position: 'relative', marginBottom: -1,
          }}>
            {t}
            {isBespoke && (
              <span style={{
                position: 'absolute', top: 7, right: 4,
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

// ─── Hero ────────────────────────────────────────────────────────────────

function Hero({ butler, detail, paused, onPause }) {
  const isActive = ACTIVE.has(butler.activity) && !paused;
  const tone = butler.status === 'degraded' ? C.red
              : butler.activity === 'awaiting approval' ? C.amber
              : isActive ? C.green : C.dim;
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: 'auto 1fr auto', gap: 18,
      alignItems: 'center', padding: '14px 0',
      borderBottom: `1px solid ${C.border}`,
    }}>
      <ButlerMark name={butler.name} size={40} tone={isActive ? 'fill' : 'neutral'} />
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
          <MonoLabel color={C.dim}>/butlers/{butler.name}</MonoLabel>
          <MonoLabel color={tone}>● {paused ? 'paused' : butler.activity}</MonoLabel>
          <MonoLabel color={C.dim}>
            pid {detail.process.pid || '—'} · port {detail.process.port} · uptime {detail.process.uptime}
          </MonoLabel>
        </div>
        <div style={{
          fontSize: 22, fontWeight: 500, letterSpacing: '-0.02em', marginTop: 6,
        }}>
          <span style={{ textTransform: 'capitalize' }}>{butler.label}</span>
          <span style={{ color: C.dim, fontWeight: 400, marginLeft: 12, fontSize: 14 }}>
            · {detail.description}
          </span>
        </div>
      </div>
      <ActionBar butler={butler.name} onPause={onPause} paused={paused} />
    </div>
  );
}

// ─── Tab content ─────────────────────────────────────────────────────────

function OverviewTab({ butler, detail, range }) {
  const stripe = (D.sessionGrid.find((s) => s.butler === butler.name) || {}).row || Array(24).fill(0);
  const pending = D.attention.filter((a) => a.butler === butler.name);
  const recent = D.feed.filter((f) => f.butler === butler.name).slice(0, 5);
  const series = range === '24h' ? null : window.seriesFor(butler.name, range);
  return (
    <>
      <Panel title="status">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <StatusDot status={butler.status} size={8} />
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13 }}>
            {butler.status.toUpperCase()} · {butler.activity}
          </span>
        </div>
        <MonoLabel color={C.dim}>last run {butler.lastRun}</MonoLabel>
      </Panel>
      <Panel title="sessions" sub={range}>
        <div className="tnum" style={{ fontSize: 26, fontWeight: 500, letterSpacing: '-0.025em' }}>{butler.sessions24h}</div>
        <MonoLabel color={C.dim}>+18% vs prior</MonoLabel>
      </Panel>
      <Panel title="spend" sub={range}>
        <div className="tnum" style={{ fontSize: 26, fontWeight: 500, letterSpacing: '-0.025em' }}>${butler.costToday.toFixed(2)}</div>
        <MonoLabel color={C.dim}>${(butler.costToday / Math.max(butler.sessions24h,1)).toFixed(3)} / session</MonoLabel>
      </Panel>
      <Panel title="awaiting">
        <div className="tnum" style={{ fontSize: 26, fontWeight: 500, letterSpacing: '-0.025em', color: pending.length ? C.amber : C.fg }}>{pending.length}</div>
        <MonoLabel color={C.dim}>{pending.length ? `oldest: ${pending[0].age}` : 'nothing pending'}</MonoLabel>
      </Panel>

      <Panel title="activity" sub={range} span={2} height={140}>
        {range === '24h' ? <Stripe24 row={stripe} height={68} /> : <BarSeries data={series} height={68} />}
        <HourAxis />
      </Panel>
      <Panel title="recent" sub={`${recent.length} events`} span={2} scroll height={140}>
        {recent.length === 0 && <MonoLabel color={C.dim}>no recent events</MonoLabel>}
        {recent.map((e, i) => (
          <div key={i} style={{
            display: 'grid', gridTemplateColumns: '46px 1fr auto',
            gap: 10, padding: '6px 0',
            borderBottom: i < recent.length - 1 ? `1px solid ${C.borderSoft}` : 'none',
          }}>
            <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: C.dim }}>{e.time}</span>
            <span style={{ fontSize: 12 }}>{e.text}</span>
            <MonoLabel>{e.kind}</MonoLabel>
          </div>
        ))}
      </Panel>

      <Panel title="awaiting your action" span={2} scroll>
        {pending.length === 0 && <MonoLabel color={C.dim}>no items pending review</MonoLabel>}
        {pending.map((a, i) => (
          <div key={i} style={{
            display: 'grid', gridTemplateColumns: '8px 1fr auto', gap: 10,
            padding: '8px 0',
            borderBottom: i < pending.length - 1 ? `1px solid ${C.borderSoft}` : 'none',
            alignItems: 'baseline',
          }}>
            <span style={{ width: 6, height: 6, background: a.severity === 'high' ? C.red : C.amber, borderRadius: 1 }} />
            <span style={{ fontSize: 12 }}>{a.title} <span style={{ color: C.dim }}>· {a.age}</span></span>
            <a href="#" style={{ color: C.fg, textDecoration: 'underline', textUnderlineOffset: 3, textDecorationColor: C.borderStrong, fontSize: 12 }}>{a.action} →</a>
          </div>
        ))}
      </Panel>
      <Panel title="config" sub={detail.config.configPath} span={2}>
        <div style={{ display: 'grid', gap: 0 }}>
          <KV k="model"        v={detail.config.model} />
          <KV k="schedule"     v={detail.config.schedule} />
          <KV k="scopes"       v={`${detail.config.scopes.length} grants`} />
          <KV k="integrations" v={detail.config.integrations.join(' · ')} />
        </div>
      </Panel>
    </>
  );
}

function ActivityTab({ butler, range }) {
  const stripe = (D.sessionGrid.find((s) => s.butler === butler.name) || {}).row || Array(24).fill(0);
  const series = range === '24h' ? null : window.seriesFor(butler.name, range);
  return (
    <>
      <Panel title="sessions" sub={range} span={4} height={160}>
        {range === '24h' ? <Stripe24 row={stripe} height={108} /> : <BarSeries data={series} height={108} />}
        <HourAxis />
      </Panel>
      <Panel title="latency · p50" sub="ms" span={1}>
        <div className="tnum" style={{ fontSize: 22, fontWeight: 500 }}>620</div>
        <MonoLabel color={C.dim}>−40 vs 24h</MonoLabel>
      </Panel>
      <Panel title="latency · p95" sub="ms" span={1}>
        <div className="tnum" style={{ fontSize: 22, fontWeight: 500 }}>1,240</div>
        <MonoLabel color={C.dim}>haiku-4-5</MonoLabel>
      </Panel>
      <Panel title="errors" sub={range} span={1}>
        <div className="tnum" style={{ fontSize: 22, fontWeight: 500 }}>{butler.status === 'degraded' ? 4 : 0}</div>
        <MonoLabel color={C.dim}>{butler.status === 'degraded' ? 'oauth failure' : 'no errors'}</MonoLabel>
      </Panel>
      <Panel title="success rate" span={1}>
        <div className="tnum" style={{ fontSize: 22, fontWeight: 500 }}>99.4%</div>
        <MonoLabel color={C.dim}>30d rolling</MonoLabel>
      </Panel>
      <Panel title="kind breakdown" sub={range} span={4}>
        <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
          {[
            { k: 'log', n: 18 }, { k: 'draft', n: 4 }, { k: 'consolidate', n: 12 },
            { k: 'investigate', n: 7 }, { k: 'idle', n: 6 },
          ].map((x) => (
            <div key={x.k}>
              <MonoLabel>{x.k}</MonoLabel>
              <div className="tnum" style={{ fontSize: 18, fontWeight: 500, marginTop: 4 }}>{x.n}</div>
            </div>
          ))}
        </div>
      </Panel>
    </>
  );
}

function LogsTab({ butler }) {
  const [filter, setFilter] = React.useState('ALL');
  const lines = window.LOG_LINES_FOR(butler.name);
  const filtered = filter === 'ALL' ? lines : lines.filter((l) => l.lvl === filter);
  return (
    <Panel title="raw log" sub="tail -f · auto-scroll" span={4} height="100%" scroll>
      <div style={{ display: 'flex', gap: 4, marginBottom: 8 }}>
        {['ALL','INFO','DEBUG','WARN','ERROR'].map((f) => (
          <button key={f} onClick={() => setFilter(f)} style={{
            background: filter === f ? C.fg : 'transparent',
            color: filter === f ? C.bg : C.mfg,
            border: `1px solid ${C.border}`, borderRadius: 3,
            fontFamily: 'var(--font-mono)', fontSize: 9, padding: '2px 6px',
            cursor: 'pointer', letterSpacing: '0.06em',
          }}>{f}</button>
        ))}
        <span style={{ flex: 1 }} />
        <MonoLabel color={C.dim}>{filtered.length} lines</MonoLabel>
      </div>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, lineHeight: 1.6 }}>
        {filtered.map((l, i) => {
          const lvlColor = l.lvl === 'ERROR' ? C.red : l.lvl === 'WARN' ? C.amber : l.lvl === 'DEBUG' ? C.dim : C.green;
          return (
            <div key={i} style={{ display: 'grid', gridTemplateColumns: '78px 56px 1fr', gap: 10 }}>
              <span style={{ color: C.dim }}>{l.ts}</span>
              <span style={{ color: lvlColor }}>{l.lvl}</span>
              <span style={{ color: C.fg }}>{l.msg}</span>
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

function ApprovalsTab({ butler }) {
  const items = D.attention.filter((a) => a.butler === butler.name);
  const seeded = items.length ? items : [{
    severity: 'low', title: 'No pending approvals', detail: 'This butler has nothing waiting for you.', age: '—', action: '',
  }];
  return (
    <Panel title="awaiting your action" sub={`${items.length} pending`} span={4} height="100%" scroll>
      {seeded.map((a, i) => (
        <div key={i} style={{
          display: 'grid', gridTemplateColumns: '8px 1fr auto', gap: 14,
          padding: '14px 0',
          borderBottom: i < seeded.length - 1 ? `1px solid ${C.borderSoft}` : 'none',
          alignItems: 'baseline',
        }}>
          <span style={{ width: 6, height: 6, background: a.severity === 'high' ? C.red : a.severity === 'medium' ? C.amber : C.dim, borderRadius: 1, marginTop: 6 }} />
          <div>
            <div style={{ fontSize: 14, fontWeight: 500 }}>{a.title}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: C.dim, marginTop: 4 }}>
              {a.detail}{a.age !== '—' && ` · ${a.age}`}
            </div>
          </div>
          {a.action && <a href="#" style={{ color: C.fg, textDecoration: 'underline', textUnderlineOffset: 4, textDecorationColor: C.borderStrong, fontSize: 13 }}>{a.action} →</a>}
        </div>
      ))}
    </Panel>
  );
}

function SpendTab({ butler, range }) {
  const series = window.seriesFor(butler.name, range === '24h' ? '24h' : range).map((x) => x * 0.04);
  const cost = D.costsByButler.find((c) => c.name === butler.name);
  const total = cost ? cost.cost : butler.costToday;
  return (
    <>
      <Panel title="spend · today" span={1}>
        <div className="tnum" style={{ fontSize: 24, fontWeight: 500 }}>${total.toFixed(2)}</div>
        <MonoLabel color={C.dim}>−4% vs avg</MonoLabel>
      </Panel>
      <Panel title="spend · 30d" span={1}>
        <div className="tnum" style={{ fontSize: 24, fontWeight: 500 }}>${(total * 27).toFixed(2)}</div>
        <MonoLabel color={C.dim}>${total.toFixed(2)} / day</MonoLabel>
      </Panel>
      <Panel title="cost · session" span={1}>
        <div className="tnum" style={{ fontSize: 24, fontWeight: 500 }}>${(total / Math.max(butler.sessions24h, 1)).toFixed(3)}</div>
        <MonoLabel color={C.dim}>haiku-4-5</MonoLabel>
      </Panel>
      <Panel title="tokens · 24h" span={1}>
        <div className="tnum" style={{ fontSize: 24, fontWeight: 500 }}>128k / 38k</div>
        <MonoLabel color={C.dim}>in / out</MonoLabel>
      </Panel>
      <Panel title="trend" sub={range} span={4} height={140}>
        <BarSeries data={series} height={92} />
        <HourAxis />
      </Panel>
      <Panel title="model breakdown" span={4}>
        <div style={{ display: 'grid', gap: 0 }}>
          <KV k="claude-haiku-4-5"  v={`$${(total * 0.85).toFixed(2)} · 92% of calls`} />
          <KV k="claude-sonnet-4-5" v={`$${(total * 0.15).toFixed(2)} · 8% (escalations)`} />
        </div>
      </Panel>
    </>
  );
}

function ConfigTab({ butler, detail }) {
  return (
    <>
      <Panel title="process" span={2}>
        <KV k="port" v={detail.process.port} mono />
        <KV k="pid" v={detail.process.pid || '—'} mono />
        <KV k="uptime" v={detail.process.uptime} />
        <KV k="config" v={detail.config.configPath} mono />
      </Panel>
      <Panel title="schedule" span={2}>
        <KV k="cadence" v={detail.config.schedule} />
        <KV k="last run" v={butler.lastRun} />
        <KV k="next run" v="on demand" />
        <KV k="model" v={detail.config.model} mono />
      </Panel>
      <Panel title="scopes · oauth" span={2}>
        {detail.config.scopes.map((s) => (
          <div key={s} style={{
            fontFamily: 'var(--font-mono)', fontSize: 11, color: C.fg,
            padding: '5px 0', borderBottom: `1px solid ${C.borderSoft}`,
            display: 'flex', justifyContent: 'space-between',
          }}>
            <span>● {s}</span>
            <MonoLabel color={C.green}>granted</MonoLabel>
          </div>
        ))}
      </Panel>
      <Panel title="integrations" span={2}>
        {detail.config.integrations.map((s) => {
          const needsAuth = /auth needed/i.test(s);
          return (
            <div key={s} style={{
              display: 'flex', justifyContent: 'space-between', padding: '5px 0',
              borderBottom: `1px solid ${C.borderSoft}`,
            }}>
              <span style={{ fontSize: 12 }}>{s.replace(/\s*\(auth needed\)\s*/, '')}</span>
              <MonoLabel color={needsAuth ? C.amber : C.green}>● {needsAuth ? 'reauth' : 'connected'}</MonoLabel>
            </div>
          );
        })}
      </Panel>
    </>
  );
}

function MemoryTab({ detail }) {
  const c = detail.counts;
  return (
    <>
      <Panel title="episodes"><div className="tnum" style={{ fontSize: 22, fontWeight: 500 }}>{c.episodes.toLocaleString()}</div><MonoLabel color={C.dim}>{c.deltas[0]} today</MonoLabel></Panel>
      <Panel title="facts"><div className="tnum" style={{ fontSize: 22, fontWeight: 500 }}>{c.facts.toLocaleString()}</div><MonoLabel color={C.dim}>{c.deltas[1]} today</MonoLabel></Panel>
      <Panel title="entities"><div className="tnum" style={{ fontSize: 22, fontWeight: 500 }}>{c.entities.toLocaleString()}</div><MonoLabel color={C.dim}>{c.deltas[2]} today</MonoLabel></Panel>
      <Panel title="rules"><div className="tnum" style={{ fontSize: 22, fontWeight: 500 }}>{c.rules.toLocaleString()}</div><MonoLabel color={C.dim}>{c.deltas[3]} today</MonoLabel></Panel>
      <Panel title="recent writes" span={4} scroll>
        {window.MEMORY_WRITES_FOR(detail).map((m, i, arr) => (
          <div key={i} style={{
            display: 'grid', gridTemplateColumns: '50px 90px 1fr',
            gap: 12, padding: '8px 0',
            borderBottom: i < arr.length - 1 ? `1px solid ${C.borderSoft}` : 'none',
          }}>
            <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: C.dim }}>{m.ts}</span>
            <MonoLabel>{m.kind}</MonoLabel>
            <span style={{ fontSize: 13 }}>{m.text}</span>
          </div>
        ))}
      </Panel>
    </>
  );
}

window.BUTLER_TABS = { OverviewTab, ActivityTab, LogsTab, ApprovalsTab, SpendTab, ConfigTab, MemoryTab };
window.BUTLER_ATOMS = { Panel, KPI, KV, MonoLabel, Stripe24, BarSeries, LineSeries, HourAxis, RangeToggle, ButlerSwitcher, Tabs, Hero };
window.BUTLER_HOOKS = { useButlerKey, ACTIVE, BASE_TABS };
