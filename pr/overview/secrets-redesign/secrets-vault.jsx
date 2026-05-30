// Direction B — The Vault.
//
// Every credential is a slot in a hairline grid — like the panel of a
// safety-deposit box wall, or the contact-print sheet of a 4×5 view
// camera. Slots are NOT cards: no fills, no shadows, no rounded
// corners. They are cut-out windows in one continuous surface,
// separated by single hairlines. Each slot carries its own evidence:
// provider mark, fingerprint, scopes, last verified, and a state
// plaque in the top-right corner that becomes the focal point only
// when state demands.
//
// What this direction does well:
//   - spatial mnemonic; you remember the position of each integration
//   - unhealthy slots claim attention without any colour appearing
//     on calm ones
//   - the slot is where evidence lives — fingerprint, scopes, dates
//     are all visible at the same height
// What it gives up:
//   - density (a slot eats 280×210px)
//   - vertical scanning of state across all rows (the eye has to grid)

const Cs_V = window.C;

function VaultUserSlot({ s }) {
  const provider = window.PROVIDERS[s.provider];
  const meta = window.STATE_CATALOG[s.state] || {};
  const stateColor = meta.tone === 'red' ? Cs_V.red
    : meta.tone === 'amber' ? Cs_V.amber
    : meta.tone === 'ok' ? Cs_V.green
    : Cs_V.dim;
  const isMissing = s.state === 'never_set';
  const needsHand = s.state === 'expired' || s.state === 'revoked' || s.state === 'scope_mismatch';
  const expiring = s.state === 'expiring';

  return (
    <div style={{
      position: 'relative',
      padding: '18px 18px 14px',
      display: 'flex', flexDirection: 'column', gap: 12,
      minHeight: 200,
      // No background, no shadow. Hairlines come from the grid container.
      // The only emphatic element on calm slots is the provider mark.
    }}>
      {/* Top: provider + state plaque */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
          <window.ProviderMark provider={s.provider} size={28} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
            <span style={{ fontFamily: 'var(--font-sans)', fontSize: 16, fontWeight: 500, color: Cs_V.fg, letterSpacing: '-0.015em' }}>
              {provider.label}
            </span>
            <window.Mono size={10} color={Cs_V.dim}>{provider.authority}</window.Mono>
          </div>
        </div>

        {/* State plaque — a small block in the top-right corner.
            On healthy slots it's a calm "ok · 14:21". On unhealthy
            slots it picks up colour. */}
        <div style={{
          display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 2,
          padding: '4px 8px',
          border: needsHand
            ? `1px solid ${stateColor}`
            : `1px solid ${Cs_V.borderSoft}`,
          borderRadius: 2,
          color: needsHand ? stateColor : Cs_V.mfg,
        }}>
          <window.Mono size={9} upper track="0.12em" color={needsHand ? stateColor : Cs_V.mfg}>
            {meta.label || 'unknown'}
          </window.Mono>
          {s.lastVerified && (
            <window.Mono size={9} color={needsHand ? stateColor : Cs_V.dim}>
              {s.state === 'expired' ? `${s.lastVerified}` : `verified ${s.lastVerified}`}
            </window.Mono>
          )}
          {expiring && <window.Mono size={9} color={Cs_V.amber}>expires {s.expires}</window.Mono>}
        </div>
      </div>

      {/* Body: fingerprint + scopes + meta */}
      {!isMissing ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, flex: 1 }}>
          {/* Fingerprint as the headline evidence */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <window.Fingerprint value={s.fingerprint} size={12} />
            <window.RevealEye revealed={false} />
          </div>
          {/* Scopes */}
          <div style={{ paddingTop: 4, borderTop: `1px solid ${Cs_V.borderSoft}` }}>
            <window.Mono size={9} upper track="0.12em" color={Cs_V.dim}>scopes</window.Mono>
            <div style={{ marginTop: 6 }}>
              <window.ScopeRow granted={s.scopesGranted} required={s.scopesRequired} compact />
            </div>
          </div>
          {/* Feeds + failure tail */}
          <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: 4 }}>
            <window.Mono size={10} color={Cs_V.dim}>feeds {s.feeds.join(', ')}</window.Mono>
            {s.state === 'expired' && s.failureTail && (
              <span style={{ fontFamily: 'var(--font-serif)', fontStyle: 'italic', fontSize: 12, color: Cs_V.red }}>
                {s.failureTail}
              </span>
            )}
          </div>
        </div>
      ) : (
        // Missing-slot empty state: serif italic line, single sentence.
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
          <span style={{ fontFamily: 'var(--font-serif)', fontStyle: 'italic', fontSize: 14, color: Cs_V.dim, marginTop: 14 }}>
            Not connected.
          </span>
          <window.Mono size={10} color={Cs_V.dim}>
            would feed {s.feeds.join(', ')} · {s.scopesRequired.length} scope{s.scopesRequired.length === 1 ? '' : 's'} required
          </window.Mono>
        </div>
      )}

      {/* Footer: actions */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, paddingTop: 10, borderTop: `1px solid ${Cs_V.borderSoft}` }}>
        {isMissing && <window.PillBtn variant="commit">connect</window.PillBtn>}
        {needsHand && <window.PillBtn variant="commit">re-authorize</window.PillBtn>}
        {expiring && <window.PillBtn>re-authorize</window.PillBtn>}
        {s.state === 'ok' && <window.PillBtn>test</window.PillBtn>}
        {!isMissing && <window.PillBtn>audit</window.PillBtn>}
      </div>
    </div>
  );
}

function VaultSystemRow({ s, last }) {
  const isMissing = s.rowState === 'missing';
  const isLocal = s.rowState === 'local';
  const isPlain = !!s.plainValue;

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '1fr 200px 140px 100px',
      columnGap: 16, alignItems: 'center',
      padding: '12px 0',
      borderBottom: last ? 'none' : `1px solid ${Cs_V.borderSoft}`,
      opacity: isMissing ? 0.65 : 1,
    }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
        <window.Mono size={12} weight={500} color={Cs_V.fg}>{s.key}</window.Mono>
        <span style={{ fontFamily: 'var(--font-serif)', fontSize: 12, color: Cs_V.mfg, lineHeight: 1.4 }}>{s.description}</span>
      </div>
      {isPlain ? (
        <window.Mono size={11} color={Cs_V.fg}>{s.plainValue}</window.Mono>
      ) : (
        <window.Fingerprint value={s.fingerprint} />
      )}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
        {isLocal && <window.Mono size={10} upper track="0.10em" color={Cs_V.fg}>local · {s.target}</window.Mono>}
        {!isLocal && !isMissing && <window.Mono size={10} upper track="0.10em" color={Cs_V.mfg}>shared</window.Mono>}
        {isMissing && <window.Mono size={10} upper track="0.10em" color={Cs_V.dim}>not set</window.Mono>}
        {s.usedBy.length > 0 && (
          <window.Mono size={10} color={Cs_V.dim}>{s.usedBy[0] === '*' ? 'all butlers' : s.usedBy.join(', ')}</window.Mono>
        )}
      </div>
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        {isMissing
          ? <window.ActionArrow>set value</window.ActionArrow>
          : <window.ActionArrow>open</window.ActionArrow>}
      </div>
    </div>
  );
}

function DirectionVault() {
  const userSecrets = window.USER_SECRETS.filter((s) => s.identity === 'tze');
  const systemSecrets = window.SYSTEM_SECRETS;
  const k = window.computeKpis();

  return (
    <div style={{
      background: Cs_V.bg, color: Cs_V.fg, fontFamily: 'var(--font-sans)',
      display: 'flex', minHeight: '100%',
    }}>
      <window.FakeRail />
      <div style={{ flex: 1, minWidth: 0, padding: '40px 48px 48px' }}>
        {/* Header */}
        <window.PageHeader
          eyebrow="secrets · the vault"
          eyebrowSub="Sat, 23 May 2026 · 14:23"
          headline={`A wall of slots. Two need attention.`}
          voice={<>Each integration sits in its own slot, evidence in plain view. Spotify expired Wednesday; WhatsApp is short one scope.</>}
          headlineMaxWidth="20ch"
          right={
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 8 }}>
              <window.Mono size={10} upper track="0.14em" color={Cs_V.mfg}>identity</window.Mono>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontFamily: 'var(--font-sans)', fontSize: 16, fontWeight: 500 }}>Tze</span>
                <window.Mono size={10} color={Cs_V.dim}>(you)</window.Mono>
                <window.PillBtn>change →</window.PillBtn>
              </div>
            </div>
          }
        />

        {/* ── Section 1: Integrations wall ──────────────── */}
        <div style={{ marginBottom: 40 }}>
          <window.SectionHead
            eyebrow={`§1 · integrations · ${userSecrets.length}`}
            right={<window.Mono size={10} color={Cs_V.dim}>identity-bound · entity_info</window.Mono>}
          />
          {/* The grid IS the vault wall — hairlines between slots, not
              around each slot. This is the move that prevents the
              "cards" reading. */}
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)',
            border: `1px solid ${Cs_V.border}`,
          }}>
            {userSecrets.map((s, i) => {
              const col = i % 3;
              const row = Math.floor(i / 3);
              const rowsTotal = Math.ceil(userSecrets.length / 3);
              return (
                <div key={s.provider} style={{
                  borderLeft: col === 0 ? 'none' : `1px solid ${Cs_V.border}`,
                  borderTop: row === 0 ? 'none' : `1px solid ${Cs_V.border}`,
                  position: 'relative',
                }}>
                  <window.Sliver state={s.state} />
                  <VaultUserSlot s={s} />
                </div>
              );
            })}
            {/* "Add" slot at the end — same geometry, mono prompt */}
            {(() => {
              const i = userSecrets.length;
              const col = i % 3;
              const row = Math.floor(i / 3);
              return (
                <button style={{
                  borderLeft: col === 0 ? 'none' : `1px solid ${Cs_V.border}`,
                  borderTop: row === 0 ? 'none' : `1px solid ${Cs_V.border}`,
                  background: 'transparent', color: Cs_V.dim,
                  padding: '18px', minHeight: 200, cursor: 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.1em',
                  textTransform: 'uppercase',
                }}>+ add integration</button>
              );
            })()}
          </div>
        </div>

        {/* ── Section 2: System secrets ───────────────── */}
        <div style={{ marginBottom: 32 }}>
          <window.SectionHead
            eyebrow={`§2 · system · ${systemSecrets.length}`}
            right={
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <window.Mono size={10} color={Cs_V.dim}>target</window.Mono>
                <window.PillBtn>shared ▾</window.PillBtn>
                <window.PillBtn variant="commit">+ add</window.PillBtn>
              </div>
            }
          />
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 200px 140px 100px', columnGap: 16, padding: '0 0 8px 0', borderBottom: `1px solid ${Cs_V.border}` }}>
            <window.Mono size={10} upper track="0.10em" color={Cs_V.dim}>key &amp; description</window.Mono>
            <window.Mono size={10} upper track="0.10em" color={Cs_V.dim}>fingerprint</window.Mono>
            <window.Mono size={10} upper track="0.10em" color={Cs_V.dim}>source &amp; users</window.Mono>
            <span />
          </div>
          {systemSecrets.map((s, i) => (
            <VaultSystemRow key={s.key} s={s} last={i === systemSecrets.length - 1} />
          ))}
        </div>

        {/* ── Section 3: CLI runtimes (compact strip) ─ */}
        <div>
          <window.SectionHead eyebrow={`§3 · cli runtimes · ${window.CLI_RUNTIMES.length}`} />
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)',
            border: `1px solid ${Cs_V.border}`,
          }}>
            {window.CLI_RUNTIMES.map((r, i) => (
              <div key={r.id} style={{
                borderLeft: i === 0 ? 'none' : `1px solid ${Cs_V.border}`,
                padding: '14px 16px',
                opacity: r.state === 'never_set' ? 0.65 : 1,
                position: 'relative',
              }}>
                <window.Sliver state={r.state} />
                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
                  <div>
                    <span style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500 }}>{r.label}</span>
                    <div><window.Mono size={10} color={Cs_V.dim}>{r.id}</window.Mono></div>
                  </div>
                  <window.StateLabel state={r.state} />
                </div>
                <div style={{ marginTop: 10 }}>
                  <window.Fingerprint value={r.fingerprint} size={11} />
                </div>
                <div style={{ marginTop: 6, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  {r.lastUsed
                    ? <window.Mono size={10} color={Cs_V.mfg}>used {r.lastUsed}</window.Mono>
                    : <span style={{ fontFamily: 'var(--font-serif)', fontStyle: 'italic', fontSize: 12, color: Cs_V.dim }}>not set.</span>}
                  {r.state === 'never_set'
                    ? <window.ActionArrow>set</window.ActionArrow>
                    : r.state === 'expiring'
                      ? <window.PillBtn>rotate</window.PillBtn>
                      : <window.ActionArrow>open</window.ActionArrow>}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

window.DirectionVault = DirectionVault;
