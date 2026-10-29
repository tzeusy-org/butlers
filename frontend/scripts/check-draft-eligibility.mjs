#!/usr/bin/env node
/** Adopted field eligibility, not content classification. Shared controls cannot opt in. */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const OWNERS = {
  'src/components/health/ConditionForm.tsx': ['health.condition'],
  'src/components/health/MealForm.tsx': ['health.meal'],
  'src/components/health/MeasurementForm.tsx': ['health.measurement'],
  'src/components/health/MedicationForm.tsx': ['health.medication'],
  'src/components/health/ResearchForm.tsx': ['health.research'],
  'src/components/health/SymptomForm.tsx': ['health.symptom'],
  'src/components/schedules/ScheduleForm.tsx': ['schedule.form'],
  'src/components/approvals/create-rule-dialog.tsx': ['approvals.rule.create'],
  'src/components/relationship/EntityVerbRail.tsx': ['relationship.interaction', 'relationship.gift', 'relationship.note'],
  'src/components/chronicles/EpisodeDrawer.tsx': ['chronicles.correction'],
  'src/components/butler-detail/ButlerStateTab.tsx': ['butler-state.set'],
  'src/components/state/StateBrowser.tsx': ['butler-state.edit'],
  'src/pages/calendar/use-butler-event-editor.ts': ['calendar.butler-event'],
  'src/components/chat/ChatPanel.tsx': ['chat.message'],
  'src/components/chat/ChatDock.tsx': ['chat.message'],
  'src/pages/ChatPage.tsx': ['chat.message'],
  'src/components/chat/FloatingChatWidget.tsx': ['chat.message'],
}
const FIELDS = {
  'chat.message': 'message',
  'health.condition': 'name status diagnosedAt notes',
  'health.meal': 'type description eatenAt calories protein carbs fat notes',
  'health.measurement': 'type scalar systolic diastolic valueObject measuredAt notes',
  'health.medication': 'name dosage frequency schedule active notes',
  'health.research': 'title content tags sourceUrl',
  'health.symptom': 'name severity occurredAt notes',
  'schedule.form': 'name cron dispatchMode prompt jobName jobArgsRaw complexity',
  'approvals.rule.create': 'tool_name description arg_constraints_text max_uses expires_days',
  'relationship.interaction': 'type summary',
  'relationship.gift': 'description occasion',
  'relationship.reach-out': 'message channel', // retired action: identity remains, registration is forbidden
  'relationship.note': 'content',
  'chronicles.correction': 'title privacy note',
  'butler-state.set': 'key value',
  'butler-state.edit': 'key value',
  'calendar.butler-event': 'butlerName eventKind title startAtLocal endAtLocal timezone recurrenceFrequency cron hasUntilAt untilAtLocal',
}
const sorted = (values) => [...values].sort().join('\0')

export function checkDraftModule(filename, source) {
  const tree = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const errors = []
  const seen = []
  const report = (node, message) => errors.push(`${filename}:${tree.getLineAndCharacterOfPosition(node.getStart()).line + 1}: ${message}`)
  const visit = (node) => {
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier) && /(?:use-draft|draft-session|draft-store)(?:\.[cm]?[jt]sx?)?$/.test(node.moduleSpecifier.text)) {
      const moduleName = node.moduleSpecifier.text.replace(/\.[cm]?[jt]sx?$/, '')
      if (moduleName.endsWith('use-draft') && !OWNERS[filename]) report(node, 'unregistered draft owner')
      if (moduleName.endsWith('draft-session') && !['src/hooks/use-draft.ts', 'src/components/ui/draft-notice.tsx', 'src/components/ui/draft-dismiss.tsx', 'src/lib/draft-navigation.ts', 'src/components/chat/ChatPanel.tsx', 'src/components/chat/ChatDock.tsx', 'src/pages/ChatPage.tsx', 'src/components/chat/FloatingChatWidget.tsx', 'src/pages/CalendarWorkspacePage.tsx'].includes(filename)) report(node, 'unregistered session access')
      if (moduleName.endsWith('draft-store') && !['src/lib/draft-session.ts', 'src/components/auth/OwnerGate.tsx'].includes(filename)) report(node, 'unregistered store access')
      if (moduleName.endsWith('use-draft')) {
        const bindings = node.importClause?.namedBindings
        if (!bindings || !ts.isNamedImports(bindings) || bindings.elements.some((entry) => entry.name.text !== 'useDraft' || entry.propertyName)) report(node, 'draft registration aliases are forbidden')
      }
    }
    if (ts.isExportDeclaration(node) && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier) && /(?:use-draft|draft-session|draft-store)(?:\.[cm]?[jt]sx?)?$/.test(node.moduleSpecifier.text)) report(node, 'draft access cannot be re-exported')
    if (ts.isCallExpression(node) && (node.expression.kind === ts.SyntaxKind.ImportKeyword || (ts.isIdentifier(node.expression) && node.expression.text === 'require')) && node.arguments.some((argument) => ts.isStringLiteral(argument) && /(?:use-draft|draft-session|draft-store)(?:\.[cm]?[jt]sx?)?$/.test(argument.text))) report(node, 'dynamic draft access is forbidden')
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === 'useDraft') {
      const [surface, , fields] = node.arguments
      const id = surface && ts.isStringLiteral(surface) ? surface.text : ''
      if (!OWNERS[filename]?.includes(id) || /secrets|provider|credential/i.test(filename)) report(node, 'forbidden or unknown draft surface')
      seen.push(id)
      const names = fields && ts.isObjectLiteralExpression(fields) ? fields.properties.map((field) => {
        if ((!ts.isPropertyAssignment(field) && !ts.isShorthandPropertyAssignment(field)) || !field.name || ts.isComputedPropertyName(field.name)) return ''
        return ts.isIdentifier(field.name) || ts.isStringLiteral(field.name) ? field.name.text : ''
      }) : []
      if (!FIELDS[id] || sorted(names) !== sorted(FIELDS[id].split(' '))) report(node, 'draft fields must be the exact explicit approved bundle')
    }
    if (OWNERS[filename] && ts.isJsxAttribute(node) && ['type', 'autoComplete', 'data-purpose'].includes(node.name.getText(tree))) {
      const value = node.initializer?.getText(tree) ?? ''
      if ((node.initializer && !ts.isStringLiteral(node.initializer)) || /password|credential|api[-_ ]?key|secret|token|private[-_ ]?key|recovery[-_ ]?code|auth[-_ ]?session|copy[-_ ]?once/i.test(value)) report(node, 'credential-purpose controls cannot share a registered draft owner')
    }
    ts.forEachChild(node, visit)
  }
  visit(tree)
  if (OWNERS[filename] && sorted(seen) !== sorted(OWNERS[filename])) errors.push(`${filename}: missing or duplicate approved registration`)
  return errors
}

function files(directory) {
  return fs.readdirSync(directory, {withFileTypes: true}).flatMap((entry) => entry.isDirectory() ? files(path.join(directory, entry.name)) : /\.[cm]?[jt]sx?$/.test(entry.name) && !/\.(?:test|spec|stories|a11y)\./.test(entry.name) ? [path.join(directory, entry.name)] : [])
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
  const errors = files(path.join(root, 'src')).flatMap((filename) => checkDraftModule(path.relative(root, filename).split(path.sep).join('/'), fs.readFileSync(filename, 'utf8')))
  if (errors.length) { console.error(errors.join('\n')); process.exitCode = 1 }
  else console.log('Draft eligibility: exact approved owners and bundles; retired reach-out inactive.')
}
