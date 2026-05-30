// The spine — left-hand index of every credential the house holds.
//
// Cycle B improvements:
//   1. "Needs hand" is a globally pinned group at the top. Across all
//      three families, anything in expired / scope_mismatch / expiring /
//      rotating / revoked floats up here.
//   2. Default sort within each family is severity-first (rank from
//      STATE_CATALOG). Tweakable via the tweaks panel.
//   3. Inline search input filters by label / key. Mono. Slim.
//   4. Identity chip lives at the top of the spine when the owner is
//      viewing — clicking flips between household members.
//   5. Numbering is global (§01..§N) and reflects the sorted order, not
//      array position. Each render recomputes; that's intentional.
//
// API:
//   <Spine entries activeKey onSelect identity onIdentityChange
//           sortMode='severity'|'recency'|'alpha' />

const Cs_S = window.C;

function SpineRow({ entry, n, active, onClick }) {
  const provider = entry.provider ? window.PROVIDERS[entry.provider] : null;
  const meta = window.STATE_CATALOG[entry.state] || {};
  const stateColor = meta.tone === 'red' ? Cs_S.red
    : meta.tone === 'amber' ? Cs_S.amber
    : meta.tone === 'ok' ? Cs_S.green
    : Cs_S.dim;
  return (
    <button onClick={onClick} style={{
      position: 'relative',
      display: 'grid',
      gridTemplateColumns: '32px 1fr 8px',
      columnGap: 8, alignItems: 'center',
      padding: '8px 14px', width: '100%',
      background: active ? Cs_S.bgElev : 'transparent',
      border: 'none', cursor: 'pointer',
      borderLeft: active ? `2px solid ${Cs_S.fg}` : `2px solid transparent`,
      textAlign: 'left',
    }}>
      {/* Severity sliver — claims attention only on sick rows */}
      {meta.sliver && active === false && (
        <span style={{
          position: 'absolute', left: 0, top: 6, bottom: 6, width: 2,
          background: stateColor,
        }} />
      )}
      <window.Mono size={10} color={active ? Cs_S.fg : Cs_S.dim}>§{String(n).padStart(2, '0')}</window.Mono>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
          {provider && <window.ProviderMark provider={entry.provider} size={14} />}
          <span style={{
            fontFamily: entry.mono ? 'var(--font-mono)' : 'var(--font-sans)',
            fontSize: entry.mono ? 10.5 : 12.5,
            fontWeight: 500,
            color: active ? Cs_S.fg : Cs_S.mfg,
            letterSpacing: entry.mono ? 'normal' : '-0.005em',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            minWidth: 0, flex: 1,
          }}>{entry.label}</span>
        </div>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 9.5, color: stateColor,
          textTransform: 'lowercase', letterSpacing: '0.04em',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>{entry.subline}</span>
      </div>
      <span style={{ width: 6, height: 6, borderRadius: 999, background: stateColor }} />
    </button>
  );
}

function SpineGroup({ eyebrow, hint, items, n0, activeKey, onSelect }) {
  if (items.length === 0) return null;
  return (
    <div style={{ paddingBottom: 12 }}>
      <div style={{
        padding: '12px 14px 6px',
        display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
      }}>
        <window.Mono size={9} upper track="0.14em" color={Cs_S.dim}>{eyebrow}</window.Mono>
        {hint && <window.Mono size={9} color={Cs_S.dim}>{hint}</window.Mono>}
      </div>
      {items.map((entry, i) => (
        <SpineRow key={entry.key} entry={entry} n={n0 + i + 1}
          active={activeKey === entry.key}
          onClick={() => onSelect(entry.key)} />
      ))}
    </div>
  );
}

function SpineSearch({ value, onChange }) {
  return (
    <div style={{
      position: 'relative', margin: '8px 12px 4px',
      borderBottom: `1px solid ${Cs_S.borderSoft}`,
    }}>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="search"
        style={{
          width: '100%', padding: '6px 0 8px 16px',
          background: 'transparent', border: 'none', outline: 'none',
          color: Cs_S.fg, fontFamily: 'var(--font-mono)', fontSize: 11,
          letterSpacing: '0.04em',
        }}
      />
      <span style={{
        position: 'absolute', left: 0, top: 7,
        color: Cs_S.dim, fontFamily: 'var(--font-mono)', fontSize: 11,
      }}>/</span>
      {value && (
        <button onClick={() => onChange('')} style={{
          position: 'absolute', right: 0, top: 6, padding: 0,
          background: 'transparent', border: 'none', cursor: 'pointer',
          color: Cs_S.dim, fontFamily: 'var(--font-mono)', fontSize: 11,
        }}>×</button>
      )}
    </div>
  );
}

function SortPicker({ mode, onChange }) {
  const opts = [
    { id: 'severity', label: 'severity' },
    { id: 'recency',  label: 'recency'  },
    { id: 'alpha',    label: 'alpha'    },
  ];
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, padding: '0 14px 10px' }}>
      <window.Mono size={9} upper track="0.14em" color={Cs_S.dim}>sort ·</window.Mono>
      {opts.map((o, i) => (
        <React.Fragment key={o.id}>
          {i > 0 && <window.Mono size={9} color={Cs_S.dim}>·</window.Mono>}
          <button onClick={() => onChange(o.id)} style={{
            background: 'transparent', border: 'none', cursor: 'pointer',
            padding: 0, fontFamily: 'var(--font-mono)', fontSize: 9.5,
            letterSpacing: '0.08em', textTransform: 'uppercase',
            color: mode === o.id ? Cs_S.fg : Cs_S.dim,
            borderBottom: mode === o.id ? `1px solid ${Cs_S.fg}` : 'none',
            paddingBottom: 1,
          }}>{o.label}</button>
        </React.Fragment>
      ))}
    </div>
  );
}

// Compare functions for the spine sort modes.
const SORTERS = {
  severity: (a, b) => window.severityRank(a.state) - window.severityRank(b.state),
  recency:  (a, b) => {
    // Most-recently-touched first. We approximate with lastUsed → lastVerified → 'never'.
    const score = (x) => x.lastTouchOrder ?? 999;
    return score(a) - score(b);
  },
  alpha:    (a, b) => (a.label || '').toLowerCase().localeCompare((b.label || '').toLowerCase()),
};

function Spine({
  entries,
  activeKey,
  onSelect,
  sortMode = 'severity',
  onSortChange,
  search,
  onSearchChange,
  identity,
  identities,
  onIdentityChange,
}) {
  // Filter, partition into needs-hand vs by-family, sort within each.
  const filtered = entries.filter((e) => {
    if (!search) return true;
    return e.label.toLowerCase().includes(search.toLowerCase());
  });

  const cmp = SORTERS[sortMode] || SORTERS.severity;
  const needsHand = filtered.filter((e) => window.needsHand(e.state)).sort(cmp);
  const restCli   = filtered.filter((e) => e.family === 'cli'    && !window.needsHand(e.state)).sort(cmp);
  const restSys   = filtered.filter((e) => e.family === 'system' && !window.needsHand(e.state)).sort(cmp);
  const restUsr   = filtered.filter((e) => e.family === 'user'   && !window.needsHand(e.state)).sort(cmp);

  let n = 0;
  const cumulate = (arr) => { const start = n; n += arr.length; return start; };

  return (
    <nav style={{
      background: Cs_S.bgDeep, borderRight: `1px solid ${Cs_S.border}`,
      overflowY: 'auto', display: 'flex', flexDirection: 'column',
    }}>
      {/* Identity strip — owner only sees the switcher; members see a static chip. */}
      <div style={{
        padding: '14px 14px 12px',
        borderBottom: `1px solid ${Cs_S.borderSoft}`,
        display: 'flex', flexDirection: 'column', gap: 8,
      }}>
        <window.Mono size={9} upper track="0.14em" color={Cs_S.dim}>viewing</window.Mono>
        {identities && identities.length > 1 ? (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {identities.map((id) => (
              <button key={id.id} onClick={() => onIdentityChange && onIdentityChange(id.id)} style={{
                display: 'inline-flex', alignItems: 'center', gap: 6,
                padding: '4px 8px', borderRadius: 3,
                background: id.id === identity.id ? Cs_S.bgElev : 'transparent',
                border: id.id === identity.id ? `1px solid ${Cs_S.borderStrong}` : `1px solid ${Cs_S.borderSoft}`,
                cursor: 'pointer',
              }}>
                <span style={{ width: 7, height: 7, borderRadius: 999, background: id.hue || Cs_S.fg }} />
                <span style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: id.id === identity.id ? Cs_S.fg : Cs_S.mfg, fontWeight: 500 }}>{id.label}</span>
                <window.Mono size={9} upper track="0.10em" color={Cs_S.dim}>{id.role}</window.Mono>
              </button>
            ))}
          </div>
        ) : (
          identity && <window.IdentityChip identity={identity} />
        )}
      </div>

      <SpineSearch value={search} onChange={onSearchChange} />
      <SortPicker mode={sortMode} onChange={onSortChange} />

      <div style={{ flex: 1, minHeight: 0 }}>
        <SpineGroup
          eyebrow={`needs hand · ${needsHand.length}`}
          hint={needsHand.length > 0 ? 'pinned' : ''}
          items={needsHand}
          n0={cumulate(needsHand)}
          activeKey={activeKey}
          onSelect={onSelect}
        />
        {needsHand.length > 0 && <div style={{ height: 1, background: Cs_S.border, margin: '4px 0' }} />}

        <SpineGroup
          eyebrow={`cli runtimes · ${restCli.length}`}
          items={restCli}
          n0={cumulate(restCli)}
          activeKey={activeKey}
          onSelect={onSelect}
        />
        <SpineGroup
          eyebrow={`system · ${restSys.length}`}
          items={restSys}
          n0={cumulate(restSys)}
          activeKey={activeKey}
          onSelect={onSelect}
        />
        <SpineGroup
          eyebrow={`integrations · ${restUsr.length}`}
          items={restUsr}
          n0={cumulate(restUsr)}
          activeKey={activeKey}
          onSelect={onSelect}
        />
      </div>

      {/* Footer — add page */}
      <div style={{
        padding: '12px 14px 18px',
        borderTop: `1px solid ${Cs_S.borderSoft}`,
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      }}>
        <window.PillBtn>+ add page</window.PillBtn>
        <window.Mono size={9} color={Cs_S.dim}>{filtered.length} of {entries.length}</window.Mono>
      </div>
    </nav>
  );
}

// ── Entry projection ─────────────────────────────────────────────────
// Build the flat list of spine entries from the source data. Each
// entry has a `family` and a `key` so the spine can sort and route.

function buildSpineEntries(identityId) {
  const userSecrets = window.USER_SECRETS.filter((s) => s.identity === identityId);

  const cli = window.CLI_RUNTIMES.map((r, i) => ({
    key: `c:${r.id}`,
    family: 'cli',
    label: r.label,
    state: r.state,
    mono: false,
    lastTouchOrder: r.state === 'never_set' ? 900 : (r.test ? i : 500),
    subline: r.state === 'never_set' ? 'not set'
      : r.state === 'expiring' ? `expires ${r.expires}`
      : `used ${r.lastUsed || '—'}`,
  }));

  const system = window.SYSTEM_SECRETS.map((s, i) => ({
    key: `s:${s.key}`,
    family: 'system',
    label: s.key,
    state: s.rowState === 'missing' ? 'never_set' : 'ok',
    mono: true,
    lastTouchOrder: s.rowState === 'missing' ? 900 : i,
    subline: s.rowState === 'missing' ? 'not set'
      : s.rowState === 'local' ? `local · ${s.target}`
      : 'shared default',
  }));

  const user = userSecrets.map((s, i) => ({
    key: `u:${s.provider}`,
    family: 'user',
    label: window.PROVIDERS[s.provider].label,
    provider: s.provider,
    state: s.state,
    mono: false,
    lastTouchOrder: s.lastUsed ? i : 800,
    subline:
      s.state === 'expired' ? 'refresh failed · 2d'
      : s.state === 'expiring' ? `expires ${s.expires}`
      : s.state === 'scope_mismatch' ? '1 scope missing'
      : s.state === 'never_set' ? 'not connected'
      : `verified ${s.lastVerified}`,
  }));

  return [...cli, ...system, ...user];
}

Object.assign(window, { Spine, buildSpineEntries });
