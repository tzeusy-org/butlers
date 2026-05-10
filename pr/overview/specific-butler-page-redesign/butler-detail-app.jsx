// Main app for /butlers/{butler}. Wires sidebar + page.

const { Panel, MonoLabel, RangeToggle, ButlerSwitcher, Tabs, Hero } = window.BUTLER_ATOMS;
const { OverviewTab, ActivityTab, LogsTab, ApprovalsTab, SpendTab, ConfigTab, MemoryTab } = window.BUTLER_TABS;
const { useButlerKey, BASE_TABS } = window.BUTLER_HOOKS;

function ButlerDetailApp() {
  const [butlerKey, setButlerKey] = useButlerKey();
  const [tab, setTab] = React.useState('Overview');
  const [range, setRange] = React.useState('24h');
  const [paused, setPaused] = React.useState(false);

  React.useEffect(() => { window.applyTheme('dark'); }, []);
  // When the butler changes, jump back to Overview to avoid stale tab.
  React.useEffect(() => { setTab('Overview'); }, [butlerKey]);

  const butler = window.BUTLERS_DATA.butlers.find((b) => b.name === butlerKey)
              || window.BUTLERS_DATA.butlers[0];
  const detail = window.BUTLER_DETAILS[butler.name];
  const tabs = [...BASE_TABS, detail.bespoke.tab];
  const Bespoke = window.BespokeFor(detail.bespoke.kind);

  const renderTab = () => {
    if (tab === 'Overview')   return <OverviewTab butler={butler} detail={detail} range={range} />;
    if (tab === 'Activity')   return <ActivityTab butler={butler} range={range} />;
    if (tab === 'Logs')       return <LogsTab butler={butler} />;
    if (tab === 'Approvals')  return <ApprovalsTab butler={butler} />;
    if (tab === 'Spend')      return <SpendTab butler={butler} range={range} />;
    if (tab === 'Config')     return <ConfigTab butler={butler} detail={detail} />;
    if (tab === 'Memory')     return <MemoryTab detail={detail} />;
    if (tab === detail.bespoke.tab && Bespoke) {
      return <Bespoke content={window.BESPOKE_CONTENT[detail.bespoke.kind]} />;
    }
    return null;
  };

  // Tabs that show range toggle
  const tabUsesRange = tab === 'Overview' || tab === 'Activity' || tab === 'Spend';

  return (
    <div style={{
      display: 'flex', minHeight: '100vh', height: '100vh',
      background: window.C.bg, color: window.C.fg,
      fontFamily: 'var(--font-sans)',
    }}>
      <Sidebar data={window.BUTLERS_DATA} theme="dark" activeRoute="/butlers" />

      {/* Main */}
      <main style={{
        flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
      }}>
        {/* Top crumb + butler switcher */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 18,
          padding: '14px 28px', borderBottom: `1px solid ${window.C.borderSoft}`,
          flexShrink: 0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <a href="butlers-app.html" style={{ color: window.C.mfg, textDecoration: 'none', fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.06em' }}>← /butlers</a>
            <span style={{ color: window.C.dim, fontFamily: 'var(--font-mono)', fontSize: 11 }}>/</span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: window.C.fg, textTransform: 'capitalize' }}>{butler.name}</span>
          </div>
          <div style={{ flex: 1, overflow: 'auto' }}>
            <ButlerSwitcher activeKey={butlerKey} onChange={setButlerKey} />
          </div>
          <NowMark now={window.BUTLERS_DATA.now} />
        </div>

        {/* Hero */}
        <div style={{ padding: '8px 28px 0', flexShrink: 0 }}>
          <Hero butler={butler} detail={detail} paused={paused} onPause={() => setPaused(!paused)} />
        </div>

        {/* Tabs row */}
        <div style={{ padding: '12px 28px 0', display: 'flex', alignItems: 'center', gap: 14, flexShrink: 0 }}>
          <Tabs tabs={tabs} value={tab} onChange={setTab} bespokeTab={detail.bespoke.tab} />
          {tabUsesRange && <RangeToggle value={range} onChange={setRange} />}
        </div>

        {/* Panel grid */}
        <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: '16px 28px 28px' }}>
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)',
            borderTop: `1px solid ${window.C.border}`, borderLeft: `1px solid ${window.C.border}`,
          }}>
            {renderTab()}
          </div>
        </div>
      </main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<ButlerDetailApp />);
