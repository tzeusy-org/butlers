// Shared atoms for the QA staffer redesign.

const Cq = window.C;

// Severity glyph — small filled square; high/med/low
function QSev({ sev, size = 6 }) {
  const map = { high: Cq.red, medium: Cq.amber, low: Cq.dim };
  return <span style={{ width: size, height: size, background: map[sev] || Cq.dim, display: 'inline-block', borderRadius: 1, flexShrink: 0 }} />;
}

// Pill that reflects PR state — drafted (dim) / open (amber) / merged (green) / closed (dim)
function QPRChip({ pr, size = 'sm' }) {
  if (!pr) return null;
  const colorMap = {
    drafted: Cq.dim, open: Cq.amber, merged: Cq.green, closed: Cq.dim, rejected: Cq.red,
  };
  const tone = colorMap[pr.state] || Cq.dim;
  const fz = size === 'lg' ? 11 : 9.5;
  const pad = size === 'lg' ? '4px 8px' : '2px 6px';
  return (
    <a href={pr.url} target="_blank" rel="noreferrer" style={{
      display: 'inline-flex', alignItems: 'center', gap: 6,
      fontFamily: 'var(--font-mono)', fontSize: fz,
      letterSpacing: '0.10em', textTransform: 'uppercase',
      color: Cq.fg, padding: pad, border: `1px solid ${Cq.border}`,
      borderRadius: 2, textDecoration: 'none', whiteSpace: 'nowrap',
    }}>
      <span style={{ width: 5, height: 5, borderRadius: 999, background: tone }} />
      <span style={{ color: Cq.fg }}>pr {pr.id}</span>
      <span style={{ color: Cq.dim }}>· {pr.state}</span>
      <span style={{ color: Cq.dim, fontVariantNumeric: 'tabular-nums' }}>+{pr.additions} −{pr.deletions}</span>
    </a>
  );
}

// State track — shows a case's position in the detect→hypothesize→pr→landed pipeline
function QStateTrack({ stage }) {
  // stage: 'detect' | 'hypothesize' | 'pr' | 'landed' | 'escalated'
  const stages = [
    { k: 'detect', l: 'detect' },
    { k: 'hypothesize', l: 'diagnose' },
    { k: 'pr', l: 'pr' },
    { k: 'landed', l: 'landed' },
  ];
  const idx = stages.findIndex((s) => s.k === stage);
  const escalated = stage === 'escalated';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.10em', textTransform: 'uppercase' }}>
      {stages.map((s, i) => {
        const active = i === idx;
        const done = i < idx;
        const tone = escalated && i >= 2 ? Cq.amber : done ? Cq.fg : active ? Cq.fg : Cq.dim;
        return (
          <React.Fragment key={s.k}>
            <span style={{ color: tone, fontWeight: active ? 500 : 400 }}>{s.l}</span>
            {i < stages.length - 1 && <span style={{ color: Cq.borderSoft }}>—</span>}
          </React.Fragment>
        );
      })}
      {escalated && (
        <span style={{ color: Cq.amber, marginLeft: 8 }}>· escalated</span>
      )}
    </div>
  );
}

function stageOf(c) {
  if (c.state.startsWith('escalated')) return 'escalated';
  if (c.pr && c.pr.state === 'merged') return 'landed';
  if (c.pr && (c.pr.state === 'open' || c.pr.state === 'drafted')) return 'pr';
  if (c.state.startsWith('open')) return 'hypothesize';
  return 'landed';
}

// Compact log line — used in the live tail
function QLogLine({ row, dense, withButler = true }) {
  const lvlColor = row.lvl === 'ERROR' ? Cq.red : row.lvl === 'WARN' ? Cq.amber : Cq.dim;
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: withButler ? '74px 48px 96px 1fr' : '74px 48px 1fr',
      gap: 10, alignItems: 'baseline',
      fontFamily: 'var(--font-mono)', fontSize: dense ? 10.5 : 11,
      lineHeight: dense ? 1.5 : 1.6,
      padding: dense ? '2px 0' : '3px 0',
    }}>
      <span style={{ color: Cq.dim, fontVariantNumeric: 'tabular-nums' }}>{row.ts}</span>
      <span style={{ color: lvlColor, letterSpacing: '0.06em' }}>{row.lvl}</span>
      {withButler && <span style={{ color: Cq.mfg, textTransform: 'lowercase' }}>{row.butler}</span>}
      <span style={{ color: Cq.fg, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{row.msg}</span>
    </div>
  );
}

// Mono eyebrow
function QEyebrow({ children, color }) {
  return <span style={{
    fontFamily: 'var(--font-mono)', fontSize: 9.5, color: color || Cq.mfg,
    textTransform: 'uppercase', letterSpacing: '0.14em',
  }}>{children}</span>;
}

// KPI strip cell
function QKpi({ label, value, unit, sub, delta, big }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <QEyebrow>{label}</QEyebrow>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
        <span className="tnum" style={{
          fontSize: big ? 32 : 26, fontWeight: 500, letterSpacing: '-0.025em',
          color: Cq.fg, lineHeight: 1, fontFamily: 'var(--font-sans)',
        }}>{value}</span>
        {unit && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: Cq.mfg }}>{unit}</span>}
      </div>
      {sub && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: Cq.dim, letterSpacing: '0.04em' }}>{sub}</span>}
      {delta && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: Cq.dim, letterSpacing: '0.04em' }}>{delta}</span>}
    </div>
  );
}

// Bars — daily PR throughput etc
function QBars({ data, height = 36, color, accentIdx }) {
  const max = Math.max(...data, 1);
  return (
    <div style={{ display: 'flex', gap: 3, alignItems: 'flex-end', height }}>
      {data.map((v, i) => (
        <div key={i} style={{
          flex: 1,
          height: 2 + (v / max) * (height - 2),
          background: i === accentIdx ? Cq.fg : (color || Cq.fg),
          opacity: i === accentIdx ? 1 : 0.55,
          borderRadius: 1,
        }} />
      ))}
    </div>
  );
}

// Filter the live tail by severity
function filterTail(rows, sev) {
  if (sev === 'all') return rows;
  if (sev === 'high') return rows.filter((r) => r.lvl === 'ERROR');
  if (sev === 'medium') return rows.filter((r) => r.lvl === 'ERROR' || r.lvl === 'WARN');
  return rows;
}

// Filter cases by severity and time range
function filterCases(cases, sev, range) {
  let out = cases;
  if (sev !== 'all') out = out.filter((c) => c.sev === sev);
  // range is purely cosmetic with synthetic data; no-op semantics for now
  return out;
}

window.QSev = QSev;
window.QPRChip = QPRChip;
window.QStateTrack = QStateTrack;
window.QLogLine = QLogLine;
window.QEyebrow = QEyebrow;
window.QKpi = QKpi;
window.QBars = QBars;
window.qaStageOf = stageOf;
window.qaFilterTail = filterTail;
window.qaFilterCases = filterCases;
