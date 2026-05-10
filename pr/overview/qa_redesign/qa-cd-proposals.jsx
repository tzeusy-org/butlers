// Renderings of the C (history) and D (aggregate usefulness) enhancement
// directions for the QA dossier. Each variation is its own DCArtboard so
// they can be compared side-by-side.

const { QSev, QPRChip, QEyebrow, qaFilterCases } = window;

const CASES = window.QA_CASES;
const NOW = window.QA_NOW;
const KPIS = window.QA_KPIS;
const PATROL = window.QA_PATROL_24H;
const PR7D = window.QA_PR_7D;
const BY_BUTLER = window.QA_BY_BUTLER_7D;

// ─── tiny helpers ───────────────────────────────────────────────────────

function Sparkline({ data, height = 18, width = 110, dotIdx, color }) {
  const Cp = window.C;
  const max = Math.max(...data, 1);
  const step = width / Math.max(data.length - 1, 1);
  const pts = data.map((v, i) => `${i * step},${height - (v / max) * (height - 2) - 1}`).join(' ');
  const dot = dotIdx != null ? { cx: dotIdx * step, cy: height - (data[dotIdx] / max) * (height - 2) - 1 } : null;
  return (
    <svg width={width} height={height} style={{ display: 'block' }}>
      <polyline points={pts} fill="none" stroke={color || Cp.fg} strokeWidth="1" opacity="0.55" />
      {dot && <circle cx={dot.cx} cy={dot.cy} r="2" fill={color || Cp.amber} />}
    </svg>
  );
}

function FrameHeader({ title, subtitle }) {
  const Cp = window.C;
  return (
    <div style={{ padding: '14px 22px', borderBottom: `1px solid ${Cp.border}` }}>
      <QEyebrow color={Cp.dim}>{subtitle}</QEyebrow>
      <div style={{ marginTop: 4, fontSize: 17, fontWeight: 500, letterSpacing: '-0.015em' }}>{title}</div>
    </div>
  );
}

function Frame({ title, subtitle, children, pad = 24 }) {
  const Cp = window.C;
  return (
    <div style={{ width: '100%', height: '100%', background: Cp.bg, color: Cp.fg, fontFamily: 'var(--font-sans)', display: 'flex', flexDirection: 'column' }}>
      <FrameHeader title={title} subtitle={subtitle} />
      <div style={{ flex: 1, padding: pad, overflow: 'auto' }}>{children}</div>
    </div>
  );
}

// ─── C1 · Case row + cadence sparkline + related cases rail ─────────────

function C1_CadenceAndRelated() {
  const Cp = window.C;
  const c = CASES[0]; // Spotify case
  // Synthetic cadence — 24 patrol cycles, signal absent until cycle 14, then climbing
  const cadence = [0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,1,2,3,3,4,4,3,4,5];
  const flagIdx = 14;
  const prIdx = 18;
  const merged = null;

  // Synthetic related cases
  const related = [
    { id: '#197', when: '2026-04-22', title: 'Spotify scope renamed user-top → user-read-top', pr: { id: '#1241', state: 'merged' } },
    { id: '#162', when: '2026-03-08', title: 'Spotify rate-limit on /me/recently-played', pr: { id: '#1198', state: 'merged' } },
    { id: '#103', when: '2026-01-14', title: 'Spotify OAuth scope drift (initial)', pr: { id: '#1102', state: 'merged' } },
  ];

  return (
    <Frame title="C1 · Cadence sparkline + related cases" subtitle="case detail · history-aware">
      <div style={{ display: 'grid', gap: 24 }}>
        {/* Case header */}
        <div style={{ display: 'grid', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <QSev sev={c.sev} size={8} />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: Cp.mfg }}>{c.id}</span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: Cp.dim }}>· {c.butler}</span>
            <span style={{ flex: 1 }} />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: Cp.dim, textTransform: 'uppercase', letterSpacing: '0.10em' }}>4th time this year</span>
          </div>
          <h3 style={{ margin: 0, fontSize: 19, fontWeight: 500, letterSpacing: '-0.015em' }}>{c.headline}</h3>
        </div>

        {/* Cadence sparkline strip — life of the case across 24 patrols */}
        <div style={{ border: `1px solid ${Cp.border}`, padding: '14px 18px', display: 'grid', gap: 10 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
            <QEyebrow>Cadence · last 24 patrols · this signal only</QEyebrow>
            <QEyebrow color={Cp.dim}>14m / cycle · 5h 36m elapsed</QEyebrow>
          </div>
          <CadenceStrip data={cadence} flagIdx={flagIdx} prIdx={prIdx} mergedIdx={merged} />
          <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--font-mono)', fontSize: 9.5, color: Cp.dim, letterSpacing: '0.04em' }}>
            <span>−5h 36m</span>
            <span style={{ color: Cp.amber }}>flagged · 14:14</span>
            <span style={{ color: Cp.fg }}>pr · 14:18</span>
            <span style={{ color: Cp.dim }}>now · 14:32</span>
          </div>
        </div>

        {/* Related cases rail */}
        <div style={{ display: 'grid', gap: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
            <QEyebrow>Seen this before · 3 prior cases · all merged</QEyebrow>
            <QEyebrow color={Cp.dim}>chronicler / spotify ingest</QEyebrow>
          </div>
          <div style={{ display: 'grid', gap: 0 }}>
            {related.map((r) => (
              <div key={r.id} style={{
                display: 'grid', gridTemplateColumns: '88px 60px 1fr auto', gap: 14,
                alignItems: 'baseline', padding: '10px 0', borderBottom: `1px solid ${Cp.borderSoft}`,
                fontSize: 12.5,
              }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: Cp.dim }}>{r.when}</span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: Cp.mfg }}>{r.id}</span>
                <span style={{ color: Cp.fg }}>{r.title}</span>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: 'var(--font-mono)', fontSize: 10, color: Cp.dim, textTransform: 'uppercase', letterSpacing: '0.10em' }}>
                  <span style={{ width: 5, height: 5, borderRadius: 999, background: Cp.green }} /> pr {r.pr.id}
                </span>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 6, fontFamily: 'var(--font-serif)', fontStyle: 'italic', fontSize: 12.5, color: Cp.mfg }}>
            QA reads this as a recurring upstream-drift pattern, not a regression in our code. Consider a pinned scope-version check in the chronicler config.
          </div>
        </div>
      </div>
    </Frame>
  );
}

function CadenceStrip({ data, flagIdx, prIdx, mergedIdx }) {
  const Cp = window.C;
  const max = Math.max(...data, 1);
  return (
    <div style={{ position: 'relative', height: 48 }}>
      <div style={{ position: 'absolute', inset: 0, display: 'flex', gap: 2, alignItems: 'flex-end' }}>
        {data.map((v, i) => (
          <div key={i} style={{
            flex: 1,
            height: v === 0 ? 1 : 4 + (v / max) * 36,
            background: v === 0 ? Cp.borderSoft : i === flagIdx ? Cp.amber : i >= prIdx ? Cp.green : Cp.fg,
            opacity: v === 0 ? 1 : i < flagIdx ? 0.45 : 0.85,
            borderRadius: 1,
          }} />
        ))}
      </div>
      {/* Markers */}
      {[
        { i: flagIdx, label: 'F', color: Cp.amber },
        { i: prIdx, label: 'P', color: Cp.fg },
        ...(mergedIdx != null ? [{ i: mergedIdx, label: 'M', color: Cp.green }] : []),
      ].map((m) => (
        <div key={m.label} style={{
          position: 'absolute', left: `calc(${(m.i + 0.5) / data.length * 100}% - 6px)`, top: -2,
          width: 12, height: 12, borderRadius: 999, background: m.color, color: Cp.bg,
          fontFamily: 'var(--font-mono)', fontSize: 8, fontWeight: 600, display: 'flex',
          alignItems: 'center', justifyContent: 'center',
        }}>{m.label}</div>
      ))}
    </div>
  );
}

// ─── C2 · Patrol journal — side rail of every QA decision ───────────────

function C2_PatrolJournal() {
  const Cp = window.C;
  const c = CASES[0];
  const journal = [
    { ts: '14:14:08', kind: 'flag', text: 'patrol cycle 217 · failure_streak crossed 4', detail: 'chronicler.ingest.spotify' },
    { ts: '14:14:11', kind: 'open', text: 'opened investigation #218', detail: 'severity heuristic · low (recoverable upstream)' },
    { ts: '14:15:02', kind: 'sample', text: 'pulled 50 most recent chronicler logs', detail: 'grep level=ERROR · 14 matches' },
    { ts: '14:15:48', kind: 'sample', text: 'read butlers/chronicler/config.toml', detail: 'extracted SPOTIFY_SCOPES list' },
    { ts: '14:16:04', kind: 'sample', text: 'fetched Spotify dev portal scope reference', detail: 'cache hit · checked 14:00 freshness' },
    { ts: '14:16:33', kind: 'consider', text: 'hypothesis · token expiry', detail: 'rejected — refresh succeeded 13:58' },
    { ts: '14:16:51', kind: 'consider', text: 'hypothesis · upstream outage', detail: 'rejected — /me/player returning 200' },
    { ts: '14:17:12', kind: 'conclude', text: 'concluded · scope name drift', detail: 'confidence 0.91' },
    { ts: '14:17:48', kind: 'draft', text: 'drafted PR #1284', detail: 'branch qa/spotify-scope-rename · 18+ / 6−' },
    { ts: '14:18:02', kind: 'wait', text: 'waiting on CI · 4 checks pending', detail: 'lint · types · integration · butler-smoke' },
    { ts: '14:32:01', kind: 'tick', text: 'patrol cycle 218 · case still open', detail: 'awaiting reauth — surfaced on /overview' },
  ];
  const palette = {
    flag: Cp.amber, open: Cp.amber, sample: Cp.fg, consider: Cp.dim,
    conclude: Cp.green, draft: Cp.fg, wait: Cp.dim, tick: Cp.dim,
  };

  return (
    <Frame title="C2 · Patrol journal" subtitle="case detail · what the QA actually did">
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 360px', gap: 28, alignItems: 'start' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
            <QSev sev={c.sev} size={8} />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: Cp.mfg }}>{c.id}</span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: Cp.dim }}>· {c.butler}</span>
          </div>
          <h3 style={{ margin: '0 0 12px', fontSize: 19, fontWeight: 500, letterSpacing: '-0.015em' }}>{c.headline}</h3>
          <p style={{ margin: 0, fontFamily: 'var(--font-serif)', fontSize: 14, color: Cp.fg, lineHeight: 1.55, maxWidth: '52ch' }}>
            {c.blurb}
          </p>
        </div>

        {/* Journal rail */}
        <div style={{ borderLeft: `1px solid ${Cp.border}`, paddingLeft: 18 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 12 }}>
            <QEyebrow>Patrol journal</QEyebrow>
            <QEyebrow color={Cp.dim}>{journal.length} entries</QEyebrow>
          </div>
          <div style={{ display: 'grid', gap: 0 }}>
            {journal.map((j, i) => (
              <div key={i} style={{
                display: 'grid', gridTemplateColumns: '54px 1fr', gap: 10,
                padding: '8px 0', borderBottom: i < journal.length - 1 ? `1px solid ${Cp.borderSoft}` : 'none',
                fontFamily: 'var(--font-mono)', fontSize: 10.5, lineHeight: 1.5, alignItems: 'baseline',
              }}>
                <span style={{ color: Cp.dim, fontVariantNumeric: 'tabular-nums' }}>{j.ts.slice(0, 5)}</span>
                <div>
                  <div>
                    <span style={{ color: palette[j.kind] || Cp.fg, textTransform: 'lowercase', letterSpacing: '0.04em' }}>{j.kind}</span>
                    <span style={{ color: Cp.fg }}> · {j.text}</span>
                  </div>
                  <div style={{ color: Cp.dim, marginTop: 2 }}>{j.detail}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </Frame>
  );
}

// ─── D1 · Editorial usefulness paragraph ────────────────────────────────

function D1_EditorialUsefulness() {
  const Cp = window.C;
  return (
    <Frame title="D1 · Editorial usefulness · top of dossier" subtitle="what QA saved you · in plain English" pad={32}>
      <div style={{ display: 'grid', gap: 22, maxWidth: 760 }}>
        <div>
          <QEyebrow>This week · QA's report card</QEyebrow>
          <p style={{ margin: '12px 0 0', fontFamily: 'var(--font-serif)', fontSize: 22, fontWeight: 400, lineHeight: 1.4, letterSpacing: '-0.01em', color: Cp.fg, textWrap: 'pretty' }}>
            Landed <span style={{ fontWeight: 600 }}>17 fixes</span> across <span style={{ fontWeight: 600 }}>4 butlers</span>, mostly upstream-drift housekeeping. Median time from detection to merge was <span style={{ fontWeight: 600 }}>38 minutes</span>. Nothing has been escalated to you since the calendar reauth on Tuesday — and that one's still waiting on a click.
          </p>
        </div>

        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 0,
          borderTop: `1px solid ${Cp.border}`, borderBottom: `1px solid ${Cp.border}`,
        }}>
          {[
            { l: 'fixes landed', v: '17', s: '+2 vs prior week' },
            { l: 'self-resolved', v: '86%', s: '14% needed you' },
            { l: 'median MTTR', v: '38m', s: '−12m vs 7d' },
            { l: 'hours saved', v: '6.4h', s: '~22m / case' },
          ].map((k, i) => (
            <div key={i} style={{ padding: '14px 18px', borderRight: i < 3 ? `1px solid ${Cp.border}` : 'none' }}>
              <QEyebrow>{k.l}</QEyebrow>
              <div className="tnum" style={{ fontSize: 22, fontWeight: 500, marginTop: 4, letterSpacing: '-0.02em' }}>{k.v}</div>
              <QEyebrow color={Cp.dim}>{k.s}</QEyebrow>
            </div>
          ))}
        </div>

        <div>
          <QEyebrow>What's likely to need you next</QEyebrow>
          <p style={{ margin: '8px 0 0', fontFamily: 'var(--font-serif)', fontSize: 14.5, color: Cp.fg, lineHeight: 1.6, maxWidth: '60ch' }}>
            One open thread — Google Calendar's revoked refresh token. Two cases in active diagnosis (chronicler/spotify, calendar/oauth). The Trader Joe's backoff PR is awaiting your review for ~21h.
          </p>
        </div>
      </div>
    </Frame>
  );
}

// ─── D2 · Patrol heartbeat strip — always-visible ───────────────────────

function D2_Heartbeat() {
  const Cp = window.C;
  // 14m × ~100 cycles = 24h
  const cycles = Array.from({ length: 96 }, (_, i) => {
    // mostly 0-1 anomalies; spikes around case detections
    if ([14, 38, 62, 80, 88].includes(i)) return 3 + Math.floor(Math.random() * 2);
    if ([15, 39, 63, 81, 89].includes(i)) return 2;
    return Math.random() < 0.15 ? 1 : 0;
  });
  return (
    <Frame title="D2 · Patrol heartbeat · always visible" subtitle="thin strip · last 24h of patrols · pulses on fresh signal" pad={32}>
      <div style={{ display: 'grid', gap: 32 }}>
        {/* Standalone preview */}
        <HeartbeatStrip cycles={cycles} bigLabels />

        <div style={{ borderTop: `1px solid ${Cp.border}`, paddingTop: 28 }}>
          <QEyebrow>In context · top of dossier</QEyebrow>
          <div style={{
            marginTop: 12, border: `1px solid ${Cp.border}`,
            display: 'grid',
          }}>
            <div style={{ padding: '12px 18px', borderBottom: `1px solid ${Cp.border}`, display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ fontSize: 14, fontWeight: 500 }}>QA Staffer</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: Cp.dim }}>14:32 · cycle 218</span>
            </div>
            <HeartbeatStrip cycles={cycles} compact />
            <div style={{ padding: '14px 18px', display: 'flex', gap: 28, fontFamily: 'var(--font-mono)', fontSize: 11, color: Cp.dim }}>
              <span><span style={{ color: Cp.fg }}>5</span> signal spikes · 24h</span>
              <span><span style={{ color: Cp.fg }}>4</span> fixes landed</span>
              <span><span style={{ color: Cp.amber }}>1</span> currently open · #218 spotify</span>
            </div>
          </div>
        </div>
      </div>
    </Frame>
  );
}

function HeartbeatStrip({ cycles, bigLabels, compact }) {
  const Cp = window.C;
  const max = Math.max(...cycles, 1);
  const h = compact ? 28 : 48;
  return (
    <div>
      <div style={{
        display: 'flex', gap: 1, alignItems: 'flex-end',
        height: h, padding: compact ? '6px 18px 4px' : 0,
      }}>
        {cycles.map((v, i) => {
          const isLast = i === cycles.length - 1;
          const tone = v >= 3 ? Cp.amber : v >= 1 ? Cp.fg : Cp.borderSoft;
          return (
            <div key={i} style={{
              flex: 1,
              height: v === 0 ? 2 : 3 + (v / max) * (h - 5),
              background: tone,
              opacity: v === 0 ? 0.6 : isLast ? 1 : 0.7,
              borderRadius: 1,
              animation: isLast && v > 0 ? 'qaPulse 1.6s ease-in-out infinite' : 'none',
            }} />
          );
        })}
      </div>
      {bigLabels && (
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8, fontFamily: 'var(--font-mono)', fontSize: 10, color: Cp.dim, letterSpacing: '0.04em' }}>
          <span>24h ago</span><span>18h</span><span>12h</span><span>6h</span><span style={{ color: Cp.fg }}>now</span>
        </div>
      )}
      <style>{`@keyframes qaPulse { 0%,100% { opacity: 1 } 50% { opacity: 0.5 } }`}</style>
    </div>
  );
}

// ─── D3 · Coverage gaps ─────────────────────────────────────────────────

function D3_CoverageGaps() {
  const Cp = window.C;
  // Hours since last patrol per butler
  const coverage = [
    { butler: 'chronicler',   lastSeen: 0,  cases: 6, status: 'fresh', last: '14:32' },
    { butler: 'memory',       lastSeen: 1,  cases: 4, status: 'fresh', last: '13:47' },
    { butler: 'health',       lastSeen: 3,  cases: 3, status: 'fresh', last: '11:22' },
    { butler: 'household',    lastSeen: 6,  cases: 2, status: 'stale', last: '08:12' },
    { butler: 'calendar',     lastSeen: 5,  cases: 1, status: 'fresh', last: '09:14' },
    { butler: 'relationship', lastSeen: 11, cases: 1, status: 'gap',   last: 'yesterday 15:18' },
    { butler: 'education',    lastSeen: 26, cases: 0, status: 'cold',  last: 'last Sunday' },
  ];
  const tone = (s) => s === 'cold' ? Cp.red : s === 'gap' ? Cp.amber : s === 'stale' ? Cp.mfg : Cp.green;

  return (
    <Frame title="D3 · Coverage gaps · what QA might be missing" subtitle="dual to 'what we caught'" pad={32}>
      <div style={{ display: 'grid', gap: 18, maxWidth: 720 }}>
        <div>
          <QEyebrow>Patrol coverage · all butlers · 24h</QEyebrow>
          <p style={{ margin: '8px 0 0', fontFamily: 'var(--font-serif)', fontSize: 14, color: Cp.mfg, lineHeight: 1.55, fontStyle: 'italic' }}>
            Two butlers haven't been patrolled in over 11 hours — relationship hasn't surfaced any signal in a week, education hasn't been touched at all.
          </p>
        </div>

        <div style={{ display: 'grid', gap: 0, border: `1px solid ${Cp.border}` }}>
          {coverage.map((b, i) => (
            <div key={b.butler} style={{
              display: 'grid', gridTemplateColumns: '24px 130px 1fr 90px 90px',
              gap: 16, alignItems: 'center', padding: '12px 16px',
              borderBottom: i < coverage.length - 1 ? `1px solid ${Cp.borderSoft}` : 'none',
            }}>
              <span style={{ width: 8, height: 8, borderRadius: 999, background: tone(b.status) }} />
              <span style={{ fontSize: 13, color: Cp.fg, textTransform: 'capitalize' }}>{b.butler}</span>
              <CoverageBar hours={b.lastSeen} max={24} status={b.status} />
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: Cp.dim, textAlign: 'right' }}>last · {b.last}</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: Cp.dim, textTransform: 'uppercase', letterSpacing: '0.10em', textAlign: 'right' }}>{b.cases} cases · 7d</span>
            </div>
          ))}
        </div>

        <div style={{ display: 'flex', gap: 16, fontFamily: 'var(--font-mono)', fontSize: 10, color: Cp.dim, textTransform: 'uppercase', letterSpacing: '0.10em' }}>
          {[['fresh', Cp.green], ['stale · 4-8h', Cp.mfg], ['gap · 8-24h', Cp.amber], ['cold · >24h', Cp.red]].map(([l, c]) => (
            <span key={l} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 5, height: 5, borderRadius: 999, background: c }} /> {l}
            </span>
          ))}
        </div>
      </div>
    </Frame>
  );
}

function CoverageBar({ hours, max, status }) {
  const Cp = window.C;
  const pct = Math.min(hours / max, 1);
  const tone = status === 'cold' ? Cp.red : status === 'gap' ? Cp.amber : status === 'stale' ? Cp.mfg : Cp.green;
  return (
    <div style={{ height: 6, background: 'oklch(1 0 0 / 0.05)', borderRadius: 1, overflow: 'hidden', position: 'relative' }}>
      <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${(1 - pct) * 100}%`, background: tone, opacity: 0.85 }} />
    </div>
  );
}

// ─── App ────────────────────────────────────────────────────────────────

function CdProposalsApp() {
  React.useEffect(() => { window.applyTheme('dark'); }, []);
  return (
    <window.DesignCanvas>
      <window.DCSection id="history" title="C · Show the staffer working over time">
        <window.DCArtboard id="c1" label="C1 · Cadence + related cases" width={1200} height={780}>
          <C1_CadenceAndRelated />
        </window.DCArtboard>
        <window.DCArtboard id="c2" label="C2 · Patrol journal" width={1200} height={780}>
          <C2_PatrolJournal />
        </window.DCArtboard>
      </window.DCSection>
      <window.DCSection id="aggregate" title="D · Show usefulness in aggregate">
        <window.DCArtboard id="d1" label="D1 · Editorial usefulness paragraph" width={1100} height={680}>
          <D1_EditorialUsefulness />
        </window.DCArtboard>
        <window.DCArtboard id="d2" label="D2 · Patrol heartbeat strip" width={1100} height={680}>
          <D2_Heartbeat />
        </window.DCArtboard>
        <window.DCArtboard id="d3" label="D3 · Coverage gaps" width={1100} height={680}>
          <D3_CoverageGaps />
        </window.DCArtboard>
      </window.DCSection>
    </window.DesignCanvas>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<CdProposalsApp />);
