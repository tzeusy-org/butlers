// Bespoke tab renderers + per-butler log/memory helpers.
//
// Each renderer is a "small dashboard" specific to a butler. Layout uses
// the 4-col Panel grid; some panels span 1, some span the full row.

const { Panel, KPI, KV, MonoLabel, LineSeries, BarSeries } = window.BUTLER_ATOMS;
const Cc = window.C;
const ButlerMark = window.ButlerMark;

// ─── Per-butler log lines ────────────────────────────────────────────────

const LOG_TEMPLATES = {
  relationship: [
    { ts: '14:28:02', lvl: 'INFO',  msg: 'scheduler.tick — no new contacts' },
    { ts: '14:27:58', lvl: 'INFO',  msg: 'draft.compose maya/sunday-brunch — model=haiku-4-5 in=412 out=128 latency=0.91s' },
    { ts: '14:27:54', lvl: 'INFO',  msg: 'draft.queue maya/sunday-brunch pending_review' },
    { ts: '14:14:11', lvl: 'DEBUG', msg: 'patrol.scan contacts.tier1 — 18 ok' },
    { ts: '14:02:03', lvl: 'INFO',  msg: 'reply.draft.start maya — last_msg_age=11m' },
    { ts: '13:47:19', lvl: 'INFO',  msg: 'warmth.recompute — 6 contacts updated' },
    { ts: '13:46:51', lvl: 'DEBUG', msg: 'memory.write fact=maya.prefers_brunch' },
    { ts: '13:11:02', lvl: 'INFO',  msg: 'tier.confirm wei → 1' },
    { ts: '11:40:00', lvl: 'INFO',  msg: 'rule.add reply<6h tier1 weekday' },
    { ts: '09:30:11', lvl: 'INFO',  msg: 'fact.add sarah.daughter.school 2026-09-04' },
    { ts: '08:48:30', lvl: 'INFO',  msg: 'call.log mom 8m mood=warm' },
    { ts: '08:02:11', lvl: 'INFO',  msg: 'scheduler.tick — sleep mode end' },
  ],
  health: [
    { ts: '14:28:02', lvl: 'INFO',  msg: 'ingest.libre3 glucose=94 mg/dL' },
    { ts: '14:14:11', lvl: 'DEBUG', msg: 'healthkit.poll — 0 new samples' },
    { ts: '13:47:19', lvl: 'INFO',  msg: 'walk.detect 28m 1.4mi avg_hr=64' },
    { ts: '13:11:02', lvl: 'INFO',  msg: 'ingest.healthkit hrv=58ms' },
    { ts: '11:14:00', lvl: 'INFO',  msg: 'walk.persist /walks/2026-05-06-09:14' },
    { ts: '08:30:11', lvl: 'DEBUG', msg: 'libre3.session refresh ok' },
    { ts: '07:30:00', lvl: 'INFO',  msg: 'sleep.summary 7h12m efficiency=0.91' },
  ],
  calendar: [
    { ts: '09:14:21', lvl: 'ERROR', msg: 'oauth.refresh google.calendar — invalid_grant' },
    { ts: '09:14:00', lvl: 'WARN',  msg: 'sync.pause google.calendar — token rotation' },
    { ts: '08:00:11', lvl: 'INFO',  msg: 'sync.tick icloud.calendar — 0 changes' },
    { ts: '07:30:02', lvl: 'INFO',  msg: 'scheduler.tick' },
  ],
  qa: [
    { ts: '14:14:11', lvl: 'INFO',  msg: 'patrol.complete — 7 sessions ok 0 anomalies' },
    { ts: '14:00:02', lvl: 'INFO',  msg: 'patrol.start' },
    { ts: '13:46:51', lvl: 'DEBUG', msg: 'metrics.scan butlers.* — within bounds' },
    { ts: '07:30:11', lvl: 'INFO',  msg: 'investigation.close #214 notion-rate-limit resolved=auto' },
  ],
  memory: [
    { ts: '13:47:19', lvl: 'INFO',  msg: 'consolidate.morning — 4 short→mid · 1 dropped · 2.1s' },
    { ts: '13:46:51', lvl: 'DEBUG', msg: 'tier.promote maya.prefers_brunch short→mid' },
    { ts: '08:00:00', lvl: 'INFO',  msg: 'consolidate.wakeup — 7 short→mid · 3 dropped · 3.4s' },
    { ts: '02:00:00', lvl: 'INFO',  msg: 'consolidate.nightly — 24 short→mid · 8 mid→long · 14.2s' },
  ],
  education: [
    { ts: '11:14:00', lvl: 'INFO',  msg: 'anki.session complete — 47 cards · 92% retention' },
    { ts: '11:08:11', lvl: 'INFO',  msg: 'anki.session start deck=spanish.core' },
    { ts: '08:00:11', lvl: 'INFO',  msg: 'reminder.fire 47 cards due' },
  ],
  chronicler: [
    { ts: '14:32:00', lvl: 'INFO',  msg: 'ingest.spotify 142 plays since mon' },
    { ts: '13:12:00', lvl: 'INFO',  msg: 'timeline.assemble 09:00–13:00 — 37 events 4 sources' },
    { ts: '08:02:11', lvl: 'INFO',  msg: 'ingest.spotify.start' },
  ],
  household: [
    { ts: '12:30:11', lvl: 'INFO',  msg: 'order.draft trader-joes — 23 items $148.20' },
    { ts: '12:30:00', lvl: 'INFO',  msg: 'order.draft.start trigger=pantry-low' },
    { ts: '08:00:00', lvl: 'INFO',  msg: 'pantry.scan — 6 items low' },
  ],
};

window.LOG_LINES_FOR = (name) => LOG_TEMPLATES[name] || [];

const MEMORY_WRITES = {
  relationship: [
    { ts: '14:02', kind: 'fact',   text: 'Maya prefers Sunday brunch over Saturday dinner.' },
    { ts: '13:11', kind: 'entity', text: 'Wei → confirmed as tier-1 (lunch cadence ≤ 14d).' },
    { ts: '11:40', kind: 'rule',   text: 'Reply within 6h to tier-1 contacts on weekdays.' },
    { ts: '09:30', kind: 'fact',   text: "Sarah's daughter starts school 2026-09-04." },
    { ts: '08:48', kind: 'fact',   text: 'Mom called 8m, mood: warm. No follow-ups needed.' },
  ],
  health: [
    { ts: '14:28', kind: 'fact', text: 'Glucose 94 mg/dL post-lunch (in range).' },
    { ts: '13:47', kind: 'fact', text: 'Walk · 28m · 1.4mi · avg_hr 64.' },
    { ts: '11:58', kind: 'fact', text: 'HRV 58ms (above 7d avg 56).' },
    { ts: '07:30', kind: 'fact', text: 'Sleep 7h12m · efficiency 0.91.' },
  ],
  calendar: [
    { ts: '09:14', kind: 'rule', text: 'Sync paused — Google Calendar OAuth expired.' },
    { ts: '08:00', kind: 'fact', text: 'iCloud Calendar synced (0 changes).' },
  ],
  qa: [
    { ts: '14:14', kind: 'fact', text: '7 sessions completed without anomaly.' },
    { ts: '07:30', kind: 'fact', text: 'Investigation #214 closed: Notion rate-limit auto-resolved.' },
  ],
  memory: [
    { ts: '13:47', kind: 'rule', text: 'Promoted 4 short-term facts → mid-term tier.' },
    { ts: '08:00', kind: 'rule', text: 'Promoted 7 short-term facts; dropped 3 stale.' },
  ],
  education: [
    { ts: '11:14', kind: 'fact', text: 'Anki review · 47 cards · 92% retention.' },
  ],
  chronicler: [
    { ts: '13:12', kind: 'fact',   text: 'Reconstructed 09:00–13:00 timeline (lunch w/ Wei at Camden).' },
    { ts: '08:02', kind: 'entity', text: 'Camden — confirmed as recurring lunch venue.' },
  ],
  household: [
    { ts: '12:30', kind: 'fact', text: 'Pantry low: eggs, oats, oat milk, bananas, lemons, garlic.' },
  ],
};

window.MEMORY_WRITES_FOR = (detail) => {
  const m = MEMORY_WRITES[detail.label?.toLowerCase()] || [];
  if (m.length) return m;
  return MEMORY_WRITES[Object.keys(MEMORY_WRITES).find((k) => detail?.label?.toLowerCase().startsWith(k.slice(0, 4)))] || [];
};

// ─── Shared atoms ────────────────────────────────────────────────────────

function BespokeKpiRow({ kpis }) {
  return kpis.map((k, i) => (
    <Panel key={i} title={k.label} span={1}>
      <KPI label="" value={k.value} sub={k.sub} tone={k.tone} />
    </Panel>
  ));
}

function MicroBar({ pct, color = Cc.fg, height = 4 }) {
  return (
    <div style={{ height, background: 'oklch(1 0 0 / 0.06)', borderRadius: 1, overflow: 'hidden' }}>
      <div style={{ width: `${Math.min(100, Math.max(0, pct * 100))}%`, height: '100%', background: color, opacity: 0.85 }} />
    </div>
  );
}

function TableHead({ cols }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: cols.map((c) => c.w).join(' '), gap: 12, padding: '4px 0', borderBottom: `1px solid ${Cc.border}` }}>
      {cols.map((c, i) => <MonoLabel key={i} color={Cc.dim}>{c.h}</MonoLabel>)}
    </div>
  );
}

function tierPillStyle() {
  return {
    fontFamily: 'var(--font-mono)', fontSize: 9, color: Cc.dim,
    border: `1px solid ${Cc.border}`, borderRadius: 2, padding: '1px 4px', textAlign: 'center',
    width: 'fit-content',
  };
}

// ─── Contacts (relationship) ─────────────────────────────────────────────

function ContactsBespoke({ content }) {
  const cols = [
    { h: '', w: '32px' }, { h: 'NAME', w: '120px' }, { h: 'LAST CONTACT', w: '1fr' }, { h: 'WARMTH', w: '120px' }, { h: '', w: '60px' }
  ];
  return (
    <>
      <BespokeKpiRow kpis={content.kpis} />

      <Panel title="tier distribution" span={2}>
        <div style={{ display: 'grid', gap: 8 }}>
          {content.tierDistribution.map((t) => (
            <div key={t.tier} style={{ display: 'grid', gridTemplateColumns: '40px 60px 1fr 50px', gap: 10, alignItems: 'center' }}>
              <span style={tierPillStyle()}>T{t.tier}</span>
              <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: Cc.mfg }}>{t.count}</span>
              <MicroBar pct={t.warm} />
              <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: Cc.dim, textAlign: 'right' }}>{t.warm.toFixed(2)}</span>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="overdue" sub={`${content.overdue.length} need follow-up`} span={2}>
        <div style={{ display: 'grid', gap: 6 }}>
          {content.overdue.map((o, i) => (
            <div key={i} style={{ display: 'grid', gridTemplateColumns: '90px 50px 1fr 60px', gap: 10, alignItems: 'baseline', padding: '4px 0', borderBottom: i < content.overdue.length - 1 ? `1px solid ${Cc.borderSoft}` : 'none' }}>
              <span style={{ fontSize: 13 }}>{o.name}</span>
              <span style={tierPillStyle()}>T{o.tier}</span>
              <MonoLabel>{o.last}</MonoLabel>
              <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: Cc.amber, textAlign: 'right' }}>+{o.owed}</span>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="watchlist · tier1+2" span={4} scroll>
        <TableHead cols={cols} />
        {content.rows.map((r, i, arr) => (
          <div key={r.key} style={{
            display: 'grid', gridTemplateColumns: cols.map((c) => c.w).join(' '),
            gap: 12, padding: '8px 0', alignItems: 'center',
            borderBottom: i < arr.length - 1 ? `1px solid ${Cc.borderSoft}` : 'none',
          }}>
            <span style={tierPillStyle()}>T{r.tier}</span>
            <span style={{ fontSize: 13 }}>{r.key}</span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: Cc.mfg }}>{r.last}</span>
            <MicroBar pct={r.warm} />
            <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, textAlign: 'right' }}>{r.warm.toFixed(2)}</span>
          </div>
        ))}
      </Panel>

      <Panel title={`thread · ${content.selected.name}`} sub={`tier ${content.selected.tier} · warmth ${content.selected.warm.toFixed(2)}`} span={3}>
        <div style={{ display: 'grid', gap: 8 }}>
          {content.selected.lastFour.map((m, i) => (
            <div key={i} style={{ display: 'grid', gridTemplateColumns: '110px 1fr', gap: 12, padding: '6px 0', borderBottom: i < content.selected.lastFour.length - 1 ? `1px solid ${Cc.borderSoft}` : 'none' }}>
              <MonoLabel>{m.ts} · {m.dir}</MonoLabel>
              <span style={{ fontSize: 13, color: m.dir === 'out' ? Cc.mfg : Cc.fg, fontStyle: m.text.startsWith('(drafted)') ? 'italic' : 'normal' }}>{m.text}</span>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="known facts" span={1}>
        <div style={{ display: 'grid', gap: 8 }}>
          {content.selected.facts.map((f, i) => (
            <div key={i} style={{ fontSize: 12, color: Cc.fg, paddingLeft: 10, borderLeft: `2px solid ${Cc.border}` }}>{f}</div>
          ))}
        </div>
      </Panel>
    </>
  );
}

// ─── Measurements (health) ───────────────────────────────────────────────

function MeasurementsBespoke({ content }) {
  return (
    <>
      <BespokeKpiRow kpis={content.kpis} />
      {content.series.map((s) => (
        <Panel key={s.name} title={s.name} sub={s.range ? `range ${s.range[0]}–${s.range[1]}` : 'last 7d'} span={2} height={140}>
          <LineSeries data={s.data} height={70} />
          {s.range && (
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6 }}>
              <MonoLabel>{Math.min(...s.data)}</MonoLabel>
              <MonoLabel>avg · {(s.data.reduce((a,b)=>a+b,0) / s.data.length).toFixed(0)}</MonoLabel>
              <MonoLabel>{Math.max(...s.data)}</MonoLabel>
            </div>
          )}
        </Panel>
      ))}

      <Panel title={`sleep stages · ${content.sleep.total}`} span={2}>
        <div style={{ display: 'flex', height: 24, borderRadius: 2, overflow: 'hidden', border: `1px solid ${Cc.border}` }}>
          {content.sleep.stages.map((st, i) => {
            const opacity = st.kind === 'awake' ? 0.25 : st.kind === 'light' ? 0.5 : st.kind === 'rem' ? 0.7 : 0.9;
            return <div key={i} style={{ flex: st.pct, background: Cc.fg, opacity }} title={`${st.kind} · ${st.mins}m`} />;
          })}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginTop: 10 }}>
          {content.sleep.stages.map((st) => (
            <div key={st.kind}>
              <MonoLabel>{st.kind}</MonoLabel>
              <div className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: Cc.fg }}>{Math.floor(st.mins / 60)}h {st.mins % 60}m</div>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="sources · 24h" span={2}>
        <div style={{ display: 'grid', gap: 6 }}>
          {content.sources.map((s, i) => (
            <div key={i} style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 10, padding: '4px 0', borderBottom: i < content.sources.length - 1 ? `1px solid ${Cc.borderSoft}` : 'none' }}>
              <span style={{ fontSize: 12 }}>{s.name} <MonoLabel>· {s.last}</MonoLabel></span>
              <MonoLabel>{s.samples}</MonoLabel>
            </div>
          ))}
        </div>
      </Panel>
    </>
  );
}

// ─── Events (calendar) ───────────────────────────────────────────────────

function WeekStrip({ week }) {
  const HOURS_START = 8;
  const HOURS_END = 22;
  const SPAN = HOURS_END - HOURS_START;
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '40px repeat(7, 1fr)', gap: 4 }}>
      <div />
      {week.map((d) => (
        <div key={d.day} style={{ textAlign: 'center', padding: '4px 0', background: d.current ? 'oklch(1 0 0 / 0.04)' : 'transparent', borderRadius: 2 }}>
          <MonoLabel color={d.current ? Cc.fg : Cc.dim}>{d.day}</MonoLabel>
          <div className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 14, color: d.current ? Cc.fg : Cc.mfg }}>{d.date}</div>
        </div>
      ))}

      <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'space-between', padding: '4px 0' }}>
        {[8, 12, 16, 20].map((h) => <MonoLabel key={h}>{h}</MonoLabel>)}
      </div>
      {week.map((d) => (
        <div key={d.day} style={{ position: 'relative', height: 140, background: d.current ? 'oklch(1 0 0 / 0.03)' : 'oklch(1 0 0 / 0.015)', border: `1px solid ${Cc.borderSoft}`, borderRadius: 2 }}>
          {d.events.map((e, i) => {
            const top = ((e.t - HOURS_START) / SPAN) * 100;
            const h = (e.dur / SPAN) * 100;
            return (
              <div key={i} title={`${e.label} · ${e.t}:00`} style={{
                position: 'absolute', left: 2, right: 2,
                top: `${top}%`, height: `${h}%`,
                background: e.past ? 'oklch(1 0 0 / 0.06)' : Cc.fg,
                opacity: e.past ? 0.5 : 0.85,
                borderRadius: 1,
                fontSize: 9, color: e.past ? Cc.dim : '#000',
                padding: '1px 3px', overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis',
                fontFamily: 'var(--font-mono)',
              }}>{e.label}</div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function EventsBespoke({ content }) {
  return (
    <>
      <BespokeKpiRow kpis={content.kpis} />

      <Panel title="this week" sub="08:00 — 22:00" span={4} height={200}>
        <WeekStrip week={content.week} />
      </Panel>

      <Panel title="upcoming" span={2} scroll>
        {content.upcoming.map((u, i, arr) => (
          <div key={i} style={{ display: 'grid', gridTemplateColumns: '120px 1fr', gap: 10, padding: '8px 0', borderBottom: i < arr.length - 1 ? `1px solid ${Cc.borderSoft}` : 'none' }}>
            <MonoLabel color={Cc.mfg}>{u.time}</MonoLabel>
            <div>
              <div style={{ fontSize: 13 }}>{u.label}</div>
              <MonoLabel>{u.meta}</MonoLabel>
            </div>
          </div>
        ))}
      </Panel>

      <Panel title="drafts · awaiting send" sub={`${content.drafts.length} pending`} span={2} scroll>
        {content.drafts.map((d, i, arr) => (
          <div key={i} style={{ padding: '8px 0', borderBottom: i < arr.length - 1 ? `1px solid ${Cc.borderSoft}` : 'none' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 10 }}>
              <span style={{ fontSize: 13 }}>{d.who} · {d.subject}</span>
              <MonoLabel>{d.ts}</MonoLabel>
            </div>
            <div style={{ fontSize: 12, color: Cc.mfg, marginTop: 4, fontStyle: 'italic' }}>"{d.preview}"</div>
            <MonoLabel color={Cc.amber}>● {d.state}</MonoLabel>
          </div>
        ))}
      </Panel>

      <Panel title="conflicts" span={2}>
        {content.conflicts.map((c, i) => (
          <div key={i} style={{ display: 'grid', gap: 6 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
              <MonoLabel color={Cc.amber}>● {c.ts}</MonoLabel>
            </div>
            <div style={{ fontSize: 13 }}>{c.label}</div>
            <MonoLabel>→ {c.resolve}</MonoLabel>
          </div>
        ))}
      </Panel>

      <Panel title="sources" span={2}>
        <div style={{ display: 'grid', gap: 8 }}>
          {content.sources.map((s, i) => (
            <div key={i} style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 10, padding: '4px 0', borderBottom: i < content.sources.length - 1 ? `1px solid ${Cc.borderSoft}` : 'none' }}>
              <span style={{ fontSize: 13 }}>{s.name}</span>
              <MonoLabel color={s.tone === 'amber' ? Cc.amber : Cc.dim}>● {s.state}</MonoLabel>
            </div>
          ))}
        </div>
      </Panel>
    </>
  );
}

// ─── Investigations (qa) ─────────────────────────────────────────────────

function InvestigationsBespoke({ content }) {
  const sel = content.selected;
  return (
    <>
      <BespokeKpiRow kpis={content.kpis} />

      <Panel title="patrol cadence · 24h" sub="bars = anomalies / patrol" span={4} height={80}>
        <div style={{ display: 'flex', gap: 2, alignItems: 'flex-end', height: 36 }}>
          {content.patrolStripe.map((v, i) => (
            <div key={i} style={{ flex: 1, height: `${v * 28}%`, background: v >= 3 ? Cc.amber : Cc.fg, opacity: v >= 3 ? 0.9 : 0.6, borderRadius: 1, minHeight: 3 }} />
          ))}
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6 }}>
          <MonoLabel>00:00</MonoLabel><MonoLabel>06:00</MonoLabel><MonoLabel>12:00</MonoLabel><MonoLabel>18:00</MonoLabel><MonoLabel>now</MonoLabel>
        </div>
      </Panel>

      <Panel title="recent" sub={`${content.investigations.length} cases`} span={4} scroll>
        <TableHead cols={[{ h:'ID', w:'60px' },{ h:'SEV', w:'70px' },{ h:'TITLE', w:'1fr' },{ h:'BUTLER', w:'110px' },{ h:'STATE', w:'200px' }]} />
        {content.investigations.map((it, i, arr) => (
          <div key={it.id} style={{
            display: 'grid', gridTemplateColumns: '60px 70px 1fr 110px 200px',
            gap: 12, padding: '10px 0', alignItems: 'center',
            borderBottom: i < arr.length - 1 ? `1px solid ${Cc.borderSoft}` : 'none',
          }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: Cc.fg }}>{it.id}</span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10,
              color: it.sev === 'high' ? Cc.red : it.sev === 'medium' ? Cc.amber : Cc.dim,
              textTransform: 'uppercase', letterSpacing: '0.10em',
            }}>● {it.sev}</span>
            <span style={{ fontSize: 13 }}>{it.title}</span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {ButlerMark && <ButlerMark name={it.butler} size={14} />}
              <span style={{ fontSize: 12, color: Cc.mfg, textTransform: 'capitalize' }}>{it.butler}</span>
            </span>
            <MonoLabel>{it.state} · {it.age}</MonoLabel>
          </div>
        ))}
      </Panel>

      <Panel title={`detail · ${sel.id}`} sub={`${sel.sev} · ${sel.butler} · ${sel.title}`} span={4}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
          <div>
            <MonoLabel color={Cc.dim}>HYPOTHESIS</MonoLabel>
            <div style={{ fontSize: 13, marginTop: 6, color: Cc.fg, lineHeight: 1.5 }}>{sel.hypothesis}</div>

            <MonoLabel color={Cc.dim} style={{ marginTop: 16 }}>NEXT</MonoLabel>
            <div style={{ fontSize: 13, marginTop: 6, color: Cc.mfg, fontStyle: 'italic' }}>{sel.next}</div>
          </div>
          <div>
            <MonoLabel color={Cc.dim}>TIMELINE</MonoLabel>
            <div style={{ display: 'grid', gap: 4, marginTop: 6 }}>
              {sel.timeline.map((t, i) => (
                <div key={i} style={{ display: 'grid', gridTemplateColumns: '50px 1fr', gap: 10, padding: '3px 0' }}>
                  <MonoLabel>{t.ts}</MonoLabel>
                  <span style={{ fontSize: 12, color: Cc.fg }}>{t.what}</span>
                </div>
              ))}
            </div>

            <MonoLabel color={Cc.dim} style={{ marginTop: 16 }}>EVIDENCE</MonoLabel>
            <ul style={{ margin: '6px 0 0 0', padding: 0, listStyle: 'none', display: 'grid', gap: 4 }}>
              {sel.evidence.map((e, i) => (
                <li key={i} style={{ fontSize: 12, color: Cc.fg, paddingLeft: 14, position: 'relative' }}>
                  <span style={{ position: 'absolute', left: 0, top: 6, width: 4, height: 4, background: Cc.dim, borderRadius: '50%' }} />
                  {e}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </Panel>
    </>
  );
}

// ─── Consolidations (memory) ─────────────────────────────────────────────

function ConsolidationsBespoke({ content }) {
  const max = Math.max(...content.tiers.map((t) => t.cap));
  return (
    <>
      <BespokeKpiRow kpis={content.kpis} />

      <Panel title="tier capacity" sub="● = used · □ = headroom" span={2}>
        <div style={{ display: 'grid', gap: 12 }}>
          {content.tiers.map((t) => (
            <div key={t.name}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
                <span style={{ fontSize: 13, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{t.name}</span>
                <MonoLabel>{t.count.toLocaleString()} / {t.cap.toLocaleString()}</MonoLabel>
              </div>
              <div style={{ position: 'relative', height: 6, background: 'oklch(1 0 0 / 0.04)', borderRadius: 1, overflow: 'hidden', width: `${(t.cap / max) * 100}%` }}>
                <div style={{ width: `${(t.count / t.cap) * 100}%`, height: '100%', background: Cc.fg, opacity: t.color === 'dim' ? 0.4 : t.color === 'mfg' ? 0.7 : 0.9 }} />
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="recent runs" span={2} scroll>
        <TableHead cols={[{ h:'TIME', w:'50px' },{ h:'KIND', w:'80px' },{ h:'MOVED', w:'1fr' },{ h:'DUR', w:'60px' }]} />
        {content.runs.map((r, i, arr) => (
          <div key={i} style={{
            display: 'grid', gridTemplateColumns: '50px 80px 1fr 60px',
            gap: 12, padding: '8px 0', fontFamily: 'var(--font-mono)', fontSize: 11,
            borderBottom: i < arr.length - 1 ? `1px solid ${Cc.borderSoft}` : 'none',
          }}>
            <span className="tnum" style={{ color: Cc.dim }}>{r.time}</span>
            <span style={{ color: Cc.fg }}>{r.kind}</span>
            <span style={{ color: Cc.mfg }}>{r.moved}</span>
            <span className="tnum" style={{ color: Cc.fg }}>{r.dur}</span>
          </div>
        ))}
      </Panel>

      <Panel title="fact browser" sub="recent writes" span={4} scroll>
        <TableHead cols={[{ h:'KIND', w:'80px' },{ h:'TIER', w:'80px' },{ h:'BUTLER', w:'120px' },{ h:'TEXT', w:'1fr' }]} />
        {content.facts.map((f, i, arr) => (
          <div key={i} style={{
            display: 'grid', gridTemplateColumns: '80px 80px 120px 1fr',
            gap: 12, padding: '8px 0', alignItems: 'center',
            borderBottom: i < arr.length - 1 ? `1px solid ${Cc.borderSoft}` : 'none',
          }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: Cc.dim, textTransform: 'uppercase', letterSpacing: '0.10em' }}>{f.kind}</span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: f.tier === 'long' ? Cc.fg : f.tier === 'mid' ? Cc.mfg : Cc.dim, textTransform: 'uppercase', letterSpacing: '0.10em' }}>● {f.tier}</span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: Cc.mfg, textTransform: 'capitalize' }}>
              {ButlerMark && <ButlerMark name={f.butler} size={12} />}
              {f.butler}
            </span>
            <span style={{ fontSize: 13 }}>{f.text}</span>
          </div>
        ))}
      </Panel>
    </>
  );
}

// ─── Decks (education) ───────────────────────────────────────────────────

function DecksBespoke({ content }) {
  const minR = Math.min(...content.retention30d);
  const maxR = Math.max(...content.retention30d);
  return (
    <>
      <BespokeKpiRow kpis={content.kpis} />

      <Panel title="decks" span={2} scroll>
        <TableHead cols={[{ h:'DECK', w:'1fr' },{ h:'DUE', w:'60px' },{ h:'TOTAL', w:'70px' },{ h:'RET', w:'80px' }]} />
        {content.decks.map((d, i, arr) => (
          <div key={d.name} style={{
            display: 'grid', gridTemplateColumns: '1fr 60px 70px 80px',
            gap: 12, padding: '10px 0', alignItems: 'center',
            borderBottom: i < arr.length - 1 ? `1px solid ${Cc.borderSoft}` : 'none',
          }}>
            <span style={{ fontSize: 13 }}>{d.name}</span>
            <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: d.due ? Cc.amber : Cc.dim }}>{d.due}</span>
            <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: Cc.mfg }}>{d.total}</span>
            <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: Cc.fg }}>{(d.retention * 100).toFixed(0)}%</span>
          </div>
        ))}
      </Panel>

      <Panel title="today's queue · next 5" sub="cmd-K to start" span={2} scroll>
        {content.queue.map((c, i, arr) => (
          <div key={i} style={{
            display: 'grid', gridTemplateColumns: '1fr 70px',
            gap: 10, padding: '10px 0',
            borderBottom: i < arr.length - 1 ? `1px solid ${Cc.borderSoft}` : 'none',
          }}>
            <div>
              <div style={{ fontSize: 14, color: Cc.fg }}>{c.front}</div>
              <MonoLabel>{c.deck} · {c.hint}</MonoLabel>
            </div>
            <MonoLabel>{c.last}</MonoLabel>
          </div>
        ))}
      </Panel>

      <Panel title="retention · 30d" sub={`min ${(minR*100).toFixed(0)}% · max ${(maxR*100).toFixed(0)}%`} span={2} height={120}>
        <LineSeries data={content.retention30d.map((r) => Math.round((r - 0.85) * 1000))} height={56} />
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6 }}>
          <MonoLabel>30d ago</MonoLabel><MonoLabel>15d</MonoLabel><MonoLabel>now</MonoLabel>
        </div>
      </Panel>

      <Panel title="streak" sub={`${content.streakWeeks.length} days`} span={2} height={120}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 3 }}>
          {content.streakWeeks.map((s, i) => (
            <div key={i} style={{ aspectRatio: '1 / 1', background: s ? Cc.fg : 'oklch(1 0 0 / 0.04)', opacity: s ? 0.7 : 1, borderRadius: 1, border: `1px solid ${Cc.borderSoft}` }} />
          ))}
        </div>
      </Panel>
    </>
  );
}

// ─── Timelines (chronicler) ──────────────────────────────────────────────

const TIMELINE_COLOR = {
  sleep: 'oklch(0.55 0.06 260)',
  media: 'oklch(0.65 0.10 60)',
  social: 'oklch(0.65 0.12 30)',
  place: 'oklch(0.62 0.08 180)',
  work: 'oklch(0.58 0.04 220)',
  walk: 'oklch(0.70 0.08 130)',
  biom: 'oklch(0.65 0.10 0)',
  gap: 'transparent',
};

function TimelinesBespoke({ content }) {
  return (
    <>
      <BespokeKpiRow kpis={content.kpis} />

      <Panel title="today · timeline" sub="reconstructed from 4 sources" span={3}>
        <div style={{ display: 'grid', gap: 0, position: 'relative', paddingLeft: 12 }}>
          {/* spine */}
          <div style={{ position: 'absolute', left: 64, top: 6, bottom: 6, width: 1, background: Cc.border }} />
          {content.todays.map((t, i) => (
            <div key={i} style={{ display: 'grid', gridTemplateColumns: '52px 16px 1fr 160px', gap: 10, alignItems: 'center', padding: '6px 0' }}>
              <MonoLabel color={Cc.mfg}>{t.t}</MonoLabel>
              <div style={{
                width: 10, height: 10, marginLeft: 3,
                borderRadius: t.kind === 'gap' ? 0 : '50%',
                border: t.kind === 'gap' ? `1px dashed ${Cc.dim}` : 'none',
                background: t.kind === 'gap' ? 'transparent' : TIMELINE_COLOR[t.kind] || Cc.fg,
              }} />
              <span style={{ fontSize: 13, color: t.kind === 'gap' ? Cc.dim : Cc.fg, fontStyle: t.kind === 'gap' ? 'italic' : 'normal' }}>{t.label}</span>
              <MonoLabel>src · {t.src}</MonoLabel>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="sources · today" span={1}>
        <div style={{ display: 'grid', gap: 8 }}>
          {content.sources.map((s, i) => (
            <div key={i} style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 10, padding: '6px 0', borderBottom: i < content.sources.length - 1 ? `1px solid ${Cc.borderSoft}` : 'none' }}>
              <div>
                <div style={{ fontSize: 13 }}>{s.name}</div>
                <MonoLabel color={s.tone === 'amber' ? Cc.amber : Cc.dim}>● {s.state}</MonoLabel>
              </div>
              <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: Cc.fg }}>{s.count}</span>
            </div>
          ))}
        </div>
      </Panel>
    </>
  );
}

// ─── Orders (household) ──────────────────────────────────────────────────

function OrdersBespoke({ content }) {
  const p = content.pending;
  return (
    <>
      <BespokeKpiRow kpis={content.kpis} />

      <Panel title={`pending · ${p.id}`} sub={`${p.vendor} · ${p.items} items · ${p.total} · ${p.window}`} span={4}>
        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 24 }}>
          <div>
            <MonoLabel color={Cc.dim}>LINE ITEMS</MonoLabel>
            <div style={{ marginTop: 8 }}>
              {p.lines.map((l, i) => (
                <div key={i} style={{ display: 'grid', gridTemplateColumns: '40px 1fr 90px', gap: 10, padding: '6px 0', borderBottom: i < p.lines.length - 1 ? `1px solid ${Cc.borderSoft}` : 'none' }}>
                  <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: Cc.dim }}>×{l.qty}</span>
                  <div>
                    <span style={{ fontSize: 13 }}>{l.name}</span>
                    {l.sub && <div><MonoLabel color={Cc.amber}>● {l.sub}</MonoLabel></div>}
                  </div>
                  <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: Cc.mfg, textAlign: 'right' }}>{l.price}</span>
                </div>
              ))}
            </div>
          </div>
          <div>
            <MonoLabel color={Cc.dim}>REASON</MonoLabel>
            <div style={{ fontSize: 13, marginTop: 6, color: Cc.fg, lineHeight: 1.5 }}>{p.reason}</div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 16 }}>
              <button style={{ fontFamily: 'var(--font-mono)', fontSize: 11, padding: '8px 12px', background: Cc.fg, color: '#000', border: 'none', borderRadius: 2, cursor: 'pointer', textTransform: 'uppercase', letterSpacing: '0.10em' }}>Approve</button>
              <button style={{ fontFamily: 'var(--font-mono)', fontSize: 11, padding: '8px 12px', background: 'transparent', color: Cc.fg, border: `1px solid ${Cc.border}`, borderRadius: 2, cursor: 'pointer', textTransform: 'uppercase', letterSpacing: '0.10em' }}>Edit</button>
              <button style={{ fontFamily: 'var(--font-mono)', fontSize: 11, padding: '8px 12px', background: 'transparent', color: Cc.dim, border: `1px solid ${Cc.borderSoft}`, borderRadius: 2, cursor: 'pointer', textTransform: 'uppercase', letterSpacing: '0.10em', gridColumn: '1 / -1' }}>Decline</button>
            </div>
          </div>
        </div>
      </Panel>

      <Panel title="pantry · low" span={2}>
        <TableHead cols={[{ h:'ITEM', w:'1fr' },{ h:'ON HAND', w:'80px' },{ h:'PAR', w:'60px' },{ h:'RUNOUT', w:'90px' }]} />
        {content.pantry.map((p, i, arr) => (
          <div key={p.item} style={{
            display: 'grid', gridTemplateColumns: '1fr 80px 60px 90px',
            gap: 10, padding: '8px 0', alignItems: 'center',
            borderBottom: i < arr.length - 1 ? `1px solid ${Cc.borderSoft}` : 'none',
          }}>
            <span style={{ fontSize: 13 }}>{p.item}</span>
            <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: p.onHand === 0 ? Cc.amber : Cc.fg }}>{p.onHand}</span>
            <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: Cc.dim }}>{p.par}</span>
            <MonoLabel color={p.runout === 'now' ? Cc.amber : Cc.dim}>{p.runout}</MonoLabel>
          </div>
        ))}
      </Panel>

      <Panel title="vendors · 30d" span={1}>
        <div style={{ display: 'grid', gap: 10 }}>
          {content.vendors.map((v, i) => (
            <div key={i}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                <span style={{ fontSize: 12 }}>{v.name}</span>
                <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: Cc.mfg }}>${v.spend}</span>
              </div>
              <MicroBar pct={v.share} />
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="recent · delivered" span={1} scroll>
        <div style={{ display: 'grid', gap: 0 }}>
          {content.history.map((h, i, arr) => (
            <div key={h.id} style={{ padding: '8px 0', borderBottom: i < arr.length - 1 ? `1px solid ${Cc.borderSoft}` : 'none' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <span style={{ fontSize: 12 }}>{h.vendor}</span>
                <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: Cc.fg }}>{h.total}</span>
              </div>
              <MonoLabel>{h.id} · {h.state}</MonoLabel>
            </div>
          ))}
        </div>
      </Panel>
    </>
  );
}

// ─── Registry ────────────────────────────────────────────────────────────

const BESPOKE_BY_KIND = {
  contacts:        ContactsBespoke,
  measurements:    MeasurementsBespoke,
  events:          EventsBespoke,
  investigations:  InvestigationsBespoke,
  consolidations:  ConsolidationsBespoke,
  decks:           DecksBespoke,
  timelines:       TimelinesBespoke,
  orders:          OrdersBespoke,
};

window.BespokeFor = (kind) => BESPOKE_BY_KIND[kind];
