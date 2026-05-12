// ---------------------------------------------------------------------------
// ButlerConfigTab — bu-k55lg (epic bu-hdavr F.3)
//
// Config tab body for the butler detail page. Uses the 4-column panel-grid
// frame from finish-butler-detail-body-panel-grid.
//
// Layout:
//   Row 1: process (span=2)       | schedule (span=2)
//   Row 2: scopes and oauth (span=2) | integrations (span=2)
//   Below: collapsed accordion — butler.toml / CLAUDE.md / AGENTS.md / MANIFESTO.md
//
// Hooks:
//   useButler(name)        — process facts + schedules
//   useButlerModules(name) — enabled modules + oauth status (scopes/integrations)
//   useButlerConfig(name)  — butler.toml / CLAUDE.md / AGENTS.md / MANIFESTO.md
//
// Doctrine gates:
//   - No <RuntimeConfigCard> anywhere in the layout.
//   - No pid field anywhere.
//   - No raw oklch/hex literals.
//   - No em-dashes in JSX text.
//   - All timestamps via <Time>.
//   - Token-only chrome.
// ---------------------------------------------------------------------------

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { ErrorLine, KV, Panel } from "./atoms";
import { useButler, useButlerConfig, useButlerModules } from "@/hooks/use-butlers";
import type { ModuleStatus, ProcessFacts } from "@/api/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Format seconds into a human-readable duration (e.g. "3d 4h", "12m", "45s"). */
function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  if (hours < 24) return remainingMinutes > 0 ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
  const days = Math.floor(hours / 24);
  const remainingHours = hours % 24;
  return remainingHours > 0 ? `${days}d ${remainingHours}h` : `${days}d`;
}

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
// Panel: Process
// ---------------------------------------------------------------------------

interface ProcessPanelBodyProps {
  processFacts: ProcessFacts | null | undefined;
  isLoading: boolean;
}

function ProcessPanelBody({ processFacts, isLoading }: ProcessPanelBodyProps) {
  if (isLoading) {
    return (
      <div className="space-y-2" data-testid="panel-process-loading">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-4 w-56" />
      </div>
    );
  }
  const unavailable = "--";
  return (
    <div data-testid="panel-process-content">
      <KV k="container" v={processFacts?.container_name ?? unavailable} mono />
      <KV k="port" v={String(processFacts?.port ?? unavailable)} mono />
      <KV
        k="registered"
        v={
          processFacts?.registered_duration_seconds != null
            ? formatDuration(processFacts.registered_duration_seconds)
            : unavailable
        }
        mono
      />
      <KV k="config" v={processFacts?.config_path ?? unavailable} mono />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel: Schedule
// ---------------------------------------------------------------------------

interface ScheduleEntry {
  name: string;
  cron: string;
  next_run_at?: string | null;
}

interface SchedulePanelBodyProps {
  schedules: ScheduleEntry[];
  isLoading: boolean;
}

function SchedulePanelBody({ schedules, isLoading }: SchedulePanelBodyProps) {
  if (isLoading) {
    return (
      <div className="space-y-2" data-testid="panel-schedule-loading">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-4 w-32" />
      </div>
    );
  }

  if (schedules.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="panel-schedule-empty">
        No schedules.
      </p>
    );
  }

  return (
    <ul className="space-y-2" data-testid="panel-schedule-list">
      {schedules.map((s) => (
        <li
          key={s.name}
          className="flex flex-col gap-0.5 py-1.5 border-b border-border/40 last:border-b-0"
        >
          <span className="text-xs font-medium text-foreground truncate">{s.name}</span>
          <span className="font-mono text-xs text-muted-foreground">{s.cron}</span>
          {s.next_run_at ? (
            <span className="text-xs text-muted-foreground">
              Next: <Time value={s.next_run_at} mode="relative" />
            </span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Panel: Scopes and OAuth
// ---------------------------------------------------------------------------

/** Map oauth_status to a compact badge. */
function OAuthStatusBadge({ status }: { status: string | null | undefined }) {
  if (status === "granted") {
    return (
      <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90 text-xs">
        authorized
      </Badge>
    );
  }
  if (status === "reauth_needed") {
    return (
      <Badge variant="destructive" className="text-xs">
        reauth needed
      </Badge>
    );
  }
  if (status === "not_configured") {
    return (
      <Badge variant="secondary" className="text-xs">
        not required
      </Badge>
    );
  }
  // No oauth_status field present — show "not required" as default
  return (
    <Badge variant="secondary" className="text-xs">
      not required
    </Badge>
  );
}

interface ScopesOauthPanelBodyProps {
  modules: ModuleStatus[];
  isLoading: boolean;
  isError: boolean;
}

function ScopesOauthPanelBody({ modules, isLoading, isError }: ScopesOauthPanelBodyProps) {
  if (isLoading) {
    return (
      <div className="space-y-2" data-testid="panel-scopes-loading">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-4 w-32" />
      </div>
    );
  }

  if (isError) {
    return <ErrorLine>Could not load module OAuth status.</ErrorLine>;
  }

  if (modules.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="panel-scopes-empty">
        No modules with OAuth.
      </p>
    );
  }

  return (
    <ul className="space-y-1" data-testid="panel-scopes-list">
      {modules.map((mod) => (
        <li
          key={mod.name}
          className="flex items-center justify-between gap-2 py-1 border-b border-border/40 last:border-b-0"
        >
          <span className="text-xs text-foreground truncate">{mod.name}</span>
          <OAuthStatusBadge status={mod.oauth_status} />
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Panel: Integrations
// ---------------------------------------------------------------------------

interface IntegrationsPanelBodyProps {
  modules: ModuleStatus[];
  isLoading: boolean;
  isError: boolean;
}

function IntegrationsPanelBody({ modules, isLoading, isError }: IntegrationsPanelBodyProps) {
  if (isLoading) {
    return (
      <div className="space-y-2" data-testid="panel-integrations-loading">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-4 w-32" />
      </div>
    );
  }

  if (isError) {
    return <ErrorLine>Could not load integrations.</ErrorLine>;
  }

  const enabled = modules.filter((m) => m.enabled);

  if (enabled.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="panel-integrations-empty">
        No modules enabled.
      </p>
    );
  }

  return (
    <div className="flex flex-wrap gap-1.5" data-testid="panel-integrations-list">
      {enabled.map((mod) => (
        <Badge key={mod.name} variant="secondary" className="text-xs">
          {mod.name}
        </Badge>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Accordion item helpers
// ---------------------------------------------------------------------------

/** Accordion item using native <details>/<summary> for zero dependencies. */
function AccordionItem({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <details
      className="group border-b border-border/60 last:border-b-0"
      data-testid="accordion-item"
    >
      <summary className="flex cursor-pointer items-center justify-between px-4 py-3 text-xs font-mono uppercase tracking-wider text-muted-foreground hover:text-foreground select-none list-none">
        <span>{title}</span>
        {/* Chevron indicator */}
        <span
          className="transition-transform group-open:rotate-180 text-muted-foreground"
          aria-hidden="true"
        >
          &#x25BE;
        </span>
      </summary>
      <div className="px-4 pb-4" data-testid="accordion-item-content">
        {children}
      </div>
    </details>
  );
}

/** Butler.toml accordion item — preserves the Formatted/Raw toggle. */
function TomlAccordionItem({ data }: { data: Record<string, unknown> | null | undefined }) {
  const [showRaw, setShowRaw] = useState(false);

  return (
    <AccordionItem title="butler.toml">
      <div className="flex justify-end mb-2">
        <Button
          variant={showRaw ? "secondary" : "outline"}
          size="xs"
          onClick={() => setShowRaw((v) => !v)}
          data-testid="toml-format-toggle"
        >
          {showRaw ? "Formatted" : "Raw"}
        </Button>
      </div>
      {data == null ? (
        <p className="text-sm text-muted-foreground">Not found</p>
      ) : showRaw ? (
        <pre className="overflow-auto rounded-md bg-muted p-4 text-xs font-mono whitespace-pre-wrap">
          {JSON.stringify(data, null, 2)}
        </pre>
      ) : (
        <pre className="overflow-auto rounded-md bg-muted p-4 text-xs font-mono whitespace-pre-wrap">
          {formatTomlValue(data)}
        </pre>
      )}
    </AccordionItem>
  );
}

/** Markdown/text accordion item. */
function MarkdownAccordionItem({
  title,
  content,
}: {
  title: string;
  content: string | null;
}) {
  return (
    <AccordionItem title={title}>
      {content !== null ? (
        <pre className="overflow-auto rounded-md bg-muted p-4 text-sm font-mono whitespace-pre-wrap">
          {content}
        </pre>
      ) : (
        <p className="text-sm text-muted-foreground">Not found</p>
      )}
    </AccordionItem>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton (panel-grid shape)
// ---------------------------------------------------------------------------

function ConfigSkeleton() {
  return (
    <div
      className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 border-t border-l border-border/60"
      data-testid="config-skeleton"
    >
      {/* process span=2 */}
      <div className="col-span-1 sm:col-span-2 border-r border-b border-border/60 p-4 space-y-3">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-4 w-56" />
      </div>
      {/* schedule span=2 */}
      <div className="col-span-1 sm:col-span-2 border-r border-b border-border/60 p-4 space-y-3">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-4 w-40" />
      </div>
      {/* scopes span=2 */}
      <div className="col-span-1 sm:col-span-2 border-r border-b border-border/60 p-4 space-y-2">
        <Skeleton className="h-5 w-24" />
        <Skeleton className="h-5 w-32" />
      </div>
      {/* integrations span=2 */}
      <div className="col-span-1 sm:col-span-2 border-r border-b border-border/60 p-4 space-y-2">
        <Skeleton className="h-5 w-20" />
        <Skeleton className="h-5 w-28" />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ButlerConfigTab
// ---------------------------------------------------------------------------

interface ButlerConfigTabProps {
  butlerName: string;
}

export default function ButlerConfigTab({ butlerName }: ButlerConfigTabProps) {
  const {
    data: butlerResponse,
    isLoading: butlerLoading,
  } = useButler(butlerName);

  const {
    data: modulesResponse,
    isLoading: modulesLoading,
    isError: modulesError,
  } = useButlerModules(butlerName);

  const {
    data: configResponse,
    isLoading: configLoading,
    isError: configError,
    error: configErrorObj,
  } = useButlerConfig(butlerName);

  const isLoading = butlerLoading || modulesLoading || configLoading;

  if (isLoading && !butlerResponse && !modulesResponse && !configResponse) {
    return <ConfigSkeleton />;
  }

  const butler = butlerResponse?.data;
  const modules = modulesResponse?.data ?? [];
  const config = configResponse?.data;

  const processFacts = butler?.process_facts ?? null;
  const schedules = butler?.schedules ?? [];

  return (
    <div data-testid="butler-config-tab">
      {/* 2x2 Panel grid */}
      <div
        className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 border-t border-l border-border/60"
        data-testid="config-panel-grid"
      >
        {/* Row 1: process (span=2) | schedule (span=2) */}
        <Panel title="process" span={2} testId="panel-process">
          <ProcessPanelBody
            processFacts={processFacts}
            isLoading={butlerLoading}
          />
        </Panel>

        <Panel title="schedule" span={2} testId="panel-schedule">
          <SchedulePanelBody
            schedules={schedules}
            isLoading={butlerLoading}
          />
        </Panel>

        {/* Row 2: scopes and oauth (span=2) | integrations (span=2) */}
        <Panel title="scopes and oauth" span={2} testId="panel-scopes">
          <ScopesOauthPanelBody
            modules={modules}
            isLoading={modulesLoading}
            isError={modulesError}
          />
        </Panel>

        <Panel title="integrations" span={2} testId="panel-integrations">
          <IntegrationsPanelBody
            modules={modules}
            isLoading={modulesLoading}
            isError={modulesError}
          />
        </Panel>
      </div>

      {/* Accordion block — collapsed by default */}
      <div
        className="border-x border-b border-border/60 mt-0"
        data-testid="config-accordion"
      >
        {configError ? (
          <div className="p-4">
            <ErrorLine>
              Failed to load config files:{" "}
              {configErrorObj instanceof Error ? configErrorObj.message : "Unknown error"}
            </ErrorLine>
          </div>
        ) : (
          <>
            <TomlAccordionItem data={config?.butler_toml} />
            <MarkdownAccordionItem title="CLAUDE.md" content={config?.claude_md ?? null} />
            <MarkdownAccordionItem title="AGENTS.md" content={config?.agents_md ?? null} />
            <MarkdownAccordionItem title="MANIFESTO.md" content={config?.manifesto_md ?? null} />
          </>
        )}
      </div>
    </div>
  );
}
