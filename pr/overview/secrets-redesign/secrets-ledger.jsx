// Direction A — The Ledger.
//
// The most Dispatch-canonical of the three. Editorial single column,
// rule-separated rows, per-family sections. Reads like a financial
// statement of credentials: every secret the house holds, top to
// bottom, with evidence in the row itself. The fingerprint is the
// proof; the value is reachable but unobtrusive.
//
// What this direction does well:
//   - dense scannable inventory; severity claims the eye, calm rows recede
//   - one row template handles every state
//   - extends trivially when providers are added
// What it gives up:
//   - no spatial mnemonic; rows look alike at a glance
//   - oauth integration setup has no signature visual moment

const Cs_L = window.C;

function LedgerUserRow({ s, last }) {
  const provider = window.PROVIDERS[s.provider];
  const meta = window.STATE_CATALOG[s.state] || {};
  const stateColor = meta.tone === 'red' ? Cs_L.red : meta.tone === 'amber' ? Cs_L.amber : meta.tone === 'ok' ? Cs_L.green : Cs_L.mfg;
  const isMissing = s.state === 'never_set';

  // Right-column action varies by state.
  let action;
  if (s.state === 'expired' || s.state === 'revoked') {
    action = <window.PillBtn variant="commit">re-authorize</window.PillBtn>;
  } else if (s.state === 'scope_mismatch') {
    action = <window.PillBtn variant="commit">re-grant scopes</window.PillBtn>;
  } else if (s.state === 'expiring') {
    action = <window.PillBtn>re-authorize</window.PillBtn>;
  } else if (s.state === 'never_set') {
    action = <window.ActionArrow>connect</window.ActionArrow>;
  } else {
    action = <window.ActionArrow>open</window.ActionArrow>;
  }

  return (
    <div style={{
      position: 'relative',
      display: 'grid',
      gridTemplateColumns: '24px 1fr 200px 140px 140px',
      columnGap: 18, alignItems: 'center',
      padding: '14px 14px 14px 18px',
      borderBottom: last ? 'none' : `1px solid ${Cs_L.borderSoft}`,
    }}>
      <window.Sliver state={s.state} />
      <window.ProviderMark provider={s.provider} size={22} />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: Cs_L.fg, letterSpacing: '-0.01em', whiteSpace: 'nowrap' }}>
            {provider.label}
          </span>
          <window.Mono size={10} color={Cs_L.dim}>· {provider.authority}</window.Mono>
        </div>
        {!isMissing && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
            <window.Fingerprint value={s.fingerprint} />
            <window.Mono size={10} color={Cs_L.dim}>· feeds {s.feeds.join(', ')}</window.Mono>
          </div>
        )}
        {isMissing && (
          <span style={{ fontFamily: 'var(--font-serif)', fontStyle: 'italic', fontSize: 13, color: Cs_L.dim }}>
            not connected · would feed {s.feeds.join(', ')}
          </span>
        )}
      </div>

      {/* Scopes column — compact grants/missing */}
      <div>
        {!isMissing ? (
          <window.ScopeRow granted={s.scopesGranted} required={s.scopesRequired} compact />
        ) : (
          <window.Mono size={10} color={Cs_L.dim}>{s.scopesRequired.length} scope{s.scopesRequired.length === 1 ? '' : 's'} required</window.Mono>
        )}
      </div>

      {/* State column — dot · label · short note */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <window.StateDot state={s.state} />
          <window.StateLabel state={s.state} />
        </div>
        {s.lastVerified && (
          <window.Mono size={10} color={Cs_L.mfg}>verified {s.lastVerified}</window.Mono>
        )}
        {s.state === 'expired' && s.failureTail && (
          <window.Mono size={10} color={Cs_L.red}>{s.failureTail}</window.Mono>
        )}
        {s.state === 'expiring' && (
          <window.Mono size={10} color={Cs_L.amber}>expires {s.expires}</window.Mono>
        )}
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>{action}</div>
    </div>
  );
}

function LedgerSystemRow({ s, last }) {
  const isMissing = s.rowState === 'missing';
  const isLocal = s.rowState === 'local';
  const isPlain = !!s.plainValue;

  let action;
  if (isMissing) action = <window.ActionArrow>set value</window.ActionArrow>;
  else if (isLocal) action = <window.ActionArrow>override</window.ActionArrow>;
  else action = <window.ActionArrow>open</window.ActionArrow>;

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '20px 1fr 200px 160px 120px',
      columnGap: 18, alignItems: 'center',
      padding: '12px 0',
      borderBottom: last ? 'none' : `1px solid ${Cs_L.borderSoft}`,
      opacity: isMissing ? 0.7 : 1,
    }}>
      <window.StateDot state={isMissing ? 'never_set' : 'ok'} />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <window.Mono size={12} weight={500} color={Cs_L.fg}>{s.key}</window.Mono>
        <span style={{ fontFamily: 'var(--font-serif)', fontSize: 13, color: Cs_L.mfg, lineHeight: 1.4 }}>
          {s.description}
        </span>
      </div>
      <div>
        {isPlain ? <window.Mono size={11} color={Cs_L.fg}>{s.plainValue}</window.Mono>
          : <window.Fingerprint value={s.fingerprint} />}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
        {isLocal && <window.Mono size={10} upper track="0.10em" color={Cs_L.fg}>local · {s.target}</window.Mono>}
        {!isLocal && !isMissing && <window.Mono size={10} upper track="0.10em" color={Cs_L.mfg}>shared default</window.Mono>}
        {isMissing && <window.Mono size={10} upper track="0.10em" color={Cs_L.dim}>not set</window.Mono>}
        {s.usedBy.length > 0 && (
          <window.Mono size={10} color={Cs_L.dim}>
            used by {s.usedBy[0] === '*' ? 'all' : s.usedBy.join(', ')}
          </window.Mono>
        )}
      </div>
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>{action}</div>
    </div>
  );
}

function DirectionLedger() {
  const userSecrets = window.USER_SECRETS.filter((s) => s.identity === 'tze');
  const systemSecrets = window.SYSTEM_SECRETS;
  const k = window.computeKpis();
  const attentionNeeded = k.integrations.needsHand;

  return (
    <div style={{
      background: Cs_L.bg, color: Cs_L.fg, fontFamily: 'var(--font-sans)',
      display: 'flex', minHeight: '100%',
    }}>
      <window.FakeRail />
      <div style={{ flex: 1, minWidth: 0, padding: '40px 48px 48px' }}>
        {/* ── Header ─────────────────────────────────── */}
        <window.PageHeader
          eyebrow="secrets · the ledger"
          eyebrowSub="Sat, 23 May 2026 · 14:23"
          headline={attentionNeeded > 0
            ? `Two credentials need attention.`
            : `Every credential, accounted for.`}
          voice={attentionNeeded > 0
            ? <>Spotify expired Wednesday; WhatsApp is short one scope. Everything else verified within the hour.</>
            : <>Nothing waiting.</>}
          headlineMaxWidth="20ch"
          right={
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 8 }}>
              <window.Mono size={10} upper track="0.14em" color={Cs_L.mfg}>identity</window.Mono>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontFamily: 'var(--font-sans)', fontSize: 16, fontWeight: 500, color: Cs_L.fg }}>Tze</span>
                <window.Mono size={10} color={Cs_L.dim}>(you)</window.Mono>
                <window.PillBtn>change →</window.PillBtn>
              </div>
            </div>
          }
        />

        {/* ── KPI strip ─────────────────────────────── */}
        <div style={{ marginBottom: 36 }}>
          <window.KpiStrip items={[
            { label: 'integrations', value: k.integrations.total, delta: `${k.integrations.healthy} healthy` },
            { label: 'needs hand',   value: k.integrations.needsHand, delta: 'expired · scope', deltaColor: k.integrations.needsHand ? Cs_L.amber : Cs_L.dim },
            { label: 'system',       value: `${k.system.configured}/${k.system.total}`, delta: `${k.system.missing} unset` },
            { label: 'cli',          value: k.cli.total, delta: `${k.cli.ok} ok` },
          ]} />
        </div>

        {/* ── Section 1: Integrations (user tab) ──── */}
        <div style={{ marginBottom: 40 }}>
          <window.SectionHead
            eyebrow={`§1 · integrations · ${userSecrets.length}`}
            right={<window.Mono size={10} color={Cs_L.dim}>identity-bound · entity_info on owner</window.Mono>}
          />
          <div style={{ display: 'grid', gridTemplateColumns: '24px 1fr 200px 140px 140px', columnGap: 18, padding: '0 14px 8px 18px', borderBottom: `1px solid ${Cs_L.border}` }}>
            <span />
            <window.Mono size={10} upper track="0.10em" color={Cs_L.dim}>provider</window.Mono>
            <window.Mono size={10} upper track="0.10em" color={Cs_L.dim}>scopes</window.Mono>
            <window.Mono size={10} upper track="0.10em" color={Cs_L.dim}>state</window.Mono>
            <span />
          </div>
          {userSecrets.map((s, i) => (
            <LedgerUserRow key={s.provider} s={s} last={i === userSecrets.length - 1} />
          ))}
        </div>

        {/* ── Section 2: System secrets ───────────── */}
        <div style={{ marginBottom: 40 }}>
          <window.SectionHead
            eyebrow={`§2 · system · ${systemSecrets.length}`}
            right={
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <window.Mono size={10} color={Cs_L.dim}>target</window.Mono>
                <window.PillBtn>shared ▾</window.PillBtn>
                <window.PillBtn variant="commit">+ add</window.PillBtn>
              </div>
            }
          />
          <div style={{ display: 'grid', gridTemplateColumns: '20px 1fr 200px 160px 120px', columnGap: 18, padding: '0 0 8px 0', borderBottom: `1px solid ${Cs_L.border}` }}>
            <span />
            <window.Mono size={10} upper track="0.10em" color={Cs_L.dim}>key &amp; description</window.Mono>
            <window.Mono size={10} upper track="0.10em" color={Cs_L.dim}>fingerprint</window.Mono>
            <window.Mono size={10} upper track="0.10em" color={Cs_L.dim}>source &amp; users</window.Mono>
            <span />
          </div>
          {systemSecrets.map((s, i) => (
            <LedgerSystemRow key={s.key} s={s} last={i === systemSecrets.length - 1} />
          ))}
        </div>

        {/* ── Section 3: CLI runtimes ─────────────── */}
        <div>
          <window.SectionHead
            eyebrow={`§3 · cli runtimes · ${window.CLI_RUNTIMES.length}`}
            right={<window.Mono size={10} color={Cs_L.dim}>command-line agent tokens</window.Mono>}
          />
          {window.CLI_RUNTIMES.map((r, i) => (
            <div key={r.id} style={{
              display: 'grid', gridTemplateColumns: '24px 1fr 200px 140px 140px',
              columnGap: 18, alignItems: 'center', padding: '12px 14px 12px 18px',
              borderBottom: i === window.CLI_RUNTIMES.length - 1 ? 'none' : `1px solid ${Cs_L.borderSoft}`,
              opacity: r.state === 'never_set' ? 0.7 : 1,
            }}>
              <window.StateDot state={r.state} />
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
                <span style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500 }}>{r.label}</span>
                <window.Mono size={10} color={Cs_L.dim}>{r.id}</window.Mono>
              </div>
              <window.Fingerprint value={r.fingerprint} />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                <window.StateLabel state={r.state} />
                {r.lastUsed && <window.Mono size={10} color={Cs_L.mfg}>used {r.lastUsed}</window.Mono>}
                {r.expires && r.state === 'expiring' && <window.Mono size={10} color={Cs_L.amber}>expires {r.expires}</window.Mono>}
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                {r.state === 'never_set' ? <window.ActionArrow>set token</window.ActionArrow>
                  : r.state === 'expiring' ? <window.PillBtn>rotate</window.PillBtn>
                  : <window.ActionArrow>open</window.ActionArrow>}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

window.DirectionLedger = DirectionLedger;
