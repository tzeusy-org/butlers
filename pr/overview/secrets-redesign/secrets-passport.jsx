// Composition — orchestrates spine + page + tweaks for /secrets.
//
// State held here:
//   identityId, activeKey, sortMode, search, revealMode
//
// Wires the spine to the page; honors the tweaks panel for reveal-mode
// and default sort; renders the page heading band + book body.

const Cs_C = window.C;

function resolveByKey(key, identityId) {
  const [kind, ...rest] = key.split(':');
  const ident = rest.join(':');
  if (kind === 'u') {
    return { kind: 'user', record: window.USER_SECRETS.find((s) => s.provider === ident && s.identity === identityId) };
  }
  if (kind === 's') {
    return { kind: 'system', record: window.SYSTEM_SECRETS.find((s) => s.key === ident) };
  }
  if (kind === 'c') {
    return { kind: 'cli', record: window.CLI_RUNTIMES.find((r) => r.id === ident) };
  }
  return { kind: null, record: null };
}

function pickDefaultKey(entries) {
  // Pick the most severe one as the default focus. Falls back to first.
  const sorted = [...entries].sort((a, b) => window.severityRank(a.state) - window.severityRank(b.state));
  return sorted[0]?.key || entries[0]?.key;
}

function DirectionPassport({ tweaks = {} }) {
  const [identityId, setIdentityId] = React.useState('tze');
  const entries = React.useMemo(() => window.buildSpineEntries(identityId), [identityId]);
  const [sortMode, setSortMode] = React.useState(tweaks.defaultSort || 'severity');
  const [search, setSearch] = React.useState('');
  const [activeKey, setActiveKey] = React.useState(() => pickDefaultKey(entries));
  // The reveal-mode tweak is read by the pages via a global; we expose
  // a tiny shim here so the change reaches the per-secret atoms.
  React.useEffect(() => { window.__revealMode = tweaks.revealMode || 'eye'; }, [tweaks.revealMode]);

  // If identity changes, rebuild entries and reselect a sensible default.
  React.useEffect(() => {
    if (!entries.find((e) => e.key === activeKey)) {
      setActiveKey(pickDefaultKey(entries));
    }
  }, [identityId]); // eslint-disable-line

  const resolved = resolveByKey(activeKey, identityId);
  const identity = window.IDENTITIES.find((i) => i.id === identityId);

  const k = window.computeKpis(identityId);
  const needsAttention = k.integrations.needsHand + k.cli.attention;

  return (
    <div style={{
      background: Cs_C.bg, color: Cs_C.fg, fontFamily: 'var(--font-sans)',
      display: 'flex', minHeight: '100%',
    }}>
      <window.FakeRail />
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        {/* Page header — eyebrow, headline, identity controls */}
        <div style={{ padding: '28px 36px 18px', borderBottom: `1px solid ${Cs_C.border}` }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', gap: 24 }}>
            <div style={{ minWidth: 0 }}>
              <window.Eyebrow sub="Sat, 23 May 2026 · 14:23">secrets</window.Eyebrow>
              <div style={{ marginTop: 10 }}>
                <window.Display maxWidth="28ch" size={32}>
                  {needsAttention === 0
                    ? 'Every credential, accounted for.'
                    : needsAttention === 1
                      ? 'One credential needs attention.'
                      : `${needsAttention} credentials need attention.`}
                </window.Display>
              </div>
              {needsAttention > 0 && (
                <div style={{ marginTop: 10, maxWidth: '60ch' }}>
                  <window.Voice italic={false}>
                    {k.integrations.needsHand > 0 && (
                      <>{k.integrations.needsHand} integration{k.integrations.needsHand === 1 ? '' : 's'} sick. </>
                    )}
                    {k.cli.attention > 0 && (
                      <>{k.cli.attention} runtime token expiring. </>
                    )}
                    Everything else verified within the hour.
                  </window.Voice>
                </div>
              )}
            </div>

            {/* KPI block + identity chip */}
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 12 }}>
              <window.IdentityChip identity={identity} compact />
              <div style={{ display: 'flex', gap: 14, alignItems: 'baseline' }}>
                <KpiCell label="integrations" value={`${k.integrations.healthy}/${k.integrations.total}`} caption={`${k.integrations.needsHand} need hand`} captionTone={k.integrations.needsHand ? 'amber' : 'dim'} />
                <Sep />
                <KpiCell label="system"       value={`${k.system.configured}/${k.system.total}`} caption={`${k.system.missing} unset`} />
                <Sep />
                <KpiCell label="cli"          value={`${k.cli.ok}/${k.cli.total}`} caption={k.cli.attention ? `${k.cli.attention} expiring` : 'all ok'} captionTone={k.cli.attention ? 'amber' : 'dim'} />
              </div>
            </div>
          </div>
        </div>

        {/* Book body — spine + page */}
        <div style={{ display: 'grid', gridTemplateColumns: '296px 1fr', flex: 1, minHeight: 0 }}>
          <window.Spine
            entries={entries}
            activeKey={activeKey}
            onSelect={setActiveKey}
            sortMode={sortMode}
            onSortChange={(m) => setSortMode(m)}
            search={search}
            onSearchChange={setSearch}
            identity={identity}
            identities={window.IDENTITIES}
            onIdentityChange={setIdentityId}
          />
          <div style={{ overflowY: 'auto', minWidth: 0 }}>
            {resolved.kind === 'user'   && resolved.record && <window.PageUser   s={resolved.record} />}
            {resolved.kind === 'system' && resolved.record && <window.PageSystem s={resolved.record} />}
            {resolved.kind === 'cli'    && resolved.record && <window.PageCli    r={resolved.record} />}
            {!resolved.record && (
              <div style={{ padding: 40 }}>
                <span style={{ fontFamily: 'var(--font-serif)', fontStyle: 'italic', color: Cs_C.dim }}>
                  No page selected.
                </span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function KpiCell({ label, value, caption, captionTone = 'dim' }) {
  const captionColor = captionTone === 'amber' ? Cs_C.amber : captionTone === 'red' ? Cs_C.red : Cs_C.dim;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3, alignItems: 'flex-end', minWidth: 96 }}>
      <window.Mono size={9} upper track="0.14em" color={Cs_C.dim}>{label}</window.Mono>
      <span style={{ fontFamily: 'var(--font-sans)', fontSize: 22, fontWeight: 500, letterSpacing: '-0.02em', color: Cs_C.fg }} className="tnum">{value}</span>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: captionColor, whiteSpace: 'nowrap' }}>{caption}</span>
    </div>
  );
}

function Sep() {
  return <span style={{ width: 1, height: 36, background: Cs_C.border, alignSelf: 'center' }} />;
}

window.DirectionPassport = DirectionPassport;
