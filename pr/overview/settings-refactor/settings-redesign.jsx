// /settings/ — redesign proposals.
//
// All three proposals live in the post-overhaul language:
//   - sans display headlines, 500 weight, tight tracking
//   - hairline rules instead of cards
//   - mono eyebrows + tabular numerals everywhere
//   - butler hue only on letter-marks
//   - state color only when state demands
//
// Each proposal answers the same brief — present everything the user
// can configure about the system — but treats settings as a different
// kind of object: a ledger, a console, or a spec sheet.

const Cs = window.C;

// ─── shared atoms ───────────────────────────────────────────────────────

function Eyebrow({ children, sub, weight = 'normal' }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'baseline', gap: 10,
      fontFamily: 'var(--font-mono)', fontSize: 9.5, color: Cs.mfg,
      textTransform: 'uppercase', letterSpacing: '0.14em',
      fontWeight: weight === 'strong' ? 500 : 400,
    }}>
      <span>{children}</span>
      {sub && <span style={{ color: Cs.dim, letterSpacing: '0.06em', textTransform: 'none' }}>{sub}</span>}
    </div>
  );
}

function Mono({ children, color, size = 10, upper = true, track = '0.10em' }) {
  return (
    <span style={{
      fontFamily: 'var(--font-mono)', fontSize: size, color: color || Cs.dim,
      textTransform: upper ? 'uppercase' : 'none', letterSpacing: track,
    }}>{children}</span>
  );
}

function Pill({ children, tone, dot = true }) {
  const colorMap = { ok: Cs.green, amber: Cs.amber, red: Cs.red };
  const c = colorMap[tone] || Cs.dim;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 6,
      fontFamily: 'var(--font-mono)', fontSize: 9.5, color: c,
      border: `1px solid ${Cs.border}`, padding: '2px 8px', borderRadius: 999,
      letterSpacing: '0.06em',
    }}>
      {dot && <span style={{ width: 5, height: 5, borderRadius: 999, background: c }} />}
      {children}
    </span>
  );
}

// An "editable value": underlined, with a tiny mono caret on hover.
// In real /settings/ this would open an inline editor; here it just shows
// what every settable value looks like in the language.
function EditValue({ children, mono = true, size = 13, color, w }) {
  return (
    <span style={{
      fontFamily: mono ? 'var(--font-mono)' : 'var(--font-sans)',
      fontSize: size, color: color || Cs.fg,
      borderBottom: `1px dashed ${Cs.borderStrong}`,
      paddingBottom: 1, cursor: 'text',
      display: 'inline-block', width: w,
    }} className="tnum">{children}</span>
  );
}

function Toggle({ on, label }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      <span style={{
        width: 28, height: 14, borderRadius: 999, position: 'relative',
        background: on ? Cs.fg : 'transparent', border: `1px solid ${on ? Cs.fg : Cs.border}`,
      }}>
        <span style={{
          position: 'absolute', top: 1, left: on ? 14 : 1,
          width: 10, height: 10, borderRadius: 999,
          background: on ? Cs.bg : Cs.mfg,
        }} />
      </span>
      {label && <Mono color={Cs.mfg}>{label}</Mono>}
    </span>
  );
}

// ─── shared data ────────────────────────────────────────────────────────

const MODELS = [
  { id: 'claude-haiku-4-5',  family: 'Claude · Haiku 4.5',
    role: 'default · low-latency', inMtok: 0.80, outMtok: 4.00,
    used_by: ['relationship', 'health', 'memory', 'education', 'household', 'calendar'],
    state: 'verified', last: '6m ago', spend7d: 412.20 },
  { id: 'claude-sonnet-4-5', family: 'Claude · Sonnet 4.5',
    role: 'reasoning · escalation', inMtok: 3.00, outMtok: 15.00,
    used_by: ['qa', 'chronicler'], state: 'verified', last: '6m ago', spend7d: 184.60 },
  { id: 'claude-opus-4-5',   family: 'Claude · Opus 4.5',
    role: 'standby', inMtok: 15.00, outMtok: 75.00,
    used_by: [], state: 'verified', last: '6m ago', spend7d: 0 },
  { id: 'gpt-5.1-mini',      family: 'OpenAI · GPT-5.1 Mini',
    role: 'fallback · benchmarks', inMtok: 0.25, outMtok: 1.00,
    used_by: [], state: 'verified', last: '2h ago', spend7d: 0.40 },
  { id: 'llama-3.3-70b',     family: 'Local · Llama 3.3 70B',
    role: 'air-gapped jobs', inMtok: 0, outMtok: 0,
    used_by: [], state: 'offline', last: '3d ago', spend7d: 0 },
];

const BUTLERS = [
  { name: 'relationship', model: 'haiku-4-5',  schedule: 'on demand',     enabled: true,  ceiling: 5.00, autosend: false },
  { name: 'health',       model: 'haiku-4-5',  schedule: 'continuous',    enabled: true,  ceiling: 2.50, autosend: true },
  { name: 'calendar',     model: 'haiku-4-5',  schedule: 'paused · auth', enabled: false, ceiling: 2.00, autosend: true },
  { name: 'qa',           model: 'sonnet-4-5', schedule: 'patrol 14m',    enabled: true,  ceiling: 8.00, autosend: false },
  { name: 'memory',       model: 'haiku-4-5',  schedule: 'nightly 02:00', enabled: true,  ceiling: 1.00, autosend: true },
  { name: 'education',    model: 'haiku-4-5',  schedule: 'twice daily',   enabled: true,  ceiling: 1.50, autosend: true },
  { name: 'chronicler',   model: 'sonnet-4-5', schedule: 'nightly 03:00', enabled: true,  ceiling: 3.00, autosend: true },
  { name: 'household',    model: 'haiku-4-5',  schedule: 'on demand',     enabled: true,  ceiling: 4.00, autosend: false },
];

// Big catalog — 50+ models grouped by provider. Used by the expanded view.
const BIG_MODELS = [
  // Anthropic
  { prov: 'Anthropic', id: 'claude-haiku-4-5',   family: 'Claude · Haiku 4.5',    role: 'default · low-latency', inMtok: 0.80, outMtok: 4.00, ctx: 200, used_by: ['relationship','health','memory','education','household','calendar'], state: 'verified', last: '6m',  spend7d: 412.20, failures7d: 0, tier: 'workhorse', priority: 100, enabled: true, usage24h: 1240, usage30d: 38400, primary: true },
  { prov: 'Anthropic', id: 'claude-sonnet-4-5',  family: 'Claude · Sonnet 4.5',   role: 'reasoning · escalation', inMtok: 3.00, outMtok: 15.00, ctx: 200, used_by: ['qa','chronicler'], state: 'verified', last: '6m', spend7d: 184.60, failures7d: 2, tier: 'reasoning', priority: 100, enabled: true, usage24h: 320, usage30d: 9600 },
  { prov: 'Anthropic', id: 'claude-opus-4-5',    family: 'Claude · Opus 4.5',     role: 'standby',                inMtok: 15.00, outMtok: 75.00, ctx: 200, used_by: [], state: 'verified', last: '6m', spend7d: 0, failures7d: 0, tier: 'reasoning', priority: 80, enabled: true, usage24h: 0, usage30d: 12 },
  { prov: 'Anthropic', id: 'claude-haiku-3-5',   family: 'Claude · Haiku 3.5',    role: 'legacy · deprecated',    inMtok: 0.25, outMtok: 1.25, ctx: 200, used_by: [], state: 'deprecated', last: '14d', spend7d: 0, failures7d: 0, tier: 'legacy', priority: 15, enabled: false, usage24h: 0, usage30d: 0 },
  { prov: 'Anthropic', id: 'claude-sonnet-3-7',  family: 'Claude · Sonnet 3.7',   role: 'legacy',                 inMtok: 3.00, outMtok: 15.00, ctx: 200, used_by: [], state: 'verified', last: '2h', spend7d: 0, failures7d: 0, tier: 'legacy', priority: 20, enabled: false, usage24h: 0, usage30d: 0 },
  { prov: 'Anthropic', id: 'claude-sonnet-4',    family: 'Claude · Sonnet 4',     role: 'legacy',                 inMtok: 3.00, outMtok: 15.00, ctx: 200, used_by: [], state: 'verified', last: '2h', spend7d: 0, failures7d: 0, tier: 'legacy', priority: 25, enabled: false, usage24h: 0, usage30d: 0 },
  { prov: 'Anthropic', id: 'claude-opus-4',      family: 'Claude · Opus 4',       role: 'legacy',                 inMtok: 15.00, outMtok: 75.00, ctx: 200, used_by: [], state: 'verified', last: '2h', spend7d: 0, failures7d: 0, tier: 'legacy', priority: 20, enabled: false, usage24h: 0, usage30d: 0 },
  // OpenAI
  { prov: 'OpenAI', id: 'gpt-5.1',              family: 'GPT-5.1',               role: 'benchmark · standby',    inMtok: 2.50, outMtok: 10.00, ctx: 400, used_by: [], state: 'verified', last: '2h', spend7d: 0, failures7d: 0, tier: 'reasoning', priority: 75, enabled: true, usage24h: 0, usage30d: 4 },
  { prov: 'OpenAI', id: 'gpt-5.1-mini',         family: 'GPT-5.1 Mini',          role: 'fallback · benchmarks',  inMtok: 0.25, outMtok: 1.00, ctx: 400, used_by: [], state: 'verified', last: '2h', spend7d: 0.40, failures7d: 0, tier: 'cheap', priority: 75, enabled: true, usage24h: 2, usage30d: 18 },
  { prov: 'OpenAI', id: 'gpt-5',                family: 'GPT-5',                 role: 'legacy',                 inMtok: 2.50, outMtok: 10.00, ctx: 200, used_by: [], state: 'verified', last: '6h', spend7d: 0, failures7d: 0, tier: 'legacy', priority: 15, enabled: false, usage24h: 0, usage30d: 0 },
  { prov: 'OpenAI', id: 'gpt-4o',               family: 'GPT-4o',                role: 'legacy',                 inMtok: 2.50, outMtok: 10.00, ctx: 128, used_by: [], state: 'verified', last: '6h', spend7d: 0, failures7d: 0, tier: 'legacy', priority: 15, enabled: false, usage24h: 0, usage30d: 0 },
  { prov: 'OpenAI', id: 'gpt-4o-mini',          family: 'GPT-4o Mini',           role: 'fallback · cheap',       inMtok: 0.15, outMtok: 0.60, ctx: 128, used_by: [], state: 'verified', last: '6h', spend7d: 0, failures7d: 0, tier: 'cheap', priority: 50, enabled: true, usage24h: 0, usage30d: 0 },
  { prov: 'OpenAI', id: 'o4-mini',              family: 'o4-mini',               role: 'reasoning · cheap',      inMtok: 1.10, outMtok: 4.40, ctx: 200, used_by: [], state: 'verified', last: '6h', spend7d: 0, failures7d: 0, tier: 'reasoning', priority: 65, enabled: true, usage24h: 0, usage30d: 6 },
  { prov: 'OpenAI', id: 'o3',                   family: 'o3',                    role: 'reasoning · standby',    inMtok: 2.00, outMtok: 8.00, ctx: 200, used_by: [], state: 'verified', last: '6h', spend7d: 0, failures7d: 1, tier: 'reasoning', priority: 70, enabled: true, usage24h: 0, usage30d: 8 },
  { prov: 'OpenAI', id: 'o1',                   family: 'o1',                    role: 'legacy reasoning',       inMtok: 15.00, outMtok: 60.00, ctx: 200, used_by: [], state: 'deprecated', last: '12d', spend7d: 0, failures7d: 0, tier: 'legacy', priority: 10, enabled: false, usage24h: 0, usage30d: 0 },
  { prov: 'OpenAI', id: 'whisper-1',            family: 'Whisper · v1',          role: 'transcription',          inMtok: 0, outMtok: 0, ctx: 0, used_by: ['chronicler'], state: 'verified', last: '12m', spend7d: 2.10, failures7d: 0, tier: 'specialty', priority: 85, enabled: true, usage24h: 18, usage30d: 520 },
  { prov: 'OpenAI', id: 'text-embedding-3-l',   family: 'text-embedding-3-large',role: 'embedding · 3072d',      inMtok: 0.13, outMtok: 0, ctx: 8, used_by: ['memory'], state: 'verified', last: '4m', spend7d: 6.40, failures7d: 0, tier: 'specialty', priority: 95, enabled: true, usage24h: 84, usage30d: 2460 },
  // Google
  { prov: 'Google', id: 'gemini-2.5-pro',       family: 'Gemini · 2.5 Pro',      role: 'long-context · standby', inMtok: 1.25, outMtok: 10.00, ctx: 2000, used_by: [], state: 'verified', last: '1h', spend7d: 0, failures7d: 0, tier: 'reasoning', priority: 70, enabled: true, usage24h: 0, usage30d: 2 },
  { prov: 'Google', id: 'gemini-2.5-flash',     family: 'Gemini · 2.5 Flash',    role: 'long-context · fast',    inMtok: 0.15, outMtok: 0.60, ctx: 1000, used_by: [], state: 'verified', last: '1h', spend7d: 0, failures7d: 0, tier: 'workhorse', priority: 80, enabled: true, usage24h: 0, usage30d: 0 },
  { prov: 'Google', id: 'gemini-2.0-flash',     family: 'Gemini · 2.0 Flash',    role: 'fallback',               inMtok: 0.10, outMtok: 0.40, ctx: 1000, used_by: [], state: 'verified', last: '4h', spend7d: 0, failures7d: 0, tier: 'cheap', priority: 45, enabled: true, usage24h: 0, usage30d: 0 },
  { prov: 'Google', id: 'gemini-1.5-pro',       family: 'Gemini · 1.5 Pro',      role: 'legacy',                 inMtok: 1.25, outMtok: 5.00, ctx: 2000, used_by: [], state: 'deprecated', last: '21d', spend7d: 0, failures7d: 0, tier: 'legacy', priority: 10, enabled: false, usage24h: 0, usage30d: 0 },
  // xAI
  { prov: 'xAI', id: 'grok-4',                  family: 'Grok · 4',              role: 'benchmark',              inMtok: 5.00, outMtok: 15.00, ctx: 256, used_by: [], state: 'verified', last: '3h', spend7d: 0, failures7d: 0, tier: 'reasoning', priority: 60, enabled: true, usage24h: 0, usage30d: 0 },
  { prov: 'xAI', id: 'grok-3',                  family: 'Grok · 3',              role: 'legacy',                 inMtok: 3.00, outMtok: 15.00, ctx: 128, used_by: [], state: 'verified', last: '3h', spend7d: 0, failures7d: 0, tier: 'legacy', priority: 15, enabled: false, usage24h: 0, usage30d: 0 },
  // Mistral
  { prov: 'Mistral', id: 'mistral-large-2',     family: 'Mistral · Large 2',     role: 'reasoning · standby',    inMtok: 2.00, outMtok: 6.00, ctx: 128, used_by: [], state: 'verified', last: '6h', spend7d: 0, failures7d: 0, tier: 'reasoning', priority: 55, enabled: true, usage24h: 0, usage30d: 0 },
  { prov: 'Mistral', id: 'mistral-medium',      family: 'Mistral · Medium',      role: 'fallback',               inMtok: 0.40, outMtok: 2.00, ctx: 32, used_by: [], state: 'verified', last: '6h', spend7d: 0, failures7d: 0, tier: 'cheap', priority: 40, enabled: true, usage24h: 0, usage30d: 0 },
  { prov: 'Mistral', id: 'mistral-small',       family: 'Mistral · Small',       role: 'cheap',                  inMtok: 0.20, outMtok: 0.60, ctx: 32, used_by: [], state: 'verified', last: '6h', spend7d: 0, failures7d: 0, tier: 'cheap', priority: 35, enabled: true, usage24h: 0, usage30d: 0 },
  { prov: 'Mistral', id: 'mixtral-8x22b',       family: 'Mixtral · 8x22B',       role: 'legacy',                 inMtok: 1.20, outMtok: 1.20, ctx: 64, used_by: [], state: 'verified', last: '12h', spend7d: 0, failures7d: 0, tier: 'legacy', priority: 15, enabled: false, usage24h: 0, usage30d: 0 },
  { prov: 'Mistral', id: 'codestral-25',        family: 'Codestral · 25',        role: 'code · standby',         inMtok: 0.30, outMtok: 0.90, ctx: 256, used_by: [], state: 'verified', last: '8h', spend7d: 0, failures7d: 0, tier: 'specialty', priority: 50, enabled: true, usage24h: 0, usage30d: 0 },
  // DeepSeek
  { prov: 'DeepSeek', id: 'deepseek-v3',        family: 'DeepSeek · V3',         role: 'reasoning · benchmark',  inMtok: 0.27, outMtok: 1.10, ctx: 64, used_by: [], state: 'verified', last: '7h', spend7d: 0, failures7d: 0, tier: 'cheap', priority: 50, enabled: true, usage24h: 0, usage30d: 0 },
  { prov: 'DeepSeek', id: 'deepseek-r1',        family: 'DeepSeek · R1',         role: 'reasoning · cheap',      inMtok: 0.55, outMtok: 2.19, ctx: 64, used_by: [], state: 'verified', last: '7h', spend7d: 0, failures7d: 0, tier: 'cheap', priority: 45, enabled: true, usage24h: 0, usage30d: 0 },
  // Qwen
  { prov: 'Qwen', id: 'qwen-2.5-72b',           family: 'Qwen · 2.5 · 72B',      role: 'fallback',               inMtok: 0.40, outMtok: 1.20, ctx: 128, used_by: [], state: 'verified', last: '8h', spend7d: 0, failures7d: 0, tier: 'cheap', priority: 40, enabled: true, usage24h: 0, usage30d: 0 },
  { prov: 'Qwen', id: 'qwq-32b',                family: 'QwQ · 32B',             role: 'reasoning · cheap',      inMtok: 0.20, outMtok: 0.60, ctx: 32, used_by: [], state: 'verified', last: '8h', spend7d: 0, failures7d: 0, tier: 'cheap', priority: 35, enabled: true, usage24h: 0, usage30d: 0 },
  // Cohere
  { prov: 'Cohere', id: 'command-r-plus',       family: 'Command · R+',          role: 'rag · standby',          inMtok: 2.50, outMtok: 10.00, ctx: 128, used_by: [], state: 'verified', last: '9h', spend7d: 0, failures7d: 0, tier: 'cheap', priority: 30, enabled: true, usage24h: 0, usage30d: 0 },
  { prov: 'Cohere', id: 'command-r',            family: 'Command · R',           role: 'rag · cheap',            inMtok: 0.15, outMtok: 0.60, ctx: 128, used_by: [], state: 'verified', last: '9h', spend7d: 0, failures7d: 0, tier: 'cheap', priority: 25, enabled: true, usage24h: 0, usage30d: 0 },
  { prov: 'Cohere', id: 'embed-v3-en',          family: 'embed · v3 · english',  role: 'embedding · 1024d',      inMtok: 0.10, outMtok: 0, ctx: 8, used_by: [], state: 'verified', last: '9h', spend7d: 0, failures7d: 0, tier: 'specialty', priority: 40, enabled: true, usage24h: 0, usage30d: 0 },
  // Voyage
  { prov: 'Voyage', id: 'voyage-3',             family: 'Voyage · 3',            role: 'embedding · backup',     inMtok: 0.06, outMtok: 0, ctx: 32, used_by: [], state: 'verified', last: '11h', spend7d: 0, failures7d: 0, tier: 'specialty', priority: 35, enabled: true, usage24h: 0, usage30d: 0 },
  // Local
  { prov: 'Local', id: 'llama-3.3-70b',         family: 'Llama · 3.3 · 70B',     role: 'air-gapped · ollama',    inMtok: 0, outMtok: 0, ctx: 128, used_by: [], state: 'offline', last: '3d', spend7d: 0, failures7d: 4, tier: 'local', priority: 20, enabled: false, usage24h: 0, usage30d: 0 },
  { prov: 'Local', id: 'llama-3.1-405b',        family: 'Llama · 3.1 · 405B',    role: 'air-gapped · big',       inMtok: 0, outMtok: 0, ctx: 128, used_by: [], state: 'offline', last: '5d', spend7d: 0, failures7d: 0, tier: 'local', priority: 15, enabled: false, usage24h: 0, usage30d: 0 },
  { prov: 'Local', id: 'llama-3.2-90b-vision', family: 'Llama · 3.2 · 90B Vision', role: 'air-gapped · vision', inMtok: 0, outMtok: 0, ctx: 128, used_by: [], state: 'offline', last: '5d', spend7d: 0, failures7d: 0, tier: 'local', priority: 15, enabled: false, usage24h: 0, usage30d: 0 },
  { prov: 'Local', id: 'qwen-2.5-coder-32b',    family: 'Qwen · 2.5 Coder · 32B', role: 'air-gapped · code',     inMtok: 0, outMtok: 0, ctx: 32, used_by: [], state: 'verified', last: '1h', spend7d: 0, failures7d: 0, tier: 'local', priority: 40, enabled: true, usage24h: 0, usage30d: 0 },
  { prov: 'Local', id: 'phi-4',                 family: 'Phi · 4',               role: 'air-gapped · cheap',     inMtok: 0, outMtok: 0, ctx: 16, used_by: [], state: 'verified', last: '1h', spend7d: 0, failures7d: 0, tier: 'local', priority: 30, enabled: true, usage24h: 0, usage30d: 0 },
  { prov: 'Local', id: 'mistral-nemo-12b',      family: 'Mistral · Nemo · 12B',  role: 'air-gapped · tiny',      inMtok: 0, outMtok: 0, ctx: 128, used_by: [], state: 'verified', last: '1h', spend7d: 0, failures7d: 0, tier: 'local', priority: 25, enabled: true, usage24h: 0, usage30d: 0 },
  { prov: 'Local', id: 'mxbai-embed-large',     family: 'mxbai · embed · large', role: 'embedding · local',      inMtok: 0, outMtok: 0, ctx: 0.5, used_by: ['memory'], state: 'verified', last: '4m', spend7d: 0, failures7d: 0, tier: 'local', priority: 50, enabled: true, usage24h: 12, usage30d: 360 },
  // House fine-tunes
  { prov: 'House', id: 'butlerhouse-default',   family: 'butlerhouse · default', role: 'tuned · brief style',    inMtok: 0.40, outMtok: 2.00, ctx: 64, used_by: [], state: 'untested', last: '—', spend7d: 0, failures7d: 0, tier: 'specialty', priority: 30, enabled: false, usage24h: 0, usage30d: 0 },
  { prov: 'House', id: 'butlerhouse-qa',        family: 'butlerhouse · qa',      role: 'tuned · qa reasoning',   inMtok: 1.20, outMtok: 4.80, ctx: 128, used_by: [], state: 'untested', last: '—', spend7d: 0, failures7d: 0, tier: 'specialty', priority: 35, enabled: false, usage24h: 0, usage30d: 0 },
  { prov: 'House', id: 'butlerhouse-chronicler',family: 'butlerhouse · chronicler', role: 'tuned · narrative',  inMtok: 0.80, outMtok: 3.20, ctx: 64, used_by: [], state: 'untested', last: '—', spend7d: 0, failures7d: 0, tier: 'specialty', priority: 30, enabled: false, usage24h: 0, usage30d: 0 },
  { prov: 'House', id: 'butlerhouse-warm',      family: 'butlerhouse · warm',    role: 'tuned · briefing voice', inMtok: 0.40, outMtok: 2.00, ctx: 32, used_by: [], state: 'error', last: '2d', spend7d: 0, failures7d: 12, tier: 'specialty', priority: 10, enabled: false, usage24h: 0, usage30d: 0 },
  // Speech / vision
  { prov: 'ElevenLabs', id: 'eleven-v3',        family: 'ElevenLabs · v3',       role: 'tts · standby',          inMtok: 0, outMtok: 0, ctx: 0, used_by: [], state: 'verified', last: '12h', spend7d: 0, failures7d: 0, tier: 'specialty', priority: 25, enabled: true, usage24h: 0, usage30d: 0 },
  { prov: 'AssemblyAI', id: 'assembly-best',    family: 'AssemblyAI · Best',     role: 'transcription · backup', inMtok: 0, outMtok: 0, ctx: 0, used_by: [], state: 'verified', last: '12h', spend7d: 0, failures7d: 0, tier: 'specialty', priority: 30, enabled: true, usage24h: 0, usage30d: 0 },
  { prov: 'Together', id: 'together-llama',     family: 'Together · Llama 3.1 70B', role: 'fallback · hosted',  inMtok: 0.88, outMtok: 0.88, ctx: 128, used_by: [], state: 'rate-limited', last: '22m', spend7d: 0, failures7d: 6, tier: 'cheap', priority: 20, enabled: true, usage24h: 0, usage30d: 14 },
  { prov: 'Together', id: 'together-mixtral',   family: 'Together · Mixtral 8x22B', role: 'fallback · hosted',  inMtok: 1.20, outMtok: 1.20, ctx: 64, used_by: [], state: 'verified', last: '6h', spend7d: 0, failures7d: 0, tier: 'cheap', priority: 30, enabled: true, usage24h: 0, usage30d: 0 },
];

const STATE_COLOR = (s) => ({
  verified: Cs.green, offline: Cs.dim, error: Cs.red, deprecated: Cs.dim,
  untested: Cs.amber, 'rate-limited': Cs.amber,
}[s] || Cs.dim);

const PERMS = [
  { id: 'memory.read',    label: 'Memory · read' },
  { id: 'memory.write',   label: 'Memory · write' },
  { id: 'sessions.spawn', label: 'Sessions · spawn' },
  { id: 'butlers.logs',   label: 'Butlers · logs' },
  { id: 'metrics.read',   label: 'Metrics · read' },
  { id: 'audit.write',    label: 'Audit · write' },
  { id: 'kill.switch',    label: 'Kill switch' },
];
const hasPerm = (b, p) => {
  if (p === 'butlers.logs' || p === 'metrics.read' || p === 'kill.switch') return b === 'qa';
  if (p === 'audit.write') return ['qa', 'memory'].includes(b);
  if (p === 'sessions.spawn') return ['qa', 'chronicler'].includes(b);
  if (p === 'memory.read') return true;
  if (p === 'memory.write') return b !== 'qa' && b !== 'chronicler';
  return false;
};

// ─── frame chrome ───────────────────────────────────────────────────────

function FakeRail({ active = 's' }) {
  const items = ['o', 'a', 'i', 'r', 'h', 'c', 'q', 'm', 'e', 'd', 'h', '·', '·', 's', 'g'];
  return (
    <div style={{
      width: 56, background: Cs.bgDeep, borderRight: `1px solid ${Cs.border}`,
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      padding: '12px 0', gap: 8, flexShrink: 0,
    }}>
      <div style={{
        width: 22, height: 22, border: `1px solid ${Cs.border}`, borderRadius: 4,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 10, color: Cs.fg, fontWeight: 600,
      }}>B</div>
      <div style={{ height: 8 }} />
      {items.map((c, i) => {
        const sel = c === active;
        return (
          <div key={i} style={{
            width: 22, height: 22, borderRadius: 4,
            background: sel ? 'oklch(1 0 0 / 0.06)' : 'transparent',
            border: sel ? `1px solid ${Cs.border}` : '1px solid transparent',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontFamily: 'var(--font-mono)', fontSize: 10,
            color: c === '·' ? Cs.dim : sel ? Cs.fg : Cs.mfg,
            textTransform: 'uppercase', position: 'relative',
          }}>{c}{sel && <span style={{
            position: 'absolute', left: -8, top: 4, bottom: 4, width: 2, background: Cs.fg,
          }} />}</div>
        );
      })}
    </div>
  );
}

function FakeBreadcrumb({ right }) {
  return (
    <div style={{
      padding: '14px 32px', borderBottom: `1px solid ${Cs.border}`,
      display: 'flex', alignItems: 'baseline', gap: 12,
      fontFamily: 'var(--font-mono)', fontSize: 10, color: Cs.dim,
      textTransform: 'uppercase', letterSpacing: '0.14em',
    }}>
      <span>butlers</span>
      <span>›</span>
      <span style={{ color: Cs.fg }}>settings</span>
      <span style={{ marginLeft: 'auto', color: Cs.mfg, letterSpacing: '0.06em', textTransform: 'none' }}>{right}</span>
    </div>
  );
}

// ─── KPI strip ──────────────────────────────────────────────────────────

function KpiStrip({ cells }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: `repeat(${cells.length}, 1fr)`,
      borderBottom: `1px solid ${Cs.border}`,
    }}>
      {cells.map((k, i) => (
        <div key={i} style={{
          padding: '18px 24px', borderRight: i < cells.length - 1 ? `1px solid ${Cs.border}` : 'none',
        }}>
          <Mono color={Cs.mfg} size={9} track="0.14em">{k.label}</Mono>
          <div className="tnum" style={{
            marginTop: 8,
            fontFamily: 'var(--font-mono)', fontSize: 28, fontWeight: 500,
            color: Cs.fg, letterSpacing: '-0.025em',
          }}>{k.value}{k.unit && <span style={{ fontSize: 13, color: Cs.dim, marginLeft: 4, fontWeight: 400 }}>{k.unit}</span>}</div>
          <div style={{ marginTop: 4 }}>
            <Mono color={k.tone || Cs.dim} size={9.5}>{k.sub}</Mono>
          </div>
        </div>
      ))}
    </div>
  );
}

// ─── Model catalog row (used by Ledger + Manifest) ──────────────────────

function ModelRow({ m, last, dense = false }) {
  const default_ = m.role.startsWith('default');
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '20px 1.4fr 1fr 110px 1.6fr 90px 60px',
      gap: 16, padding: dense ? '10px 0' : '14px 0',
      alignItems: 'center',
      borderBottom: last ? 'none' : `1px solid ${Cs.borderSoft}`,
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: 999,
        background: m.state === 'verified' ? Cs.green : m.state === 'offline' ? Cs.dim : Cs.amber,
        opacity: m.state === 'offline' ? 0.4 : 0.85, justifySelf: 'center',
      }} />
      <div style={{ display: 'flex', flexDirection: 'column' }}>
        <span style={{ fontSize: 13.5, color: Cs.fg, fontWeight: default_ ? 500 : 400 }}>
          {m.family}{default_ && <span style={{ marginLeft: 10, fontFamily: 'var(--font-mono)', fontSize: 9, color: Cs.mfg, letterSpacing: '0.10em', textTransform: 'uppercase' }}>· default</span>}
        </span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: Cs.dim, marginTop: 2 }}>{m.id}</span>
      </div>
      <span style={{ fontSize: 12, color: Cs.mfg }}>{m.role}</span>
      <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: m.state === 'offline' ? Cs.dim : Cs.fg }}>
        {m.state === 'offline' ? '—' : `${m.inMtok.toFixed(2)} → ${m.outMtok.toFixed(2)}`}
      </span>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
        {m.used_by.length === 0 && <Mono color={Cs.dim}>—</Mono>}
        {m.used_by.map((u) => (
          <span key={u} style={{
            display: 'inline-flex', alignItems: 'center', gap: 5,
            fontSize: 10, fontFamily: 'var(--font-mono)', color: Cs.mfg,
            border: `1px solid ${Cs.border}`, padding: '1px 6px', borderRadius: 2,
          }}>{u}</span>
        ))}
      </div>
      <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: m.spend7d > 0 ? Cs.fg : Cs.dim }}>
        {m.spend7d > 0 ? `$${m.spend7d.toFixed(2)}` : '—'}
      </span>
      <Mono color={m.state === 'offline' ? Cs.dim : Cs.mfg} size={9.5}>{m.last}</Mono>
    </div>
  );
}

function ModelCatalog({ dense = false }) {
  return (
    <div>
      <div style={{
        display: 'grid',
        gridTemplateColumns: '20px 1.4fr 1fr 110px 1.6fr 90px 60px',
        gap: 16, padding: '6px 0',
        borderBottom: `1px solid ${Cs.border}`,
      }}>
        <Mono>·</Mono><Mono>model</Mono><Mono>role</Mono>
        <Mono>$/mtok in → out</Mono><Mono>used by</Mono>
        <Mono>spend · 7d</Mono><Mono>verified</Mono>
      </div>
      {MODELS.map((m, i) => <ModelRow key={m.id} m={m} last={i === MODELS.length - 1} dense={dense} />)}
    </div>
  );
}

// ─── Butler config row ──────────────────────────────────────────────────

function ButlerConfigRow({ b, last, showAutosend = true }) {
  const paused = b.schedule.includes('paused');
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: showAutosend
        ? '20px 130px 130px 1fr 80px 100px 60px'
        : '20px 130px 130px 1fr 80px 60px',
      gap: 16, padding: '12px 0', alignItems: 'center',
      borderBottom: last ? 'none' : `1px solid ${Cs.borderSoft}`,
    }}>
      <window.ButlerMark name={b.name} size={16} tone="neutral" />
      <span style={{ fontSize: 13, color: Cs.fg, textTransform: 'capitalize' }}>{b.name}</span>
      <EditValue mono>{b.model}</EditValue>
      <EditValue mono color={paused ? Cs.amber : Cs.mfg} size={12}>{b.schedule}</EditValue>
      <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: Cs.fg }}>
        <EditValue mono>${b.ceiling.toFixed(2)}</EditValue><span style={{ color: Cs.dim }}> / day</span>
      </span>
      {showAutosend && <Toggle on={b.autosend} label={b.autosend ? 'auto' : 'ask'} />}
      <span style={{ display: 'flex', justifyContent: 'flex-end' }}><Toggle on={b.enabled} /></span>
    </div>
  );
}

function ButlerConfigTable({ showAutosend = true }) {
  return (
    <div>
      <div style={{
        display: 'grid',
        gridTemplateColumns: showAutosend
          ? '20px 130px 130px 1fr 80px 100px 60px'
          : '20px 130px 130px 1fr 80px 60px',
        gap: 16, padding: '6px 0',
        borderBottom: `1px solid ${Cs.border}`,
      }}>
        <Mono>·</Mono><Mono>butler</Mono><Mono>model</Mono>
        <Mono>schedule</Mono><Mono>ceiling</Mono>
        {showAutosend && <Mono>approvals</Mono>}
        <Mono>on</Mono>
      </div>
      {BUTLERS.map((b, i) => (
        <ButlerConfigRow key={b.name} b={b} last={i === BUTLERS.length - 1} showAutosend={showAutosend} />
      ))}
    </div>
  );
}

// ─── Permissions matrix (used in Manifest) ──────────────────────────────

function PermissionsMatrix() {
  const butlers = BUTLERS.map((b) => b.name);
  return (
    <div style={{ border: `1px solid ${Cs.border}` }}>
      <div style={{
        display: 'grid', gridTemplateColumns: `260px repeat(${butlers.length}, 1fr)`,
        background: Cs.bgDeep, borderBottom: `1px solid ${Cs.border}`,
      }}>
        <div style={{ padding: '12px 16px' }}><Mono color={Cs.mfg}>permission</Mono></div>
        {butlers.map((b) => (
          <div key={b} style={{
            padding: '10px 8px', display: 'flex', flexDirection: 'column',
            alignItems: 'center', gap: 5, borderLeft: `1px solid ${Cs.borderSoft}`,
          }}>
            <window.ButlerMark name={b} size={18} tone="neutral" />
            <Mono color={Cs.mfg} size={9} track="0.06em" upper={false}>{b.slice(0, 4)}</Mono>
          </div>
        ))}
      </div>
      {PERMS.map((p, i) => (
        <div key={p.id} style={{
          display: 'grid', gridTemplateColumns: `260px repeat(${butlers.length}, 1fr)`,
          borderBottom: i < PERMS.length - 1 ? `1px solid ${Cs.borderSoft}` : 'none',
        }}>
          <div style={{ padding: '14px 16px', display: 'grid', gap: 2 }}>
            <span style={{ fontSize: 13, color: Cs.fg }}>{p.label}</span>
            <Mono color={Cs.dim} size={9.5}>{p.id}</Mono>
          </div>
          {butlers.map((b) => (
            <div key={b} style={{
              padding: '14px 8px', display: 'flex', alignItems: 'center', justifyContent: 'center',
              borderLeft: `1px solid ${Cs.borderSoft}`, cursor: 'pointer',
            }}>
              {hasPerm(b, p.id) ? (
                <span style={{ width: 9, height: 9, borderRadius: 2, background: Cs.fg, opacity: 0.85 }} />
              ) : (
                <span style={{ width: 9, height: 9, borderRadius: 2, border: `1px solid ${Cs.borderSoft}` }} />
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────
// A · The Ledger — editorial, single column.
//
// Settings as a system report. Same shape as the Overview: display
// headline, KPI strip, hairline-ruled sections that read as one
// continuous document. Voice line up top, then numerals, then the
// catalog (featured), then per-butler config, then everything else.
// ───────────────────────────────────────────────────────────────────────

function SettingsLedger() {
  return (
    <div style={{ height: '100%', background: Cs.bg, color: Cs.fg, display: 'flex', fontFamily: 'var(--font-sans)' }}>
      <FakeRail />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <FakeBreadcrumb right="system · 9 sections · last change 2h ago" />

        <div style={{ overflow: 'auto', padding: '40px 56px 56px' }}>
          {/* Headline */}
          <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 56, marginBottom: 36 }}>
            <div>
              <Eyebrow sub="Wed, 14 May 2026 · 16:42">settings · the ledger</Eyebrow>
              <h1 style={{
                margin: '14px 0 12px',
                fontSize: 44, fontWeight: 500, letterSpacing: '-0.025em',
                lineHeight: 1.06, maxWidth: '14ch',
              }}>Everything the staff has been told.</h1>
              <p style={{
                margin: 0, fontFamily: 'var(--font-serif)', fontSize: 16,
                color: Cs.fg, lineHeight: 1.6, maxWidth: '50ch',
              }}>
                Two providers configured, eight butlers staffed, one paused on a reauth.
                Spend month-to-date is comfortably under ceiling. The household tongue is English (UK);
                quiet hours run from twenty-two hundred to seven.
              </p>
            </div>
            <div style={{ alignSelf: 'end' }}>
              <Eyebrow>per-user OAuth lives separately</Eyebrow>
              <div style={{ marginTop: 8, fontSize: 13, color: Cs.mfg, lineHeight: 1.5 }}>
                Personal integrations — Google, Spotify, Telegram, Steam — sit on
                <span style={{ color: Cs.fg, fontFamily: 'var(--font-mono)', fontSize: 11 }}> /secrets/user</span>.
                This page is system-side only.
              </div>
              <div style={{ marginTop: 16, display: 'flex', gap: 8 }}>
                <Pill tone="ok">5 models verified · 6m ago</Pill>
                <Pill tone="amber">calendar · reauth</Pill>
              </div>
            </div>
          </div>

          {/* KPI strip */}
          <KpiStrip cells={[
            { label: 'spend · mtd',      value: '612.40', unit: '$', sub: 'ceiling $1,200 · 51%', tone: Cs.green },
            { label: 'spend · today',    value: '2.40',   unit: '$', sub: 'avg $20.41 · −88%',    tone: Cs.green },
            { label: 'butlers · staffed',value: '7',      unit: '/8', sub: 'calendar paused · 4h', tone: Cs.amber },
            { label: 'memory · objects', value: '14,231', sub: 'cap 28,500 · 50%' },
          ]} />

          {/* §1 Model catalog — featured */}
          <section style={{ marginTop: 40 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 18 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 14 }}>
                <Mono color={Cs.dim} size={11}>§1</Mono>
                <h2 style={{ margin: 0, fontSize: 24, fontWeight: 500, letterSpacing: '-0.015em' }}>Model catalog</h2>
                <Mono color={Cs.mfg} size={11} upper={false} track="0.04em">5 verified · 2 active · default <span style={{ color: Cs.fg }}>haiku-4-5</span></Mono>
              </div>
              <span style={{
                fontFamily: 'var(--font-mono)', fontSize: 10, padding: '4px 10px',
                border: `1px solid ${Cs.borderStrong}`, borderRadius: 2,
                color: Cs.fg, letterSpacing: '0.10em', textTransform: 'uppercase', cursor: 'pointer',
              }}>+ add provider</span>
            </div>
            <ModelCatalog />
          </section>

          {/* §2 Butlers — per-butler config */}
          <section style={{ marginTop: 48 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, marginBottom: 14 }}>
              <Mono color={Cs.dim} size={11}>§2</Mono>
              <h2 style={{ margin: 0, fontSize: 22, fontWeight: 500, letterSpacing: '-0.015em' }}>Per-butler configuration</h2>
              <Mono color={Cs.mfg} size={11} upper={false} track="0.04em">model · schedule · spend ceiling · approvals</Mono>
            </div>
            <ButlerConfigTable />
          </section>

          {/* §3 Memory · §4 Approvals — twin columns */}
          <section style={{ marginTop: 48, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 56 }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, marginBottom: 14 }}>
                <Mono color={Cs.dim} size={11}>§3</Mono>
                <h2 style={{ margin: 0, fontSize: 20, fontWeight: 500, letterSpacing: '-0.015em' }}>Memory tiers</h2>
              </div>
              <ConfigLine label="Short-term capacity"  helper="Drops oldest after cap." value={<EditValue>500</EditValue>} />
              <ConfigLine label="Mid-term capacity"    helper="Promoted in morning consolidation." value={<EditValue>8,000</EditValue>} />
              <ConfigLine label="Long-term capacity"   helper="Promoted nightly only." value={<EditValue>20,000</EditValue>} />
              <ConfigLine label="Drop policy"          helper="When a tier is full." value={<EditValue mono={false}>oldest · low-recall</EditValue>} />
              <ConfigLine label="Compaction window"    helper="Idle window for nightly compaction." value={<EditValue>02:00 → 04:00</EditValue>} last />
            </div>
            <div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, marginBottom: 14 }}>
                <Mono color={Cs.dim} size={11}>§4</Mono>
                <h2 style={{ margin: 0, fontSize: 20, fontWeight: 500, letterSpacing: '-0.015em' }}>Approvals &amp; quiet hours</h2>
              </div>
              <ConfigLine label="System spend ceiling"  helper="Pauses all butlers if exceeded." value={<EditValue>$1,200 / mo</EditValue>} />
              <ConfigLine label="Default request expiry" helper="Auto-decline if not approved." value={<EditValue>24h</EditValue>} />
              <ConfigLine label="Quiet hours"            helper="No notifications, queued only." value={<EditValue>22:00 → 07:00</EditValue>} />
              <ConfigLine label="Re-auth grace"          helper="Hold a butler before pausing on token expiry." value={<EditValue>15m</EditValue>} />
              <ConfigLine label="Briefing source"        helper="Where the morning briefing comes from." value={<span style={{ display: 'inline-flex', gap: 6 }}><Pill tone="ok">llm · cached 5m</Pill></span>} mono={false} last />
            </div>
          </section>

          {/* §5 Household · §6 Appearance · §7 Data — quick rows */}
          <section style={{ marginTop: 48, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 56 }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, marginBottom: 14 }}>
                <Mono color={Cs.dim} size={11}>§5</Mono>
                <h2 style={{ margin: 0, fontSize: 18, fontWeight: 500, letterSpacing: '-0.015em' }}>Household</h2>
              </div>
              <ConfigLine label="Name"       value={<EditValue mono={false}>Lim Residence</EditValue>} />
              <ConfigLine label="Timezone"   value={<EditValue>Asia/Singapore</EditValue>} />
              <ConfigLine label="Tongue"     value={<EditValue mono={false}>English (UK)</EditValue>} />
              <ConfigLine label="Address-of" value={<EditValue mono={false}>Tze</EditValue>} last />
            </div>
            <div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, marginBottom: 14 }}>
                <Mono color={Cs.dim} size={11}>§6</Mono>
                <h2 style={{ margin: 0, fontSize: 18, fontWeight: 500, letterSpacing: '-0.015em' }}>Appearance</h2>
              </div>
              <ConfigLine label="Theme"     value={<EditValue mono={false}>dark · paper-warm</EditValue>} />
              <ConfigLine label="Density"   value={<EditValue mono={false}>standard</EditValue>} />
              <ConfigLine label="Briefing voice" value={<EditValue mono={false}>serif · standard length</EditValue>} />
              <ConfigLine label="Empty-state voice" value={<EditValue mono={false}>"Nothing waiting."</EditValue>} last />
            </div>
            <div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, marginBottom: 14 }}>
                <Mono color={Cs.dim} size={11}>§7</Mono>
                <h2 style={{ margin: 0, fontSize: 18, fontWeight: 500, letterSpacing: '-0.015em' }}>Data</h2>
              </div>
              <ConfigLine label="Export"      value={<a style={linkS}>download archive →</a>} />
              <ConfigLine label="Audit log"   value={<a style={linkS}>open /audit →</a>} />
              <ConfigLine label="Reset memory" value={<a style={{ ...linkS, color: Cs.amber, textDecorationColor: Cs.amber }}>tier · 7-day cool-down →</a>} />
              <ConfigLine label="Wipe system" value={<a style={{ ...linkS, color: Cs.red, textDecorationColor: Cs.red }}>destructive · requires phrase →</a>} last />
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

const linkS = {
  fontFamily: 'var(--font-mono)', fontSize: 12, color: Cs.fg,
  textDecoration: 'underline', textUnderlineOffset: 4,
  textDecorationColor: Cs.borderStrong, cursor: 'pointer',
};

function ConfigLine({ label, helper, value, last, mono = true }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '1fr auto',
      gap: 16, padding: '12px 0', alignItems: 'baseline',
      borderBottom: last ? `1px solid ${Cs.border}` : `1px solid ${Cs.borderSoft}`,
      borderTop: 'none',
    }}>
      <div>
        <div style={{ fontSize: 13, color: Cs.fg }}>{label}</div>
        {helper && <div style={{ fontSize: 12, color: Cs.dim, marginTop: 2 }}>{helper}</div>}
      </div>
      <div style={{ fontFamily: mono ? 'var(--font-mono)' : 'var(--font-sans)', fontSize: 12, color: Cs.mfg, textAlign: 'right' }}>
        {value}
      </div>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────
// B · The Console — panel grid, NOC style.
//
// Settings as a status board. 3×2 panel grid. Each panel is a settings
// domain with its current state on display + inline-editable values.
// Same family as /butlers: hairline cells, no card chrome, hue only on
// the letter-marks inside the Butlers panel.
// ───────────────────────────────────────────────────────────────────────

// Subtle attention tint applied to a panel that needs human eyes.
// State color is foreground+border per the design language, but a 4–7%
// fill on the *whole panel* registers as "look here" without becoming
// SaaS-flashy. Pairs with a small badge in the panel header.
const ATTN_BG = {
  amber: 'oklch(0.81 0.185 84 / 0.06)',
  red:   'oklch(0.685 0.25 29 / 0.06)',
};

function ConsolePanel({ title, eyebrow, status, right, children, col, row, totalCols, totalRows, minH = 280, attention, expand }) {
  const tint = attention && ATTN_BG[attention];
  const railColor = attention === 'red' ? Cs.red : attention === 'amber' ? Cs.amber : null;
  return (
    <div style={{
      position: 'relative',
      padding: '14px 18px 16px',
      borderRight: col < totalCols - 1 ? `1px solid ${Cs.border}` : 'none',
      borderBottom: row < totalRows - 1 ? `1px solid ${Cs.border}` : 'none',
      display: 'flex', flexDirection: 'column', gap: 10,
      minHeight: minH,
      background: tint || 'transparent',
    }}>
      {/* attention rail */}
      {railColor && (
        <span style={{
          position: 'absolute', left: 0, top: 0, bottom: 0, width: 2, background: railColor,
        }} />
      )}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <Mono color={Cs.mfg} size={9} track="0.14em">{eyebrow}</Mono>
        <span style={{ flex: 1 }} />
        {status}
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}>
        <h3 style={{
          margin: 0, fontSize: 16, fontWeight: 500, letterSpacing: '-0.015em',
          display: 'flex', alignItems: 'baseline', gap: 8,
        }}>
          {title}
          {expand && (
            <a style={{
              fontFamily: 'var(--font-mono)', fontSize: 10, color: Cs.dim,
              letterSpacing: '0.10em', textTransform: 'uppercase',
              textDecoration: 'none', cursor: 'pointer',
            }}>↗ {expand}</a>
          )}
        </h3>
        {right}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, flex: 1, minHeight: 0 }}>
        {children}
      </div>
    </div>
  );
}

// Attention strip — top-of-page summary for what needs human eyes today.
function AttentionStrip({ items }) {
  if (!items?.length) return null;
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: `repeat(${items.length}, 1fr)`,
      borderBottom: `1px solid ${Cs.border}`,
    }}>
      {items.map((it, i) => {
        const color = it.tone === 'red' ? Cs.red : Cs.amber;
        const tint = it.tone === 'red' ? ATTN_BG.red : ATTN_BG.amber;
        return (
          <div key={i} style={{
            display: 'grid', gridTemplateColumns: '8px 1fr auto',
            gap: 12, padding: '12px 18px', alignItems: 'center',
            borderRight: i < items.length - 1 ? `1px solid ${Cs.border}` : 'none',
            background: tint,
            position: 'relative',
          }}>
            <span style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 2, background: color }} />
            <span style={{ width: 6, height: 6, borderRadius: 999, background: color, opacity: 0.85 }} />
            <div style={{ minWidth: 0 }}>
              <Mono color={color} size={9} track="0.14em">{it.kind}</Mono>
              <div style={{ fontSize: 13, color: Cs.fg, marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{it.text}</div>
            </div>
            <a style={{ ...linkS, fontSize: 11 }}>{it.action} →</a>
          </div>
        );
      })}
    </div>
  );
}

function MiniSpark({ values, color, w = 80, h = 22 }) {
  const min = Math.min(...values), max = Math.max(...values);
  const range = max - min || 1;
  const step = w / (values.length - 1);
  const pts = values.map((v, i) => `${i * step},${h - ((v - min) / range) * (h - 2) - 1}`).join(' ');
  return (
    <svg width={w} height={h}>
      <polyline points={pts} fill="none" stroke={color || Cs.fg} strokeWidth="1.25" strokeLinejoin="round" />
    </svg>
  );
}

function CapacityBar({ pct, color }) {
  return (
    <div style={{
      height: 4, background: 'oklch(1 0 0 / 0.05)', borderRadius: 2, overflow: 'hidden',
    }}>
      <div style={{ width: `${pct}%`, height: '100%', background: color || Cs.fg, opacity: 0.85 }} />
    </div>
  );
}

function SettingsConsole() {
  const primary = MODELS[0]; // default · highlighted
  const inUse = BIG_MODELS.filter((m) => m.used_by.length > 0 && m.id !== primary.id);
  const attentionModels = BIG_MODELS.filter((m) => m.state === 'error' || m.state === 'rate-limited');
  const verifiedCount = BIG_MODELS.filter((m) => m.state === 'verified').length;
  const totalModels = BIG_MODELS.length;
  const sparkVals = [12, 18, 14, 22, 19, 26, 24, 28, 30, 22, 19, 24, 26, 22];
  const burnByButler = [
    { name: 'qa',           amt: 184.60 },
    { name: 'relationship', amt: 120.40 },
    { name: 'chronicler',   amt:  84.10 },
    { name: 'health',       amt:  42.20 },
    { name: 'household',    amt:  31.80 },
    { name: 'education',    amt:  22.40 },
    { name: 'memory',       amt:  16.70 },
    { name: 'calendar',     amt:  10.20 },
  ];
  const maxBurn = Math.max(...burnByButler.map((b) => b.amt));
  const waiting = [
    { butler: 'household',    text: 'Order Acqua Panna · 12-pack · $42.80',           age: '2h 14m' },
    { butler: 'relationship', text: 'Draft to Mei · "thank-you for the cocktail"',    age: '4h 02m' },
  ];

  return (
    <div style={{ height: '100%', background: Cs.bg, color: Cs.fg, display: 'flex', fontFamily: 'var(--font-sans)' }}>
      <FakeRail />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <FakeBreadcrumb right="system · 6 panels · refreshes every 5s" />

        {/* Header */}
        <div style={{
          display: 'grid', gridTemplateColumns: '1fr auto',
          gap: 24, alignItems: 'baseline',
          padding: '20px 28px 14px', borderBottom: `1px solid ${Cs.border}`,
        }}>
          <div>
            <Eyebrow>settings · console</Eyebrow>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, marginTop: 6 }}>
              <h1 style={{ margin: 0, fontSize: 26, fontWeight: 500, letterSpacing: '-0.025em' }}>The system, at a glance.</h1>
              <Mono color={Cs.dim} size={11} upper={false} track="0.04em">six panels · every value editable in place</Mono>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Pill tone="ok">{verifiedCount}/{totalModels} models verified</Pill>
            <Pill>$612.40 mtd</Pill>
          </div>
        </div>

        {/* Attention strip — what needs you, right now */}
        <AttentionStrip items={[
          { tone: 'red',   kind: 'auth · renewal required', text: 'Calendar · Google OAuth expired 4h 12m ago', action: 'reauthorize' },
          { tone: 'amber', kind: 'approvals · 2 waiting',    text: 'Oldest is 4h 02m · default expiry 24h',     action: 'review' },
        ]} />

        {/* 3 × 2 panel grid */}
        <div style={{
          flex: 1,
          display: 'grid',
          gridTemplateColumns: 'repeat(3, 1fr)',
          gridAutoRows: 'minmax(360px, 1fr)',
        }}>
          {/* ─── Panel 1 — Models (featured default + compact in-use) ─── */}
          <ConsolePanel
            eyebrow={`§1 · models · ${totalModels} total`}
            title="Model catalog"
            status={<Pill tone="ok">{verifiedCount} verified</Pill>}
            col={0} row={0} totalCols={3} totalRows={2}
            expand="/settings/models"
          >
            {/* featured default */}
            <div style={{
              padding: '10px 12px',
              border: `1px solid ${Cs.borderStrong}`, borderRadius: 2,
              background: 'oklch(1 0 0 / 0.025)',
              display: 'grid', gap: 8,
            }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                <span style={{ width: 6, height: 6, borderRadius: 999, background: Cs.green }} />
                <span style={{ fontSize: 13.5, color: Cs.fg, fontWeight: 500 }}>{primary.family}</span>
                <Mono color={Cs.green} size={9} track="0.10em">· default</Mono>
                <span style={{ flex: 1 }} />
                <Mono color={Cs.dim} size={9.5}>200k ctx · 6m ago</Mono>
              </div>
              <Mono color={Cs.dim} size={10} upper={false} track="0.02em">{primary.id} · role <span style={{ color: Cs.mfg }}>{primary.role}</span></Mono>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 10, alignItems: 'center' }}>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
                  {primary.used_by.map((u) => (
                    <span key={u} style={{
                      display: 'inline-flex', alignItems: 'center', gap: 4,
                      fontSize: 9.5, fontFamily: 'var(--font-mono)', color: Cs.mfg,
                      border: `1px solid ${Cs.border}`, padding: '1px 5px', borderRadius: 2,
                    }}><window.ButlerMark name={u} size={9} tone="neutral" />{u.slice(0, 4)}</span>
                  ))}
                </div>
                <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: Cs.fg }}>
                  {primary.inMtok.toFixed(2)}→{primary.outMtok.toFixed(2)}
                </span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <Mono color={Cs.dim} size={9.5} upper={false} track="0.04em">spend · 7d</Mono>
                <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <MiniSpark values={[28, 42, 31, 55, 48, 62, 58, 71, 64]} color={Cs.fg} w={64} h={14} />
                  <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: Cs.fg }}>${primary.spend7d.toFixed(2)}</span>
                </span>
              </div>
            </div>

            {/* compact in-use rows */}
            <Mono color={Cs.dim} size={9} track="0.14em" >also in use · {inUse.length}</Mono>
            {inUse.map((m) => (
              <div key={m.id} style={{
                display: 'grid', gridTemplateColumns: '8px 1fr auto auto', gap: 8,
                alignItems: 'center', padding: '4px 0',
                borderBottom: `1px solid ${Cs.borderSoft}`,
              }}>
                <span style={{ width: 5, height: 5, borderRadius: 999, background: Cs.green, opacity: 0.85 }} />
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, minWidth: 0 }}>
                  <span style={{ fontSize: 12, color: Cs.fg }}>{m.family.split(' · ')[1] || m.family}</span>
                  <Mono color={Cs.dim} size={9.5} upper={false} track="0.04em">{m.used_by.join(',')}</Mono>
                </div>
                <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: Cs.mfg }}>
                  {m.inMtok.toFixed(2)}→{m.outMtok.toFixed(2)}
                </span>
                <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: Cs.fg, width: 50, textAlign: 'right' }}>
                  ${m.spend7d.toFixed(2)}
                </span>
              </div>
            ))}

            {/* models needing attention (tinted rows) */}
            {attentionModels.map((m) => {
              const tone = m.state === 'error' ? 'red' : 'amber';
              const color = tone === 'red' ? Cs.red : Cs.amber;
              return (
                <div key={m.id} style={{
                  display: 'grid', gridTemplateColumns: '8px 1fr auto', gap: 8,
                  alignItems: 'center', padding: '4px 6px',
                  background: ATTN_BG[tone], borderRadius: 2,
                }}>
                  <span style={{ width: 5, height: 5, borderRadius: 999, background: color }} />
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, minWidth: 0 }}>
                    <span style={{ fontSize: 12, color: Cs.fg }}>{m.family.split(' · ').slice(-2).join(' · ')}</span>
                    <Mono color={color} size={9} track="0.10em">{m.state}</Mono>
                  </div>
                  <Mono color={Cs.mfg} size={9.5} upper={false} track="0.04em">{m.failures7d ? `${m.failures7d} failures · 7d` : ''}</Mono>
                </div>
              );
            })}

            <div style={{ marginTop: 'auto', display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <Mono color={Cs.dim} size={9.5} upper={false} track="0.04em">+ {totalModels - inUse.length - 1 - attentionModels.length} standby · {new Set(BIG_MODELS.map((m) => m.prov)).size} providers</Mono>
              <a style={{ ...linkS, fontSize: 11 }}>manage all {totalModels} →</a>
            </div>
          </ConsolePanel>

          {/* ─── Panel 2 — Butlers (single col, calendar tinted red) ─── */}
          <ConsolePanel
            eyebrow="§2 · butlers · 8"
            title="The staff"
            status={<Pill tone="amber">1 paused</Pill>}
            col={1} row={0} totalCols={3} totalRows={2}
            expand="/butlers"
          >
            <div>
              <div style={{
                display: 'grid', gridTemplateColumns: '18px 96px 70px 1fr 60px 50px 32px',
                gap: 8, padding: '4px 0',
                borderBottom: `1px solid ${Cs.border}`,
              }}>
                <Mono>·</Mono><Mono>butler</Mono><Mono>model</Mono>
                <Mono>schedule</Mono><Mono>$/day</Mono><Mono>approvals</Mono><Mono>on</Mono>
              </div>
              {BUTLERS.map((b, i) => {
                const paused = b.schedule.includes('paused');
                const rowTint = paused ? ATTN_BG.red : 'transparent';
                return (
                  <div key={b.name} style={{
                    display: 'grid',
                    gridTemplateColumns: '18px 96px 70px 1fr 60px 50px 32px',
                    gap: 8, padding: '7px 4px', alignItems: 'center',
                    borderBottom: i < BUTLERS.length - 1 ? `1px solid ${Cs.borderSoft}` : 'none',
                    background: rowTint,
                    position: 'relative',
                    marginLeft: paused ? -18 : 0,
                    paddingLeft: paused ? 22 : 4,
                  }}>
                    {paused && (
                      <span style={{
                        position: 'absolute', left: 0, top: 0, bottom: 0, width: 2, background: Cs.red,
                      }} />
                    )}
                    <window.ButlerMark name={b.name} size={14} tone={b.enabled ? 'fill' : 'neutral'} />
                    <span style={{ fontSize: 12, color: Cs.fg, textTransform: 'capitalize' }}>{b.name}</span>
                    <Mono color={Cs.mfg} size={10} upper={false} track="0.02em">{b.model.split('-').slice(0, 2).join('-')}</Mono>
                    {paused ? (
                      <Mono color={Cs.red} size={10} upper={false} track="0.04em">paused · reauth required</Mono>
                    ) : (
                      <Mono color={Cs.mfg} size={10} upper={false} track="0.02em">{b.schedule}</Mono>
                    )}
                    <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: Cs.fg }}>${b.ceiling.toFixed(2)}</span>
                    <Mono color={Cs.dim} size={9} track="0.04em">{b.autosend ? 'auto' : 'ask'}</Mono>
                    <Toggle on={b.enabled} />
                  </div>
                );
              })}
            </div>
          </ConsolePanel>

          {/* ─── Panel 3 — Spend (+ per-butler bars) ─── */}
          <ConsolePanel
            eyebrow="§3 · spend"
            title="Ceiling &amp; burn"
            status={<Pill tone="ok">51% of cap</Pill>}
            col={2} row={0} totalCols={3} totalRows={2}
            expand="/settings/spend"
          >
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
              <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 28, fontWeight: 500, color: Cs.fg, letterSpacing: '-0.025em' }}>$612.40</span>
              <Mono color={Cs.dim} upper={false} track="0.04em">/ $1,200 mtd</Mono>
              <span style={{ flex: 1 }} />
              <MiniSpark values={sparkVals} color={Cs.fg} w={88} h={20} />
            </div>
            <CapacityBar pct={51} />

            <div style={{ marginTop: 4 }}>
              <Mono color={Cs.dim} size={9} track="0.14em">by butler · 7d</Mono>
              <div style={{ marginTop: 6, display: 'grid', gap: 4 }}>
                {burnByButler.map((b) => (
                  <div key={b.name} style={{ display: 'grid', gridTemplateColumns: '14px 88px 1fr 60px', gap: 6, alignItems: 'center' }}>
                    <window.ButlerMark name={b.name} size={10} tone="neutral" />
                    <Mono color={Cs.mfg} size={10} upper={false} track="0.02em">{b.name}</Mono>
                    <div style={{ height: 6, background: 'oklch(1 0 0 / 0.04)', borderRadius: 1 }}>
                      <div style={{
                        width: `${(b.amt / maxBurn) * 100}%`,
                        height: '100%', background: Cs.fg, opacity: 0.7, borderRadius: 1,
                      }} />
                    </div>
                    <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: Cs.fg, textAlign: 'right' }}>${b.amt.toFixed(2)}</span>
                  </div>
                ))}
              </div>
            </div>

            <div style={{ display: 'grid', gap: 4, marginTop: 'auto' }}>
              <ConfigLineMini label="Monthly ceiling"     value={<EditValue>$1,200</EditValue>} />
              <ConfigLineMini label="Daily soft warning"  value={<EditValue>$45</EditValue>} />
              <ConfigLineMini label="Pause at ceiling"    value={<Toggle on />} />
            </div>
          </ConsolePanel>

          {/* ─── Panel 4 — Memory ─── */}
          <ConsolePanel
            eyebrow="§4 · memory"
            title="Tier capacities"
            status={<Mono color={Cs.dim} size={9.5}>compaction 02:00</Mono>}
            col={0} row={1} totalCols={3} totalRows={2}
            expand="/memory"
          >
            {[
              { name: 'Short-term', cap: 500,    used: 312,   pctNote: 'drops oldest after cap' },
              { name: 'Mid-term',   cap: 8000,   used: 5840,  pctNote: 'promoted morning · 02:00' },
              { name: 'Long-term',  cap: 20000,  used: 8079,  pctNote: 'promoted nightly only' },
            ].map((t) => {
              const pct = Math.round((t.used / t.cap) * 100);
              return (
                <div key={t.name} style={{ display: 'grid', gap: 4 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                    <span style={{ fontSize: 12, color: Cs.fg }}>{t.name}</span>
                    <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: Cs.mfg }}>
                      <EditValue size={10.5}>{t.used.toLocaleString()}</EditValue><span style={{ color: Cs.dim }}> / </span><EditValue size={10.5}>{t.cap.toLocaleString()}</EditValue> <span style={{ color: Cs.dim }}>· {pct}%</span>
                    </span>
                  </div>
                  <CapacityBar pct={pct} />
                  <Mono color={Cs.dim} size={9} track="0.04em" upper={false}>{t.pctNote}</Mono>
                </div>
              );
            })}

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, padding: '8px 0' }}>
              <div>
                <Mono color={Cs.mfg} size={9} track="0.14em">hit rate · 7d</Mono>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginTop: 4 }}>
                  <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 16, color: Cs.fg, fontWeight: 500 }}>94.2%</span>
                  <MiniSpark values={[91, 92, 90, 93, 94, 93, 94, 95, 94]} color={Cs.fg} w={48} h={12} />
                </div>
              </div>
              <div>
                <Mono color={Cs.mfg} size={9} track="0.14em">drops · 7d</Mono>
                <div className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 16, color: Cs.fg, fontWeight: 500, marginTop: 4 }}>142</div>
              </div>
            </div>

            <div style={{ marginTop: 'auto', display: 'grid', gap: 4 }}>
              <ConfigLineMini label="Drop policy"       value={<EditValue mono={false}>oldest · low-recall</EditValue>} />
              <ConfigLineMini label="Embed model"       value={<EditValue>text-embedding-3-l</EditValue>} />
            </div>
          </ConsolePanel>

          {/* ─── Panel 5 — Approvals (AMBER ATTENTION) ─── */}
          <ConsolePanel
            eyebrow="§5 · approvals"
            title="Asking permission"
            status={<Pill tone="amber">2 waiting</Pill>}
            col={1} row={1} totalCols={3} totalRows={2}
            attention="amber"
            expand="/approvals"
          >
            {/* Now-waiting list */}
            <div>
              <Mono color={Cs.amber} size={9} track="0.14em">awaiting you</Mono>
              <div style={{ marginTop: 6 }}>
                {waiting.map((w, i) => (
                  <div key={i} style={{
                    display: 'grid', gridTemplateColumns: '14px 1fr auto auto', gap: 8,
                    alignItems: 'center', padding: '7px 0',
                    borderBottom: i < waiting.length - 1 ? `1px solid ${Cs.borderSoft}` : 'none',
                  }}>
                    <window.ButlerMark name={w.butler} size={12} tone="neutral" />
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontSize: 12, color: Cs.fg, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{w.text}</div>
                      <Mono color={Cs.dim} size={9} upper={false} track="0.04em">{w.butler} · {w.age} old</Mono>
                    </div>
                    <Mono color={Cs.dim} size={9.5}>approve</Mono>
                    <Mono color={Cs.dim} size={9.5}>deny</Mono>
                  </div>
                ))}
              </div>
              <div style={{ marginTop: 6 }}>
                <a style={{ ...linkS, fontSize: 11 }}>review all · /approvals →</a>
              </div>
            </div>

            <div style={{ height: 1, background: Cs.border, margin: '4px 0' }} />

            <div style={{ display: 'grid', gap: 3 }}>
              <ConfigLineMini label="Default expiry"   value={<EditValue>24h</EditValue>} />
              <ConfigLineMini label="Re-auth grace"    value={<EditValue>15m</EditValue>} />
              <ConfigLineMini label="Quiet hours"      value={<EditValue>22:00 → 07:00</EditValue>} />
              <ConfigLineMini label="QA auto-merge"    value={<EditValue mono={false}>low &amp; medium</EditValue>} />
              <ConfigLineMini label="Notify · channel" value={<EditValue mono={false}>desktop · telegram</EditValue>} />
            </div>
            <div style={{ marginTop: 'auto', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <Mono color={Cs.dim} size={9.5} upper={false} track="0.04em">in quiet window?</Mono>
              <Mono color={Cs.fg} size={10.5} upper={false} track="0.04em">no · 5h 18m to quiet</Mono>
            </div>
          </ConsolePanel>

          {/* ─── Panel 6 — Permissions / Data ─── */}
          <ConsolePanel
            eyebrow="§6 · permissions &amp; data"
            title="What each butler may do"
            status={<a style={{ ...linkS, fontSize: 10.5 }}>open matrix →</a>}
            col={2} row={1} totalCols={3} totalRows={2}
            expand="/settings/permissions"
          >
            {/* compressed matrix preview (all 7 perms) */}
            <div style={{ display: 'grid', gridTemplateColumns: `auto repeat(${BUTLERS.length}, 1fr)`, gap: 4, alignItems: 'center', paddingBottom: 6, borderBottom: `1px solid ${Cs.borderSoft}` }}>
              <span />
              {BUTLERS.map((b) => <window.ButlerMark key={b.name} name={b.name} size={11} tone="neutral" />)}
              {PERMS.map((p) => (
                <React.Fragment key={p.id}>
                  <Mono color={Cs.mfg} size={9.5} upper={false} track="0.02em">{p.label}</Mono>
                  {BUTLERS.map((b) => (
                    <span key={b.name} style={{
                      width: 7, height: 7, borderRadius: 1.5,
                      background: hasPerm(b.name, p.id) ? Cs.fg : 'transparent',
                      border: hasPerm(b.name, p.id) ? 'none' : `1px solid ${Cs.borderSoft}`,
                      opacity: hasPerm(b.name, p.id) ? 0.85 : 1,
                      justifySelf: 'center',
                    }} />
                  ))}
                </React.Fragment>
              ))}
            </div>

            <div style={{ marginTop: 'auto', display: 'grid', gap: 3 }}>
              <ConfigLineMini label="Audit log"        value={<a style={linkS}>open /audit →</a>} />
              <ConfigLineMini label="Export archive"   value={<a style={linkS}>download · 142 MB →</a>} />
              <ConfigLineMini label="Reset memory"     value={<a style={{ ...linkS, color: Cs.amber, textDecorationColor: Cs.amber }}>tier · cool-down →</a>} />
              <ConfigLineMini label="Wipe system"      value={<a style={{ ...linkS, color: Cs.red, textDecorationColor: Cs.red }}>destructive →</a>} />
            </div>
          </ConsolePanel>
        </div>
      </div>
    </div>
  );
}

function ConfigLineMini({ label, value }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
      padding: '5px 0', borderBottom: `1px solid ${Cs.borderSoft}`,
    }}>
      <span style={{ fontSize: 12, color: Cs.mfg }}>{label}</span>
      <span style={{ fontSize: 12, color: Cs.fg }}>{value}</span>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────
// C · The Manifest — numbered spec sheet.
//
// Settings as a printed document. Sticky mono-numeral mini-TOC on the
// left. The body reads as nine numbered sections, hairline rules
// throughout, every value an underline-dashed editable field. The
// distinctive moment is the permissions matrix at §8.
// ───────────────────────────────────────────────────────────────────────

const TOC = [
  { n: 1, label: 'Models',      sub: '5 · 2 active' },
  { n: 2, label: 'Butlers',     sub: '8 · 1 paused' },
  { n: 3, label: 'Memory',      sub: '14,231 / 28,500' },
  { n: 4, label: 'Spend',       sub: '$612 mtd' },
  { n: 5, label: 'Approvals',   sub: '24h expiry' },
  { n: 6, label: 'Briefing',    sub: 'llm · cached' },
  { n: 7, label: 'Household',   sub: 'Asia/Singapore' },
  { n: 8, label: 'Permissions', sub: 'matrix · 8 × 7' },
  { n: 9, label: 'Data',        sub: 'export · reset' },
];

function ManifestSection({ n, title, hint, children }) {
  return (
    <section id={`s${n}`} style={{ marginBottom: 56, scrollMarginTop: 32 }}>
      <div style={{
        display: 'grid', gridTemplateColumns: '48px 1fr auto',
        gap: 16, alignItems: 'baseline',
        borderBottom: `1px solid ${Cs.border}`, paddingBottom: 12, marginBottom: 18,
      }}>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 13, color: Cs.dim,
          letterSpacing: '0.04em',
        }} className="tnum">§{String(n).padStart(2, '0')}</span>
        <h2 style={{ margin: 0, fontSize: 22, fontWeight: 500, letterSpacing: '-0.015em' }}>{title}</h2>
        {hint && <Mono color={Cs.dim} size={10} upper={false} track="0.04em">{hint}</Mono>}
      </div>
      {children}
    </section>
  );
}

function SettingsManifest() {
  return (
    <div style={{ height: '100%', background: Cs.bg, color: Cs.fg, display: 'flex', fontFamily: 'var(--font-sans)' }}>
      <FakeRail />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <FakeBreadcrumb right="system manifest · 9 sections · printable" />

        <div style={{ display: 'grid', gridTemplateColumns: '220px 1fr', flex: 1, minHeight: 0 }}>
          {/* TOC */}
          <nav style={{
            borderRight: `1px solid ${Cs.border}`,
            padding: '32px 24px',
            background: 'transparent',
            position: 'sticky', top: 0, alignSelf: 'flex-start',
          }}>
            <Eyebrow>contents</Eyebrow>
            <div style={{ marginTop: 16, display: 'grid', gap: 2 }}>
              {TOC.map((t, i) => (
                <a key={t.n} href={`#s${t.n}`} style={{
                  display: 'grid', gridTemplateColumns: '28px 1fr', gap: 8,
                  padding: '8px 0', textDecoration: 'none', color: Cs.fg,
                  borderBottom: i < TOC.length - 1 ? `1px solid ${Cs.borderSoft}` : 'none',
                  alignItems: 'baseline',
                }}>
                  <span className="tnum" style={{
                    fontFamily: 'var(--font-mono)', fontSize: 11, color: Cs.dim,
                    letterSpacing: '0.04em',
                  }}>§{String(t.n).padStart(2, '0')}</span>
                  <div>
                    <div style={{ fontSize: 13, color: i === 0 ? Cs.fg : Cs.mfg }}>{t.label}</div>
                    <Mono color={Cs.dim} size={9.5} upper={false} track="0.04em">{t.sub}</Mono>
                  </div>
                </a>
              ))}
            </div>
            <div style={{ marginTop: 24, paddingTop: 14, borderTop: `1px solid ${Cs.border}` }}>
              <Mono color={Cs.dim} size={9.5} upper={false} track="0.04em">
                personal integrations live separately at <span style={{ color: Cs.fg, fontFamily: 'var(--font-mono)' }}>/secrets</span>
              </Mono>
            </div>
          </nav>

          {/* Body */}
          <div style={{ overflow: 'auto', padding: '40px 56px 80px' }}>
            {/* Manifest hero */}
            <div style={{ marginBottom: 40 }}>
              <Eyebrow sub="version 4.2.1 · committed 14 May 2026 02:11">manifest</Eyebrow>
              <h1 style={{
                margin: '12px 0 14px',
                fontSize: 44, fontWeight: 500, letterSpacing: '-0.025em',
                lineHeight: 1.06, maxWidth: '16ch',
              }}>The household, on paper.</h1>
              <p style={{
                margin: 0, fontFamily: 'var(--font-serif)', fontSize: 16,
                color: Cs.fg, lineHeight: 1.6, maxWidth: '54ch', fontStyle: 'italic',
              }}>
                Every directive given to the staff. Change a value in place; the next briefing reflects it.
                Underlined values are editable — click to amend.
              </p>
              <div style={{ display: 'flex', gap: 10, marginTop: 18 }}>
                <Pill tone="ok">all keys verified · 6m</Pill>
                <Pill tone="amber">calendar · reauth · 4h</Pill>
                <Pill>spend $612 · ceiling $1,200</Pill>
              </div>
            </div>

            <ManifestSection n={1} title="Models" hint="5 verified · 2 active across 8 butlers">
              <ModelCatalog dense />
            </ManifestSection>

            <ManifestSection n={2} title="Butlers" hint="model · schedule · ceiling · approvals">
              <ButlerConfigTable />
            </ManifestSection>

            <ManifestSection n={3} title="Memory" hint="tier capacities · drop policy · compaction">
              <ConfigLine label="Short-term capacity"  helper="Conversational window. Drops oldest when full." value={<EditValue>500</EditValue>} />
              <ConfigLine label="Mid-term capacity"    helper="Promoted in morning consolidation." value={<EditValue>8,000</EditValue>} />
              <ConfigLine label="Long-term capacity"   helper="Promoted nightly only. Vetted by chronicler." value={<EditValue>20,000</EditValue>} />
              <ConfigLine label="Drop policy"          helper="What goes first when a tier saturates." value={<EditValue mono={false}>oldest · low-recall · low-recency</EditValue>} />
              <ConfigLine label="Compaction window"    helper="Idle period for nightly compaction." value={<EditValue>02:00 → 04:00</EditValue>} last />
            </ManifestSection>

            <ManifestSection n={4} title="Spend" hint="ceilings · warnings · burn-rate">
              <ConfigLine label="Monthly system ceiling" helper="All butlers pause if exceeded." value={<EditValue>$1,200</EditValue>} />
              <ConfigLine label="Daily soft warning"     helper="Notification only — no pause." value={<EditValue>$45</EditValue>} />
              <ConfigLine label="Per-butler ceilings"    helper="Set per butler in §2." value={<a style={linkS}>see §2 ↑</a>} />
              <ConfigLine label="Action on breach"       helper="What happens at ceiling." value={<EditValue mono={false}>pause &amp; notify</EditValue>} last />
            </ManifestSection>

            <ManifestSection n={5} title="Approvals" hint="expiry · quiet hours · auto-merge">
              <ConfigLine label="Default request expiry" helper="Auto-decline if not approved in time." value={<EditValue>24h</EditValue>} />
              <ConfigLine label="Re-auth grace"          helper="Hold before pausing on token expiry." value={<EditValue>15m</EditValue>} />
              <ConfigLine label="Quiet hours"            helper="No notifications; everything queues." value={<EditValue>22:00 → 07:00</EditValue>} />
              <ConfigLine label="QA auto-merge"          helper="Severities QA may merge without asking." value={<EditValue mono={false}>low &amp; medium</EditValue>} />
              <ConfigLine label="Notify · channel"       helper="Where approvals are surfaced." value={<EditValue mono={false}>desktop · telegram</EditValue>} last />
            </ManifestSection>

            <ManifestSection n={6} title="Briefing" hint="how the morning dispatch is written">
              <ConfigLine label="Source"  helper="LLM-composed or templated." value={<span style={{ display: 'inline-flex', gap: 6 }}><Pill tone="ok">llm · cached 5m</Pill></span>} mono={false} />
              <ConfigLine label="Voice"   helper="Default is serif roman; serif italic for empty states." value={<EditValue mono={false}>serif · standard length</EditValue>} />
              <ConfigLine label="Length"  helper="How long the prose paragraph runs." value={<EditValue mono={false}>3–4 sentences</EditValue>} />
              <ConfigLine label="Tone"    helper="Disposition of the writing." value={<EditValue mono={false}>composed · understated</EditValue>} />
              <ConfigLine label="Refresh" helper="How often the briefing recomposes." value={<EditValue>on focus · every 5m</EditValue>} last />
            </ManifestSection>

            <ManifestSection n={7} title="Household" hint="who you are to the staff">
              <ConfigLine label="Name"        value={<EditValue mono={false}>Lim Residence</EditValue>} />
              <ConfigLine label="Address-of"  helper="The form of address used in briefings." value={<EditValue mono={false}>Tze</EditValue>} />
              <ConfigLine label="Timezone"    value={<EditValue>Asia/Singapore</EditValue>} />
              <ConfigLine label="Tongue"      helper="Spelling, idioms, units." value={<EditValue mono={false}>English (UK) · metric</EditValue>} />
              <ConfigLine label="Theme"       value={<EditValue mono={false}>dark · paper-warm</EditValue>} last />
            </ManifestSection>

            <ManifestSection n={8} title="Permissions" hint="what each butler is allowed to do · click to grant or revoke">
              <PermissionsMatrix />
              <div style={{ marginTop: 14, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <Mono color={Cs.dim} size={9.5} upper={false} track="0.04em">
                  ● granted · □ denied · changes apply immediately and are audited
                </Mono>
                <a style={linkS}>open /audit →</a>
              </div>
            </ManifestSection>

            <ManifestSection n={9} title="Data" hint="export · reset · wipe">
              <ConfigLine label="Export full archive" helper="Memory, audit, configuration. Encrypted zip." value={<a style={linkS}>download · 142 MB →</a>} mono={false} />
              <ConfigLine label="Audit log"           helper="Every directive, every change, since 02 Feb." value={<a style={linkS}>open /audit →</a>} mono={false} />
              <ConfigLine label="Reset memory · tier" helper="Drops one tier. 7-day cool-down before refill." value={<a style={{ ...linkS, color: Cs.amber, textDecorationColor: Cs.amber }}>destructive →</a>} mono={false} />
              <ConfigLine label="Wipe system"         helper="Removes all configuration and memory. Requires phrase." value={<a style={{ ...linkS, color: Cs.red, textDecorationColor: Cs.red }}>requires confirmation →</a>} mono={false} last />
            </ManifestSection>

            {/* Footer */}
            <div style={{
              marginTop: 40, paddingTop: 18, borderTop: `1px solid ${Cs.border}`,
              display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
            }}>
              <Mono color={Cs.dim} size={10} upper={false} track="0.04em">
                end of manifest · last commit <span style={{ color: Cs.mfg }}>14 May 2026 02:11</span> · v4.2.1
              </Mono>
              <span style={{
                fontFamily: 'var(--font-mono)', fontSize: 10, padding: '4px 10px',
                border: `1px solid ${Cs.borderStrong}`, borderRadius: 2,
                color: Cs.fg, letterSpacing: '0.10em', textTransform: 'uppercase', cursor: 'pointer',
              }}>commit changes →</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────
// D · Model catalog · expanded view.
//
// What the Models panel in the Console opens into. Scales to 50+ models.
// Grouped-by-provider list on the left; selected-model detail panel on
// the right. Filter chips along the top filter the list in-place.
// Attention rows (error / rate-limited) carry a tint so trouble is
// visible during a scroll without breaking the calm.
// ───────────────────────────────────────────────────────────────────────

// Tiny ▲/▼ buttons for the priority stepper. +/-5 per click.
function PriorityStepper({ value }) {
  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center', gap: 2,
      fontFamily: 'var(--font-mono)', fontSize: 11,
    }}>
      <button onClick={(e) => e.stopPropagation()} title="priority −5" style={{
        all: 'unset', cursor: 'pointer', padding: '0 4px',
        color: Cs.dim, fontSize: 9,
      }}>▼</button>
      <span className="tnum" style={{
        color: Cs.fg, fontWeight: 500, minWidth: 24, textAlign: 'center',
      }}>{value}</span>
      <button onClick={(e) => e.stopPropagation()} title="priority +5" style={{
        all: 'unset', cursor: 'pointer', padding: '0 4px',
        color: Cs.dim, fontSize: 9,
      }}>▲</button>
    </div>
  );
}

// Canonical catalog grid template — shared by header, group header, rows.
const CATALOG_COLS = '64px 12px 32px 1.5fr 70px 1.1fr 56px 56px 58px 64px 92px 110px';

function CatalogRow({ m, selected, onSelect }) {
  const default_ = m.role.startsWith('default');
  const tone = m.state === 'error' ? 'red' : m.state === 'rate-limited' ? 'amber' : null;
  const tint = tone ? ATTN_BG[tone] : 'transparent';
  const stateColor = STATE_COLOR(m.state);
  const dim = !m.enabled;
  return (
    <div
      onClick={() => onSelect(m.id)}
      style={{
        display: 'grid', gridTemplateColumns: CATALOG_COLS,
        gap: 10, padding: '7px 14px', alignItems: 'center',
        borderBottom: `1px solid ${Cs.borderSoft}`,
        background: selected ? 'oklch(1 0 0 / 0.05)' : tint,
        borderLeft: selected ? `2px solid ${Cs.fg}` : tone ? `2px solid ${stateColor}` : '2px solid transparent',
        paddingLeft: 12,
        cursor: 'pointer',
        opacity: dim ? 0.55 : 1,
      }}
    >
      {/* priority stepper */}
      <PriorityStepper value={m.priority} />
      {/* state dot */}
      <span style={{
        width: 5, height: 5, borderRadius: 999,
        background: stateColor, opacity: m.state === 'offline' || m.state === 'deprecated' ? 0.4 : 0.85,
        justifySelf: 'center',
      }} />
      {/* enable toggle */}
      <span onClick={(e) => e.stopPropagation()}><Toggle on={m.enabled} /></span>
      {/* model + default */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, minWidth: 0 }}>
        <span style={{
          fontSize: 12.5, color: Cs.fg, fontWeight: default_ ? 500 : 400,
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        }}>{m.family}</span>
        {default_ && <Mono color={Cs.green} size={9} track="0.10em">default</Mono>}
      </div>
      {/* provider */}
      <Mono color={Cs.dim} size={9.5} track="0.04em" upper={false}>{m.prov.toLowerCase()}</Mono>
      {/* role */}
      <Mono color={tone ? stateColor : Cs.mfg} size={10} upper={false} track="0.02em">
        {tone ? `${m.state} · ${m.role}` : m.role}
      </Mono>
      {/* $/mtok in */}
      <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: m.inMtok ? Cs.fg : Cs.dim, textAlign: 'right' }}>
        {m.inMtok ? `$${m.inMtok.toFixed(2)}` : '—'}
      </span>
      {/* $/mtok out */}
      <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: m.outMtok ? Cs.fg : Cs.dim, textAlign: 'right' }}>
        {m.outMtok ? `$${m.outMtok.toFixed(2)}` : '—'}
      </span>
      {/* usage 24h */}
      <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: m.usage24h > 0 ? Cs.fg : Cs.dim, textAlign: 'right' }}>
        {m.usage24h > 0 ? m.usage24h.toLocaleString() : '—'}
      </span>
      {/* usage 30d */}
      <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: m.usage30d > 0 ? Cs.fg : Cs.dim, textAlign: 'right' }}>
        {m.usage30d > 0 ? m.usage30d.toLocaleString() : '—'}
      </span>
      {/* used by */}
      <div style={{ display: 'flex', flexWrap: 'nowrap', gap: 2, overflow: 'hidden' }}>
        {m.used_by.length === 0 ? (
          <Mono color={Cs.dim} size={9.5}>—</Mono>
        ) : m.used_by.slice(0, 4).map((u) => (
          <window.ButlerMark key={u} name={u} size={11} tone="neutral" />
        ))}
        {m.used_by.length > 4 && <Mono color={Cs.dim} size={9}>+{m.used_by.length - 4}</Mono>}
      </div>
      {/* actions */}
      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }} onClick={(e) => e.stopPropagation()}>
        <a style={catalogActionS}>test</a>
        <a style={catalogActionS}>edit</a>
        <a style={{ ...catalogActionS, color: Cs.red, textDecorationColor: Cs.red }}>del</a>
      </div>
    </div>
  );
}

const catalogActionS = {
  fontFamily: 'var(--font-mono)', fontSize: 10, color: Cs.mfg,
  textDecoration: 'underline', textDecorationColor: Cs.borderStrong,
  textUnderlineOffset: 3, cursor: 'pointer', letterSpacing: '0.04em',
};

function CatalogGroupHeader({ tier, count, verified, attention, spend, expanded, onToggle }) {
  const meta = TIER_META[tier] || { label: tier, desc: '' };
  return (
    <div
      onClick={onToggle}
      style={{
        display: 'grid', gridTemplateColumns: '14px 240px 1fr auto auto auto auto', gap: 12,
        padding: '12px 14px', alignItems: 'center',
        background: Cs.bgDeep, borderBottom: `1px solid ${Cs.border}`,
        borderTop: `1px solid ${Cs.border}`,
        cursor: 'pointer', position: 'sticky', top: 34, zIndex: 1,
      }}>
      <Mono color={Cs.dim} size={10}>{expanded ? '▾' : '▸'}</Mono>
      <div>
        <Mono color={Cs.fg} size={11} track="0.10em">{meta.label}</Mono>
        <Mono color={Cs.dim} size={9} track="0.04em" upper={false}>{meta.desc}</Mono>
      </div>
      <span />
      <Mono color={Cs.dim} size={9} track="0.10em">{count} models</Mono>
      <Mono color={verified > 0 ? Cs.green : Cs.dim} size={9} track="0.10em">{verified} verified</Mono>
      {attention > 0
        ? <Mono color={Cs.amber} size={9} track="0.10em">{attention} attn</Mono>
        : <span />}
      <span className="tnum" style={{
        fontFamily: 'var(--font-mono)', fontSize: 10.5,
        color: spend > 0 ? Cs.fg : Cs.dim, width: 70, textAlign: 'right',
      }}>{spend > 0 ? `$${spend.toFixed(2)}` : '—'}</span>
    </div>
  );
}

// Canonical tier order + copy. The catalog is grouped by what a model
// is FOR, not which company made it. Provider stays visible per row.
const TIER_ORDER = ['reasoning', 'workhorse', 'cheap', 'specialty', 'local', 'legacy'];
const TIER_META = {
  reasoning: { label: 'reasoning',   desc: 'escalation · hard problems · long-form' },
  workhorse: { label: 'workhorse',   desc: 'default day-to-day · low-latency' },
  cheap:     { label: 'cheap',       desc: 'fallback · cost-sensitive · benchmarks' },
  specialty: { label: 'specialty',   desc: 'embed · transcribe · vision · code · tuned' },
  local:     { label: 'local',       desc: 'air-gapped · on-prem · ollama' },
  legacy:    { label: 'legacy',      desc: 'deprecated · kept for parity' },
};

function FilterChip({ children, active, tone, onClick }) {
  const color = tone === 'red' ? Cs.red : tone === 'amber' ? Cs.amber : tone === 'green' ? Cs.green : null;
  return (
    <button
      onClick={onClick}
      style={{
        all: 'unset', cursor: 'pointer',
        display: 'inline-flex', alignItems: 'center', gap: 5,
        fontFamily: 'var(--font-mono)', fontSize: 10,
        padding: '3px 9px', borderRadius: 2,
        border: `1px solid ${active ? Cs.fg : Cs.border}`,
        background: active ? Cs.fg : 'transparent',
        color: active ? Cs.bg : color || Cs.mfg,
        letterSpacing: '0.04em', textTransform: 'lowercase',
      }}
    >{children}</button>
  );
}

function DetailRow({ label, value, span }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '110px 1fr', gap: 12,
      padding: '8px 0', borderBottom: `1px solid ${Cs.borderSoft}`,
      alignItems: 'baseline',
    }}>
      <Mono color={Cs.mfg} size={9.5} track="0.14em">{label}</Mono>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: Cs.fg }}>{value}</div>
    </div>
  );
}

function ModelDetail({ m }) {
  if (!m) return null;
  const stateColor = STATE_COLOR(m.state);
  const attn = m.state === 'error' || m.state === 'rate-limited';
  const tone = m.state === 'error' ? 'red' : m.state === 'rate-limited' ? 'amber' : null;
  const default_ = m.role.startsWith('default');
  return (
    <div style={{
      position: 'relative',
      borderLeft: `1px solid ${Cs.border}`,
      padding: '20px 24px', overflow: 'auto',
      background: attn ? ATTN_BG[tone] : 'transparent',
      display: 'flex', flexDirection: 'column', gap: 16,
    }}>
      {attn && (
        <span style={{
          position: 'absolute', left: 0, top: 0, bottom: 0, width: 2, background: stateColor,
        }} />
      )}

      <div>
        <Eyebrow sub={m.id}>{m.prov.toLowerCase()}</Eyebrow>
        <h2 style={{
          margin: '10px 0 4px',
          fontSize: 22, fontWeight: 500, letterSpacing: '-0.02em', lineHeight: 1.15,
        }}>{m.family}</h2>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
          <Pill tone={m.state === 'verified' ? 'ok' : tone}>{m.state} · {m.last}</Pill>
          {default_ && <Pill tone="ok">default</Pill>}
          {m.used_by.length > 0 && <Pill>in use · {m.used_by.length}</Pill>}
        </div>
      </div>

      {/* big numbers */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 0, borderTop: `1px solid ${Cs.border}`, borderBottom: `1px solid ${Cs.border}` }}>
        <div style={{ padding: '12px 0', borderRight: `1px solid ${Cs.border}`, paddingRight: 14 }}>
          <Mono color={Cs.mfg} size={9} track="0.14em">$/mtok in → out</Mono>
          <div className="tnum" style={{
            fontFamily: 'var(--font-mono)', fontSize: 22, fontWeight: 500,
            color: Cs.fg, letterSpacing: '-0.02em', marginTop: 6,
          }}>
            {m.inMtok || m.outMtok ? `${m.inMtok.toFixed(2)} → ${m.outMtok.toFixed(2)}` : '—'}
          </div>
        </div>
        <div style={{ padding: '12px 0 12px 14px' }}>
          <Mono color={Cs.mfg} size={9} track="0.14em">spend · 7d</Mono>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 6 }}>
            <span className="tnum" style={{ fontFamily: 'var(--font-mono)', fontSize: 22, fontWeight: 500, color: Cs.fg, letterSpacing: '-0.02em' }}>
              {m.spend7d > 0 ? `$${m.spend7d.toFixed(2)}` : '—'}
            </span>
            {m.spend7d > 0 && <MiniSpark values={[18, 28, 22, 32, 28, 38, 36, 44, 42]} color={Cs.fg} w={56} h={14} />}
          </div>
        </div>
      </div>

      {/* details */}
      <div style={{ display: 'grid', gap: 0 }}>
        <DetailRow label="provider"   value={m.prov} />
        <DetailRow label="model id"   value={<EditValue mono size={11}>{m.id}</EditValue>} />
        <DetailRow label="role"       value={<EditValue mono={false} size={12}>{m.role}</EditValue>} />
        <DetailRow label="context"    value={`${m.ctx >= 1000 ? `${m.ctx/1000}M` : `${m.ctx}k`} tokens`} />
        <DetailRow label="api key"    value={<EditValue mono size={11}>sk-***************a4f1</EditValue>} />
        <DetailRow label="failures · 7d" value={<span style={{ color: m.failures7d > 0 ? Cs.amber : Cs.dim }}>{m.failures7d}</span>} />
        <DetailRow label="used by"    value={
          m.used_by.length === 0 ? <Mono color={Cs.dim}>—</Mono> : (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
              {m.used_by.map((u) => (
                <span key={u} style={{
                  display: 'inline-flex', alignItems: 'center', gap: 5,
                  fontSize: 10.5, fontFamily: 'var(--font-mono)', color: Cs.fg,
                  border: `1px solid ${Cs.border}`, padding: '2px 6px', borderRadius: 2,
                }}><window.ButlerMark name={u} size={10} tone="fill" />{u}</span>
              ))}
            </div>
          )
        } />
      </div>

      {attn && (
        <div style={{ padding: '10px 12px', borderLeft: `2px solid ${stateColor}`, background: 'oklch(1 0 0 / 0.02)' }}>
          <Mono color={stateColor} size={9} track="0.14em">recent failures</Mono>
          <div style={{ marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 10.5, color: Cs.mfg, lineHeight: 1.6 }}>
            {m.state === 'error' && <>
              <div>16:38  500  upstream rejected · model not found</div>
              <div>16:31  500  upstream rejected · model not found</div>
              <div>14:02  500  authentication required</div>
            </>}
            {m.state === 'rate-limited' && <>
              <div>17:02  429  per-minute limit hit · backed off 12s</div>
              <div>16:54  429  per-minute limit hit · backed off 8s</div>
            </>}
          </div>
        </div>
      )}

      {/* actions */}
      <div style={{ marginTop: 'auto', display: 'grid', gap: 6, paddingTop: 14, borderTop: `1px solid ${Cs.border}` }}>
        {!default_ && <a style={{ ...linkS, fontSize: 12 }}>set as default →</a>}
        <a style={{ ...linkS, fontSize: 12 }}>add as fallback →</a>
        <a style={{ ...linkS, fontSize: 12 }}>test connection · run 1 call →</a>
        <a style={{ ...linkS, fontSize: 12 }}>rotate api key →</a>
        <a style={{ ...linkS, fontSize: 12, color: Cs.red, textDecorationColor: Cs.red }}>remove from catalog →</a>
      </div>
    </div>
  );
}

function ModelCatalogExpanded() {
  const [selectedId, setSelectedId] = React.useState('claude-haiku-4-5');
  const [stateFilter, setStateFilter] = React.useState('all');
  const [tierFilter, setTierFilter] = React.useState('all');

  // group by complexity tier, preserving canonical order
  const groups = {};
  for (const t of TIER_ORDER) groups[t] = [];
  for (const m of BIG_MODELS) {
    (groups[m.tier] || (groups[m.tier] = [])).push(m);
  }
  // sort within each tier · priority desc, then enabled desc
  for (const t of Object.keys(groups)) {
    groups[t].sort((a, b) => {
      if (b.priority !== a.priority) return b.priority - a.priority;
      return (b.enabled ? 1 : 0) - (a.enabled ? 1 : 0);
    });
  }

  const stateCounts = {
    all: BIG_MODELS.length,
    verified: BIG_MODELS.filter((m) => m.state === 'verified').length,
    attention: BIG_MODELS.filter((m) => ['error','rate-limited','untested'].includes(m.state)).length,
    deprecated: BIG_MODELS.filter((m) => m.state === 'deprecated').length,
    offline: BIG_MODELS.filter((m) => m.state === 'offline').length,
  };
  const tierCounts = Object.fromEntries(TIER_ORDER.map((t) => [t, groups[t].length]));

  const filtered = (rows) => rows.filter((m) => {
    if (stateFilter !== 'all') {
      if (stateFilter === 'attention') {
        if (!['error','rate-limited','untested'].includes(m.state)) return false;
      } else if (m.state !== stateFilter) return false;
    }
    return true;
  });

  const visibleTiers = TIER_ORDER.filter((t) => (tierFilter === 'all' || tierFilter === t) && groups[t].length > 0);
  const selected = BIG_MODELS.find((m) => m.id === selectedId);
  const providerCount = new Set(BIG_MODELS.map((m) => m.prov)).size;

  return (
    <div style={{ height: '100%', background: Cs.bg, color: Cs.fg, display: 'flex', fontFamily: 'var(--font-sans)' }}>
      <FakeRail />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        {/* breadcrumb */}
        <div style={{
          padding: '14px 28px', borderBottom: `1px solid ${Cs.border}`,
          display: 'flex', alignItems: 'baseline', gap: 12,
          fontFamily: 'var(--font-mono)', fontSize: 10, color: Cs.dim,
          textTransform: 'uppercase', letterSpacing: '0.14em',
        }}>
          <span>butlers</span><span>›</span>
          <a style={{ color: Cs.mfg, textDecoration: 'none' }}>settings</a><span>›</span>
          <span style={{ color: Cs.fg }}>model catalog</span>
          <span style={{ marginLeft: 'auto', color: Cs.mfg, letterSpacing: '0.06em', textTransform: 'none' }}>
            {BIG_MODELS.length} models · {stateCounts.verified} verified · {providerCount} providers · 6 tiers
          </span>
        </div>

        {/* header */}
        <div style={{
          padding: '22px 28px 16px', borderBottom: `1px solid ${Cs.border}`,
          display: 'grid', gridTemplateColumns: '1fr auto', gap: 24, alignItems: 'baseline',
        }}>
          <div>
            <Eyebrow>settings · §1 · model catalog</Eyebrow>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, marginTop: 8 }}>
              <h1 style={{ margin: 0, fontSize: 32, fontWeight: 500, letterSpacing: '-0.025em', lineHeight: 1.05 }}>
                Every model the staff can call.
              </h1>
              <Mono color={Cs.dim} size={11} upper={false} track="0.04em">
                grouped by complexity tier · default <span style={{ color: Cs.fg }}>claude-haiku-4-5</span>
              </Mono>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Pill tone="ok">all keys re-verified · 6m ago</Pill>
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: 10, padding: '4px 10px',
              border: `1px solid ${Cs.borderStrong}`, borderRadius: 2,
              color: Cs.fg, letterSpacing: '0.10em', textTransform: 'uppercase', cursor: 'pointer',
            }}>+ add provider</span>
          </div>
        </div>

        {/* attention strip — if anything in the catalog needs attention */}
        {stateCounts.attention > 0 && (
          <AttentionStrip items={[
            { tone: 'red',   kind: 'auth · error',         text: 'butlerhouse-warm · 12 auth failures in 24h · model unreachable',  action: 'reauthorize' },
            { tone: 'amber', kind: 'rate · throttle',      text: 'Together · Llama 3.1 70B · per-minute limit · 6 backoffs in 1h', action: 'review limits' },
          ]} />
        )}

        {/* filter bar */}
        <div style={{
          padding: '10px 28px', borderBottom: `1px solid ${Cs.border}`,
          display: 'flex', alignItems: 'center', gap: 18, flexWrap: 'wrap',
          fontFamily: 'var(--font-mono)', fontSize: 10,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <Mono color={Cs.dim} size={9}>tier</Mono>
            <FilterChip active={tierFilter === 'all'} onClick={() => setTierFilter('all')}>all · {stateCounts.all}</FilterChip>
            {TIER_ORDER.map((t) => (
              <FilterChip key={t} active={tierFilter === t} onClick={() => setTierFilter(t)}>
                {t} · {tierCounts[t]}
              </FilterChip>
            ))}
          </div>
          <div style={{ width: 1, height: 18, background: Cs.border }} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <Mono color={Cs.dim} size={9}>state</Mono>
            <FilterChip active={stateFilter === 'all'}        onClick={() => setStateFilter('all')}>all</FilterChip>
            <FilterChip active={stateFilter === 'verified'}   tone="green"  onClick={() => setStateFilter('verified')}>verified · {stateCounts.verified}</FilterChip>
            <FilterChip active={stateFilter === 'attention'}  tone="amber"  onClick={() => setStateFilter('attention')}>attention · {stateCounts.attention}</FilterChip>
            <FilterChip active={stateFilter === 'deprecated'} onClick={() => setStateFilter('deprecated')}>deprecated · {stateCounts.deprecated}</FilterChip>
            <FilterChip active={stateFilter === 'offline'}    onClick={() => setStateFilter('offline')}>offline · {stateCounts.offline}</FilterChip>
          </div>
          <span style={{ flex: 1 }} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Mono color={Cs.dim} size={9}>sort within tier</Mono>
            <FilterChip active>priority ▾</FilterChip>
          </div>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '3px 10px', border: `1px solid ${Cs.border}`, borderRadius: 2,
          }}>
            <Mono color={Cs.dim} size={9}>⌕</Mono>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: Cs.dim }}>search {BIG_MODELS.length} models</span>
          </div>
        </div>

        {/* body: list + detail */}
        <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1fr 420px', minHeight: 0 }}>
          {/* list */}
          <div style={{ overflow: 'auto', position: 'relative' }}>
            {/* sticky column header */}
            <div style={{
              display: 'grid',
              gridTemplateColumns: CATALOG_COLS,
              gap: 10, padding: '8px 14px',
              borderBottom: `1px solid ${Cs.border}`,
              background: Cs.bgDeep, position: 'sticky', top: 0, zIndex: 2,
            }}>
              <Mono>priority</Mono><Mono>·</Mono><Mono>on</Mono>
              <Mono>model</Mono><Mono>provider</Mono><Mono>role</Mono>
              <Mono>$/m · in</Mono><Mono>$/m · out</Mono>
              <Mono>calls · 24h</Mono><Mono>calls · 30d</Mono>
              <Mono>used by</Mono><Mono>actions</Mono>
            </div>
            {visibleTiers.map((tier) => {
              const rows = filtered(groups[tier]);
              if (!rows.length) return null;
              const verified = rows.filter((m) => m.state === 'verified').length;
              const attention = rows.filter((m) => ['error','rate-limited','untested'].includes(m.state)).length;
              const spend = rows.reduce((s, m) => s + m.spend7d, 0);
              return (
                <div key={tier}>
                  <CatalogGroupHeader tier={tier} count={rows.length} verified={verified} attention={attention} spend={spend} expanded />
                  {rows.map((m) => (
                    <CatalogRow key={m.id} m={m} selected={m.id === selectedId} onSelect={setSelectedId} />
                  ))}
                </div>
              );
            })}
            <div style={{ padding: '20px 14px', display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <Mono color={Cs.dim} size={9.5} upper={false} track="0.04em">
                end of catalog · last refresh 6m ago · keys re-verified daily 04:00
              </Mono>
              <a style={{ ...linkS, fontSize: 11 }}>verify all now →</a>
            </div>
          </div>

          {/* detail */}
          <ModelDetail m={selected} />
        </div>
      </div>
    </div>
  );
}

// Expose atoms so settings-expanded.jsx can build expanded views on
// the same vocabulary without duplicating components.
Object.assign(window, {
  S_Eyebrow: Eyebrow, S_Mono: Mono, S_Pill: Pill,
  S_EditValue: EditValue, S_Toggle: Toggle,
  S_FakeRail: FakeRail, S_FakeBreadcrumb: FakeBreadcrumb,
  S_KpiStrip: KpiStrip, S_MiniSpark: MiniSpark, S_CapacityBar: CapacityBar,
  S_ConfigLine: ConfigLine, S_ConfigLineMini: ConfigLineMini,
  S_AttentionStrip: AttentionStrip, S_ATTN_BG: ATTN_BG,
  S_BUTLERS: BUTLERS, S_MODELS: MODELS, S_BIG_MODELS: BIG_MODELS,
  S_PERMS: PERMS, S_hasPerm: hasPerm, S_STATE_COLOR: STATE_COLOR,
  S_linkS: linkS,
});

window.SETTINGS_REDESIGN = { SettingsLedger, SettingsConsole, SettingsManifest, ModelCatalogExpanded };
