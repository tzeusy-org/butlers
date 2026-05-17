// Filters & routing · the ingestion pipeline
//
// Shape: the page IS the pipeline. Five gates between arrival and execution
// (accept · dedupe · tier · route · execute). The diagram up top shows the
// 24h funnel as honest numbers. Below the diagram, each gate is a section
// with its rules listed, in the order they fire. Two adjacent surfaces sit
// alongside: the priority-contacts list (which is data, not rules) and the
// per-channel acceptance defaults.
//
// The goal of this page is to make filtering EXPLAINABLE. After reading
// it once you should be able to predict what happens to a new event before
// you click anything.

function IngestionFilters() {
  const C = window.C;
  const rules = window.INGESTION_RULES;
  const pipe  = window.PIPELINE_STATS;

  // Bucket rules by which gate they fire at.
  const rulesByGate = {
    accept:  rules.filter((r) => r.action.startsWith('drop') || r.action.startsWith('preserve')),
    dedupe:  [], // canonicalisation rules live in code, not here yet
    tier:    rules.filter((r) => r.action.startsWith('tier')),
    route:   rules.filter((r) => r.action.startsWith('route')),
    execute: [], // replay policy lives elsewhere
  };

  return (
    <div style={{ background: C.bg, color: C.fg, minHeight: '100%' }}>
      <div style={{ maxWidth: 1500, margin: '0 auto', padding: '40px 56px 80px' }}>

        <window.PageHeader
          eyebrow={`Filters · ${pipe.total_received.toLocaleString()} events · last 24h`}
          title="How signals earn dispatch."
          sub="Five gates between arriving and acting. Rules at each gate decide whether the system stores, drops, tiers, routes, or replays. The diagram is the honest count; the lists below are the rules behind those counts."
        />

        {/* ─── Pipeline diagram ─────────────────────────────────────── */}
        <div style={{ marginTop: 36 }}>
          <PipelineDiagram pipe={pipe} />
        </div>

        {/* ─── Five gates ──────────────────────────────────────────── */}
        <div style={{ marginTop: 56 }}>
          {pipe.stages.map((stage, i) => (
            <GateSection key={stage.key} stage={stage} index={i}
              rules={rulesByGate[stage.key] || []} />
          ))}
        </div>

        {/* ─── Two adjacent surfaces — priority + channel defaults ─── */}
        <div style={{
          marginTop: 64,
          display: 'grid', gridTemplateColumns: '1.3fr 1fr', gap: 56,
        }}>
          <PrioritySendersBlock />
          <ChannelDefaultsBlock />
        </div>

        {/* ─── Disabled / archived ─────────────────────────────────── */}
        {rules.filter((r) => !r.enabled).length > 0 && (
          <div style={{ marginTop: 56 }}>
            <div style={{
              padding: '14px 0 10px',
              borderBottom: `1px solid ${C.borderSoft}`,
              display: 'flex', alignItems: 'baseline', gap: 12,
            }}>
              <Eyebrow>archived</Eyebrow>
              <Mono color={C.dim} size={10}>{rules.filter((r) => !r.enabled).length} disabled rule</Mono>
            </div>
            {rules.filter((r) => !r.enabled).map((r) => (
              <div key={r.id} style={{
                display: 'grid', gridTemplateColumns: '14px 1fr auto',
                gap: 14, padding: '12px 0',
                borderBottom: `1px solid ${C.borderSoft}`,
                alignItems: 'baseline', opacity: 0.55,
              }}>
                <span style={{ width: 6, height: 6, borderRadius: 999, background: C.dim, marginTop: 4 }} />
                <div>
                  <div style={{ fontSize: 14, color: C.mfg, fontFamily: 'var(--font-serif)', fontStyle: 'italic' }}>
                    {r.name}
                  </div>
                  <Mono color={C.dim} size={10} style={{ display: 'block', marginTop: 4 }}>{r.note}</Mono>
                </div>
                <PillBtn>restore</PillBtn>
              </div>
            ))}
          </div>
        )}

        {/* ─── Footer ──────────────────────────────────────────────── */}
        <div style={{ marginTop: 56, padding: '24px 0 0', borderTop: `1px solid ${C.border}`,
          display: 'flex', alignItems: 'baseline', gap: 16 }}>
          <div style={{ maxWidth: '52ch' }}>
            <Eyebrow>add rule</Eyebrow>
            <div style={{
              marginTop: 6, fontFamily: 'var(--font-serif)', fontSize: 13.5,
              color: C.mfg, lineHeight: 1.55,
            }}>
              Rules are written in a small DSL — channel matchers, sender / kind /
              header predicates, and one verdict per rule:{' '}
              <Mono>drop</Mono>,{' '}<Mono>preserve</Mono>,{' '}<Mono>tier</Mono>,{' '}
              <Mono>route</Mono>. New rules are inserted into their gate; you
              can drag to re-order within a gate.
            </div>
          </div>
          <span style={{ marginLeft: 'auto' }} />
          <PillBtn kind="commit">+ add rule</PillBtn>
          <PillBtn>open DSL</PillBtn>
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// The pipeline diagram — five stages as a funnel.
// One row of mono numbers reading left to right with drop branches below.
// Each stage has the in→out delta visualised as a hairline bar segment.
// ──────────────────────────────────────────────────────────────────────
function PipelineDiagram({ pipe }) {
  const C = window.C;
  const stages = pipe.stages;
  const total = stages[0].in;
  // Scale each stage's "in" relative to total for the funnel width.
  const segments = stages.map((s, i) => {
    const inPct  = (s.in  / total) * 100;
    const outPct = (s.out / total) * 100;
    const dropPct = inPct - outPct;
    return { ...s, inPct, outPct, dropPct };
  });

  return (
    <div style={{
      padding: '24px 0', borderTop: `1px solid ${C.border}`, borderBottom: `1px solid ${C.border}`,
    }}>
      {/* Top row: 5 stage labels with running totals */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(5, 1fr)',
        gap: 0,
      }}>
        {segments.map((s, i) => (
          <div key={s.key} style={{
            paddingLeft: i === 0 ? 0 : 18,
            paddingRight: i === 4 ? 0 : 18,
            borderRight: i < 4 ? `1px solid ${C.borderSoft}` : 'none',
          }}>
            <Eyebrow>§{i + 1} · {s.label}</Eyebrow>
            <div className="tnum" style={{
              marginTop: 8, fontFamily: 'var(--font-mono)', fontSize: 28,
              fontWeight: 500, letterSpacing: '-0.02em', color: C.fg,
              display: 'flex', alignItems: 'baseline', gap: 8,
            }}>
              {s.out.toLocaleString()}
              {s.dropPct > 0 && (
                <Mono color={C.red} size={11} style={{ letterSpacing: '0.04em' }}>
                  − {(s.in - s.out).toLocaleString()}
                </Mono>
              )}
              {s.preserved && (
                <Mono color={C.amber} size={11} style={{ letterSpacing: '0.04em' }}>
                  − {s.preserved.toLocaleString()} preserved
                </Mono>
              )}
            </div>
            <Mono color={C.dim} size={10} style={{ display: 'block', marginTop: 6 }}>
              {s.gloss}
            </Mono>
          </div>
        ))}
      </div>

      {/* Funnel bar — proportional widths */}
      <div style={{
        marginTop: 22,
        display: 'flex', alignItems: 'stretch',
        height: 10, width: '100%',
        position: 'relative',
      }}>
        {segments.map((s, i) => {
          const w = (s.out / total) * 100;
          const wIn = (s.in / total) * 100;
          return (
            <div key={s.key} style={{
              width: wIn + '%', position: 'relative',
              borderRight: i < 4 ? `1px solid ${C.bg}` : 'none',
            }}>
              {/* "out" portion stays solid; "drop" portion fades */}
              <div style={{ position: 'absolute', inset: 0, background: C.fg, opacity: 0.85,
                width: (s.out / s.in) * 100 + '%' }} />
              {s.in > s.out && (
                <div style={{
                  position: 'absolute', top: 0, bottom: 0,
                  left: (s.out / s.in) * 100 + '%',
                  right: 0,
                  background: s.preserved ? C.amber : C.red,
                  opacity: 0.6,
                }} />
              )}
            </div>
          );
        })}
      </div>

      {/* Axis labels */}
      <div style={{
        marginTop: 8,
        display: 'flex', justifyContent: 'space-between',
        fontFamily: 'var(--font-mono)', fontSize: 9.5, color: C.dim,
        letterSpacing: '0.06em',
      }}>
        <span>received · {pipe.total_received.toLocaleString()}</span>
        <span>—</span>
        <span>dispatched · {pipe.stages[pipe.stages.length - 1].out.toLocaleString()}</span>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Per-gate section — eyebrow + gloss, then a stack of rules.
// ──────────────────────────────────────────────────────────────────────
function GateSection({ stage, index, rules }) {
  const C = window.C;
  // Hard-coded "non-rule policy" notes for the gates whose behaviour lives
  // outside the rules DSL — they still belong in the page so the gate
  // isn't a black hole.
  const codePolicy = {
    dedupe: 'Canonicalises (source, ts) before checking the dedup window. Window = 90s for sensor data, 24h for content. Lives in butlers/<x>/ingest/dedup.py.',
    execute: 'Failures retry exponentially up to 3 times, then queue for human-initiated replay. Errors surface in QA when failure_streak ≥ 4.',
  };

  return (
    <div style={{ marginBottom: 40 }}>
      {/* Section header */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'auto 1fr auto', gap: 20,
        alignItems: 'baseline',
        padding: '0 0 12px',
        borderBottom: `1px solid ${C.border}`,
      }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
          <Eyebrow>§{index + 1}</Eyebrow>
          <h2 style={{
            margin: 0, fontSize: 28, fontWeight: 500, letterSpacing: '-0.02em',
            color: C.fg, textTransform: 'lowercase',
          }}>{stage.label}.</h2>
        </div>
        <div style={{
          fontFamily: 'var(--font-serif)', fontSize: 15, color: C.mfg,
          letterSpacing: 0, maxWidth: '58ch', lineHeight: 1.5,
        }}>{stage.gloss}</div>
        <div style={{ textAlign: 'right' }}>
          <Mono color={C.dim} size={10}>
            in {stage.in.toLocaleString()} · out {stage.out.toLocaleString()}
            {stage.in > stage.out && (
              <span style={{ color: stage.preserved ? C.amber : C.red, marginLeft: 6 }}>
                · − {(stage.in - stage.out).toLocaleString()}
              </span>
            )}
          </Mono>
        </div>
      </div>

      {/* Rules (or code-policy gloss) */}
      <div style={{ marginTop: 4 }}>
        {rules.length > 0 ? (
          rules.map((r) => <RuleRow key={r.id} rule={r} />)
        ) : (
          <div style={{
            padding: '20px 0',
            fontFamily: 'var(--font-serif)', fontSize: 14, fontStyle: 'italic',
            color: C.mfg, maxWidth: '70ch', lineHeight: 1.55,
          }}>
            {codePolicy[stage.key] || 'No rules at this gate. Policy lives in code.'}
          </div>
        )}

        {/* Stage-specific tiering breakdown */}
        {stage.tiered && (
          <div style={{
            marginTop: 14, padding: '14px 0',
            display: 'grid', gridTemplateColumns: 'auto auto 1fr', gap: 18,
            alignItems: 'baseline',
            borderTop: `1px solid ${C.borderSoft}`,
          }}>
            <Eyebrow>tiered · 24h</Eyebrow>
            <div className="tnum" style={{ display: 'flex', gap: 24, fontFamily: 'var(--font-mono)', fontSize: 13 }}>
              <span><span style={{ color: C.amber }}>●</span> priority · {stage.tiered.priority}</span>
              <span><span style={{ color: C.dim }}>○</span> default · {stage.tiered.default.toLocaleString()}</span>
            </div>
            <Mono color={C.dim} size={10} style={{ textAlign: 'right' }}>
              priority list governs · see below
            </Mono>
          </div>
        )}

        {/* Accept-stage drop summary */}
        {stage.drops && stage.drops.length > 0 && (
          <div style={{
            marginTop: 14, padding: '14px 0',
            borderTop: `1px solid ${C.borderSoft}`,
          }}>
            <Eyebrow style={{ marginBottom: 8 }}>drops · 24h</Eyebrow>
            {stage.drops.map((d, i) => (
              <div key={i} style={{
                display: 'grid', gridTemplateColumns: '80px 1fr auto',
                gap: 14, padding: '6px 0',
                fontFamily: 'var(--font-mono)', fontSize: 11.5, color: C.fg,
                letterSpacing: '0.02em',
              }}>
                <Mono color={C.red}>− {d.count.toLocaleString()}</Mono>
                <span style={{ fontFamily: 'var(--font-serif)', fontSize: 13, color: C.fg, fontStyle: 'italic' }}>
                  {d.label}
                </span>
                <Mono color={C.dim} size={10}>{d.rule}</Mono>
              </div>
            ))}
          </div>
        )}

        {/* Execute-stage outcome breakdown */}
        {stage.executed && (
          <div style={{
            marginTop: 14, padding: '14px 0',
            display: 'grid', gridTemplateColumns: 'auto 1fr', gap: 18,
            alignItems: 'baseline',
            borderTop: `1px solid ${C.borderSoft}`,
          }}>
            <Eyebrow>outcome · 24h</Eyebrow>
            <div className="tnum" style={{ display: 'flex', gap: 24, fontFamily: 'var(--font-mono)', fontSize: 13 }}>
              <span><span style={{ color: C.green }}>●</span> ok · {stage.executed.ok}</span>
              {stage.executed.replay_pending > 0 && (
                <span><span style={{ color: C.amber }}>◐</span> replay · {stage.executed.replay_pending}</span>
              )}
              {stage.executed.errored > 0 && (
                <span><span style={{ color: C.red }}>■</span> error · {stage.executed.errored}</span>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// One rule.
// ──────────────────────────────────────────────────────────────────────
function RuleRow({ rule }) {
  const C = window.C;
  const r = rule;
  const verb = r.action.split(' ')[0];
  const actionColor = verb === 'drop' ? C.red
                     : verb === 'route' ? C.fg
                     : verb === 'tier' ? C.amber
                     : C.mfg;

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '14px 1fr 200px 60px 28px',
      gap: 20, padding: '18px 0',
      borderBottom: `1px solid ${C.borderSoft}`,
      alignItems: 'flex-start',
    }}>
      <span style={{ width: 6, height: 6, borderRadius: 999, background: C.green, marginTop: 8 }} />

      <div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
          <div style={{ fontSize: 16, fontWeight: 500, letterSpacing: '-0.01em' }}>{r.name}</div>
          <Mono color={C.dim} size={10}>·  {r.owner}</Mono>
        </div>
        <div style={{
          marginTop: 6, fontFamily: 'var(--font-serif)', fontSize: 14,
          color: C.fg, lineHeight: 1.5, maxWidth: '60ch', letterSpacing: 0,
        }}>{r.note}</div>

        <div style={{
          marginTop: 12, padding: '10px 14px',
          background: window.__theme === 'light' ? 'oklch(0 0 0 / 0.02)' : 'oklch(1 0 0 / 0.025)',
          border: `1px solid ${C.borderSoft}`,
          fontFamily: 'var(--font-mono)', fontSize: 11.5, color: C.fg,
          letterSpacing: 0, lineHeight: 1.6, whiteSpace: 'pre-wrap',
        }}>
          <span style={{ color: C.dim }}>when </span>
          <span>{r.when}</span>
          <span style={{ color: C.dim }}>{'\n→ '}</span>
          <span style={{ color: actionColor }}>{r.action}</span>
        </div>

        {r.examples.length > 0 && (
          <div style={{
            marginTop: 10, display: 'flex', flexWrap: 'wrap', gap: 14,
            fontFamily: 'var(--font-serif)', fontStyle: 'italic',
            fontSize: 12.5, color: C.mfg, letterSpacing: 0,
          }}>
            {r.examples.map((ex, i) => (
              <span key={i}>{ex}{i < r.examples.length - 1 && <span style={{ color: C.dim, marginLeft: 14 }}>·</span>}</span>
            ))}
          </div>
        )}
      </div>

      <div style={{ paddingTop: 4 }}>
        <Mono size={18} style={{ fontVariantNumeric: 'tabular-nums', letterSpacing: '-0.015em' }}>
          {r.matches24h.toLocaleString()}
        </Mono>
        <Mono color={C.dim} size={10} style={{ display: 'block', marginTop: 2 }}>
          matched · 24h
        </Mono>
      </div>

      <div style={{ paddingTop: 4 }}>
        <Toggle on={r.enabled} />
      </div>

      <a href="#" onClick={(ev) => ev.preventDefault()}
        style={{ color: C.dim, fontSize: 13, textDecoration: 'none', paddingTop: 6, justifySelf: 'end' }}>›</a>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Priority senders — data, not rules.
// ──────────────────────────────────────────────────────────────────────
function PrioritySendersBlock() {
  const C = window.C;
  const list = window.PRIORITY_CONTACTS;
  return (
    <div>
      <div style={{
        display: 'flex', alignItems: 'baseline', gap: 12,
        padding: '12px 0', borderBottom: `1px solid ${C.border}`,
      }}>
        <Eyebrow>priority · senders</Eyebrow>
        <Mono color={C.dim} size={10}>{list.length} contacts · bypass batching</Mono>
        <span style={{ marginLeft: 'auto' }} />
        <PillBtn>+ add</PillBtn>
      </div>
      <div style={{
        marginTop: 14,
        fontFamily: 'var(--font-serif)', fontSize: 14,
        color: C.mfg, lineHeight: 1.5, maxWidth: '60ch',
      }}>
        Messages from these contacts skip the default tier. The system pings
        the named butler immediately rather than waiting for the next batch.
        This is the only place a person is first-class in filtering.
      </div>

      <div style={{ marginTop: 18 }}>
        <div style={{
          display: 'grid', gridTemplateColumns: '1.4fr 1fr 100px 100px 60px 24px',
          gap: 14, padding: '10px 0 8px',
          borderBottom: `1px solid ${C.borderSoft}`,
          fontFamily: 'var(--font-mono)', fontSize: 9.5, color: C.mfg,
          letterSpacing: '0.14em', textTransform: 'uppercase',
        }}>
          <span>name · handle</span>
          <span>channel · routes to</span>
          <span>added</span>
          <span style={{ textAlign: 'right' }}>last</span>
          <span></span>
          <span></span>
        </div>
        {list.map((c, i) => (
          <div key={i} style={{
            display: 'grid', gridTemplateColumns: '1.4fr 1fr 100px 100px 60px 24px',
            gap: 14, padding: '12px 0',
            borderBottom: `1px solid ${C.borderSoft}`,
            alignItems: 'baseline',
          }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 13.5, fontWeight: 500, letterSpacing: '-0.005em' }}>{c.name}</div>
              <Mono color={C.dim} size={10} style={{ display: 'block', marginTop: 3 }}>{c.handle}</Mono>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <ChannelGlyph channel={c.channel} size={12} />
              <Mono size={10.5} color={C.fg}>{c.channel}</Mono>
              <Mono color={C.dim} size={10}>→</Mono>
              <window.BMark name={c.butler} size={12} tone="fill" />
              <Mono color={C.fg} size={10.5}>{c.butler}</Mono>
            </div>
            <Mono color={C.dim} size={10}>{c.added}</Mono>
            <Mono color={C.dim} size={10} style={{ textAlign: 'right' }}>{c.lastSeen}</Mono>
            <a href="#" onClick={(ev) => ev.preventDefault()} style={{
              fontFamily: 'var(--font-mono)', fontSize: 10, color: C.dim,
              textDecoration: 'underline', textUnderlineOffset: 3, textDecorationColor: C.borderSoft,
              letterSpacing: '0.06em',
            }}>edit</a>
            <a href="#" onClick={(ev) => ev.preventDefault()} style={{ color: C.dim, fontSize: 12 }}>×</a>
          </div>
        ))}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Per-channel default policy — what happens to unmatched events on each
// channel.
// ──────────────────────────────────────────────────────────────────────
function ChannelDefaultsBlock() {
  const C = window.C;
  const list = window.CHANNEL_DEFAULTS;
  return (
    <div>
      <div style={{
        display: 'flex', alignItems: 'baseline', gap: 12,
        padding: '12px 0', borderBottom: `1px solid ${C.border}`,
      }}>
        <Eyebrow>channel · defaults</Eyebrow>
        <Mono color={C.dim} size={10}>fallback policy per connector</Mono>
      </div>
      <div style={{
        marginTop: 14,
        fontFamily: 'var(--font-serif)', fontSize: 14,
        color: C.mfg, lineHeight: 1.5, maxWidth: '46ch',
      }}>
        When no rule matches, this is what the channel does. Most channels
        route to a butler; Home Assistant is preserve-only because the
        volume is too high to dispatch on by default.
      </div>

      <div style={{ marginTop: 18 }}>
        {list.map((c, i) => (
          <div key={i} style={{
            display: 'grid', gridTemplateColumns: '160px 200px 1fr',
            gap: 14, padding: '12px 0',
            borderBottom: `1px solid ${C.borderSoft}`,
            alignItems: 'baseline',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <ChannelGlyph channel={c.channel} size={14} />
              <Mono size={11.5}>{c.channel}</Mono>
            </div>
            <Mono color={C.fg} size={11}>{c.policy}</Mono>
            <div style={{
              fontFamily: 'var(--font-serif)', fontSize: 12.5, fontStyle: 'italic',
              color: C.mfg, lineHeight: 1.45,
            }}>{c.note}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Toggle({ on }) {
  const C = window.C;
  return (
    <div title={on ? 'enabled' : 'disabled'}
      style={{
        width: 32, height: 18, borderRadius: 999,
        border: `1px solid ${C.borderStrong}`,
        background: on ? C.fg : 'transparent',
        position: 'relative', cursor: 'pointer',
      }}>
      <div style={{
        position: 'absolute', top: 1, left: on ? 15 : 1,
        width: 14, height: 14, borderRadius: 999,
        background: on ? C.bg : C.borderStrong,
        transition: 'left 120ms ease',
      }} />
    </div>
  );
}

window.IngestionFilters = IngestionFilters;
