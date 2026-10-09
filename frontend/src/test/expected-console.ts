import { realpathSync } from "node:fs";
import { pathToFileURL } from "node:url";
/** A local negative scenario owns its exact console calls, never the whole test. */
export async function withExpectedConsoleError<T>(
  matches: (args: readonly unknown[]) => boolean,
  count: number,
  action: () => T | Promise<T>,
): Promise<T> {
  if (!Number.isSafeInteger(count) || count < 1) throw new Error("invalid expected console count");
  const previous = console.error;
  let observed = 0;
  console.error = (...args: unknown[]) => {
    if (!matches(args) || ++observed > count) return previous(...args);
  };
  try {
    const result = await action();
    if (observed !== count) throw new Error("expected console error was not observed");
    return result;
  } finally {
    console.error = previous;
  }
}

// Full public React18.3.1 server-render format from its installed printWarning.
const SSR_LAYOUT_FORMAT = "Warning: useLayoutEffect does nothing on the server, because its effect cannot be encoded into the server renderer's output format. This will lead to a mismatch between the initial, non-hydrated UI and the intended UI. To avoid this, useLayoutEffect should only be used in components that render exclusively on the client. See https://reactjs.org/link/uselayouteffect-ssr for common fixes.%s";

// Exact first frames from the pinned installed Radix source; never search
// a caller stack or admit an anonymous frame by a broad filename pattern.
const ANONYMOUS_SSR_FIRST_FRAMES = new Map([
  ["RadixSelectValue", ["react-select", 220, 13]],
  ["RadixPortal", ["react-portal", 11, 22]],
  ["RadixTabs", ["react-tabs", 24, 7]],
  ["RadixRovingFocusItem", ["react-roving-focus", 124, 7]],
  ["RadixSwitchBubbleInput", ["react-switch", 101, 5]],
].map(([name, position]) => {
  const [packageName, line, column] = position as [string, number, number];
  const url = pathToFileURL(realpathSync(`node_modules/@radix-ui/${packageName}/dist/index.mjs`)).href;
  return [`    at ${url}:${line}:${column}`, name as string];
}));

/**
 * Mixed DOM/SSR specs must retain Radix's actual mounted layout effects.
 * Permit only the known non-hydrated synchronous render's public SSR warning,
 * with a source-derived finite budget for each explicitly named hook owner.
 * No warning after the render, different warning, or extra hook is admitted.
 */
export function withExpectedSsrLayoutWarnings<T>(
  budgets: Readonly<Record<string, number>>,
  render: () => T,
): T {
  const remaining = new Map(Object.entries(budgets));
  for (const [owner, count] of remaining) {
    if (!/^[A-Za-z][A-Za-z0-9]*$/.test(owner) || !Number.isSafeInteger(count) || count < 0) {
      throw new Error("invalid SSR layout warning budget");
    }
  }
  const previous = console.error;
  console.error = (...args: unknown[]) => {
    // React18 passes only its full fixed format and the current component stack.
    const owner = args.length === 2 && args[0] === SSR_LAYOUT_FORMAT && typeof args[1] === "string"
      ? /^\n {4}at ([A-Za-z][A-Za-z0-9]*)(?: |\n|$)/.exec(args[1])?.[1]
        ?? ANONYMOUS_SSR_FIRST_FRAMES.get(args[1].split("\n")[1])
      : undefined;
    const left = owner ? remaining.get(owner) : undefined;
    if (left === undefined || left === 0) return previous(...args);
    remaining.set(owner!, left - 1);
  };
  try {
    const result = render();
    if (result instanceof Promise) throw new Error("SSR allowance requires a synchronous render");
    return result;
  } finally {
    console.error = previous;
  }
}
