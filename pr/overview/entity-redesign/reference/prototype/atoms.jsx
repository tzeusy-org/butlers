// Atoms — small shared bits used across catalog + explorations.

const colors = {
  bg:   'var(--bg)',
  bgE:  'var(--bg-elev)',
  bgD:  'var(--bg-deep)',
  fg:   'var(--fg)',
  mfg:  'var(--mfg)',
  dim:  'var(--dim)',
  border: 'var(--border)',
  borderSoft: 'var(--border-soft)',
  borderStrong: 'var(--border-strong)',
  red: 'var(--red)', amber: 'var(--amber)', green: 'var(--green)', blue: 'var(--blue)',
};

function Eyebrow({ children, style }) {
  return <div className="eyebrow" style={style}>{children}</div>;
}

function Voice({ children, italic, style }) {
  return (
    <p style={{
      fontFamily: 'var(--font-serif)', fontSize: 16, lineHeight: 1.6,
      color: colors.fg, margin: 0,
      fontStyle: italic ? 'italic' : 'normal',
      maxWidth: '64ch',
      ...style,
    }}>{children}</p>
  );
}

function Display({ children, style, size = 44 }) {
  return (
    <h1 style={{
      fontFamily: 'var(--font-sans)',
      fontWeight: 500, fontSize: size,
      letterSpacing: '-0.025em', lineHeight: 1.08,
      margin: 0, maxWidth: '14ch', textWrap: 'balance',
      ...style,
    }}>{children}</h1>
  );
}

function Title({ children, style, size = 24 }) {
  return (
    <h2 style={{
      fontFamily: 'var(--font-sans)',
      fontWeight: 500, fontSize: size,
      letterSpacing: '-0.015em', lineHeight: 1.2,
      margin: 0,
      ...style,
    }}>{children}</h2>
  );
}

// Color for type marks. Subtle, monochrome-leaning. Type is identified by the
// glyph, not by hue.
function typeColor(type) {
  return {
    person:       'var(--cat-relationship)',
    organization: 'var(--cat-household)',
    place:        'var(--cat-calendar)',
    product:      'var(--cat-education)',
    account:      'var(--cat-qa)',
    event:        'var(--cat-chronicler)',
    group:        'var(--cat-memory)',
  }[type] || 'var(--fg)';
}

// Entity mark — initials in a hairline square, with a small type-glyph subbed
// in for non-person types. Plays the role of an avatar without inventing
// imagery.
function EntityMark({ entity, size = 18, tone = 'neutral' }) {
  if (!entity) return null;
  const initials = entity.type === 'person'
    ? entity.name.split(/\s+/).slice(0, 2).map((w) => w[0]).join('').toUpperCase()
    : (TYPES[entity.type]?.glyph || '?');
  const isOwner = entity.role === 'owner';
  const isUnident = entity.state === 'unidentified';

  const hue = typeColor(entity.type);
  const bg = tone === 'fill' ? hue : 'transparent';
  const fg = tone === 'fill' ? '#fff' : (isUnident ? 'var(--amber)' : 'var(--fg)');
  const border = tone === 'fill' ? 'transparent'
    : isOwner ? 'var(--fg)'
    : isUnident ? 'var(--amber)'
    : 'var(--border-strong)';
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      width: size, height: size, borderRadius: 3,
      background: bg, color: fg, border: `1px solid ${border}`,
      fontFamily: entity.type === 'person' ? 'var(--font-sans)' : 'var(--font-mono)',
      fontWeight: entity.type === 'person' ? 600 : 500,
      fontSize: Math.max(8, Math.round(size * (entity.type === 'person' ? 0.42 : 0.5))),
      letterSpacing: entity.type === 'person' ? '-0.02em' : '0.02em',
      flexShrink: 0,
      lineHeight: 1,
    }}>{initials.slice(0, 2)}</span>
  );
}

function TierBadge({ tier, size = 'sm' }) {
  if (tier == null) return null;
  if (tier === 0) {
    return (
      <span style={{
        fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.1em',
        textTransform: 'uppercase', color: 'var(--fg)',
        border: '1px solid var(--fg)', borderRadius: 2, padding: '1px 4px',
      }}>OWNER</span>
    );
  }
  const lbl = ['', 'inner', 'close', 'extended', 'acq', 'distant'][tier] || '';
  return (
    <span title={`Dunbar tier ${tier}`} style={{
      display: 'inline-flex', alignItems: 'baseline', gap: 4,
      color: 'var(--mfg)', fontFamily: 'var(--font-mono)', fontSize: 9,
      textTransform: 'uppercase', letterSpacing: '0.1em',
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: 1,
        background: `var(--tier-${tier})`, display: 'inline-block',
      }} />
      <span>t{tier} · {lbl}</span>
    </span>
  );
}

function StateDot({ kind, size = 6 }) {
  const map = { ok: 'var(--green)', degraded: 'var(--amber)', error: 'var(--red)', waiting: 'var(--mfg)' };
  return (
    <span style={{
      width: size, height: size, borderRadius: 999,
      background: map[kind] || map.waiting, display: 'inline-block',
      flexShrink: 0,
    }} />
  );
}

// A rule-row — the canonical list primitive in Dispatch.
function Row({ left, mid, right, padding = '10px 0', onClick, active }) {
  const style = {
    display: 'grid',
    gridTemplateColumns: 'auto 1fr auto',
    alignItems: 'center', gap: 14,
    padding,
    borderBottom: '1px solid var(--border-soft)',
    cursor: onClick ? 'pointer' : 'default',
    background: active ? 'oklch(1 0 0 / 0.04)' : 'transparent',
    transition: 'background 80ms linear',
  };
  return (
    <div style={style} onClick={onClick}
         onMouseEnter={onClick ? (e) => { if (!active) e.currentTarget.style.background = 'oklch(1 0 0 / 0.04)'; } : undefined}
         onMouseLeave={onClick ? (e) => { if (!active) e.currentTarget.style.background = 'transparent'; } : undefined}>
      <div>{left}</div>
      <div style={{ minWidth: 0 }}>{mid}</div>
      <div>{right}</div>
    </div>
  );
}

// A pressable mono pill used for filter chips. Doubles as toggle.
function Pill({ children, active, onClick, count, title }) {
  return (
    <button onClick={onClick} aria-pressed={!!active} title={title}
      style={{
        border: `1px solid ${active ? 'var(--fg)' : 'var(--border-strong)'}`,
        background: active ? 'var(--fg)' : 'transparent',
        color: active ? 'var(--bg)' : 'var(--mfg)',
        borderRadius: 3, padding: '3px 8px',
        fontFamily: 'var(--font-mono)', fontSize: 10,
        textTransform: 'uppercase', letterSpacing: '0.06em',
        cursor: 'pointer', lineHeight: 1,
        display: 'inline-flex', alignItems: 'center', gap: 6,
      }}>
      <span>{children}</span>
      {count != null && (
        <span style={{ opacity: active ? 0.7 : 0.55 }}>·&nbsp;{count}</span>
      )}
    </button>
  );
}

// Section frame — eyebrow above, hairlines top & bottom; lets us partition the
// long memo without ever using a card.
function Section({ eyebrow, title, lede, children, id }) {
  return (
    <section id={id} style={{ padding: '40px 0', borderTop: '1px solid var(--border)' }}>
      {eyebrow && <Eyebrow style={{ marginBottom: 18 }}>{eyebrow}</Eyebrow>}
      {title && <Title size={28} style={{ marginBottom: 14, maxWidth: '24ch' }}>{title}</Title>}
      {lede && <Voice style={{ marginBottom: 28 }}>{lede}</Voice>}
      {children}
    </section>
  );
}

// Artboard — frames an exploration on the canvas, but adopts Dispatch idiom
// (rule above + below; mono eyebrow; no card chrome). Each artboard names what
// the direction is, what hypothesis it tests, and renders the design beneath.
function Artboard({ idx, name, hypothesis, height, children }) {
  return (
    <div style={{
      borderTop: '1px solid var(--border)',
      paddingTop: 24, marginTop: 0, marginBottom: 56,
    }}>
      <div style={{
        display: 'grid', gridTemplateColumns: '60px 1fr', gap: 18,
        marginBottom: 20, alignItems: 'baseline',
      }}>
        <div>
          <Eyebrow style={{ marginBottom: 4 }}>direction</Eyebrow>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 20,
            color: 'var(--fg)', fontWeight: 500,
          }} className="tnum">0{idx}</div>
        </div>
        <div>
          <Eyebrow style={{ marginBottom: 6 }}>{name}</Eyebrow>
          <div style={{ fontFamily: 'var(--font-serif)', fontSize: 17, lineHeight: 1.5, color: 'var(--fg)', maxWidth: '58ch' }}>
            {hypothesis}
          </div>
        </div>
      </div>
      <div style={{
        border: '1px solid var(--border)',
        background: 'var(--bg-deep)',
        height,
        overflow: 'hidden',
        position: 'relative',
      }}>
        {children}
      </div>
    </div>
  );
}

Object.assign(window, {
  colors, Eyebrow, Voice, Display, Title,
  EntityMark, TierBadge, StateDot, Row, Pill,
  Section, Artboard, typeColor,
});
