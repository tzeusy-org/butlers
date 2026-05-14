// Sidebar — icon-rail. Mirrors frontend/src/components/layout/Sidebar.tsx
// structure but in a 56px collapsed rail: one glyph per item, hover label,
// indented sub-icons under butler groups. Active state = solid mark.

const NAV = [
  {
    title: 'Main',
    items: [
      { path: '/',          label: 'Overview', icon: 'overview' },
      { path: '/butlers',   label: 'Butlers',  icon: 'butlers' },
      { path: '/qa',        label: 'QA',       icon: 'qa', butler: 'qa' },
      { path: '/ingestion', label: 'Ingestion',icon: 'ingestion' },
      { path: '/approvals', label: 'Approvals',icon: 'approvals', badgeKey: 'approvals' },
      { path: '/memory',    label: 'Memory',   icon: 'memory' },
      { path: '/entities',  label: 'Entities', icon: 'entities' },
      { path: '/secrets',   label: 'Secrets',  icon: 'secrets' },
      { path: '/settings',  label: 'Settings', icon: 'settings', badgeKey: 'reauth' },
    ],
  },
  {
    title: 'Butlers',
    items: [
      // group: relationships, with two indented sub-routes
      { kind: 'group', butler: 'relationship', label: 'Relationships', children: [
        { path: '/contacts', label: 'Contacts', glyph: 'C' },
        { path: '/groups',   label: 'Groups',   glyph: 'G' },
      ]},
      { path: '/education',           label: 'Education',  butler: 'education',  glyph: 'E' },
      { path: '/health/measurements', label: 'Health',     butler: 'health',     glyph: 'H' },
      { path: '/calendar',            label: 'Calendar',   butler: 'calendar',   glyph: 'K' },
      { path: '/chronicles',          label: 'Chronicles', butler: 'chronicler', glyph: 'C' },
    ],
  },
  {
    title: 'Operations',
    items: [
      { path: '/timeline',      label: 'Timeline',      icon: 'timeline' },
      { path: '/notifications', label: 'Notifications', icon: 'notifications' },
      { path: '/issues',        label: 'Issues',        icon: 'issues' },
      { path: '/sessions',      label: 'Sessions',      icon: 'sessions' },
      { path: '/audit-log',     label: 'Audit Log',     icon: 'audit' },
      { path: '/system',        label: 'System',        icon: 'system' },
    ],
  },
];

// SVG icon set — minimal hairline. 16px viewBox.
const ICONS = {
  overview:      <><rect x="2" y="2" width="5" height="5"/><rect x="9" y="2" width="5" height="5"/><rect x="2" y="9" width="5" height="5"/><rect x="9" y="9" width="5" height="5"/></>,
  butlers:       <><circle cx="8" cy="6" r="2.5"/><path d="M3 14c0-2.5 2.2-4 5-4s5 1.5 5 4"/></>,
  qa:            <><circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5L14 14"/></>,
  ingestion:     <><path d="M8 2v8M4.5 6.5L8 10l3.5-3.5"/><path d="M2 12h12"/></>,
  approvals:     <><path d="M3 4.5L6.5 8 13 2.5"/><path d="M3 9.5L6.5 13 13 7.5"/></>,
  memory:        <><circle cx="8" cy="8" r="5"/><circle cx="8" cy="8" r="2"/></>,
  entities:      <><rect x="2.5" y="2.5" width="4" height="4"/><rect x="9.5" y="2.5" width="4" height="4"/><rect x="2.5" y="9.5" width="4" height="4"/><rect x="9.5" y="9.5" width="4" height="4"/></>,
  secrets:       <><rect x="3.5" y="7" width="9" height="7" rx="1"/><path d="M5 7V5a3 3 0 016 0v2"/></>,
  settings:      <><circle cx="8" cy="8" r="2"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.5 3.5l1.4 1.4M11.1 11.1l1.4 1.4M3.5 12.5l1.4-1.4M11.1 4.9l1.4-1.4"/></>,
  timeline:      <><path d="M2 8h12"/><circle cx="4" cy="8" r="1.5"/><circle cx="11" cy="8" r="1.5"/></>,
  notifications: <><path d="M4 11V7a4 4 0 018 0v4l1 2H3z"/><path d="M6.5 14a1.5 1.5 0 003 0"/></>,
  issues:        <><path d="M8 2L14 13H2z"/><path d="M8 7v3M8 11.5v.5"/></>,
  sessions:      <><rect x="2" y="3" width="12" height="9" rx="1"/><path d="M2 6h12"/></>,
  audit:         <><path d="M3.5 2h6L13 5.5V14H3.5z"/><path d="M9 2v4h4M5.5 9h5M5.5 11.5h5"/></>,
  system:        <><rect x="2.5" y="2.5" width="11" height="11" rx="1.5"/><circle cx="8" cy="8" r="2"/></>,
};

function Icon({ name, size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none"
         stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round"
         style={{ display: 'block' }}>
      {ICONS[name] || ICONS.system}
    </svg>
  );
}

function Sidebar({ data, theme = 'dark', activeRoute = '/' }) {
  const [hover, setHover] = React.useState(null);
  const [groupOpen, setGroupOpen] = React.useState({ Relationships: true });
  // Patch the static NAV with active flags from activeRoute
  const liveNav = React.useMemo(() => NAV.map((section) => ({
    ...section,
    items: section.items.map((item) =>
      item.path ? { ...item, active: item.path === activeRoute } : item
    ),
  })), [activeRoute]);
  const isDark = theme !== 'light';
  const railBg = isDark ? 'oklch(0.115 0 0)' : 'oklch(0.97 0.004 85)';
  const tooltipBg = isDark ? 'oklch(0.205 0 0)' : 'oklch(1 0 0)';
  const brandBg = isDark ? 'oklch(0.985 0 0)' : 'oklch(0.18 0 0)';
  const brandFg = isDark ? 'oklch(0.145 0 0)' : 'oklch(0.985 0 0)';
  const activeFill = isDark ? 'oklch(1 0 0 / 0.06)' : 'oklch(0 0 0 / 0.05)';
  const dotRing = railBg;
  const tooltipShadow = isDark ? '0 4px 12px oklch(0 0 0 / 0.4)' : '0 4px 12px oklch(0 0 0 / 0.10)';

  const badges = {
    approvals: data.attention.filter((a) => a.kind === 'approval').length,
    reauth:    data.attention.filter((a) => a.kind === 'reauth').length,
  };
  const statusByButler = {};
  data.butlers.forEach((b) => { statusByButler[b.name] = b.status; });

  const ctx = { isDark, railBg, tooltipBg, activeFill, dotRing, tooltipShadow };

  return (
    <nav style={{
      width: 56, flexShrink: 0,
      background: railBg,
      borderRight: `1px solid ${C.border}`,
      display: 'flex', flexDirection: 'column', alignItems: 'stretch',
      position: 'sticky', top: 0, height: '100vh',
      fontFamily: 'var(--font-sans)',
    }}>
      {/* Brand */}
      <div style={{
        height: 56, display: 'flex', alignItems: 'center', justifyContent: 'center',
        borderBottom: `1px solid ${C.borderSoft}`,
      }}>
        <div style={{
          width: 28, height: 28, borderRadius: 5,
          background: brandBg, color: brandFg,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontWeight: 700, fontSize: 14, letterSpacing: '-0.02em',
        }}>B</div>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', overflowX: 'visible', padding: '6px 0' }}>
        {liveNav.map((section, si) => (
          <div key={section.title} style={{
            paddingTop: 6, paddingBottom: 6,
            borderTop: si > 0 ? `1px solid ${C.borderSoft}` : 'none',
          }}>
            {section.items.map((item, i) => (
              item.kind === 'group'
                ? <RailGroup key={i} item={item} open={groupOpen[item.label]}
                    onToggle={() => setGroupOpen({ ...groupOpen, [item.label]: !groupOpen[item.label] })}
                    statusByButler={statusByButler} hover={hover} setHover={setHover} ctx={ctx} />
                : <RailItem key={i} item={item} badges={badges} statusByButler={statusByButler}
                    hover={hover} setHover={setHover} ctx={ctx} />
            ))}
          </div>
        ))}
      </div>

      {/* Footer */}
      <div style={{
        height: 40, display: 'flex', alignItems: 'center', justifyContent: 'center',
        borderTop: `1px solid ${C.borderSoft}`,
      }} title="1 butler degraded · 2 awaiting you">
        <StatusDot status="degraded" size={6} />
      </div>
    </nav>
  );
}

function RailItem({ item, badges, statusByButler, indent = 0, hover, setHover, glyph, ctx = {} }) {
  const badge = item.badgeKey ? badges[item.badgeKey] : 0;
  const status = item.butler ? statusByButler[item.butler] : null;
  const id = item.path;
  const showLabel = hover === id;

  const tone = item.butler ? null : null;

  return (
    <div style={{ position: 'relative' }}
         onMouseEnter={() => setHover(id)}
         onMouseLeave={() => setHover(null)}>
      <a href={(window.ROUTE_FILES && window.ROUTE_FILES[item.path]) || item.path} style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: 36, marginLeft: indent,
        color: item.active ? C.fg : C.mfg,
        background: item.active ? (ctx.activeFill || 'oklch(1 0 0 / 0.06)') : 'transparent',
        borderLeft: item.active ? `2px solid ${C.fg}` : '2px solid transparent',
        textDecoration: 'none',
        position: 'relative',
      }}>
        {item.butler
          ? <ButlerMark name={item.butler} size={18} tone={item.active ? 'fill' : 'neutral'} />
          : glyph
            ? <span style={{
                fontSize: 10, fontFamily: 'var(--font-mono)', fontWeight: 600,
                width: 16, height: 16, display: 'flex', alignItems: 'center', justifyContent: 'center',
                border: `1px solid ${C.border}`, borderRadius: 3,
              }}>{glyph}</span>
            : <Icon name={item.icon || 'system'} />}

        {/* Status indicator (for butler items) */}
        {status && status !== 'ok' && (
          <span style={{
            position: 'absolute', top: 6, right: 10,
            width: 6, height: 6, borderRadius: 999,
            background: status === 'degraded' ? C.amber : status === 'error' ? C.red : C.dim,
            border: `1.5px solid ${ctx.dotRing || 'oklch(0.115 0 0)'}`,
          }} />
        )}

        {/* Badge */}
        {badge > 0 && (
          <span style={{
            position: 'absolute', top: 4, right: 6,
            minWidth: 14, height: 14, padding: '0 3px',
            background: item.badgeKey === 'reauth' ? C.red : C.amber,
            color: item.badgeKey === 'reauth' ? '#fff' : 'oklch(0.145 0 0)',
            fontSize: 9, fontWeight: 700,
            borderRadius: 7, display: 'inline-flex',
            alignItems: 'center', justifyContent: 'center',
            fontFamily: 'var(--font-mono)',
            border: `1.5px solid ${ctx.dotRing || 'oklch(0.115 0 0)'}`,
          }}>{badge}</span>
        )}
      </a>

      {/* Hover label — floats out to the right */}
      {showLabel && (
        <div style={{
          position: 'absolute', left: 56, top: '50%', transform: 'translateY(-50%)',
          background: ctx.tooltipBg || 'oklch(0.205 0 0)', color: C.fg,
          border: `1px solid ${C.borderStrong}`,
          padding: '5px 10px', fontSize: 12,
          whiteSpace: 'nowrap', borderRadius: 4,
          fontFamily: 'var(--font-sans)', zIndex: 50,
          pointerEvents: 'none',
          boxShadow: ctx.tooltipShadow || '0 4px 12px oklch(0 0 0 / 0.4)',
        }}>
          {item.label}
          {badge > 0 && (
            <span style={{
              marginLeft: 8, color: item.badgeKey === 'reauth' ? C.red : C.amber,
              fontFamily: 'var(--font-mono)', fontSize: 10,
            }}>· {badge}</span>
          )}
        </div>
      )}
    </div>
  );
}

function RailGroup({ item, open, onToggle, statusByButler, hover, setHover, ctx = {} }) {
  const id = `group-${item.label}`;
  const showLabel = hover === id;
  const status = statusByButler[item.butler];
  return (
    <div>
      <div style={{ position: 'relative' }}
           onMouseEnter={() => setHover(id)}
           onMouseLeave={() => setHover(null)}>
        <button onClick={onToggle} style={{
          width: '100%', height: 36, background: 'transparent', border: 'none',
          cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: C.mfg, position: 'relative',
        }}>
          <ButlerMark name={item.butler} size={18} />
          {status && status !== 'ok' && (
            <span style={{
              position: 'absolute', top: 6, right: 10,
              width: 6, height: 6, borderRadius: 999,
              background: status === 'degraded' ? C.amber : C.red,
              border: `1.5px solid ${ctx.dotRing || 'oklch(0.115 0 0)'}`,
            }} />
          )}
          <span style={{
            position: 'absolute', bottom: 4, right: 6,
            color: C.dim, fontSize: 8, fontFamily: 'var(--font-mono)',
          }}>{open ? '▾' : '▸'}</span>
        </button>
        {showLabel && (
          <div style={{
            position: 'absolute', left: 56, top: '50%', transform: 'translateY(-50%)',
            background: ctx.tooltipBg || 'oklch(0.205 0 0)', color: C.fg,
            border: `1px solid ${C.borderStrong}`,
            padding: '5px 10px', fontSize: 12,
            whiteSpace: 'nowrap', borderRadius: 4, zIndex: 50, pointerEvents: 'none',
            boxShadow: ctx.tooltipShadow || '0 4px 12px oklch(0 0 0 / 0.4)',
          }}>{item.label}</div>
        )}
      </div>
      {open && item.children.map((c, i) => (
        <RailItem key={i} item={c} badges={{}} statusByButler={statusByButler}
          glyph={c.glyph} indent={16} hover={hover} setHover={setHover} ctx={ctx} />
      ))}
    </div>
  );
}

window.Sidebar = Sidebar;
