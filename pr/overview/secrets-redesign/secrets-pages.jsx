// Page renderers — one per credential kind, with per-kind variation.
//
// Cycle C delivers:
//   - drop the previous "voice paragraph that grows a state-line"; the
//     state plaque + What-Breaks block carry the dramatic weight.
//   - per-kind KV strips (oauth shows scopes + cadence; webhook shows
//     incoming URL + signing fingerprint; apikey shows kind + cadence;
//     token shows issued + expires).
//   - the stamp glyph in the audit list — different shape per action,
//     so a column of stamps reads as a narrative even before the
//     captions are scanned.
//   - the "passport spread" layout: heading + state plaque, then a
//     dense KV band, then a two-column body (left: scopes + breaks +
//     feeds; right: probe + audit), then the cross-reference footer.
//
// Common helpers below; per-kind exports at the bottom.

const Cs_X = window.C;

// ── Shared building blocks ──────────────────────────────────────────

// A tiny KV with mono value.
function KV({ label, value, valueColor, mono = true, size = 13 }) {
  return (
    <div>
      <window.Mono size={9} upper track="0.14em" color={Cs_X.dim}>{label}</window.Mono>
      <div style={{ marginTop: 4 }}>
        {mono
          ? <window.Mono size={size} color={valueColor || Cs_X.fg}>{value}</window.Mono>
          : <span style={{ fontFamily: 'var(--font-sans)', fontSize: size, color: valueColor || Cs_X.fg }}>{value}</span>}
      </div>
    </div>
  );
}

// Block heading with optional right caption.
function BlockHead({ eyebrow, right }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
      <window.Mono size={10} upper track="0.14em" color={Cs_X.dim}>{eyebrow}</window.Mono>
      {right && <window.Mono size={9} color={Cs_X.dim}>{right}</window.Mono>}
    </div>
  );
}

// Stamp row — uses StampGlyph + serif note + mono date.
function StampRow({ event, last }) {
  const [date, time] = event.ts.split(' ');
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '22px 76px 1fr',
      columnGap: 12, padding: '8px 0',
      borderBottom: last ? 'none' : `1px solid ${Cs_X.borderSoft}`,
      alignItems: 'flex-start',
    }}>
      <div style={{ paddingTop: 1 }}><window.StampGlyph action={event.action} size={12} /></div>
      <div>
        <window.Mono size={10} color={Cs_X.fg}>{date}</window.Mono>
        <div><window.Mono size={9} color={Cs_X.dim}>{time}</window.Mono></div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
          <window.Mono size={10} upper track="0.10em" color={Cs_X.fg}>{event.action}</window.Mono>
          <window.Mono size={9} color={Cs_X.dim}>· {event.actor}</window.Mono>
        </div>
        <span style={{ fontFamily: 'var(--font-serif)', fontSize: 12, color: Cs_X.mfg, lineHeight: 1.4 }}>
          {event.note}
        </span>
      </div>
    </div>
  );
}

// Visa row — used inside scope blocks for oauth + cli pages.
function VisaRow({ scope, state }) {
  const color = state === 'missing' ? Cs_X.amber : state === 'granted' ? Cs_X.fg : Cs_X.dim;
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '12px 1fr auto',
      columnGap: 10, alignItems: 'baseline', padding: '7px 0',
      borderBottom: `1px solid ${Cs_X.borderSoft}`,
    }}>
      <window.Mono size={10} color={state === 'missing' ? Cs_X.amber : Cs_X.mfg}>{state === 'missing' ? '∅' : '✓'}</window.Mono>
      <window.Mono size={11} color={color}>{scope}</window.Mono>
      <window.Mono size={9} upper track="0.10em" color={color}>
        {state === 'missing' ? 'not granted' : state === 'granted' ? 'granted' : 'extra'}
      </window.Mono>
    </div>
  );
}

// Heading band — used by every page. provider/key/runtime label on
// the left, state plaque on the right. Plaque IS the headline state.
function HeadingBand({
  eyebrowLeft, eyebrowSub, title, titleMono = false, subtitle,
  mark, stateColor, stateLabel, stateLines = [],
}) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 24 }}>
      <div style={{ minWidth: 0 }}>
        <window.Eyebrow sub={eyebrowSub}>{eyebrowLeft}</window.Eyebrow>
        <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 14, minWidth: 0 }}>
          {mark}
          <div style={{ minWidth: 0 }}>
            <h1 style={{
              fontFamily: titleMono ? 'var(--font-mono)' : 'var(--font-sans)',
              fontSize: titleMono ? 24 : 30,
              fontWeight: 500,
              letterSpacing: titleMono ? '0.005em' : '-0.025em',
              lineHeight: 1.05, margin: 0,
              color: Cs_X.fg,
            }}>{title}</h1>
            {subtitle && <window.Mono size={11} color={Cs_X.mfg}>{subtitle}</window.Mono>}
          </div>
        </div>
      </div>
      {/* Plaque */}
      <div style={{
        padding: '8px 12px',
        border: `1.5px solid ${stateColor}`, color: stateColor,
        display: 'flex', flexDirection: 'column', gap: 3, alignItems: 'flex-end',
        transform: 'rotate(1.5deg)', flexShrink: 0,
      }}>
        <window.Mono size={12} upper track="0.18em" color={stateColor} weight={500}>{stateLabel}</window.Mono>
        {stateLines.map((line, i) => (
          <window.Mono key={i} size={9} color={stateColor}>{line}</window.Mono>
        ))}
      </div>
    </div>
  );
}

// Cross-reference footer — used on every page; entries vary per kind.
function CrossRefFooter({ refs }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: `repeat(${refs.length}, 1fr)`, columnGap: 36,
      padding: '14px 0', borderTop: `1px solid ${Cs_X.border}`,
    }}>
      {refs.map((r, i) => (
        <div key={i}>
          <window.Mono size={9} upper track="0.14em" color={Cs_X.dim}>{r.eyebrow}</window.Mono>
          <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
            {r.children}
          </div>
        </div>
      ))}
    </div>
  );
}

// Footer commits — always-on layout, content varies by state.
function CommitFooter({ left, right }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      paddingTop: 14, borderTop: `1px solid ${Cs_X.border}`,
    }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>{left}</div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>{right}</div>
    </div>
  );
}

// ── Page: User secret (oauth / token / webhook / apikey) ────────────

function PageUser({ s }) {
  const provider = window.PROVIDERS[s.provider];
  const meta = window.STATE_CATALOG[s.state] || {};
  const stateColor = meta.tone === 'red' ? Cs_X.red : meta.tone === 'amber' ? Cs_X.amber : meta.tone === 'ok' ? Cs_X.green : Cs_X.dim;
  const grantedSet = new Set(s.scopesGranted);
  const requiredSet = new Set(s.scopesRequired);
  const allScopes = Array.from(new Set([...s.scopesGranted, ...s.scopesRequired]));
  const isOauth = provider.kind === 'oauth';
  const isWebhook = provider.kind === 'webhook';
  const isApikey = provider.kind === 'apikey';
  const isMissing = s.state === 'never_set';
  const sick = s.state !== 'ok' && s.state !== 'never_set';

  const stateLines = [];
  if (s.state === 'expired' && s.failureTail) stateLines.push(s.failureTail);
  if (s.state === 'expiring' && s.expires) stateLines.push(`expires ${s.expires}`);
  if (s.state === 'ok' && s.lastVerified) stateLines.push(`verified ${s.lastVerified}`);
  if (s.state === 'scope_mismatch') stateLines.push(`${requiredSet.size - grantedSet.size} scope missing`);
  if (s.state === 'never_set') stateLines.push('never connected');

  return (
    <div style={{ padding: '28px 36px 28px', display: 'flex', flexDirection: 'column', gap: 18 }}>
      <HeadingBand
        eyebrowLeft="issuing authority"
        eyebrowSub={`kind · ${provider.kind}`}
        title={provider.label}
        subtitle={`${provider.authority} · ${provider.kind}`}
        mark={<window.ProviderMark provider={s.provider} size={36} />}
        stateColor={stateColor}
        stateLabel={meta.label || 'unknown'}
        stateLines={stateLines}
      />

      {/* One short serif caption — what this credential is FOR. No
          state-conditional sentences (state lives on the plaque). */}
      <span style={{ fontFamily: 'var(--font-serif)', fontSize: 15, lineHeight: 1.55, color: Cs_X.mfg, maxWidth: '60ch' }}>
        {provider.brief}{' '}Feeds the {s.feeds.join(' and ')} butler{s.feeds.length === 1 ? '' : 's'}.
      </span>

      {/* Dense KV band — kind-aware columns */}
      <div style={{ padding: '14px 0', borderTop: `1px solid ${Cs_X.border}`, borderBottom: `1px solid ${Cs_X.border}` }}>
        {isWebhook ? (
          <div style={{ display: 'grid', gridTemplateColumns: '180px 1fr 130px 130px', columnGap: 20, alignItems: 'baseline' }}>
            <div>
              <window.Mono size={9} upper track="0.16em" color={Cs_X.dim}>passport no.</window.Mono>
              <div style={{ marginTop: 4 }}><window.FingerprintRow value={s.fingerprint} size={13} /></div>
            </div>
            <KV mono label="incoming url" value={s.webhook || '—'} valueColor={Cs_X.fg} size={12} />
            <KV mono label="issued"  value={s.issued || '—'} valueColor={s.issued ? Cs_X.fg : Cs_X.dim} />
            <KV mono label="last seen" value={s.lastVerified || '—'} valueColor={s.lastVerified ? Cs_X.fg : Cs_X.dim} />
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: '180px 110px 110px 130px 130px 1fr', columnGap: 20, alignItems: 'baseline' }}>
            <div>
              <window.Mono size={9} upper track="0.16em" color={Cs_X.dim}>passport no.</window.Mono>
              <div style={{ marginTop: 4 }}><window.FingerprintRow value={s.fingerprint} size={13} /></div>
            </div>
            <KV label="issued"  value={s.issued || '—'} valueColor={s.issued ? Cs_X.fg : Cs_X.dim} />
            <KV label="expires" value={s.expires || 'no expiry'} valueColor={s.state === 'expired' ? Cs_X.red : s.state === 'expiring' ? Cs_X.amber : (s.expires ? Cs_X.fg : Cs_X.mfg)} />
            <KV label="last verified" value={s.lastVerified || '—'} valueColor={s.lastVerified ? Cs_X.fg : Cs_X.dim} />
            <KV label="last used"     value={s.lastUsed || '—'}     valueColor={s.lastUsed ? Cs_X.fg : Cs_X.dim} />
            <div>
              <window.Mono size={9} upper track="0.14em" color={Cs_X.dim}>scopes</window.Mono>
              <div style={{ marginTop: 6 }}>
                {requiredSet.size > 0
                  ? <window.ScopeBalance granted={s.scopesGranted} required={s.scopesRequired} width={120} />
                  : <window.Mono size={11} color={Cs_X.dim}>no scope set</window.Mono>}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Body — two columns: scopes / breaks / feeds on the left, probe + stamps on the right */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 1fr', columnGap: 36 }}>
        {/* LEFT */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          {/* Scopes block (oauth + apikey + token if scopes apply) */}
          {requiredSet.size > 0 && (
            <div>
              <BlockHead
                eyebrow="visa permissions · scopes"
                right={`${[...grantedSet].filter((sc) => requiredSet.has(sc)).length}/${requiredSet.size} required`}
              />
              <div style={{ marginTop: 8, borderTop: `1px solid ${Cs_X.border}` }}>
                {allScopes.map((scope) => {
                  const state = grantedSet.has(scope) ? (requiredSet.has(scope) ? 'granted' : 'extra') : 'missing';
                  return <VisaRow key={scope} scope={scope} state={state} />;
                })}
              </div>
            </div>
          )}
          {/* What breaks — the dramatic anchor */}
          {s.breaks && s.breaks.length > 0 && (
            <window.WhatBreaks breaks={s.breaks} state={s.state} />
          )}
          {/* Feeds */}
          <div>
            <window.Mono size={9} upper track="0.14em" color={Cs_X.dim}>feeds</window.Mono>
            <div style={{ marginTop: 8, display: 'flex', gap: 14, flexWrap: 'wrap', paddingTop: 8, borderTop: `1px solid ${Cs_X.borderSoft}` }}>
              {s.feeds.map((f) => (
                <span key={f} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <window.ButlerMark name={f} size={14} tone="fill" />
                  <span style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: Cs_X.fg }}>{f}</span>
                </span>
              ))}
            </div>
          </div>
        </div>

        {/* RIGHT */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          {/* Probe — the 1-call test result */}
          <div>
            <BlockHead eyebrow="probe · last test" right={s.test ? (s.test.ok ? 'ok' : 'failed') : 'never'} />
            <div style={{ marginTop: 10, paddingTop: 8, borderTop: `1px solid ${Cs_X.border}` }}>
              <window.ProbeResult test={s.test} />
            </div>
          </div>
          {/* Stamps — audit */}
          <div>
            <BlockHead eyebrow="stamps · audit" right={`${s.audit.length} event${s.audit.length === 1 ? '' : 's'}`} />
            <div style={{ marginTop: 8, borderTop: `1px solid ${Cs_X.border}` }}>
              {s.audit.length === 0 && (
                <span style={{ display: 'block', paddingTop: 10, fontFamily: 'var(--font-serif)', fontStyle: 'italic', color: Cs_X.dim }}>
                  No stamps yet.
                </span>
              )}
              {s.audit.map((e, i) => <StampRow key={i} event={e} last={i === s.audit.length - 1} />)}
              {s.audit.length > 0 && (
                <div style={{ paddingTop: 10 }}>
                  <window.ActionArrow>open /audit ↗</window.ActionArrow>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Cross-references — kind-aware */}
      <CrossRefFooter refs={[
        {
          eyebrow: 'elsewhere',
          children: <>
            <window.ActionArrow>/ingestion/connectors/{s.provider}/{s.identity}</window.ActionArrow>
            <window.ActionArrow>/entities/{s.identity === 'tze' ? 'owner' : s.identity}</window.ActionArrow>
            {isOauth && <window.ActionArrow>{provider.authority} ↗</window.ActionArrow>}
          </>,
        },
        {
          eyebrow: 'config',
          children: <>
            <window.Mono size={11} color={Cs_X.mfg}>kind · {provider.kind}</window.Mono>
            <window.Mono size={11} color={Cs_X.mfg}>endpoint · {provider.authority}</window.Mono>
            <window.Mono size={11} color={Cs_X.mfg}>cadence · {provider.cadence}</window.Mono>
            {isWebhook && <window.Mono size={11} color={Cs_X.mfg}>secret · rotates with token</window.Mono>}
          </>,
        },
        {
          eyebrow: 'identity',
          children: <>
            <window.IdentityChip identity={window.IDENTITIES.find((i) => i.id === s.identity)} compact />
            <window.Mono size={10} color={Cs_X.dim}>entity_info · field #{s.provider}</window.Mono>
          </>,
        },
      ]} />

      {/* Footer commits */}
      <CommitFooter
        left={<>
          {(s.state === 'expired' || s.state === 'revoked' || s.state === 'scope_mismatch') && <window.PillBtn variant="commit">re-authorize</window.PillBtn>}
          {s.state === 'expiring' && <window.PillBtn variant="commit">rotate</window.PillBtn>}
          {isMissing && <window.PillBtn variant="commit">connect</window.PillBtn>}
          {!isMissing && <window.PillBtn>test</window.PillBtn>}
          {!isMissing && !sick && <window.PillBtn>rotate</window.PillBtn>}
        </>}
        right={<>
          {s.fingerprint && <window.PillBtn>reveal value</window.PillBtn>}
          {!isMissing && <window.PillBtn variant="danger">disconnect</window.PillBtn>}
        </>}
      />
    </div>
  );
}

// ── Page: System secret ──────────────────────────────────────────────

function PageSystem({ s }) {
  const isMissing = s.rowState === 'missing';
  const isLocal = s.rowState === 'local';
  const isPlain = !!s.plainValue;
  const stateColor = isMissing ? Cs_X.dim : Cs_X.green;
  const stateLabel = isMissing ? 'not set' : isLocal ? 'local override' : 'shared default';
  const stateLines = [];
  if (isLocal) stateLines.push(`target · ${s.target}`);
  else if (!isMissing) stateLines.push(`verified ${s.lastVerified}`);

  return (
    <div style={{ padding: '28px 36px 28px', display: 'flex', flexDirection: 'column', gap: 18 }}>
      <HeadingBand
        eyebrowLeft={`category · ${s.category}`}
        title={s.key}
        titleMono
        mark={null}
        stateColor={stateColor}
        stateLabel={stateLabel}
        stateLines={stateLines}
      />

      <span style={{ fontFamily: 'var(--font-serif)', fontSize: 15, lineHeight: 1.55, color: Cs_X.mfg, maxWidth: '60ch' }}>
        {s.description}
      </span>

      <div style={{ padding: '14px 0', borderTop: `1px solid ${Cs_X.border}`, borderBottom: `1px solid ${Cs_X.border}` }}>
        <div style={{ display: 'grid', gridTemplateColumns: '200px 140px 1fr', columnGap: 24, alignItems: 'baseline' }}>
          <div>
            <window.Mono size={9} upper track="0.16em" color={Cs_X.dim}>{isPlain ? 'value' : 'fingerprint'}</window.Mono>
            <div style={{ marginTop: 4 }}>
              {isPlain
                ? <window.Mono size={13} color={Cs_X.fg}>{s.plainValue}</window.Mono>
                : <window.FingerprintRow value={s.fingerprint} size={13} />}
            </div>
          </div>
          <KV label="last verified" value={s.lastVerified || '—'} valueColor={s.lastVerified ? Cs_X.fg : Cs_X.dim} />
          <div>
            <window.Mono size={9} upper track="0.14em" color={Cs_X.dim}>used by</window.Mono>
            <div style={{ marginTop: 6, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              {s.usedBy.length === 0 && <window.Mono size={11} color={Cs_X.dim}>nobody yet</window.Mono>}
              {s.usedBy[0] === '*' && (
                <span style={{ fontFamily: 'var(--font-serif)', fontStyle: 'italic', fontSize: 13, color: Cs_X.fg }}>
                  every butler that talks to a model.
                </span>
              )}
              {s.usedBy[0] !== '*' && s.usedBy.map((b) => (
                <span key={b} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <window.ButlerMark name={b} size={14} tone="fill" />
                  <span style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: Cs_X.fg }}>{b}</span>
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', columnGap: 36 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          {s.breaks && s.breaks.length > 0 && <window.WhatBreaks breaks={s.breaks} state={isMissing ? 'never_set' : 'ok'} />}
          {!s.breaks?.length && (
            <div>
              <window.Mono size={9} upper track="0.14em" color={Cs_X.dim}>what breaks</window.Mono>
              <div style={{ marginTop: 8, paddingTop: 10, borderTop: `1px solid ${Cs_X.border}` }}>
                <span style={{ fontFamily: 'var(--font-serif)', fontStyle: 'italic', fontSize: 13, color: Cs_X.dim }}>
                  Nothing routed here yet.
                </span>
              </div>
            </div>
          )}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div>
            <BlockHead eyebrow="probe · last test" right={s.test ? (s.test.ok ? 'ok' : 'failed') : 'never'} />
            <div style={{ marginTop: 10, paddingTop: 8, borderTop: `1px solid ${Cs_X.border}` }}>
              <window.ProbeResult test={s.test} />
            </div>
          </div>
          <div>
            <BlockHead eyebrow="stamps · audit" right={`${s.audit.length} event${s.audit.length === 1 ? '' : 's'}`} />
            <div style={{ marginTop: 8, borderTop: `1px solid ${Cs_X.border}` }}>
              {s.audit.length === 0 && (
                <span style={{ display: 'block', paddingTop: 10, fontFamily: 'var(--font-serif)', fontStyle: 'italic', color: Cs_X.dim }}>
                  No stamps yet.
                </span>
              )}
              {s.audit.map((e, i) => <StampRow key={i} event={e} last={i === s.audit.length - 1} />)}
            </div>
          </div>
        </div>
      </div>

      <CrossRefFooter refs={[
        {
          eyebrow: 'elsewhere',
          children: <>
            <window.ActionArrow>/audit?key={s.key}</window.ActionArrow>
            {s.usedBy.length > 0 && s.usedBy[0] !== '*' && <window.ActionArrow>/butlers/{s.usedBy[0]}</window.ActionArrow>}
          </>,
        },
        {
          eyebrow: 'storage',
          children: <>
            <window.Mono size={11} color={Cs_X.mfg}>butler_secrets · {s.target || 'shared'}</window.Mono>
            <window.Mono size={11} color={Cs_X.mfg}>category · {s.category}</window.Mono>
            <window.Mono size={11} color={Cs_X.mfg}>scope · {isLocal ? 'per butler' : 'shared default'}</window.Mono>
          </>,
        },
      ]} />

      <CommitFooter
        left={isMissing
          ? <window.PillBtn variant="commit">set value</window.PillBtn>
          : <>
              <window.PillBtn>test</window.PillBtn>
              <window.PillBtn>rotate</window.PillBtn>
              {!isLocal && <window.PillBtn>override · per butler</window.PillBtn>}
            </>}
        right={<>
          {s.fingerprint && !isPlain && <window.PillBtn>reveal value</window.PillBtn>}
          {!isMissing && <window.PillBtn variant="danger">delete</window.PillBtn>}
        </>}
      />
    </div>
  );
}

// ── Page: CLI runtime ────────────────────────────────────────────────

function PageCli({ r }) {
  const meta = window.STATE_CATALOG[r.state] || {};
  const stateColor = meta.tone === 'red' ? Cs_X.red : meta.tone === 'amber' ? Cs_X.amber : meta.tone === 'ok' ? Cs_X.green : Cs_X.dim;
  const isMissing = r.state === 'never_set';
  const stateLines = [];
  if (r.state === 'expiring' && r.expires) stateLines.push(`expires ${r.expires}`);
  if (r.state === 'ok' && r.lastUsed) stateLines.push(`used ${r.lastUsed}`);
  if (isMissing) stateLines.push('paste a token to enable');

  return (
    <div style={{ padding: '28px 36px 28px', display: 'flex', flexDirection: 'column', gap: 18 }}>
      <HeadingBand
        eyebrowLeft="command-line agent"
        eyebrowSub={r.id}
        title={r.label}
        subtitle={r.id}
        mark={null}
        stateColor={stateColor}
        stateLabel={meta.label || 'unknown'}
        stateLines={stateLines}
      />

      <span style={{ fontFamily: 'var(--font-serif)', fontSize: 15, lineHeight: 1.55, color: Cs_X.mfg, maxWidth: '60ch' }}>
        Token used by the {r.label} CLI to authenticate against the system.
      </span>

      <div style={{ padding: '14px 0', borderTop: `1px solid ${Cs_X.border}`, borderBottom: `1px solid ${Cs_X.border}` }}>
        <div style={{ display: 'grid', gridTemplateColumns: '200px 110px 130px 130px', columnGap: 24, alignItems: 'baseline' }}>
          <div>
            <window.Mono size={9} upper track="0.16em" color={Cs_X.dim}>passport no.</window.Mono>
            <div style={{ marginTop: 4 }}><window.FingerprintRow value={r.fingerprint} size={13} /></div>
          </div>
          <KV label="issued"    value={r.issued  || '—'} valueColor={r.issued  ? Cs_X.fg : Cs_X.dim} />
          <KV label="expires"   value={r.expires || 'no expiry'} valueColor={r.state === 'expiring' ? Cs_X.amber : (r.expires ? Cs_X.fg : Cs_X.mfg)} />
          <KV label="last used" value={r.lastUsed || '—'} valueColor={r.lastUsed ? Cs_X.fg : Cs_X.dim} />
        </div>
      </div>

      {/* Body — scopes (cli scopes are tiny but real), probe + how-to-use, audit */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 1fr', columnGap: 36 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          {r.scopesRequired && r.scopesRequired.length > 0 && (
            <div>
              <BlockHead
                eyebrow="capabilities"
                right={`${(r.scopesGranted || []).length}/${r.scopesRequired.length} required`}
              />
              <div style={{ marginTop: 8, borderTop: `1px solid ${Cs_X.border}` }}>
                {Array.from(new Set([...(r.scopesGranted || []), ...r.scopesRequired])).map((scope) => {
                  const grantedSet = new Set(r.scopesGranted || []);
                  const requiredSet = new Set(r.scopesRequired);
                  const state = grantedSet.has(scope) ? (requiredSet.has(scope) ? 'granted' : 'extra') : 'missing';
                  return <VisaRow key={scope} scope={scope} state={state} />;
                })}
              </div>
            </div>
          )}
          <div>
            <window.Mono size={9} upper track="0.14em" color={Cs_X.dim}>how to use</window.Mono>
            <div style={{
              marginTop: 8, padding: '12px 14px',
              border: `1px solid ${Cs_X.borderSoft}`, background: Cs_X.bgElev,
            }}>
              <window.Mono size={11} color={Cs_X.fg}>
                $ {r.id} --token $({r.id.toUpperCase().replace('-', '_')}_TOKEN)
              </window.Mono>
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div>
            <BlockHead eyebrow="probe · last test" right={r.test ? (r.test.ok ? 'ok' : 'failed') : 'never'} />
            <div style={{ marginTop: 10, paddingTop: 8, borderTop: `1px solid ${Cs_X.border}` }}>
              <window.ProbeResult test={r.test} />
            </div>
          </div>
        </div>
      </div>

      <CrossRefFooter refs={[
        {
          eyebrow: 'elsewhere',
          children: <>
            <window.ActionArrow>/audit?actor={r.id}</window.ActionArrow>
          </>,
        },
        {
          eyebrow: 'config',
          children: <>
            <window.Mono size={11} color={Cs_X.mfg}>runtime · {r.id}</window.Mono>
            <window.Mono size={11} color={Cs_X.mfg}>scope · session-bound</window.Mono>
          </>,
        },
      ]} />

      <CommitFooter
        left={isMissing
          ? <window.PillBtn variant="commit">set token</window.PillBtn>
          : <>
              <window.PillBtn variant={r.state === 'expiring' ? 'commit' : 'pill'}>rotate</window.PillBtn>
              <window.PillBtn>test</window.PillBtn>
            </>}
        right={<>
          {r.fingerprint && <window.PillBtn>reveal token</window.PillBtn>}
          {!isMissing && <window.PillBtn variant="danger">revoke</window.PillBtn>}
        </>}
      />
    </div>
  );
}

Object.assign(window, { PageUser, PageSystem, PageCli });
