// Ingestion app — page shell.
//
// Route shape mirrors butlers/{butler}:
//   /ingestion              → Timeline (default)
//   /ingestion/connectors   → Roster (clickable rows open detail)
//   /ingestion/connectors/<id> → Detail (e.g. Spotify)
//   /ingestion/filters      → Filters (the pipeline + rules)
//
// The page owns the sub-nav and the inner-route state. The current tab is
// the only thing in the URL hash (#timeline / #connectors / #filters) so a
// reload lands on the same view.

function getInitial() {
  const hash = (window.location.hash || '').replace('#', '');
  const [tab, sub] = hash.split('/');
  return { tab: ['timeline', 'connectors', 'filters'].includes(tab) ? tab : 'timeline', sub };
}

function App() {
  const init = getInitial();
  const [tab, setTab] = React.useState(init.tab);
  const [selectedConnector, setSelectedConnector] = React.useState(init.sub || null);

  React.useEffect(() => {
    let h = '#' + tab;
    if (tab === 'connectors' && selectedConnector) h += '/' + selectedConnector;
    window.history.replaceState(null, '', h);
  }, [tab, selectedConnector]);

  // ── Theme toggle (light/dark) — keep dark canonical, allow light review.
  const [theme, setTheme] = React.useState(localStorage.getItem('ingestion.theme') || 'dark');
  React.useEffect(() => {
    window.applyTheme(theme);
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('ingestion.theme', theme);
  }, [theme]);

  const C = window.C;

  // Expose connector navigation for Roster rows to use.
  window.openConnector = (id) => { setTab('connectors'); setSelectedConnector(id); };
  window.backToConnectors = () => setSelectedConnector(null);

  return (
    <div style={{ background: C.bg, color: C.fg, minHeight: '100vh',
      fontFamily: 'var(--font-sans)',
    }}>
      <PageNav tab={tab} onChange={(t) => { setTab(t); if (t !== 'connectors') setSelectedConnector(null); }} />

      {tab === 'timeline'   && <window.V1_Ledger />}
      {tab === 'connectors' && (
        selectedConnector
          ? <ConnectorDetailHost id={selectedConnector} onBack={() => setSelectedConnector(null)} />
          : <window.ConnectorsRoster onOpen={(id) => setSelectedConnector(id)} />
      )}
      {tab === 'filters'    && <window.IngestionFilters />}

      {/* Theme toggle */}
      <button type="button" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
        title={`Switch to ${theme === 'dark' ? 'light' : 'dark'}`}
        style={{
          position: 'fixed', bottom: 18, right: 18, zIndex: 100,
          border: `1px solid ${C.border}`, borderRadius: 3,
          background: window.PALETTES[theme].bgElev, color: C.fg,
          padding: '6px 10px',
          fontFamily: 'var(--font-mono)', fontSize: 10,
          letterSpacing: '0.10em', textTransform: 'uppercase', cursor: 'pointer',
        }}>{theme}</button>
    </div>
  );
}

// ─── Sub-nav for /ingestion ───────────────────────────────────────────
function PageNav({ tab, onChange }) {
  const C = window.C;
  const tabs = [
    { id: 'timeline',   label: 'Timeline',   note: 'events' },
    { id: 'connectors', label: 'Connectors', note: 'sources' },
    { id: 'filters',    label: 'Filters',    note: 'rules · pipeline' },
  ];
  return (
    <div style={{
      position: 'sticky', top: 0, zIndex: 50,
      background: window.__theme === 'light' ? 'oklch(0.985 0.003 85 / 0.92)' : 'oklch(0.115 0 0 / 0.92)',
      backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)',
      borderBottom: `1px solid ${C.border}`,
    }}>
      <div style={{
        maxWidth: 1500, margin: '0 auto', padding: '0 56px',
        display: 'flex', alignItems: 'center', gap: 32,
      }}>
        {/* Page identity */}
        <div style={{ padding: '14px 0', display: 'flex', alignItems: 'baseline', gap: 10 }}>
          <Mono color={C.dim} size={10} style={{ letterSpacing: '0.10em', textTransform: 'uppercase' }}>
            / ingestion
          </Mono>
        </div>

        {/* Sub-nav */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 28, flex: 1 }}>
          {tabs.map((t) => {
            const active = t.id === tab;
            return (
              <button key={t.id} type="button" onClick={() => onChange(t.id)}
                style={{
                  background: 'transparent', border: 'none', cursor: 'pointer',
                  padding: '16px 0',
                  fontFamily: 'var(--font-sans)', fontSize: 14,
                  fontWeight: active ? 500 : 400, letterSpacing: '-0.005em',
                  color: active ? C.fg : C.mfg,
                  borderBottom: `1px solid ${active ? C.fg : 'transparent'}`,
                  marginBottom: -1,
                  display: 'inline-flex', alignItems: 'baseline', gap: 8,
                }}>
                {t.label}
                <Mono color={C.dim} size={9.5}>· {t.note}</Mono>
              </button>
            );
          })}
        </div>

        {/* Right-rail meta (just decoration here — links a real shell would show) */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
          <a href="#" onClick={(ev) => ev.preventDefault()}
            style={{
              fontFamily: 'var(--font-mono)', fontSize: 10,
              letterSpacing: '0.10em', textTransform: 'uppercase',
              color: C.dim, textDecoration: 'underline', textUnderlineOffset: 3,
              textDecorationColor: C.borderStrong,
            }}>+ add connector</a>
        </div>
      </div>
    </div>
  );
}

// Wrapper that adds a back chip to the Spotify detail.
function ConnectorDetailHost({ id, onBack }) {
  // Currently only Spotify is fleshed out; everything else falls through
  // to a placeholder so navigation stays consistent.
  if (id === 'spotify') return <window.ConnectorDetailSpotify onBack={onBack} />;
  return <ConnectorDetailStub id={id} onBack={onBack} />;
}

function ConnectorDetailStub({ id, onBack }) {
  const C = window.C;
  const c = (window.CONNECTOR_DETAILS || []).find((x) => x.id === id);
  return (
    <div style={{ background: C.bg, color: C.fg, minHeight: '100%' }}>
      <div style={{ maxWidth: 1500, margin: '0 auto', padding: '40px 56px 80px' }}>
        <div style={{ marginBottom: 18 }}>
          <a href="#" onClick={(ev) => { ev.preventDefault(); onBack(); }} style={{
            fontFamily: 'var(--font-mono)', fontSize: 10, color: C.dim,
            textDecoration: 'underline', textUnderlineOffset: 4,
            textDecorationColor: C.borderStrong, letterSpacing: '0.10em', textTransform: 'uppercase',
          }}>← ingestion / connectors</a>
        </div>
        <Eyebrow style={{ marginBottom: 8 }}>connector · {id}</Eyebrow>
        <h1 style={{ margin: 0, fontSize: 36, fontWeight: 500, letterSpacing: '-0.025em' }}>
          {c?.label || id}.
        </h1>
        <div style={{
          marginTop: 12, fontFamily: 'var(--font-serif)', fontStyle: 'italic',
          fontSize: 15, color: C.mfg, maxWidth: '50ch', lineHeight: 1.5,
        }}>
          Detail page not yet designed for this channel — Spotify is the
          reference. Open <a href="#" onClick={(ev) => { ev.preventDefault(); window.openConnector('spotify'); }}
            style={{ color: C.fg, textDecoration: 'underline', textDecorationColor: C.borderStrong }}>Spotify</a>{' '}
          to see the shape.
        </div>
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
