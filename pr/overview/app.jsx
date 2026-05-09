// Demo harness — Editorial focus, with state cycler so you can see how the
// briefing changes across system states. Plus a backend-integration sketch.

const { useTweaks } = window;
const { useBriefing } = window;

// Build alternate state snapshots so we can preview different briefings.
const BASE = window.BUTLERS_DATA;

const SCENARIOS = {
  'as-is': {
    label: 'As-is (afternoon, mild)',
    transform: (d) => d, // current state
  },
  'all-quiet': {
    label: 'All quiet',
    transform: (d) => ({
      ...d,
      attention: [],
      butlers: d.butlers.map((b) => ({ ...b, status: 'ok', activity: b.activity === 'paused' ? 'idle' : b.activity })),
    }),
  },
  'urgent': {
    label: 'Urgent (one high)',
    transform: (d) => ({ ...d, attention: d.attention.filter((a) => a.severity === 'high') }),
  },
  'busy': {
    label: 'Busy (many items)',
    transform: (d) => ({
      ...d,
      attention: [
        ...d.attention,
        { id: 'a5', kind: 'approval', severity: 'medium', butler: 'education', title: 'Confirm Anki deck import', detail: 'CS basics deck — 412 cards.', action: 'Review', age: '23m' },
        { id: 'a6', kind: 'approval', severity: 'low',    butler: 'chronicler', title: 'Review timeline gap (12:00–12:30)', detail: 'No location, no events. Walking?', action: 'Annotate', age: '2h' },
      ],
    }),
  },
  'evening': {
    label: 'Evening, quiet',
    transform: (d) => ({
      ...d,
      now: new Date('2026-05-06T20:14:00'),
      attention: [d.attention[2]], // just maya draft
    }),
  },
};

function App() {
  const [scenario, setScenario] = React.useState('as-is');
  const [useLLM, setUseLLM] = React.useState(false);
  const [theme, setTheme] = React.useState('dark');

  // Apply theme to <html> + the live C palette + force a re-render
  React.useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.applyTheme(theme);
  }, [theme]);

  const data = React.useMemo(() => {
    const sc = SCENARIOS[scenario] || SCENARIOS['as-is'];
    return sc.transform(BASE);
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
      {/* Top control bar */}
      <div style={{
        position: 'sticky', top: 0, zIndex: 10,
        background: barBg, backdropFilter: 'blur(8px)',
        borderBottom: `1px solid ${barBorder}`,
        padding: '10px 24px',
        display: 'flex', alignItems: 'center', gap: 16,
        fontFamily: 'var(--font-mono)', fontSize: 11,
      }}>
        <span style={{ color: barFg, textTransform: 'uppercase', letterSpacing: '0.1em' }}>
          Editorial · briefing demo
        </span>
        <span style={{ color: barDim }}>·</span>
        <span style={{ color: barFg }}>scenario</span>
        <div style={{ display: 'flex', gap: 4 }}>
          {Object.entries(SCENARIOS).map(([k, v]) => (
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
        <div style={{ display: 'flex', gap: 4, marginRight: 12 }}>
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
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, color: barFg, cursor: 'pointer' }}>
          <input type="checkbox" checked={useLLM} onChange={(e) => setUseLLM(e.target.checked)} />
          Use LLM elaboration
        </label>
      </div>

      <div style={{ display: 'flex', minHeight: 'calc(100vh - 41px)' }}>
        <window.Sidebar data={data} theme={theme} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <V4WithBriefing data={data} useLLM={useLLM} theme={theme} />
          <BackendSketch theme={theme} />
        </div>
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
