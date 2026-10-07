/** Track local zero-fill data flow into count-strip consumers. */
export const countBucketTruthRule = {
  meta: { type: 'problem', schema: [], messages: {
    zero: 'Count buckets require source time keys and availability; zero-filled local arrays cannot feed count strips.',
  } },
  create(context) {
    const tainted = new Set()
    const sinks = []
    const aliases = []
    function zeroFill(node) {
      if (node.type !== 'CallExpression') return false
      const callee = node.callee
      if (callee.type !== 'MemberExpression' || callee.computed) return false
      if (callee.property.name === 'fill' && node.arguments[0]?.type === 'Literal' && node.arguments[0].value === 0) {
        const object = callee.object
        return (object.type === 'CallExpression' || object.type === 'NewExpression') && object.callee?.name === 'Array'
      }
      return callee.object.name === 'Array' && callee.property.name === 'from' &&
        ['ArrowFunctionExpression', 'FunctionExpression'].includes(node.arguments[1]?.type) &&
        (node.arguments[1].body.value === 0 || node.arguments[1].body.body?.some(statement =>
          statement.type === 'ReturnStatement' && statement.argument?.value === 0))
    }
    function identifiers(node, found = new Set()) {
      if (!node || typeof node !== 'object') return found
      if (node.type === 'Identifier') found.add(node.name)
      for (const [key, value] of Object.entries(node)) {
        if (key === 'parent') continue
        if (Array.isArray(value)) for (const child of value) identifiers(child, found)
        else if (value && typeof value === 'object') identifiers(value, found)
      }
      return found
    }
    function visitExpression(node, name) {
      if (!node) return
      if (zeroFill(node)) { tainted.add(name) }
      aliases.push({ name, refs: identifiers(node), node })
    }
    return {
      ArrowFunctionExpression(node) {
        if (node.body.type !== 'BlockStatement' && node.parent.type === 'VariableDeclarator' && node.parent.id.type === 'Identifier') visitExpression(node.body, node.parent.id.name)
      },
      VariableDeclarator(node) { if (node.id.type === 'Identifier') visitExpression(node.init, node.id.name) },
      AssignmentExpression(node) { if (node.left.type === 'Identifier') visitExpression(node.right, node.left.name) },
      JSXOpeningElement(node) {
        if (['BucketStrip', 'ActivityStripe', 'Sparkline', 'ConnectorHistogram'].includes(node.name.name)) {
          for (const attribute of node.attributes) if (attribute.value?.expression) sinks.push(attribute.value.expression)
        }
      },
      ReturnStatement(node) {
        let fn = node.parent
        while (fn && !['FunctionDeclaration', 'FunctionExpression', 'ArrowFunctionExpression'].includes(fn.type)) fn = fn.parent
        const name = fn?.id?.name ?? (fn?.parent?.type === 'VariableDeclarator' ? fn.parent.id.name : undefined)
        if (name) visitExpression(node.argument, name)
      },
      CallExpression(node) {
        if (['denseCountBuckets', 'sessionCountBuckets', 'bucketSessions'].includes(node.callee.name)) {
          sinks.push(...node.arguments)
        }
      },
      'Program:exit'() {
        let changed = true
        while (changed) {
          changed = false
          for (const alias of aliases) if (!tainted.has(alias.name) && [...alias.refs].some(name => tainted.has(name))) {
            tainted.add(alias.name); changed = true
          }
        }
        for (const sink of sinks) {
          if (zeroFill(sink) || [...identifiers(sink)].some(name => tainted.has(name))) {
            context.report({ node: sink, messageId: 'zero' })
          }
        }
      },
    }
  },
}
