// V2 — Narrative feed. Reads top-to-bottom like a story.
// Single-column dominant. Editorial serif headline. Time anchors. Inline-action affordances.

function V2Narrative({ density = 'comfortable' }) {
  const d = window.BUTLERS_DATA;
  const compact = density === 'compact';
  const gap = compact ? 16 : 24;

  // Group feed by hour bucket
  const buckets = {};
  d.feed.forEach((f) => {
    const h = f.time.slice(0, 2);
    if (!buckets[h]) buckets[h] = [];
    buckets[h].push(f);
  });
  const hourKeys = Object.keys(buckets).sort((a, b) => b.localeCompare(a));

  return (
    <div style={{
      width: '100%', height: '100%', background: C.bg, color: C.fg,
      fontFamily: 'var(--font-sans)', overflow: 'auto',
    }}>
      <div style={{ maxWidth: 760, margin: '0 auto', padding: compact ? '32px 32px' : '56px 48px' }}>
        {/* Slug line */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10,
          fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
          textTransform: 'uppercase', letterSpacing: '0.12em', marginBottom: 28,
        }}>
          <span>Wed · 6 May · 14:32</span>
          <span style={{ color: C.dim }}>—</span>
          <span>{d.kpis.sessionsToday.value} sessions today</span>
          <span style={{ color: C.dim }}>—</span>
          <span style={{ color: C.amber }}>2 need you</span>
        </div>

        {/* Headline — quiet, declarative, NOT italic-serif AI hero */}
        <h1 style={{
          fontFamily: 'var(--font-sans)', fontWeight: 600,
          fontSize: 36, lineHeight: 1.15, letterSpacing: '-0.02em',
          margin: 0, marginBottom: 14, maxWidth: '20ch',
        }}>
          Seven of eight butlers are well. Calendar lost its Google token at 9:14.
        </h1>
        <p style={{
          fontFamily: 'var(--font-serif)', fontSize: 17, lineHeight: 1.55,
          color: C.mfg, margin: 0, marginBottom: gap, maxWidth: '52ch',
        }}>
          Maya is still waiting for your reply about Sunday. The household
          butler has a $148 grocery basket ready for you to look at before
          five. Otherwise, an ordinary afternoon.
        </p>

        {/* Inline attention strip — the two things that need YOU, made unmistakable */}
        <div style={{
          borderTop: `1px solid ${C.border}`,
          borderBottom: `1px solid ${C.border}`,
          padding: '14px 0', marginBottom: gap + 8,
        }}>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
            textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 10,
          }}>For your attention</div>
          {d.attention.map((a, i) => (
            <div key={a.id} style={{
              display: 'flex', alignItems: 'baseline', gap: 14,
              padding: '6px 0', fontSize: 14,
              borderBottom: i < d.attention.length - 1 ? `1px solid ${C.borderSoft}` : 'none',
            }}>
              <Sev level={a.severity} />
              <span style={{ color: C.dim, fontFamily: 'var(--font-mono)', fontSize: 11, width: 56 }} className="tnum">
                {a.age}
              </span>
              <span style={{ flex: 1 }}>
                <span style={{ fontWeight: 500 }}>{a.title}</span>
                <span style={{ color: C.mfg, marginLeft: 8 }}>{a.detail}</span>
              </span>
              <a href={`#${a.id}`} style={{
                color: a.severity === 'high' ? C.red : C.fg,
                textDecoration: 'underline', textUnderlineOffset: 3,
                fontWeight: 500, whiteSpace: 'nowrap',
              }}>{a.action} →</a>
            </div>
          ))}
        </div>

        {/* The day, in order */}
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
          textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 10,
        }}>The day, in order</div>

        {hourKeys.map((h) => (
          <div key={h} style={{ marginBottom: 20 }}>
            <div style={{
              display: 'flex', alignItems: 'baseline', gap: 12,
              borderBottom: `1px dashed ${C.borderSoft}`, paddingBottom: 4, marginBottom: 8,
            }}>
              <span style={{
                fontFamily: 'var(--font-serif)', fontStyle: 'italic',
                fontSize: 18, color: C.fg, fontWeight: 500,
              }} className="tnum">{h}:00</span>
              <span style={{ color: C.dim, fontSize: 11, fontFamily: 'var(--font-mono)' }}>
                {buckets[h].length} {buckets[h].length === 1 ? 'event' : 'events'}
              </span>
            </div>
            {buckets[h].map((f, i) => (
              <div key={i} style={{
                display: 'grid', gridTemplateColumns: '56px 22px 1fr',
                gap: 10, padding: '6px 0', alignItems: 'baseline',
              }}>
                <span style={{ color: C.dim, fontFamily: 'var(--font-mono)', fontSize: 11 }} className="tnum">
                  {f.time}
                </span>
                <ButlerMark name={f.butler} size={18} />
                <div style={{ fontSize: 14, lineHeight: 1.5 }}>
                  <span style={{
                    color: f.kind === 'error' ? C.red : C.fg,
                  }}>{f.text}</span>
                  {f.meta && <span style={{ color: C.dim, marginLeft: 8, fontSize: 12 }}>{f.meta}</span>}
                  {f.cta && (
                    <a href={f.cta} style={{
                      color: C.amber, textDecoration: 'underline', textUnderlineOffset: 3,
                      marginLeft: 10, fontSize: 12, fontWeight: 500,
                    }}>open →</a>
                  )}
                </div>
              </div>
            ))}
          </div>
        ))}

        {/* Coming up */}
        <div style={{
          marginTop: 32, paddingTop: 18, borderTop: `1px solid ${C.border}`,
        }}>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 10, color: C.mfg,
            textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 12,
          }}>Coming up · today</div>
          {d.upcoming.map((u, i) => (
            <div key={i} style={{
              display: 'grid', gridTemplateColumns: '56px 22px 1fr auto',
              gap: 10, padding: '5px 0', alignItems: 'baseline', fontSize: 13,
            }}>
              <span style={{ color: C.dim, fontFamily: 'var(--font-mono)', fontSize: 11 }} className="tnum">
                {u.time}
              </span>
              {u.kind === 'butler' ? <ButlerMark name={u.butler} size={18} /> : <span style={{ width: 18 }} />}
              <div>
                <span>{u.label}</span>
                {u.meta && <span style={{ color: C.dim, marginLeft: 8, fontSize: 11 }}>{u.meta}</span>}
              </div>
              <span style={{ color: C.dim, fontFamily: 'var(--font-mono)', fontSize: 10 }}>
                {u.dur || u.kind}
              </span>
            </div>
          ))}
        </div>

        {/* Footer signature */}
        <div style={{
          marginTop: 40, paddingTop: 12, borderTop: `1px solid ${C.borderSoft}`,
          fontFamily: 'var(--font-mono)', fontSize: 10, color: C.dim,
          letterSpacing: '0.06em',
        }}>
          Generated continuously · last refresh 14:32:04 · {d.kpis.momentsLogged.value} moments indexed
        </div>
      </div>
    </div>
  );
}

window.V2Narrative = V2Narrative;
