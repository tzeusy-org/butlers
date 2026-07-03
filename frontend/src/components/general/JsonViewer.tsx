/**
 * JsonViewer — recursive collapsible tree rendering of any JSON value.
 *
 * Features:
 * - Recursive collapsible tree for objects and arrays
 * - Syntax highlighting: keys, strings, numbers, booleans, null
 * - Copy-to-clipboard button at root level
 * - Pure Tailwind styling, no external dependencies
 * - Reusable across entity detail and state browser
 */

import { useCallback, useState } from "react";

import { Button } from "@/components/ui/button";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface JsonViewerProps {
  /** The data to render. */
  data: unknown;
  /** Whether to start with all nodes collapsed. Defaults to false. */
  defaultCollapsed?: boolean;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isArray(value: unknown): value is unknown[] {
  return Array.isArray(value);
}

// ---------------------------------------------------------------------------
// Value renderer
// ---------------------------------------------------------------------------

function ValueSpan({ value }: { value: unknown }) {
  if (value === null) {
    return <span className="text-rose-400 italic">null</span>;
  }
  if (typeof value === "boolean") {
    return (
      <span className="text-amber-500 font-medium">
        {value ? "true" : "false"}
      </span>
    );
  }
  if (typeof value === "number") {
    return <span className="text-sky-400">{String(value)}</span>;
  }
  if (typeof value === "string") {
    return (
      <span className="text-emerald-400">
        &quot;{value}&quot;
      </span>
    );
  }
  // fallback
  return <span className="text-muted-foreground">{String(value)}</span>;
}

// ---------------------------------------------------------------------------
// Collapsible node
// ---------------------------------------------------------------------------

interface NodeProps {
  label?: string;
  value: unknown;
  defaultCollapsed: boolean;
  depth: number;
}

function JsonNode({ label, value, defaultCollapsed, depth }: NodeProps) {
  const isObj = isObject(value);
  const isArr = isArray(value);
  const isCollapsible = isObj || isArr;

  const [collapsed, setCollapsed] = useState(defaultCollapsed && depth > 0);

  if (!isCollapsible) {
    return (
      <div className="flex items-baseline gap-1" style={{ paddingLeft: depth * 16 }}>
        {label != null && (
          <>
            <span className="text-violet-400 font-medium">{label}</span>
            <span className="text-muted-foreground">:</span>{" "}
          </>
        )}
        <ValueSpan value={value} />
      </div>
    );
  }

  const entries = isArr
    ? (value as unknown[]).map((v, i) => [String(i), v] as const)
    : Object.entries(value as Record<string, unknown>);

  const bracketOpen = isArr ? "[" : "{";
  const bracketClose = isArr ? "]" : "}";
  const itemCount = entries.length;

  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        aria-expanded={!collapsed}
        className="flex cursor-pointer items-baseline gap-1 hover:bg-accent/30 rounded-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        style={{ paddingLeft: depth * 16 }}
        onClick={() => setCollapsed((c) => !c)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setCollapsed((c) => !c);
          }
        }}
      >
        <span aria-hidden="true" className="text-muted-foreground select-none text-xs w-4 inline-block text-center">
          {collapsed ? "\u25B6" : "\u25BC"}
        </span>
        {label != null && (
          <>
            <span className="text-violet-400 font-medium">{label}</span>
            <span className="text-muted-foreground">:</span>{" "}
          </>
        )}
        <span className="text-muted-foreground">{bracketOpen}</span>
        {collapsed && (
          <>
            <span className="text-muted-foreground text-xs ml-1">
              {itemCount} {itemCount === 1 ? "item" : "items"}
            </span>
            <span className="text-muted-foreground">{bracketClose}</span>
          </>
        )}
      </div>
      {!collapsed && (
        <>
          {entries.map(([key, val]) => (
            <JsonNode
              key={key}
              label={isArr ? undefined : key}
              value={val}
              defaultCollapsed={defaultCollapsed}
              depth={depth + 1}
            />
          ))}
          <div style={{ paddingLeft: depth * 16 }}>
            <span className="text-muted-foreground ml-4">{bracketClose}</span>
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// JsonViewer (root)
// ---------------------------------------------------------------------------

export default function JsonViewer({
  data,
  defaultCollapsed = false,
}: JsonViewerProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    const text = JSON.stringify(data, null, 2);
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [data]);

  return (
    <div className="space-y-2">
      {/* Copy button */}
      <div className="flex justify-end">
        <Button variant="ghost" size="sm" onClick={handleCopy} className="text-xs">
          {copied ? "Copied!" : "Copy JSON"}
        </Button>
      </div>

      {/* Tree */}
      <div className="font-mono text-sm leading-relaxed">
        <JsonNode
          value={data}
          defaultCollapsed={defaultCollapsed}
          depth={0}
        />
      </div>
    </div>
  );
}
