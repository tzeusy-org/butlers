// Slim harness for the /butlers/ page. Same scenario picker + theme toggle
// as the Overview, since states like "calendar paused" change what the page
// reads. No LLM toggle here — the voice paragraph is composed locally from
// state, not from a model.

const BASE_B = window.BUTLERS_DATA;

const SCENARIOS_B = {
  'as-is':     { label: 'As-is (calendar paused)',  transform: (d) => d },
  'all-quiet': {
    label: 'All quiet',
    transform: (d) => ({
      ...d,
      butlers: d.butlers.map((b) => ({
        ...b,
        status: 'ok',
        activity: b.activity === 'paused' ? 'idle'
                : b.activity === 'awaiting approval' ? 'idle'
                : b.activity,
        lastRun: b.activity === 'paused' ? '4m ago' : b.lastRun,
      })),
    }),
  },
  'two-down': {
    label: 'Two paused',
    transform: (d) => ({
      ...d,
      butlers: d.butlers.map((b) => {
        if (b.name === 'calendar') return b;
        if (b.name === 'chronicler') return { ...b, status: 'degraded', activity: 'paused', lastRun: '1h 12m', loadPct: 0 };
        return b;
      }),
    }),
  },
  'evening': {
    label: 'Evening (low traffic)',
    transform: (d) => ({
      ...d,
      now: new Date('2026-05-06T20:14:00'),
      butlers: d.butlers.map((b) => ({
        ...b,
        sessions24h: Math.max(1, Math.round(b.sessions24h * 0.85)),
        loadPct: Math.max(0, Math.round(b.loadPct * 0.4)),
        activity: b.activity === 'patrol' ? 'idle'
                : b.activity === 'running' ? 'idle'
                : b.activity,
      })),
    }),
  },
};

function ButlersApp() {
  const [scenario, setScenario] = React.useState('as-is');
  const [theme, setTheme] = React.useState('dark');

  React.useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.applyTheme(theme);
  }, [theme]);

  const data = React.useMemo(() => {
    const sc = SCENARIOS_B[scenario] || SCENARIOS_B['as-is'];
    return sc.transform(BASE_B);
  }, [scenario]);

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
    <div key={theme} style={{
      minHeight: '100vh', background: pageBg, color: pageFg,
      fontFamily: 'var(--font-sans)',
    }}>
      {/* Top control bar — same shape as Overview's */}
      <div style={{
        position: 'sticky', top: 0, zIndex: 10,
        background: barBg, backdropFilter: 'blur(8px)',
        borderBottom: `1px solid ${barBorder}`,
        padding: '10px 24px',
        display: 'flex', alignItems: 'center', gap: 16,
        fontFamily: 'var(--font-mono)', fontSize: 11,
      }}>
        <span style={{ color: barFg, textTransform: 'uppercase', letterSpacing: '0.1em' }}>
          Butlers · roster
        </span>
        <span style={{ color: barDim }}>·</span>
        <a href="Overview.html" style={{
          color: barFg, textDecoration: 'underline',
          textUnderlineOffset: 4, textDecorationColor: barBorder,
          fontSize: 11, textTransform: 'lowercase', letterSpacing: '0.05em',
        }}>← back to overview</a>
        <span style={{ color: barDim }}>·</span>
        <span style={{ color: barFg }}>scenario</span>
        <div style={{ display: 'flex', gap: 4 }}>
          {Object.entries(SCENARIOS_B).map(([k, v]) => (
            <button key={k}
              onClick={() => setScenario(k)}
              style={{
                background: scenario === k ? activeBg : 'transparent',
                color: scenario === k ? activeFg : pageFg,
                border: `1px solid ${btnBorder}`,
                padding: '4px 10px', fontFamily: 'var(--font-mono)', fontSize: 11,
                cursor: 'pointer', borderRadius: 3,
              }}>{v.label}</button>
          ))}
        </div>
        <div style={{ flex: 1 }} />
        <div style={{ display: 'flex', gap: 4 }}>
          {['dark', 'light'].map((th) => (
            <button key={th}
              onClick={() => setTheme(th)}
              style={{
                background: theme === th ? activeBg : 'transparent',
                color: theme === th ? activeFg : pageFg,
                border: `1px solid ${btnBorder}`,
                padding: '4px 10px', fontFamily: 'var(--font-mono)', fontSize: 11,
                cursor: 'pointer', borderRadius: 3, textTransform: 'lowercase',
              }}>{th}</button>
          ))}
        </div>
      </div>

      <div style={{ display: 'flex', minHeight: 'calc(100vh - 41px)' }}>
        <window.Sidebar data={data} theme={theme} activeRoute="/butlers" />
        <div style={{ flex: 1, minWidth: 0 }}>
          <window.ButlersIndex data={data} />
        </div>
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<ButlersApp />);
