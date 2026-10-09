/** The ledger also rejects a console error swallowed by an application catch. */
export function createConsoleGuard() {
  let unexpected = 0;
  const method = (kind: "error" | "warn") => (...args: unknown[]) => {
    unexpected += 1;
    // Closed public species only, never the application/test arguments.
    const species = args.some(arg => typeof arg === "string" && arg.includes("useLayoutEffect does nothing on the server")) ? "ssr-layout"
      : args.some(arg => typeof arg === "string" && arg.includes("not wrapped in act")) ? "missing-act"
      : "other";
    throw new Error(`unexpected console.${kind}:${species}`);
  };
  return {
    error: method("error"),
    warn: method("warn"),
    assertClean() {
      if (unexpected !== 0) throw new Error(`unexpected console calls: ${unexpected}`);
    },
  };
}
