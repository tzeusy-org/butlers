/**
 * Settings Permissions Page — /settings/permissions
 *
 * Implements §6.8 of the settings-redesign OpenSpec:
 *  - Permissions × Butlers matrix with inherited (dim) vs explicit (foreground) cells
 *  - Cell-flip modal requires non-empty reason before submit
 *  - Audit reel — last 15 entries from GET /api/audit-log?limit=15
 *  - Data ops sub-grid: export (scope picker → signed URL), wipe (phrase input)
 *  - Webhooks table: list, add, edit, test, delete
 *
 * Design language: Dispatch. No card chrome, no word-badges — state is a
 * {dot, glyph, colour} only. Display weight 500 (never 700). Numerals are
 * tabular. Mirrors SettingsConsolePage / SettingsModelsPage and the shared
 * atoms in components/butler-detail/atoms.tsx.
 *
 * CSS: .attention-row[data-tone="red"] from frontend/src/index.css — the only
 * state-color-on-background pattern, reserved here for the data-wipe danger zone.
 */

import { useEffect, useState } from "react";

import { ExternalLink, Loader2 } from "lucide-react";

import { useAuditLog } from "@/hooks/use-audit-log";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PermissionCell {
  granted: boolean;
  reason: string | null;
  updated_at: string | null;
  inherited: boolean;
}

interface PermissionsMatrix {
  butlers: string[];
  permissions: string[];
  cells: Record<string, Record<string, PermissionCell>>;
}

interface WebhookRow {
  id: string;
  endpoint: string;
  events: string[];
  enabled: boolean;
  last_test_at: string | null;
  last_test_ok: boolean | null;
  retry_policy: { max_attempts: number; backoff_seconds: number };
  created_at: string;
  updated_at: string;
}

type ExportScope = "all" | "memory" | "audit" | "config";

// ---------------------------------------------------------------------------
// Shared mono eyebrow — 10px uppercase, 0.14em tracking, muted
// ---------------------------------------------------------------------------

function Eyebrow({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <p
      className={cn(
        "font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground leading-none",
        className,
      )}
    >
      {children}
    </p>
  );
}

/**
 * Hairline section frame — a mono eyebrow header above a hairline-bordered body.
 * Replaces the old shadcn card chrome (no card components anywhere on this page).
 */
function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-col gap-1.5">
        <Eyebrow>{title}</Eyebrow>
        {description ? (
          <p className="text-xs text-muted-foreground leading-relaxed">{description}</p>
        ) : null}
      </div>
      {children}
    </section>
  );
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function fetchPermissions(): Promise<PermissionsMatrix> {
  const resp = await fetch("/api/permissions");
  if (!resp.ok) throw new Error(`GET /api/permissions failed: ${resp.status}`);
  const body = await resp.json();
  return body.data as PermissionsMatrix;
}

async function putPermission(
  butler: string,
  perm: string,
  granted: boolean,
  reason: string,
): Promise<void> {
  const resp = await fetch(`/api/permissions/${encodeURIComponent(butler)}/${encodeURIComponent(perm)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ granted, reason }),
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body?.detail?.error ?? `PUT failed: ${resp.status}`);
  }
}

async function fetchWebhooks(): Promise<WebhookRow[]> {
  const resp = await fetch("/api/webhooks");
  if (!resp.ok) throw new Error(`GET /api/webhooks failed: ${resp.status}`);
  const body = await resp.json();
  return body.data as WebhookRow[];
}

async function deleteWebhook(id: string): Promise<void> {
  const resp = await fetch(`/api/webhooks/${id}`, { method: "DELETE" });
  if (!resp.ok) throw new Error(`DELETE /api/webhooks/${id} failed: ${resp.status}`);
}

async function testWebhook(id: string): Promise<{ ok: boolean; status_code: number | null; latency_ms: number | null }> {
  const resp = await fetch(`/api/webhooks/${id}/test`, { method: "POST" });
  if (!resp.ok) throw new Error(`POST /api/webhooks/${id}/test failed: ${resp.status}`);
  const body = await resp.json();
  return body.data;
}

async function postExport(scope: ExportScope): Promise<{ signed_url: string; expires_at: string }> {
  const resp = await fetch("/api/data/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope }),
  });
  if (!resp.ok) throw new Error(`POST /api/data/export failed: ${resp.status}`);
  const body = await resp.json();
  return body.data;
}

async function createWebhook(
  endpoint: string,
  events: string[],
  secret?: string,
): Promise<WebhookRow> {
  const resp = await fetch("/api/webhooks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ endpoint, events, secret: secret || undefined }),
  });
  if (!resp.ok) throw new Error(`POST /api/webhooks failed: ${resp.status}`);
  const body = await resp.json();
  return body.data as WebhookRow;
}

// ---------------------------------------------------------------------------
// Permission Matrix Section
// ---------------------------------------------------------------------------

interface CellFlipModalProps {
  open: boolean;
  butler: string;
  perm: string;
  currentGranted: boolean;
  onConfirm: (reason: string) => Promise<void>;
  onClose: () => void;
}

function CellFlipModal({
  open,
  butler,
  perm,
  currentGranted,
  onConfirm,
  onClose,
}: CellFlipModalProps) {
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) setReason("");
  }, [open]);

  const isBlank = !reason.trim();

  async function handleSubmit() {
    if (isBlank) return;
    setSubmitting(true);
    try {
      await onConfirm(reason);
      onClose();
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="font-medium">
            {currentGranted ? "Revoke" : "Grant"} permission
          </DialogTitle>
          <DialogDescription>
            <span className="font-mono text-sm">
              {butler} · {perm}
            </span>
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 py-2">
          <Label
            htmlFor="flip-reason"
            className="font-mono text-[11px] uppercase tracking-widest"
          >
            Reason (required)
          </Label>
          <Input
            id="flip-reason"
            placeholder="Why are you changing this permission?"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            autoFocus
          />
          {isBlank && (
            <p className="text-xs text-muted-foreground">
              A non-empty reason is required before submitting.
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button
            variant={currentGranted ? "destructive" : "default"}
            disabled={isBlank || submitting}
            onClick={handleSubmit}
          >
            {submitting ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : null}
            {currentGranted ? "Revoke" : "Grant"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface PermissionsMatrixSectionProps {
  matrix: PermissionsMatrix;
  onCellFlip: (butler: string, perm: string, granted: boolean) => void;
}

function PermissionsMatrixSection({ matrix, onCellFlip }: PermissionsMatrixSectionProps) {
  if (matrix.butlers.length === 0 || matrix.permissions.length === 0) {
    return (
      <p className="text-sm italic font-serif text-muted-foreground">
        No permissions or butlers found.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto border-t border-l border-border/60">
      <table className="text-sm border-collapse min-w-max w-full">
        <thead>
          <tr>
            <th className="text-left font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground px-3 py-2 border-r border-b border-border/60">
              permission
            </th>
            {matrix.butlers.map((b) => (
              <th
                key={b}
                className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground px-3 py-2 border-r border-b border-border/60 text-center"
              >
                {b}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {matrix.permissions.map((perm) => (
            <tr key={perm}>
              <td className="font-mono text-xs px-3 py-2 pr-6 whitespace-nowrap border-r border-b border-border/60">
                {perm}
              </td>
              {matrix.butlers.map((butler) => {
                const cell = matrix.cells[butler]?.[perm];
                const inherited = cell?.inherited ?? true;
                const granted = cell?.granted ?? false;

                return (
                  <td
                    key={butler}
                    className="px-3 py-2 text-center border-r border-b border-border/60"
                  >
                    <button
                      onClick={() => onCellFlip(butler, perm, granted)}
                      className={cn(
                        "inline-flex h-6 w-6 items-center justify-center rounded-full font-mono text-xs leading-none transition-colors",
                        inherited
                          ? "opacity-40 cursor-default"
                          : granted
                            ? "text-[var(--green)] hover:bg-muted/40"
                            : "text-muted-foreground hover:bg-muted/40",
                      )}
                      title={cell?.reason ?? undefined}
                      data-testid={`perm-cell-${butler}-${perm}`}
                      aria-label={`${butler} ${perm}: ${granted ? "granted" : "denied"}${inherited ? " (inherited)" : ""}`}
                    >
                      {granted ? "●" : "○"}
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Audit Reel Section
// ---------------------------------------------------------------------------

/** Heuristic: destructive actions read in red; everything else stays neutral. */
function isDestructiveAction(action: string): boolean {
  return /\b(revoke|delete|wipe|remove|disable|destroy)\b/i.test(action);
}

function AuditReelSection() {
  const { data, isLoading } = useAuditLog({ limit: 15 });
  const entries = data?.data ?? [];

  return (
    <div className="flex flex-col gap-0 border-t border-l border-border/60">
      {isLoading && (
        <div className="flex flex-col gap-0">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="border-r border-b border-border/60 px-4 py-2">
              <Skeleton className="h-5 w-full" />
            </div>
          ))}
        </div>
      )}
      {!isLoading && (
        <>
          {entries.map((entry) => (
            <div
              key={entry.id}
              className="flex items-baseline gap-3 border-r border-b border-border/60 px-4 py-2 text-sm"
            >
              <span className="font-mono text-xs tabular-nums text-muted-foreground whitespace-nowrap">
                {new Date(entry.ts).toLocaleTimeString()}
              </span>
              <span className="font-mono text-xs text-muted-foreground whitespace-nowrap">
                {entry.actor}
              </span>
              <span
                className={cn(
                  "font-serif text-sm flex-1 min-w-0",
                  isDestructiveAction(entry.action) && "text-[var(--red)]",
                )}
              >
                {entry.action}
              </span>
            </div>
          ))}
          {entries.length === 0 && (
            <div className="border-r border-b border-border/60 px-4 py-3">
              <p className="font-serif italic text-sm text-muted-foreground">
                No recent audit entries.
              </p>
            </div>
          )}
          <a
            href="/audit-log"
            className="border-r border-b border-border/60 px-4 py-2 font-mono text-[11px] uppercase tracking-wider text-muted-foreground hover:text-foreground transition-colors inline-flex items-center gap-1"
          >
            Full audit log <ExternalLink className="h-3 w-3" />
          </a>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Data Ops Section
// ---------------------------------------------------------------------------

function DataOpsSection() {
  const [exportScope, setExportScope] = useState<ExportScope>("all");
  const [exportLoading, setExportLoading] = useState(false);
  const [exportUrl, setExportUrl] = useState<string | null>(null);

  async function handleExport() {
    setExportLoading(true);
    setExportUrl(null);
    try {
      const result = await postExport(exportScope);
      setExportUrl(result.signed_url);
      toast.success("Export ready");
    } catch (err) {
      toast.error(`Export failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setExportLoading(false);
    }
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 border-t border-l border-border/60">
      {/* Export */}
      <div className="flex flex-col gap-3 border-r border-b border-border/60 px-4 py-4">
        <div className="flex flex-col gap-1.5">
          <Eyebrow>Export data</Eyebrow>
          <p className="text-xs text-muted-foreground leading-relaxed" data-testid="export-description">
            Download an AES-256-GCM encrypted export of your data. Decrypt using{" "}
            <span className="font-mono">DASHBOARD_EXPORT_ENCRYPTION_KEY</span>.
          </p>
        </div>
        <div className="flex gap-2">
          <Select
            value={exportScope}
            onValueChange={(v) => setExportScope(v as ExportScope)}
          >
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All data</SelectItem>
              <SelectItem value="memory">Memory</SelectItem>
              <SelectItem value="audit">Audit log</SelectItem>
              <SelectItem value="config">Config</SelectItem>
            </SelectContent>
          </Select>
          <Button onClick={handleExport} disabled={exportLoading} variant="outline">
            {exportLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Export"}
          </Button>
        </div>
        {exportUrl && (
          <a
            href={exportUrl}
            className="text-xs font-mono text-primary hover:underline break-all"
          >
            {exportUrl}
          </a>
        )}
      </div>

      {/* Wipe — temporarily disabled */}
      <div
        className="flex flex-col gap-3 border-r border-b border-border/60 px-4 py-4 opacity-50"
        data-testid="wipe-panel-disabled"
      >
        <div className="flex flex-col gap-1.5">
          <Eyebrow>Wipe all data</Eyebrow>
          <p className="text-xs text-muted-foreground leading-relaxed">
            Temporarily disabled — a safer implementation is in progress.
          </p>
        </div>
        <Button variant="destructive" disabled className="self-start">
          Wipe everything
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Webhooks Section
// ---------------------------------------------------------------------------

interface AddWebhookModalProps {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

function AddWebhookModal({ open, onClose, onCreated }: AddWebhookModalProps) {
  const [endpoint, setEndpoint] = useState("");
  const [events, setEvents] = useState("");
  const [secret, setSecret] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) {
      setEndpoint("");
      setEvents("");
      setSecret("");
    }
  }, [open]);

  async function handleCreate() {
    if (!endpoint.trim()) return;
    setSubmitting(true);
    try {
      const evtList = events
        .split(",")
        .map((e) => e.trim())
        .filter(Boolean);
      await createWebhook(endpoint.trim(), evtList, secret.trim() || undefined);
      toast.success("Webhook created");
      onCreated();
      onClose();
    } catch (err) {
      toast.error(`Create failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="font-medium">Add webhook</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 py-2">
          <div className="space-y-1">
            <Label htmlFor="wh-endpoint">Endpoint URL</Label>
            <Input
              id="wh-endpoint"
              placeholder="https://example.com/webhook"
              value={endpoint}
              onChange={(e) => setEndpoint(e.target.value)}
              autoFocus
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="wh-events">Events (comma-separated)</Label>
            <Input
              id="wh-events"
              placeholder="permission.set, data.export"
              value={events}
              onChange={(e) => setEvents(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="wh-secret">Secret (optional)</Label>
            <Input
              id="wh-secret"
              type="password"
              placeholder="Signing secret"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button
            disabled={!endpoint.trim() || submitting}
            onClick={handleCreate}
          >
            {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function WebhooksSection() {
  const [webhooks, setWebhooks] = useState<WebhookRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [addOpen, setAddOpen] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  async function reload() {
    try {
      const whs = await fetchWebhooks();
      setWebhooks(whs);
    } catch (err) {
      toast.error(`Failed to load webhooks: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void reload();
  }, []);

  async function handleTest(id: string) {
    setTestingId(id);
    try {
      const result = await testWebhook(id);
      if (result.ok) {
        toast.success(
          `Test passed — HTTP ${result.status_code} in ${result.latency_ms?.toFixed(0) ?? "?"}ms`,
        );
      } else {
        toast.error(`Test failed — HTTP ${result.status_code ?? "no response"}`);
      }
      await reload();
    } catch (err) {
      toast.error(`Test error: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setTestingId(null);
    }
  }

  async function handleDelete(id: string) {
    setDeletingId(id);
    try {
      await deleteWebhook(id);
      toast.success("Webhook deleted");
      setWebhooks((prev) => prev.filter((w) => w.id !== id));
    } catch (err) {
      toast.error(`Delete failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex justify-end">
        <button
          onClick={() => setAddOpen(true)}
          className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground hover:text-foreground transition-colors"
        >
          Add webhook →
        </button>
      </div>

      {loading ? (
        <div className="flex flex-col gap-0 border-t border-l border-border/60">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="border-r border-b border-border/60 px-4 py-3">
              <Skeleton className="h-5 w-full" />
            </div>
          ))}
        </div>
      ) : webhooks.length === 0 ? (
        <p className="text-sm italic font-serif text-muted-foreground">
          No webhooks registered.
        </p>
      ) : (
        <div className="overflow-x-auto border-t border-l border-border/60">
          <table className="text-sm border-collapse w-full min-w-max">
            <thead>
              <tr>
                <th className="text-left font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground px-4 py-2 border-r border-b border-border/60">
                  Endpoint
                </th>
                <th className="text-left font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground px-4 py-2 border-r border-b border-border/60">
                  Events
                </th>
                <th className="text-left font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground px-4 py-2 border-r border-b border-border/60">
                  Last test
                </th>
                <th className="text-right font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground px-4 py-2 border-r border-b border-border/60">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody>
              {webhooks.map((wh) => (
                <tr key={wh.id} data-testid={`webhook-row-${wh.id}`}>
                  <td className="font-mono text-xs max-w-xs truncate px-4 py-2 border-r border-b border-border/60">
                    {wh.endpoint}
                  </td>
                  <td className="text-xs px-4 py-2 border-r border-b border-border/60">
                    {wh.events.length > 0 ? wh.events.join(", ") : "—"}
                  </td>
                  <td
                    className="text-xs px-4 py-2 border-r border-b border-border/60"
                    data-testid={`webhook-last-test-${wh.id}`}
                  >
                    {wh.last_test_at ? (
                      <span className="flex items-center gap-1.5">
                        {wh.last_test_ok ? (
                          <span
                            className="h-1.5 w-1.5 rounded-full bg-[var(--green)] shrink-0"
                            data-testid="webhook-test-ok"
                            aria-hidden
                          />
                        ) : (
                          <span
                            className="h-1.5 w-1.5 rounded-full bg-[var(--red)] shrink-0"
                            data-testid="webhook-test-fail"
                            aria-hidden
                          />
                        )}
                        <span className="font-mono tabular-nums text-muted-foreground">
                          {new Date(wh.last_test_at).toLocaleString()}
                        </span>
                      </span>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </td>
                  <td className="text-right px-4 py-2 border-r border-b border-border/60">
                    <div className="flex justify-end gap-3">
                      <button
                        onClick={() => handleTest(wh.id)}
                        disabled={testingId === wh.id}
                        title="Test webhook"
                        data-testid={`webhook-test-${wh.id}`}
                        className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground hover:text-foreground transition-colors disabled:opacity-40 whitespace-nowrap"
                      >
                        {testingId === wh.id ? "Testing…" : "Test →"}
                      </button>
                      <button
                        onClick={() => handleDelete(wh.id)}
                        disabled={deletingId === wh.id}
                        title="Delete webhook"
                        data-testid={`webhook-delete-${wh.id}`}
                        className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground hover:text-[var(--red)] transition-colors disabled:opacity-40 whitespace-nowrap"
                      >
                        {deletingId === wh.id ? "Deleting…" : "Delete →"}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <AddWebhookModal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onCreated={reload}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function SettingsPermissionsPage() {
  const [matrix, setMatrix] = useState<PermissionsMatrix | null>(null);
  const [matrixLoading, setMatrixLoading] = useState(true);

  // Cell flip modal state
  const [flipModal, setFlipModal] = useState<{
    open: boolean;
    butler: string;
    perm: string;
    granted: boolean;
  } | null>(null);

  async function loadMatrix() {
    try {
      const m = await fetchPermissions();
      setMatrix(m);
    } catch (err) {
      toast.error(
        `Failed to load permissions: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setMatrixLoading(false);
    }
  }

  useEffect(() => {
    void loadMatrix();
  }, []);

  function handleCellFlip(butler: string, perm: string, granted: boolean) {
    setFlipModal({ open: true, butler, perm, granted });
  }

  async function handleFlipConfirm(reason: string) {
    if (!flipModal) return;
    const { butler, perm, granted } = flipModal;
    await putPermission(butler, perm, !granted, reason);
    toast.success(`Permission ${!granted ? "granted" : "revoked"}: ${butler} · ${perm}`);
    await loadMatrix();
  }

  return (
    <div className="max-w-5xl mx-auto space-y-8">
      {/* Page header */}
      <div>
        <Eyebrow className="mb-2">system · permissions</Eyebrow>
        <h1 className="text-3xl font-medium tracking-tight leading-tight">
          Permissions &amp; data
        </h1>
      </div>

      {/* Permissions matrix */}
      <Section
        title="Permissions matrix"
        description="Flip cells to grant or revoke per-butler permissions. A reason is required for every change and is recorded in the audit log."
      >
        {matrixLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-6 w-full" />
            ))}
          </div>
        ) : matrix ? (
          <PermissionsMatrixSection matrix={matrix} onCellFlip={handleCellFlip} />
        ) : (
          <p className="text-sm italic font-serif text-muted-foreground">
            Failed to load matrix.
          </p>
        )}
      </Section>

      {/* Cell flip modal */}
      {flipModal && (
        <CellFlipModal
          open={flipModal.open}
          butler={flipModal.butler}
          perm={flipModal.perm}
          currentGranted={flipModal.granted}
          onConfirm={handleFlipConfirm}
          onClose={() => setFlipModal(null)}
        />
      )}

      {/* Audit reel */}
      <Section title="Audit reel" description="Last 15 entries from the audit log.">
        <AuditReelSection />
      </Section>

      {/* Data ops */}
      <Section title="Data operations">
        <DataOpsSection />
      </Section>

      {/* Webhooks */}
      <Section
        title="Webhooks"
        description="Outbound webhook registrations. Events are signed with HMAC-SHA256."
      >
        <WebhooksSection />
      </Section>
    </div>
  );
}
