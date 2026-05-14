// Standalone Qa page — dossier with claim-anchored blurb, reasoning trace,
// counter-evidence, inline diff, and why-this-fix.

const { QSev, QPRChip, QStateTrack, QLogLine, QEyebrow, QKpi,
  qaStageOf, qaFilterCases } = window;

function QaPageHeader({ now }) {
  const Cp = window.C;
  const t = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  const d = now.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
  return (
    <div style={{
      padding: '20px 28px 16px', borderBottom: `1px solid ${Cp.border}`,
      display: 'grid', gridTemplateColumns: '1fr auto', gap: 24, alignItems: 'baseline',
    }}>
      <div>
        <QEyebrow>QA Staffer · dossier</QEyebrow>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 16, flexWrap: 'wrap', marginTop: 8 }}>
          <h1 style={{ margin: 0, fontSize: 26, fontWeight: 500, letterSpacing: '-0.025em' }}>What the staff caught and fixed</h1>
          <QEyebrow color={Cp.dim}>port :8474 · sonnet 4-5 · patrol every 14m</QEyebrow>
        </div>
      </div>
      <div style={{ textAlign: 'right' }}>
        <div className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 18, fontWeight: 500, color: Cp.fg, letterSpacing: '-0.02em' }}>{t}</div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: Cp.dim, letterSpacing: '0.10em', textTransform: 'uppercase', marginTop: 2 }}>{d}</div>
      </div>
    </div>
  );
}

function QaKpiStrip({ kpis }) {
  const Cp = window.C;
  const cells = [
    { label: 'prs landed · 24h',   value: kpis.prsLanded.value,    sub: kpis.prsLanded.sub,    delta: kpis.prsLanded.delta },
    { label: 'mttr · 24h',         value: kpis.mttr.value,         unit: kpis.mttr.unit, sub: kpis.mttr.sub, delta: kpis.mttr.delta },
    { label: 'self-resolved · 7d', value: kpis.selfResolved.value, unit: kpis.selfResolved.unit, sub: kpis.selfResolved.sub, delta: kpis.selfResolved.delta },
    { label: 'hours saved · 7d',   value: kpis.hoursSaved.value,   unit: kpis.hoursSaved.unit,   sub: kpis.hoursSaved.sub, delta: kpis.hoursSaved.delta },
  ];
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', borderBottom: `1px solid ${Cp.border}` }}>
      {cells.map((k, i) => (
        <div key={k.label} style={{ padding: '18px 24px', borderRight: i < 3 ? `1px solid ${Cp.border}` : 'none' }}>
          <QKpi {...k} />
        </div>
      ))}
    </div>
  );
}

// Build claim → number map and render the blurb with numbered superscript anchors
function ClaimAnchoredBlurb({ segments, claimNums, hovered, setHovered }) {
  const Cp = window.C;
  if (!segments) return null;
  return (
    <p style={{ margin: 0, fontFamily: 'var(--font-serif)', fontSize: 14.5, color: Cp.fg, lineHeight: 1.6, maxWidth: '60ch' }}>
      {segments.map((seg, i) => {
        if (typeof seg === 'string') return <span key={i}>{seg}</span>;
        const n = claimNums[seg.claim];
        const isHover = hovered === seg.claim;
        return (
          <span key={i}
            onMouseEnter={() => setHovered(seg.claim)}
            onMouseLeave={() => setHovered(null)}
            style={{
              background: isHover ? 'oklch(0.81 0.185 84 / 0.15)' : 'transparent',
              boxShadow: isHover ? `inset 0 -1px 0 ${Cp.amber}` : `inset 0 -1px 0 ${Cp.borderSoft}`,
              transition: 'background 120ms ease',
              cursor: 'help', padding: '0 2px',
            }}
          >{seg.text}<sup style={{
            fontFamily: 'var(--font-mono)', fontSize: 9, color: isHover ? Cp.amber : Cp.mfg,
            marginLeft: 2, marginRight: 1, fontWeight: 500,
          }}>[{n}]</sup></span>
        );
      })}
    </p>
  );
}

function EvidenceLine({ row, claimNums, claims, hovered, setHovered }) {
  const Cp = window.C;
  // Which claims reference this evidence?
  const claimList = Object.entries(claims || {}).filter(([, c]) => c.evidenceIds?.includes(row.id)).map(([k]) => k);
  const isHi = claimList.includes(hovered);
  const lvlColor = row.lvl === 'ERROR' ? Cp.red : row.lvl === 'WARN' ? Cp.amber : Cp.dim;
  return (
    <div onMouseEnter={() => claimList[0] && setHovered(claimList[0])}
         onMouseLeave={() => setHovered(null)}
         style={{
            display: 'grid',
            gridTemplateColumns: '20px 74px 48px 90px 1fr',
            gap: 8, alignItems: 'baseline',
            fontFamily: 'var(--font-mono)', fontSize: 10.5, lineHeight: 1.55,
            padding: '3px 6px', borderRadius: 2,
            background: isHi ? 'oklch(0.81 0.185 84 / 0.10)' : 'transparent',
            transition: 'background 120ms ease',
            cursor: claimList.length ? 'help' : 'default',
          }}>
      <span style={{ color: isHi ? Cp.amber : Cp.dim, fontSize: 9 }}>
        {claimList.length > 0 ? claimList.map((c) => `[${claimNums[c]}]`).join('') : ''}
      </span>
      <span style={{ color: Cp.dim, fontVariantNumeric: 'tabular-nums' }}>{row.ts}</span>
      <span style={{ color: lvlColor, letterSpacing: '0.06em' }}>{row.lvl}</span>
      <span style={{ color: Cp.mfg }}>{row.butler}</span>
      <span style={{ color: Cp.fg, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{row.msg}</span>
    </div>
  );
}

function PatrolJournal({ steps }) {
  const Cp = window.C;
  if (!steps?.length) return null;
  const stepColor = {
    flagged: Cp.amber, opened: Cp.amber,
    sampled: Cp.fg, 'cross-checked': Cp.fg, drafted: Cp.fg,
    considered: Cp.dim, wait: Cp.dim, tick: Cp.dim,
    concluded: Cp.green, merged: Cp.green,
  };
  return (
    <div style={{ display: 'grid', gap: 0 }}>
      {steps.map((s, i) => (
        <div key={i} style={{
          display: 'grid', gridTemplateColumns: '54px 100px 1fr', gap: 12,
          padding: '8px 0', borderBottom: i < steps.length - 1 ? `1px solid ${Cp.borderSoft}` : 'none',
          fontFamily: 'var(--font-mono)', fontSize: 10.5, lineHeight: 1.5, alignItems: 'baseline',
        }}>
          <span style={{ color: Cp.dim, fontVariantNumeric: 'tabular-nums' }}>{s.ts}</span>
          <span style={{ color: stepColor[s.step] || Cp.fg, textTransform: 'lowercase', letterSpacing: '0.04em' }}>{s.step}</span>
          <div>
            <div style={{ color: Cp.fg }}>{s.text}</div>
            {s.detail && <div style={{ color: Cp.dim, marginTop: 2 }}>{s.detail}</div>}
          </div>
        </div>
      ))}
    </div>
  );
}

function CounterEvidence({ items }) {
  const Cp = window.C;
  if (!items?.length) return null;
  return (
    <div style={{ display: 'grid', gap: 6 }}>
      {items.map((c, i) => (
        <div key={i} style={{
          display: 'grid', gridTemplateColumns: '1fr auto', gap: 12,
          fontFamily: 'var(--font-mono)', fontSize: 10.5, lineHeight: 1.55, alignItems: 'baseline',
          padding: '4px 0', borderBottom: i < items.length - 1 ? `1px solid ${Cp.borderSoft}` : 'none',
        }}>
          <div>
            <span style={{ color: Cp.fg }}>{c.hypothesis}</span>
            <span style={{ color: Cp.dim }}> · {c.reason}</span>
          </div>
          <span style={{ color: Cp.dim, textTransform: 'uppercase', letterSpacing: '0.10em', fontSize: 9 }}>{c.verdict}</span>
        </div>
      ))}
    </div>
  );
}

function DiffPreview({ lines }) {
  const Cp = window.C;
  if (!lines?.length) return null;
  return (
    <div style={{
      border: `1px solid ${Cp.border}`, background: Cp.bgDeep,
      fontFamily: 'var(--font-mono)', fontSize: 10.5, lineHeight: 1.55,
      overflowX: 'auto',
    }}>
      {lines.map((l, i) => {
        if (l.kind === 'meta') {
          return (
            <div key={i} style={{
              padding: '4px 10px', background: 'oklch(1 0 0 / 0.04)',
              color: Cp.mfg, fontSize: 10, letterSpacing: '0.04em',
              borderTop: i > 0 ? `1px solid ${Cp.borderSoft}` : 'none',
              borderBottom: `1px solid ${Cp.borderSoft}`,
            }}>{l.text}</div>
          );
        }
        const tone = l.kind === '+' ? Cp.green : l.kind === '-' ? Cp.red : Cp.dim;
        const bg = l.kind === '+' ? 'oklch(0.62 0.10 145 / 0.08)' : l.kind === '-' ? 'oklch(0.55 0.18 25 / 0.08)' : 'transparent';
        return (
          <div key={i} style={{
            display: 'grid', gridTemplateColumns: '18px 1fr', gap: 0,
            background: bg, padding: '1px 0',
          }}>
            <span style={{ color: tone, textAlign: 'center' }}>{l.kind === ' ' ? '' : l.kind}</span>
            <span style={{ color: Cp.fg, whiteSpace: 'pre' }}>{l.text}</span>
          </div>
        );
      })}
    </div>
  );
}

function PRPanel({ pr, whyThisFix }) {
  const Cp = window.C;
  const stateColor = { drafted: Cp.dim, open: Cp.amber, merged: Cp.green, closed: Cp.dim, rejected: Cp.red }[pr.state];
  return (
    <div style={{ border: `1px solid ${Cp.border}`, padding: '14px 16px', display: 'grid', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ width: 6, height: 6, borderRadius: 999, background: stateColor }} />
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: Cp.fg }}>pr {pr.id}</span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: Cp.dim, textTransform: 'uppercase', letterSpacing: '0.10em' }}>· {pr.state}</span>
        <span style={{ flex: 1 }} />
        <a href={pr.url} target="_blank" rel="noreferrer" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: Cp.fg, textDecoration: 'underline', textUnderlineOffset: 4, textDecorationColor: Cp.borderStrong }}>open →</a>
      </div>
      <div style={{ fontSize: 14, fontWeight: 500, letterSpacing: '-0.005em', lineHeight: 1.4 }}>{pr.title}</div>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: Cp.dim, letterSpacing: '0.04em' }}>
        {pr.branch} · CI {pr.ci} · +{pr.additions} −{pr.deletions}
      </div>

      {whyThisFix && (
        <div style={{ display: 'grid', gap: 4 }}>
          <QEyebrow>Why this fix</QEyebrow>
          <p style={{ margin: 0, fontFamily: 'var(--font-serif)', fontSize: 13, color: Cp.fg, lineHeight: 1.55, fontStyle: 'italic' }}>{whyThisFix}</p>
        </div>
      )}

      <div style={{ display: 'grid', gap: 4 }}>
        <QEyebrow>Diff preview</QEyebrow>
        <DiffPreview lines={pr.diff || []} />
      </div>

      <div style={{ display: 'flex', gap: 6, fontFamily: 'var(--font-mono)', fontSize: 10, color: Cp.dim }}>
        <span>opened {pr.opened}</span>
        {pr.merged && <span>· merged {pr.merged}</span>}
      </div>
    </div>
  );
}

function CaseDossier({ sel }) {
  const Cp = window.C;
  const [hovered, setHovered] = React.useState(null);

  // Build claim numbers in order of appearance in segments
  const claimNums = {};
  let n = 1;
  (sel.blurbSegments || []).forEach((s) => {
    if (typeof s !== 'string' && s.claim && !claimNums[s.claim]) claimNums[s.claim] = n++;
  });

  const prWithDiff = sel.pr ? { ...sel.pr, diff: sel.diff } : null;

  return (
    <div style={{ padding: '20px 32px', display: 'grid', gridTemplateColumns: '1.3fr 1fr', gap: 32, alignContent: 'start', overflow: 'auto' }}>
      <div style={{ gridColumn: '1 / -1', display: 'grid', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <QSev sev={sel.sev} size={8} />
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: Cp.mfg }}>{sel.id}</span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: Cp.dim }}>· {sel.butler}</span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: Cp.dim }}>· detected {sel.detected}</span>
          <span style={{ flex: 1 }} />
          <QStateTrack stage={qaStageOf(sel)} />
        </div>
        <h2 style={{ margin: 0, fontSize: 22, fontWeight: 500, letterSpacing: '-0.02em', lineHeight: 1.2 }}>{sel.headline}</h2>
      </div>

      {/* Left column — diagnosis + evidence */}
      <div style={{ display: 'grid', gap: 18 }}>
        <div style={{ display: 'grid', gap: 8 }}>
          <QEyebrow>Diagnosis</QEyebrow>
          <ClaimAnchoredBlurb segments={sel.blurbSegments} claimNums={claimNums} hovered={hovered} setHovered={setHovered} />
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: Cp.dim, fontStyle: 'italic', letterSpacing: '0.02em' }}>
            hover a claim to see its evidence ↓
          </div>
        </div>

        <div style={{ display: 'grid', gap: 8 }}>
          <QEyebrow>Hypothesis</QEyebrow>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5, color: Cp.fg }}>{sel.hypothesis}</div>
        </div>

        <div style={{ display: 'grid', gap: 6 }}>
          <QEyebrow>Evidence · log fragments</QEyebrow>
          <div style={{ display: 'grid', gap: 0 }}>
            {(sel.evidence || []).map((r, i) => (
              <EvidenceLine key={i} row={r} claimNums={claimNums} claims={sel.claims} hovered={hovered} setHovered={setHovered} />
            ))}
          </div>
        </div>

        {sel.counterEvidence?.length > 0 && (
          <div style={{ display: 'grid', gap: 6 }}>
            <QEyebrow>Considered & ruled out</QEyebrow>
            <CounterEvidence items={sel.counterEvidence} />
          </div>
        )}
      </div>

      {/* Right column — proposed fix */}
      <div style={{ display: 'grid', gap: 10, alignContent: 'start' }}>
        <QEyebrow>Proposed fix</QEyebrow>
        {prWithDiff ? <PRPanel pr={prWithDiff} whyThisFix={sel.whyThisFix} /> : (
          <div style={{ fontFamily: 'var(--font-serif)', fontSize: 13, color: Cp.dim, fontStyle: 'italic' }}>No PR — escalated to user.</div>
        )}
      </div>

      {/* Full-width — patrol journal */}
      {sel.reasoning?.length > 0 && (
        <div style={{ gridColumn: '1 / -1', borderTop: `1px solid ${Cp.border}`, paddingTop: 18, display: 'grid', gap: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
            <QEyebrow>Patrol journal · every QA decision on this case</QEyebrow>
            <QEyebrow color={Cp.dim}>{sel.reasoning.length} entries · patrol every 14m</QEyebrow>
          </div>
          <PatrolJournal steps={sel.reasoning} />
        </div>
      )}
    </div>
  );
}

function QaDossier({ data, sev }) {
  const Cp = window.C;
  const cases = qaFilterCases(data.cases, sev, '24h');
  const [selId, setSelId] = React.useState(cases[0]?.id);
  const sel = cases.find((c) => c.id === selId) || cases[0];
  return (
    <div style={{ background: Cp.bg, color: Cp.fg, fontFamily: 'var(--font-sans)', display: 'flex', flexDirection: 'column', minHeight: '100%' }}>
      <QaPageHeader now={data.now} />
      <QaKpiStrip kpis={data.kpis} />
      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '320px 1fr', minHeight: 0 }}>
        <div style={{ borderRight: `1px solid ${Cp.border}`, padding: '14px 0' }}>
          <div style={{ padding: '0 20px 10px' }}><QEyebrow>Cases · last 7d</QEyebrow></div>
          {cases.map((c) => {
            const active = sel && c.id === sel.id;
            return (
              <button key={c.id} onClick={() => setSelId(c.id)} style={{
                width: '100%', textAlign: 'left', background: active ? 'oklch(1 0 0 / 0.04)' : 'transparent',
                border: 'none', borderLeft: active ? `2px solid ${Cp.fg}` : '2px solid transparent',
                padding: '12px 18px', cursor: 'pointer', color: Cp.fg,
                display: 'grid', gap: 4, borderBottom: `1px solid ${Cp.borderSoft}`,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <QSev sev={c.sev} />
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: Cp.mfg }}>{c.id}</span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: Cp.dim }}>· {c.butler}</span>
                  <span style={{ flex: 1 }} />
                  {c.pr && <span style={{ width: 5, height: 5, borderRadius: 999, background: c.pr.state === 'merged' ? Cp.green : c.pr.state === 'open' ? Cp.amber : Cp.dim }} />}
                </div>
                <div style={{ fontSize: 12.5, lineHeight: 1.35 }}>{c.headline}</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: Cp.dim, letterSpacing: '0.04em' }}>{c.detected} · {c.age}</div>
              </button>
            );
          })}
        </div>

        {sel && <CaseDossier sel={sel} />}
      </div>
    </div>
  );
}

function QaApp() {
  const [theme, setTheme] = React.useState('dark');
  const [sev, setSev] = React.useState('all');

  React.useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.applyTheme(theme);
  }, [theme]);

  const data = React.useMemo(() => ({
    now: window.QA_NOW, cases: window.QA_CASES, tail: window.QA_TAIL,
    kpis: window.QA_KPIS, patrol24h: window.QA_PATROL_24H,
    pr7d: window.QA_PR_7D, byButler: window.QA_BY_BUTLER_7D,
  }), [theme]);

  const isDark = theme === 'dark';
  const barBg     = isDark ? 'oklch(0.115 0 0 / 0.92)' : 'oklch(0.985 0.003 85 / 0.92)';
  const barBorder = isDark ? 'oklch(1 0 0 / 0.10)'    : 'oklch(0 0 0 / 0.10)';
  const barFg     = isDark ? 'oklch(0.708 0 0)'       : 'oklch(0.46 0 0)';
  const barDim    = isDark ? 'oklch(0.55 0 0)'        : 'oklch(0.62 0 0)';
  const pageBg    = isDark ? 'oklch(0.115 0 0)'       : 'oklch(0.965 0.005 85)';
  const pageFg    = isDark ? 'oklch(0.985 0 0)'       : 'oklch(0.18 0 0)';
  const btnBorder = isDark ? 'oklch(1 0 0 / 0.18)'    : 'oklch(0 0 0 / 0.20)';
  const activeBg  = isDark ? 'oklch(0.985 0 0)'       : 'oklch(0.18 0 0)';
  const activeFg  = isDark ? 'oklch(0.145 0 0)'       : 'oklch(0.985 0 0)';

  return (
    <div key={theme} style={{ minHeight: '100vh', background: pageBg, color: pageFg, fontFamily: 'var(--font-sans)' }}>
      <div style={{
        position: 'sticky', top: 0, zIndex: 10,
        background: barBg, backdropFilter: 'blur(8px)', borderBottom: `1px solid ${barBorder}`,
        padding: '10px 24px', display: 'flex', alignItems: 'center', gap: 16,
        fontFamily: 'var(--font-mono)', fontSize: 11,
      }}>
        <span style={{ color: barFg, textTransform: 'uppercase', letterSpacing: '0.1em' }}>QA staffer · /qa</span>
        <span style={{ color: barDim }}>·</span>
        <span style={{ color: barFg }}>severity</span>
        <div style={{ display: 'flex', gap: 4 }}>
          {['all','high','medium','low'].map((s) => (
            <button key={s} onClick={() => setSev(s)} style={{
              background: sev === s ? activeBg : 'transparent', color: sev === s ? activeFg : pageFg,
              border: `1px solid ${btnBorder}`, padding: '4px 10px',
              fontFamily: 'var(--font-mono)', fontSize: 11, cursor: 'pointer', borderRadius: 3, textTransform: 'lowercase',
            }}>{s}</button>
          ))}
        </div>
        <div style={{ flex: 1 }} />
        <div style={{ display: 'flex', gap: 4 }}>
          {['dark','light'].map((th) => (
            <button key={th} onClick={() => setTheme(th)} style={{
              background: theme === th ? activeBg : 'transparent', color: theme === th ? activeFg : pageFg,
              border: `1px solid ${btnBorder}`, padding: '4px 10px',
              fontFamily: 'var(--font-mono)', fontSize: 11, cursor: 'pointer', borderRadius: 3, textTransform: 'lowercase',
            }}>{th}</button>
          ))}
        </div>
      </div>
      <div style={{ display: 'flex', minHeight: 'calc(100vh - 41px)' }}>
        <window.Sidebar data={window.BUTLERS_DATA} theme={theme} activeRoute="/qa" />
        <div style={{ flex: 1, minWidth: 0 }}>
          <QaDossier data={data} sev={sev} />
        </div>
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<QaApp />);
