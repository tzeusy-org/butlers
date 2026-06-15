/**
 * RuleEditor — create / edit / DSL-test panel for the /ingestion/filters surface.
 *
 * This is the shared rule authoring affordance for FiltersPipeline. It replaces
 * the dead-end '+ add rule', per-rule 'edit', and 'open DSL' buttons with a
 * working flow backed by the unified ingestion-rules API:
 *   - create → useCreateIngestionRule (POST  /api/switchboard/ingestion-rules)
 *   - edit   → useUpdateIngestionRule (PATCH /api/switchboard/ingestion-rules/:id)
 *   - test   → useTestIngestionRule   (POST  /api/switchboard/ingestion-rules/test)
 *
 * Design language matches the filters surface (mono labels, serif gloss, oklch
 * filter tokens, hairline borders) rather than the shadcn Card/Sheet stack used
 * by the (now removed) connector-detail rules form. The rule-type/condition
 * field structure and validation are lifted from that form so we do not
 * duplicate authoring logic.
 *
 * Modes:
 *   mode="create"  → blank create form (from '+ add rule')
 *   mode="edit"    → form prefilled from `rule` (from per-rule 'edit')
 *   mode="dsl"     → create form with the DSL test panel expanded (from 'open DSL')
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Filters Pipeline"
 */

import { useState } from 'react'
import {
  useCreateIngestionRule,
  useUpdateIngestionRule,
  useTestIngestionRule,
} from '@/hooks/use-ingestion-rules'
import type {
  IngestionRule,
  IngestionRuleTestEnvelope,
  IngestionRuleTestResult,
} from '@/api/types'

// ---------------------------------------------------------------------------
// Constants — rule-type catalogue (lifted from the connector rules form)
// ---------------------------------------------------------------------------

export type EditorMode = 'create' | 'edit' | 'dsl'

const RULE_TYPES: { value: string; label: string }[] = [
  { value: 'sender_domain', label: 'Sender domain' },
  { value: 'sender_address', label: 'Sender address' },
  { value: 'header_condition', label: 'Email header' },
  { value: 'mime_type', label: 'MIME attachment type' },
  { value: 'chat_id', label: 'Chat ID' },
  { value: 'source_channel', label: 'Source channel' },
]

/**
 * Verdicts the policy engine actually honors (global scope).
 *
 * These MUST stay in lock-step with the runtime evaluator and the backend
 * validator:
 *   - runtime parse/dispatch: src/butlers/ingestion_policy.py
 *     (_VALID_GLOBAL_ACTIONS + the `route_to:` prefix form)
 *   - backend create/patch validation:
 *     roster/switchboard/api/models.py::validate_ingestion_action
 *
 * The previous vocabulary (drop/preserve/tier/route) was INERT — the evaluator
 * never matched it, so editor-authored rules produced meaningless verdicts and
 * the backend rejected them at create with 422. The labels below are
 * human-friendly; the `value` is the literal action the engine matches.
 *
 * `route_to` is special: it is stored as `route_to:<butler>`, so selecting it
 * reveals a target-butler field and the submit handler assembles the prefixed
 * form.
 */
const ROUTE_TO_VALUE = 'route_to'

const ACTIONS: { value: string; label: string }[] = [
  { value: 'skip', label: 'skip (drop, bypass LLM)' },
  { value: 'metadata_only', label: 'metadata only' },
  { value: 'low_priority_queue', label: 'low priority queue' },
  { value: 'pass_through', label: 'pass through (default)' },
  { value: ROUTE_TO_VALUE, label: 'route to butler…' },
]

const DEFAULT_ACTION = 'skip'

/**
 * Split a stored `action` string into the editor's (action, target) pair.
 *
 * `route_to:finance` → { action: 'route_to', target: 'finance' }
 * `skip`             → { action: 'skip',     target: '' }
 */
function parseAction(stored: string | undefined | null): {
  action: string
  target: string
} {
  const raw = (stored ?? '').trim()
  if (raw.startsWith('route_to:')) {
    return { action: ROUTE_TO_VALUE, target: raw.slice('route_to:'.length) }
  }
  return { action: raw || DEFAULT_ACTION, target: '' }
}

/** Assemble the literal action string the evaluator matches. */
function composeAction(action: string, target: string): string {
  if (action === ROUTE_TO_VALUE) {
    return `route_to:${target.trim()}`
  }
  return action
}

function defaultConditionForType(ruleType: string): Record<string, unknown> {
  switch (ruleType) {
    case 'sender_domain':
      return { domain: '', match: 'exact' }
    case 'sender_address':
      return { address: '' }
    case 'header_condition':
      return { header: '', op: 'present', value: null }
    case 'mime_type':
      return { type: '' }
    case 'chat_id':
      return { chat_id: '' }
    case 'source_channel':
      return { channel: '' }
    default:
      return {}
  }
}

/** Returns a validation error message, or null when the condition is complete. */
function validateCondition(
  ruleType: string,
  condition: Record<string, unknown>,
): string | null {
  const need = (key: string, label: string) =>
    String(condition[key] ?? '').trim() ? null : `${label} is required.`
  switch (ruleType) {
    case 'sender_domain':
      return need('domain', 'Domain')
    case 'sender_address':
      return need('address', 'Email address')
    case 'header_condition':
      return need('header', 'Header name')
    case 'mime_type':
      return need('type', 'MIME type')
    case 'chat_id':
      return need('chat_id', 'Chat ID')
    case 'source_channel':
      return need('channel', 'Channel')
    default:
      return null
  }
}

// ---------------------------------------------------------------------------
// Shared field primitives (filters design language)
// ---------------------------------------------------------------------------

const labelCls =
  'block font-mono text-[10px] tracking-[0.14em] uppercase text-muted-foreground/70 mb-1.5'
const inputCls =
  'w-full bg-transparent border border-border px-2.5 py-1.5 font-mono text-[12px] focus:outline-none focus:border-foreground'

function TextField({
  label,
  value,
  onChange,
  placeholder,
  testid,
  lowercase = false,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  testid: string
  lowercase?: boolean
}) {
  return (
    <label className="block">
      <span className={labelCls}>{label}</span>
      <input
        type="text"
        className={inputCls}
        placeholder={placeholder}
        value={value}
        onChange={(e) =>
          onChange(lowercase ? e.target.value.toLowerCase() : e.target.value)
        }
        data-testid={testid}
      />
    </label>
  )
}

// ---------------------------------------------------------------------------
// Condition editor (type-specific fields)
// ---------------------------------------------------------------------------

function ConditionFields({
  ruleType,
  condition,
  onChange,
}: {
  ruleType: string
  condition: Record<string, unknown>
  onChange: (c: Record<string, unknown>) => void
}) {
  switch (ruleType) {
    case 'sender_domain':
      return (
        <div className="space-y-3">
          <TextField
            label="domain"
            value={String(condition.domain ?? '')}
            onChange={(v) => onChange({ ...condition, domain: v })}
            placeholder="e.g. noreply.example.com"
            testid="rule-editor-condition-domain"
            lowercase
          />
          <label className="block">
            <span className={labelCls}>match</span>
            <select
              className={inputCls}
              value={String(condition.match ?? 'exact')}
              onChange={(e) => onChange({ ...condition, match: e.target.value })}
              data-testid="rule-editor-condition-domain-match"
            >
              <option value="exact">exact</option>
              <option value="suffix">suffix (includes subdomains)</option>
            </select>
          </label>
        </div>
      )

    case 'sender_address':
      return (
        <TextField
          label="email address"
          value={String(condition.address ?? '')}
          onChange={(v) => onChange({ ...condition, address: v })}
          placeholder="e.g. alerts@example.com"
          testid="rule-editor-condition-address"
          lowercase
        />
      )

    case 'header_condition': {
      const op = String(condition.op ?? 'present')
      const needsValue = op === 'equals' || op === 'contains'
      return (
        <div className="space-y-3">
          <TextField
            label="header name"
            value={String(condition.header ?? '')}
            onChange={(v) => onChange({ ...condition, header: v })}
            placeholder="e.g. List-Unsubscribe"
            testid="rule-editor-condition-header"
          />
          <label className="block">
            <span className={labelCls}>operator</span>
            <select
              className={inputCls}
              value={op}
              onChange={(e) => {
                const newOp = e.target.value
                onChange({
                  ...condition,
                  op: newOp,
                  value: newOp === 'present' ? null : (condition.value ?? ''),
                })
              }}
              data-testid="rule-editor-condition-header-op"
            >
              <option value="present">is present</option>
              <option value="equals">equals</option>
              <option value="contains">contains</option>
            </select>
          </label>
          {needsValue && (
            <TextField
              label="value"
              value={String(condition.value ?? '')}
              onChange={(v) => onChange({ ...condition, value: v })}
              placeholder={op === 'equals' ? 'exact value' : 'substring to match'}
              testid="rule-editor-condition-header-value"
            />
          )}
        </div>
      )
    }

    case 'mime_type':
      return (
        <TextField
          label="mime type"
          value={String(condition.type ?? '')}
          onChange={(v) => onChange({ ...condition, type: v })}
          placeholder="e.g. text/calendar or image/*"
          testid="rule-editor-condition-mime"
          lowercase
        />
      )

    case 'chat_id':
      return (
        <TextField
          label="chat id"
          value={String(condition.chat_id ?? '')}
          onChange={(v) => onChange({ ...condition, chat_id: v })}
          placeholder="e.g. 123456789"
          testid="rule-editor-condition-chat-id"
        />
      )

    case 'source_channel':
      return (
        <TextField
          label="channel"
          value={String(condition.channel ?? '')}
          onChange={(v) => onChange({ ...condition, channel: v })}
          placeholder="e.g. telegram, gmail"
          testid="rule-editor-condition-channel"
          lowercase
        />
      )

    default:
      return null
  }
}

// ---------------------------------------------------------------------------
// DSL test panel — evaluates a sample envelope against active rules
// ---------------------------------------------------------------------------

/**
 * Parse a "Key: Value\nKey2: Value2" header string into a dict.
 * Lines without a colon are ignored.
 */
function parseHeadersText(raw: string): Record<string, string> {
  const result: Record<string, string> = {}
  for (const line of raw.split('\n')) {
    const idx = line.indexOf(':')
    if (idx < 1) continue
    const key = line.slice(0, idx).trim()
    const value = line.slice(idx + 1).trim()
    if (key) result[key] = value
  }
  return result
}

/**
 * Parse a comma-separated list of MIME types into an array.
 * e.g. "text/calendar, image/png" → ["text/calendar", "image/png"]
 */
function parseMimeParts(raw: string): string[] {
  return raw
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
}

function DslTestPanel() {
  const testRule = useTestIngestionRule()
  const [senderAddress, setSenderAddress] = useState('')
  const [sourceChannel, setSourceChannel] = useState('')
  // headers: free-form "Key: Value" text — one per line
  const [headersText, setHeadersText] = useState('')
  // mime_parts: comma-separated MIME type list
  const [mimePartsText, setMimePartsText] = useState('')
  // raw_key: opaque envelope key (e.g. message-id, calendar UID)
  const [rawKey, setRawKey] = useState('')
  const [result, setResult] = useState<IngestionRuleTestResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function handleRun() {
    setError(null)
    setResult(null)
    const envelope: IngestionRuleTestEnvelope = {}
    if (senderAddress.trim()) envelope.sender_address = senderAddress.trim()
    if (sourceChannel.trim()) envelope.source_channel = sourceChannel.trim()
    const headers = parseHeadersText(headersText)
    if (Object.keys(headers).length > 0) envelope.headers = headers
    const mimeParts = parseMimeParts(mimePartsText)
    if (mimeParts.length > 0) envelope.mime_parts = mimeParts
    if (rawKey.trim()) envelope.raw_key = rawKey.trim()
    try {
      const resp = await testRule.mutateAsync({ envelope })
      setResult(resp.data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Test failed.')
    }
  }

  return (
    <div
      className="mt-5 pt-5 border-t border-border/60 space-y-3"
      data-testid="rule-editor-dsl-panel"
    >
      <span className="font-mono text-[10px] tracking-[0.14em] uppercase text-muted-foreground">
        test against active rules
      </span>
      <p className="font-serif text-[12.5px] text-muted-foreground leading-snug max-w-[52ch]">
        Build a sample envelope and see which verdict the live rule set returns.
      </p>
      <div className="grid grid-cols-2 gap-3">
        <TextField
          label="sender address"
          value={senderAddress}
          onChange={setSenderAddress}
          placeholder="alerts@example.com"
          testid="rule-editor-test-sender"
          lowercase
        />
        <TextField
          label="source channel"
          value={sourceChannel}
          onChange={setSourceChannel}
          placeholder="gmail"
          testid="rule-editor-test-channel"
          lowercase
        />
        <label className="block col-span-2">
          <span className={labelCls}>headers (one per line: Key: Value)</span>
          <textarea
            className={`${inputCls} resize-y min-h-[56px]`}
            placeholder={"List-Unsubscribe: <mailto:unsub@example.com>\nX-Mailer: Outlook"}
            value={headersText}
            onChange={(e) => setHeadersText(e.target.value)}
            data-testid="rule-editor-test-headers"
          />
        </label>
        <TextField
          label="mime parts (comma-separated)"
          value={mimePartsText}
          onChange={setMimePartsText}
          placeholder="text/calendar, image/png"
          testid="rule-editor-test-mime-parts"
          lowercase
        />
        <TextField
          label="raw key (optional)"
          value={rawKey}
          onChange={setRawKey}
          placeholder="e.g. message-id or calendar UID"
          testid="rule-editor-test-raw-key"
        />
      </div>
      <button
        type="button"
        className="font-mono text-[11px] border border-foreground/30 px-3 py-1.5 hover:bg-foreground/5 transition-colors text-muted-foreground disabled:opacity-50"
        onClick={handleRun}
        disabled={testRule.isPending}
        data-testid="rule-editor-test-run"
      >
        {testRule.isPending ? 'testing…' : 'run test'}
      </button>

      {error && (
        <div
          className="font-mono text-[11px] text-[color:var(--filter-red,oklch(0.62_0.20_25))] border border-[color:var(--filter-red,oklch(0.62_0.20_25))]/30 px-3 py-2"
          data-testid="rule-editor-test-error"
        >
          {error}
        </div>
      )}

      {result && (
        <div
          className="font-mono text-[11px] border border-border px-3 py-2 leading-relaxed bg-foreground/[0.02]"
          data-testid="rule-editor-test-result"
        >
          <div>
            <span className="text-muted-foreground">matched: </span>
            <span>{result.matched ? 'yes' : 'no'}</span>
          </div>
          {result.decision && (
            <div>
              <span className="text-muted-foreground">decision: </span>
              <span>{result.decision}</span>
            </div>
          )}
          {result.target_butler && (
            <div>
              <span className="text-muted-foreground">target: </span>
              <span>{result.target_butler}</span>
            </div>
          )}
          <div className="text-muted-foreground mt-1">{result.reason}</div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// RuleEditor
// ---------------------------------------------------------------------------

export interface RuleEditorProps {
  mode: EditorMode
  /** The rule being edited (mode="edit"); ignored for create/dsl. */
  rule?: IngestionRule | null
  onClose: () => void
  /** Called after a successful create/update so the parent can react. */
  onSaved?: () => void
}

export function RuleEditor({ mode, rule, onClose, onSaved }: RuleEditorProps) {
  const isEditing = mode === 'edit' && rule != null

  const [ruleType, setRuleType] = useState<string>(
    rule?.rule_type ?? 'sender_domain',
  )
  const [condition, setCondition] = useState<Record<string, unknown>>(
    rule?.condition ?? defaultConditionForType(rule?.rule_type ?? 'sender_domain'),
  )
  const initialAction = parseAction(rule?.action)
  const [action, setAction] = useState<string>(initialAction.action)
  const [routeTarget, setRouteTarget] = useState<string>(initialAction.target)
  const [priority, setPriority] = useState<number>(rule?.priority ?? 100)
  const [name, setName] = useState<string>(rule?.name ?? '')
  const [description, setDescription] = useState<string>(rule?.description ?? '')
  const [error, setError] = useState<string | null>(null)
  const [showDsl, setShowDsl] = useState<boolean>(mode === 'dsl')

  const createRule = useCreateIngestionRule()
  const updateRule = useUpdateIngestionRule()
  const isSaving = createRule.isPending || updateRule.isPending

  function handleRuleTypeChange(newType: string) {
    setRuleType(newType)
    setCondition(defaultConditionForType(newType))
  }

  async function handleSave() {
    setError(null)

    const condError = validateCondition(ruleType, condition)
    if (condError) {
      setError(condError)
      return
    }

    if (action === ROUTE_TO_VALUE && !routeTarget.trim()) {
      setError('Target butler is required for the "route to butler" verdict.')
      return
    }

    // Assemble the literal action the policy engine matches (route_to:<butler>
    // for routing; the bare runtime verb otherwise). This is what the backend
    // validator accepts and what ingestion_policy.py dispatches on.
    const composedAction = composeAction(action, routeTarget)

    try {
      if (isEditing && rule) {
        await updateRule.mutateAsync({
          id: rule.id,
          body: {
            condition,
            action: composedAction,
            priority,
            name: name.trim() || null,
            description: description.trim() || null,
          },
        })
      } else {
        await createRule.mutateAsync({
          scope: 'global',
          rule_type: ruleType,
          condition,
          action: composedAction,
          priority,
          name: name.trim() || null,
          description: description.trim() || null,
        })
      }
      onSaved?.()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save rule.')
    }
  }

  return (
    <div
      className="mt-8 border border-foreground/20 p-5 bg-foreground/[0.015]"
      data-testid="rule-editor"
    >
      {/* Header */}
      <div className="flex items-baseline justify-between mb-5">
        <h3 className="m-0 text-lg font-medium tracking-[-0.02em] lowercase">
          {isEditing ? 'edit rule' : 'new rule'}
        </h3>
        <button
          type="button"
          className="font-mono text-[12px] text-muted-foreground hover:text-foreground"
          onClick={onClose}
          aria-label="Close rule editor"
          data-testid="rule-editor-close"
        >
          ×
        </button>
      </div>

      <div className="grid grid-cols-2 gap-5">
        {/* Left column: name + type + condition */}
        <div className="space-y-4">
          <TextField
            label="name (optional)"
            value={name}
            onChange={setName}
            placeholder="e.g. Block marketing mail"
            testid="rule-editor-name"
          />

          <label className="block">
            <span className={labelCls}>rule type</span>
            <select
              className={inputCls}
              value={ruleType}
              onChange={(e) => handleRuleTypeChange(e.target.value)}
              disabled={isEditing}
              data-testid="rule-editor-type"
            >
              {RULE_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>

          <div className="border border-border/60 p-3 space-y-3">
            <span className="font-mono text-[10px] tracking-[0.14em] uppercase text-muted-foreground/70">
              condition
            </span>
            <ConditionFields
              ruleType={ruleType}
              condition={condition}
              onChange={setCondition}
            />
          </div>
        </div>

        {/* Right column: verdict + priority + description */}
        <div className="space-y-4">
          <label className="block">
            <span className={labelCls}>verdict</span>
            <select
              className={inputCls}
              value={action}
              onChange={(e) => setAction(e.target.value)}
              data-testid="rule-editor-action"
            >
              {ACTIONS.map((a) => (
                <option key={a.value} value={a.value}>
                  {a.label}
                </option>
              ))}
            </select>
          </label>

          {action === ROUTE_TO_VALUE && (
            <TextField
              label="target butler"
              value={routeTarget}
              onChange={setRouteTarget}
              placeholder="e.g. finance"
              testid="rule-editor-route-target"
              lowercase
            />
          )}

          <label className="block">
            <span className={labelCls}>priority (lower = higher)</span>
            <input
              type="number"
              min={0}
              className={inputCls}
              value={priority}
              onChange={(e) =>
                setPriority(Math.max(0, parseInt(e.target.value, 10) || 0))
              }
              data-testid="rule-editor-priority"
            />
          </label>

          <TextField
            label="description (optional)"
            value={description}
            onChange={setDescription}
            placeholder="Why this rule exists"
            testid="rule-editor-description"
          />
        </div>
      </div>

      {error && (
        <div
          className="mt-4 font-mono text-[11px] text-[color:var(--filter-red,oklch(0.62_0.20_25))] border border-[color:var(--filter-red,oklch(0.62_0.20_25))]/30 px-3 py-2"
          data-testid="rule-editor-error"
        >
          {error}
        </div>
      )}

      {/* Actions */}
      <div className="mt-5 flex items-center gap-3">
        <button
          type="button"
          className="font-mono text-[11px] border border-foreground px-3 py-1.5 hover:bg-foreground hover:text-background transition-colors disabled:opacity-50"
          onClick={handleSave}
          disabled={isSaving}
          data-testid="rule-editor-save"
        >
          {isSaving ? 'saving…' : isEditing ? 'save changes' : 'create rule'}
        </button>
        <button
          type="button"
          className="font-mono text-[11px] border border-foreground/30 px-3 py-1.5 hover:bg-foreground/5 transition-colors text-muted-foreground"
          onClick={onClose}
          data-testid="rule-editor-cancel"
        >
          cancel
        </button>
        <button
          type="button"
          className="ml-auto font-mono text-[11px] text-muted-foreground hover:text-foreground underline underline-offset-2 decoration-muted-foreground/30"
          onClick={() => setShowDsl((s) => !s)}
          data-testid="rule-editor-toggle-dsl"
        >
          {showDsl ? 'hide DSL test' : 'open DSL test'}
        </button>
      </div>

      {showDsl && <DslTestPanel />}
    </div>
  )
}
