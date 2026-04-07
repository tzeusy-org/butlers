import { useState } from "react";

import { CardSkeleton } from "@/components/skeletons";
import RuntimeConfigCard from "@/components/butler-detail/RuntimeConfigCard";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useButlerConfig } from "@/hooks/use-butlers";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ButlerConfigTabProps {
  butlerName: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Render a TOML-like Record as a formatted key-value list.
 * Nested objects are indented and displayed recursively.
 */
function formatTomlValue(value: unknown, indent = 0): string {
  const prefix = "  ".repeat(indent);

  if (value === null || value === undefined) {
    return `${prefix}(empty)`;
  }

  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return `${prefix}${String(value)}`;
  }

  if (Array.isArray(value)) {
    if (value.length === 0) return `${prefix}[]`;
    return value.map((item) => formatTomlValue(item, indent)).join("\n");
  }

  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return `${prefix}{}`;
    return entries
      .map(([k, v]) => {
        if (typeof v === "object" && v !== null && !Array.isArray(v)) {
          return `${prefix}[${k}]\n${formatTomlValue(v, indent + 1)}`;
        }
        return `${prefix}${k} = ${JSON.stringify(v)}`;
      })
      .join("\n");
  }

  return `${prefix}${String(value)}`;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** Card displaying structured TOML with a Raw toggle. */
function TomlSection({ data }: { data: Record<string, unknown> }) {
  const [showRaw, setShowRaw] = useState(false);

  return (
    <Card>
      <CardHeader>
        <CardTitle>butler.toml</CardTitle>
        <CardAction>
          <Button
            variant={showRaw ? "secondary" : "outline"}
            size="xs"
            onClick={() => setShowRaw((v) => !v)}
          >
            {showRaw ? "Formatted" : "Raw"}
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        {showRaw ? (
          <pre className="overflow-auto rounded-md bg-muted p-4 text-xs font-mono whitespace-pre-wrap">
            {JSON.stringify(data, null, 2)}
          </pre>
        ) : (
          <pre className="overflow-auto rounded-md bg-muted p-4 text-xs font-mono whitespace-pre-wrap">
            {formatTomlValue(data)}
          </pre>
        )}
      </CardContent>
    </Card>
  );
}

/** Card displaying a markdown/text file content. */
function MarkdownSection({
  title,
  content,
}: {
  title: string;
  content: string | null;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {content !== null ? (
          <pre className="overflow-auto rounded-md bg-muted p-4 text-sm font-mono whitespace-pre-wrap">
            {content}
          </pre>
        ) : (
          <p className="text-sm text-muted-foreground">Not found</p>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function ConfigSkeleton() {
  return (
    <div className="space-y-6">
      <CardSkeleton lines={6} />
      <CardSkeleton lines={4} />
      <CardSkeleton lines={4} />
      <CardSkeleton lines={4} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// ButlerConfigTab
// ---------------------------------------------------------------------------

export default function ButlerConfigTab({ butlerName }: ButlerConfigTabProps) {
  const { data: configResponse, isLoading, isError, error } = useButlerConfig(butlerName);

  if (isLoading) {
    return <ConfigSkeleton />;
  }

  if (isError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Configuration</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-destructive">
            Failed to load configuration: {error instanceof Error ? error.message : "Unknown error"}
          </p>
        </CardContent>
      </Card>
    );
  }

  const config = configResponse?.data;

  if (!config) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Configuration</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">No configuration data available</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <RuntimeConfigCard butlerName={butlerName} />
      <TomlSection data={config.butler_toml} />
      <MarkdownSection title="CLAUDE.md" content={config.claude_md} />
      <MarkdownSection title="AGENTS.md" content={config.agents_md} />
      <MarkdownSection title="MANIFESTO.md" content={config.manifesto_md} />
    </div>
  );
}
