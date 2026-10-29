/** Native TypeScript syntax supplies ownership and all call branches. */
import ts from "typescript";
export const W = '\0';
export function scanClient(source) {
    const request = { text: source }, cap = 16;
    const sf = ts.createSourceFile('client.ts', request.text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
    const functions = new Map();
    for (const n of sf.statements)
        if (ts.isFunctionDeclaration(n) && n.name && n.body)
            functions.set(n.name.text, n);
    const visit = (node, predicate) => { const found = []; function walk(n) { if (predicate(n))
        found.push(n); ts.forEachChild(n, walk); } walk(node); return found; };
    const text = n => n.getText(sf);
    const property = n => n && n.name && (ts.isIdentifier(n.name) || ts.isStringLiteral(n.name)) ? n.name.text : null;
    const literal = n => n && (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) ? n.text : null;
    const locals = n => new Map(visit(n, x => ts.isVariableDeclaration(x) && ts.isIdentifier(x.name) && x.initializer).map(x => [x.name.text, x.initializer]));
    const hasLiteral = s => s.split('/').some(x => x && x !== W);
    function paths(n, env, seen = new Set()) {
        if (!n)
            return [];
        const s = literal(n);
        if (s !== null)
            return [s];
        if (ts.isParenthesizedExpression(n) || ts.isAsExpression(n) || ts.isNonNullExpression(n) || ts.isSatisfiesExpression(n))
            return paths(n.expression, env, seen);
        if (ts.isIdentifier(n)) {
            if (seen.has(n.text) || !env.has(n.text))
                return [W];
            return paths(env.get(n.text), env, new Set([...seen, n.text]));
        }
        if (ts.isConditionalExpression(n))
            return [...paths(n.whenTrue, env, seen), ...paths(n.whenFalse, env, seen)];
        if (ts.isTemplateExpression(n)) {
            let p = [n.head.text];
            for (const span of n.templateSpans) {
                const part = paths(span.expression, env, seen);
                p = p.flatMap(a => (part.length ? part : [W]).map(b => a + b + span.literal.text));
                if (p.length > cap)
                    throw Error('more than ' + cap + ' path candidates');
            }
            return p;
        }
        if (ts.isLiteralTypeNode(n))
            return paths(n.literal, env, seen);
        if (ts.isUnionTypeNode(n))
            return n.types.flatMap(t => paths(t, env, seen));
        return [W];
    }
    function propertyValues(o, name, env, seen = new Set()) {
        if (!o)
            return [];
        if (ts.isParenthesizedExpression(o) || ts.isAsExpression(o) || ts.isSatisfiesExpression(o))
            return propertyValues(o.expression, name, env, seen);
        if (ts.isIdentifier(o)) {
            if (seen.has(o.text) || !env.has(o.text))
                return [];
            return propertyValues(env.get(o.text), name, env, new Set([...seen, o.text]));
        }
        if (ts.isConditionalExpression(o))
            return [...propertyValues(o.whenTrue, name, env, seen), ...propertyValues(o.whenFalse, name, env, seen)];
        if (!ts.isObjectLiteralExpression(o))
            return [];
        const found = [];
        for (const p of o.properties) {
            if (ts.isSpreadAssignment(p))
                found.push(...propertyValues(p.expression, name, env, seen));
            else if (property(p) === name)
                found.push(ts.isShorthandPropertyAssignment(p) ? p.name : p.initializer);
        }
        return found;
    }
    function queryKeys(fn, call, env) {
        const names = new Set(), seen = new Set();
        function add(expr, owner, variables) {
            if (!expr)
                return;
            if (ts.isIdentifier(expr)) {
                const id = owner.pos + ':' + expr.text;
                if (seen.has(id))
                    return;
                seen.add(id);
                add(variables.get(expr.text), owner, variables);
                for (const a of visit(owner.body, n => ts.isBinaryExpression(n) && n.operatorToken.kind === ts.SyntaxKind.EqualsToken)) {
                    const l = a.left;
                    if (ts.isElementAccessExpression(l) && ts.isIdentifier(l.expression) && l.expression.text === expr.text) {
                        const key = literal(l.argumentExpression);
                        if (key !== null)
                            names.add(key);
                    }
                    if (ts.isPropertyAccessExpression(l) && ts.isIdentifier(l.expression) && l.expression.text === expr.text)
                        names.add(l.name.text);
                }
                return;
            }
            if (ts.isObjectLiteralExpression(expr)) {
                for (const p of expr.properties) {
                    if (ts.isSpreadAssignment(p))
                        add(p.expression, owner, variables);
                    else if (property(p))
                        names.add(property(p));
                }
                return;
            }
            if (ts.isCallExpression(expr) && ts.isIdentifier(expr.expression) && functions.has(expr.expression.text)) {
                const helper = functions.get(expr.expression.text);
                for (const r of visit(helper.body, ts.isReturnStatement))
                    add(r.expression, helper, locals(helper.body));
            }
        }
        for (const q of propertyValues(call.arguments[1], 'query', env))
            add(q, fn, env);
        // Legacy literal fixtures retain URLSearchParams syntax; TypeScript owns
        // parsing those calls as well, without the retired fragment scanner.
        const queryRoots = new Map();
        function roots(node, owner, variables, seen = new Set()) {
            if (!node) return false;
            if (ts.isIdentifier(node) && variables.has(node.text)) {
                const id = owner.pos + ":" + node.text;
                if (seen.has(id)) return false;
                const value = variables.get(node.text);
                const next = new Set([...seen, id]);
                const isParams = ts.isNewExpression(value) && ts.isIdentifier(value.expression)
                    && value.expression.text === "URLSearchParams";
                if (isParams || roots(value, owner, variables, next)) {
                    if (!queryRoots.has(owner)) queryRoots.set(owner, new Set());
                    queryRoots.get(owner).add(node.text);
                    return true;
                }
                return false;
            }
            if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && functions.has(node.expression.text)) {
                const helper = functions.get(node.expression.text);
                for (const returned of visit(helper.body, ts.isReturnStatement))
                    roots(returned.expression, helper, locals(helper.body), seen);
                return false;
            }
            let found = false;
            ts.forEachChild(node, child => { if (roots(child, owner, variables, seen)) found = true; });
            return found;
        }
        roots(call.arguments[0], fn, env);
        for (const q of propertyValues(call.arguments[1], 'query', env)) roots(q, fn, env);
        for (const [owner, referenced] of queryRoots) {
            for (const c of visit(owner.body, ts.isCallExpression)) {
                if (ts.isPropertyAccessExpression(c.expression) && c.expression.name.text === 'set'
                    && ts.isIdentifier(c.expression.expression) && referenced.has(c.expression.expression.text)) {
                    const key = literal(c.arguments[0]);
                    if (key === null) throw Error('unresolved URLSearchParams query key');
                    names.add(key);
                }
            }
        }
        return [...names];
    }
    const census = {}, contracts = {}, bodies = {}, constants = {};
    try {
        for (const [name, fn] of functions) {
            bodies[name] = request.text.slice(fn.body.getStart(sf) + 1, fn.body.end - 1);
            const env = locals(fn.body);
            constants[name] = Object.fromEntries([...env].map(([k, v]) => [k, text(v)]));
            for (const p of fn.parameters)
                if (ts.isIdentifier(p.name) && p.type)
                    env.set(p.name.text, p.type);
            const calls = visit(fn.body, n => ts.isCallExpression(n) && ts.isIdentifier(n.expression) && n.expression.text === 'apiFetch');
            if (calls.length)
                census[name] = calls.length;
            const rows = [];
            for (const c of calls) {
                const resolved = [...new Set(paths(c.arguments[0], env).map(p => p.split('?')[0]).filter(hasLiteral))];
                if (!resolved.length)
                    continue;
                const methodNodes = propertyValues(c.arguments[1], 'method', env);
                const methods = [...new Set(methodNodes.flatMap(n => paths(n, env)))];
                if (methods.includes(W) || methods.length > 1)
                    throw Error(name + ': unresolved or mixed request methods');
                const method = methods[0] ?? 'GET';
                rows.push({ paths: resolved, method: method.toLowerCase(), query_names: queryKeys(fn, c, env) });
            }
            if (rows.length)
                contracts[name] = { name, paths: [...new Set(rows.flatMap(r => r.paths))], method: rows[0].method, query_names: [...new Set(rows.flatMap(r => r.query_names))], calls: rows, resolved_calls: rows.length };
        }
        return { bodies, constants, census, contracts, wholeCallCount: visit(sf, n => ts.isCallExpression(n) && ts.isIdentifier(n.expression) && n.expression.text === "apiFetch").length };
    }
    catch (error) {
        throw error;
    }
}
export function pathMatches(template, apiPath) {
    const left = ('/api' + template).split('/'), right = apiPath.split('/');
    return left.length === right.length && left.every((segment, i) => {
        const target = right[i];
        if (target.startsWith('{') && target.endsWith('}'))
            return segment !== '';
        if (segment.includes(W) && !segment.replaceAll(W, ''))
            return false;
        const pattern = segment.split(W).map(part => [...part].map(char => '\\^$.*+?()[]{}|'.includes(char) ? '\\' + char : char).join('')).join('[^/]*');
        return new RegExp('^' + pattern + '$').test(target);
    });
}
function matchingOperations(template, method, paths) {
    // Canonical generated paths name a declared operation exactly. A generic
    // sibling cannot excuse its missing verb/query contract.
    const exact = '/api' + template;
    if (Object.prototype.hasOwnProperty.call(paths, exact))
        return paths[exact][method] ? [paths[exact][method]] : [];
    return Object.entries(paths).filter(([path, ops]) =>
        ops[method] && pathMatches(template, path)).map(([, ops]) => ops[method]);
}
export function pathOffenders(contracts, paths) {
    return Object.entries(contracts).flatMap(([name, c]) => {
        if (!Array.isArray(c.calls)) throw Error('missing per-call contract: ' + name);
        return c.calls.flatMap(call => call.paths.filter(p => !matchingOperations(p, call.method, paths).length).map(p => name + ': ' + call.method + ' ' + p));
    });
}
export function queryOffenders(contracts, paths) {
    return Object.entries(contracts).flatMap(([name, c]) => {
        if (!Array.isArray(c.calls)) throw Error('missing per-call contract: ' + name);
        return c.calls.flatMap(call => call.paths.flatMap(p => {
            const matched = matchingOperations(p, call.method, paths);
            if (!matched.length) return [];
            return call.query_names.filter(q => matched.some(op =>
                !(op.parameters ?? []).some(param => param.in === 'query' && param.name === q)
            )).map(q => name + ': ' + q);
        }));
    });
}
