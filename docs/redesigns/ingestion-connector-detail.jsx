// Connector detail · "The Spotify dossier"
//
// Direction: a single connector opened up — left column narrates state and
// recent activity, right column lists the operational knobs (scopes,
// schedule, filters that target this channel, recent incidents, raw config).
//
// This is the page you land on when a row in the Roster / Board is opened.
// In the live Spotify scenario it doubles as the place where the user
// performs the re-auth (since QA's open investigation #218 links here).

function ConnectorDetailSpotify({ onBack }) {
  const C = window.C;
  const c = window.CONNECTOR_DETAILS.find((x) => x.id === 'spotify');
  if (!c) return null;

  const spotifyEvents = window.EVENTS.filter((e) => e.channel === 'spotify');
  const max = Math.max(...c.spark24h, 1);

  return (
    <div style={{ background: C.bg, color: C.fg, minHeight: '100%' }}>
      <div style={{ maxWidth: 1500, margin: '0 auto', padding: '40px 56px 80px' }}>

        {/* Breadcrumb */}
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 18 }}>
          <a href="#" onClick={(ev) => { ev.preventDefault(); onBack && onBack(); }} style={{
            fontFamily: 'var(--font-mono)', fontSize: 10, color: C.dim,
            textDecoration: 'underline', textUnderlineOffset: 4,
            textDecorationColor: C.borderStrong, letterSpacing: '0.10em', textTransform: 'uppercase',
          }}>← ingestion / connectors</a>
        </div>

        {/* Header band */}
        <div style={{
          display: 'grid', gridTemplateColumns: '1fr auto',
          gap: 32, alignItems: 'flex-start',
          paddingBottom: 24, borderBottom: `1px solid ${C.border}`,
        }}>
          <div>
            <Eyebrow style={{ marginBottom: 8 }}>connector · spotify · /me/player/recently-played</Eyebrow>
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 16 }}>
              <ChannelGlyph channel="spotify" size={56} />
              <div>
                <h1 style={{ margin: 0, fontSize: 44, fontWeight: 500,
                  letterSpacing: '-0.025em', lineHeight: 1.05, color: C.fg }}>
                  Spotify.
                </h1>
                <div style={{
                  marginTop: 6, display: 'flex', alignItems: 'baseline', gap: 14,
                  fontFamily: 'var(--font-mono)', fontSize: 11, color: C.mfg,
                  letterSpacing: '0.04em',
                }}>
                  <span>{c.kind}</span>
                  <span>·</span>
                  <span>{c.config.cadence}</span>
                  <span>·</span>
                  <span>last · {c.lastEventAt}</span>
                </div>
              </div>
            </div>
            <div style={{
              marginTop: 18, fontFamily: 'var(--font-serif)', fontSize: 17,
              color: C.fg, letterSpacing: 0, maxWidth: '50ch', lineHeight: 1.5,
            }}>
              Polls the Spotify Web API every ten minutes for recently played
              tracks. Routes each play to chronicler so the day's listening
              history shows up in the timeline.
            </div>
          </div>

          {/* The reauth call-to-action */}
          <div style={{
            border: `1px solid ${C.red}`,
            padding: '18px 22px',
            minWidth: 320, maxWidth: 360,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ width: 6, height: 6, borderRadius: 999, background: C.red }} />
              <Mono color={C.red} size={10} style={{ letterSpacing: '0.10em', textTransform: 'uppercase' }}>
                reauth required
              </Mono>
            </div>
            <div style={{
              marginTop: 10, fontFamily: 'var(--font-serif)', fontSize: 15,
              color: C.fg, letterSpacing: 0, lineHeight: 1.45,
            }}>
              Spotify rotated the OAuth scope name for
              {' '}<span style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5 }}>
                /me/player/recently-played
              </span>{' '}
              on May 5. The poll has been 401-ing since then; QA opened
              {' '}<a href="#" onClick={(ev) => ev.preventDefault()}
                style={{ color: C.fg, textDecoration: 'underline',
                  textUnderlineOffset: 3, textDecorationColor: C.borderStrong }}>
                #218
              </a> to track the fix.
            </div>
            <div style={{ marginTop: 14, display: 'flex', gap: 8 }}>
              <PillBtn kind="commit">re-authorize</PillBtn>
              <PillBtn>view #218</PillBtn>
            </div>
          </div>
        </div>

        {/* The two-column body */}
        <div style={{
          marginTop: 36,
          display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 56,
          alignItems: 'start',
        }}>
          {/* LEFT — narrative + throughput */}
          <div>
            {/* KPI strip */}
            <div style={{
              display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 24,
              padding: '14px 0', borderTop: `1px solid ${C.border}`, borderBottom: `1px solid ${C.border}`,
            }}>
              {[
                { k: 'events · 24h',   v: c.events24h, delta: '+8%' },
                { k: 'sessions · 24h', v: c.sessions24h, delta: 'fan-out 1:1' },
                { k: 'cost · 24h',     v: window.fmtCost(c.cost24h), delta: 'gpt-5.4-nano' },
                { k: 'latency · p50',  v: c.config.latencyMs + 'ms', delta: 'poll RTT' },
              ].map((it, i) => (
                <div key={i}>
                  <Eyebrow>{it.k}</Eyebrow>
                  <div className="tnum" style={{
                    marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 26,
                    fontWeight: 500, letterSpacing: '-0.02em', color: C.fg,
                  }}>{it.v}</div>
                  <Mono color={C.dim} size={10} style={{ display: 'block', marginTop: 4 }}>{it.delta}</Mono>
                </div>
              ))}
            </div>

            {/* 24h throughput */}
            <div style={{ marginTop: 28 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 12 }}>
                <Eyebrow>throughput · 24h</Eyebrow>
                <Mono color={C.dim} size={10}>plays per hour</Mono>
                <span style={{ marginLeft: 'auto' }} />
                <Mono color={C.dim} size={10}>peak · 24 · 13:00</Mono>
              </div>
              <ConnectorHistogram data={c.spark24h} max={max} />
            </div>

            {/* Recent events from this channel */}
            <div style={{ marginTop: 32 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 8 }}>
                <Eyebrow>recent · {spotifyEvents.length}</Eyebrow>
                <Mono color={C.dim} size={10}>since 00:00</Mono>
                <span style={{ marginLeft: 'auto' }} />
                <a href="#" onClick={(ev) => ev.preventDefault()} style={{
                  fontFamily: 'var(--font-mono)', fontSize: 10, color: C.fg,
                  textDecoration: 'underline', textUnderlineOffset: 3,
                  textDecorationColor: C.borderStrong, letterSpacing: '0.06em', textTransform: 'uppercase',
                }}>view in timeline →</a>
              </div>
              <div>
                {spotifyEvents.map((e) => (
                  <div key={e.id} style={{
                    display: 'grid', gridTemplateColumns: '60px 14px 1fr auto auto',
                    gap: 14, padding: '12px 0',
                    borderBottom: `1px solid ${C.borderSoft}`,
                    alignItems: 'baseline',
                  }}>
                    <Mono size={11}>{e.t}</Mono>
                    <span style={{ width: 6, height: 6, borderRadius: 999,
                      background: e.status === 'ingested' ? C.green : C.amber,
                      marginTop: 4 }} />
                    <div style={{ minWidth: 0 }}>
                      <span style={{ fontSize: 13.5, letterSpacing: '-0.005em' }}>
                        {e.summary}
                      </span>
                      <Mono color={C.dim} size={10} style={{ display: 'block', marginTop: 3 }}>
                        chronicler · {window.fmtDur(e.durationMs)} · {window.fmtTok(e.tokensIn)} in → {window.fmtTok(e.tokensOut)} out
                      </Mono>
                    </div>
                    <Mono color={C.dim} size={10}>{window.fmtCost(e.cost)}</Mono>
                    <a href="#" onClick={(ev) => ev.preventDefault()} style={{ color: C.dim, fontSize: 12, textDecoration: 'none' }}>›</a>
                  </div>
                ))}
              </div>
            </div>

            {/* Incidents */}
            <div style={{ marginTop: 32 }}>
              <Eyebrow style={{ marginBottom: 10 }}>incidents · 24h</Eyebrow>
              <div>
                {c.incidents.map((it, i) => (
                  <div key={i} style={{
                    display: 'grid', gridTemplateColumns: '60px 14px 1fr',
                    gap: 14, padding: '11px 0',
                    borderBottom: `1px solid ${C.borderSoft}`,
                    alignItems: 'baseline',
                  }}>
                    <Mono size={11}>{it.ts}</Mono>
                    <span style={{
                      width: 6, height: 6, borderRadius: 1,
                      background: it.kind === 'error' ? C.red : (it.kind === 'qa.alert' ? C.red : C.amber),
                      marginTop: 4,
                    }} />
                    <div>
                      <Mono color={C.dim} size={10} style={{ letterSpacing: '0.10em', textTransform: 'uppercase' }}>
                        {it.kind}
                      </Mono>
                      <div style={{
                        marginTop: 2, fontFamily: 'var(--font-serif)', fontSize: 13.5,
                        color: C.fg, lineHeight: 1.5,
                      }}>{it.text}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* RIGHT — index: scopes, schedule, filters, config */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 32 }}>
            {/* Scopes */}
            <div>
              <Eyebrow style={{ marginBottom: 10 }}>oauth scopes · 3</Eyebrow>
              <div>
                {c.scopes.map((s, i) => {
                  const isBroken = s === 'user-read-recently-played';
                  return (
                    <div key={i} style={{
                      display: 'grid', gridTemplateColumns: '12px 1fr auto',
                      gap: 10, padding: '10px 0',
                      borderBottom: `1px solid ${C.borderSoft}`,
                      alignItems: 'baseline',
                    }}>
                      <span style={{ width: 6, height: 6, borderRadius: 999,
                        background: isBroken ? C.red : C.green, marginTop: 4 }} />
                      <Mono size={11.5} color={isBroken ? C.red : C.fg}>{s}</Mono>
                      <Mono color={C.dim} size={10}>{isBroken ? 'mismatch' : 'granted'}</Mono>
                    </div>
                  );
                })}
              </div>
              <div style={{
                marginTop: 10, fontFamily: 'var(--font-serif)', fontSize: 12.5,
                color: C.mfg, lineHeight: 1.5, fontStyle: 'italic',
              }}>
                Reauthorising will request the rotated scope name and resume the poll.
              </div>
            </div>

            {/* Schedule */}
            <div>
              <Eyebrow style={{ marginBottom: 10 }}>schedule</Eyebrow>
              <KV label="cadence" value={<Mono>poll · every 10m</Mono>} />
              <KV label="next run" value={<Mono>11:40:11</Mono>} />
              <KV label="paused"   value={<Mono color={C.dim}>no — failing on retry</Mono>} />
              <div style={{ marginTop: 14, display: 'flex', gap: 8 }}>
                <PillBtn>pause poll</PillBtn>
                <PillBtn>run now</PillBtn>
              </div>
            </div>

            {/* Filters targeting this channel */}
            <div>
              <Eyebrow style={{ marginBottom: 10 }}>routing rules · 1</Eyebrow>
              <a href="#" onClick={(ev) => ev.preventDefault()} style={{
                display: 'block',
                padding: '12px 0', borderBottom: `1px solid ${C.borderSoft}`,
                textDecoration: 'none', color: C.fg,
              }}>
                <div style={{ fontSize: 13.5, fontWeight: 500, letterSpacing: '-0.005em' }}>Spotify — skips</div>
                <Mono color={C.dim} size={10} style={{ marginTop: 4, display: 'block' }}>
                  drop plays under 30s · 22 matches today
                </Mono>
              </a>
              <div style={{ marginTop: 10 }}>
                <PillBtn>+ add rule</PillBtn>
              </div>
            </div>

            {/* Raw config */}
            <div>
              <Eyebrow style={{ marginBottom: 10 }}>config</Eyebrow>
              <KV label="endpoint"  value={<Mono size={10.5}>{c.config.endpoint}</Mono>} />
              <KV label="latency · p50" value={<Mono>{c.config.latencyMs}ms</Mono>} />
              <KV label="auth · type"   value={<Mono>oauth · authorization code · PKCE</Mono>} />
              <KV label="enabled"   value={<Mono color={C.fg}>yes</Mono>} />
              <div style={{ marginTop: 14, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <PillBtn>rotate token</PillBtn>
                <PillBtn>copy config</PillBtn>
                <PillBtn>disconnect</PillBtn>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function KV({ label, value }) {
  const C = window.C;
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '100px 1fr', gap: 12,
      padding: '8px 0',
      borderBottom: `1px solid ${C.borderSoft}`,
      alignItems: 'baseline',
    }}>
      <Eyebrow>{label}</Eyebrow>
      <div style={{ minWidth: 0 }}>{value}</div>
    </div>
  );
}

function ConnectorHistogram({ data, max }) {
  const C = window.C;
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: 96, width: '100%' }}>
        {data.map((v, i) => {
          const h = Math.max(2, (v / (max || 1)) * 96);
          const isPeak = v === Math.max(...data);
          return (
            <div key={i} style={{
              flex: 1, position: 'relative',
              height: h,
              background: v === 0 ? C.borderSoft : (isPeak ? C.fg : window.__theme === 'light' ? 'oklch(0.18 0 0 / 0.55)' : 'oklch(0.985 0 0 / 0.65)'),
            }} />
          );
        })}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6,
        fontFamily: 'var(--font-mono)', fontSize: 9.5, color: C.dim, letterSpacing: '0.06em',
      }}>
        <span>00</span><span>03</span><span>06</span><span>09</span><span>12</span><span>15</span><span>18</span><span>21</span><span>23</span>
      </div>
    </div>
  );
}

window.ConnectorDetailSpotify = ConnectorDetailSpotify;
