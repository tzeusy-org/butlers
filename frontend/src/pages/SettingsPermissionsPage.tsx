/**
 * Settings Permissions Page — /settings/permissions
 *
 * Implements §6.8 of the settings-redesign OpenSpec:
 *  - Permissions × Butlers matrix with inherited (dim) vs explicit (foreground) cells
 *  - Cell-flip modal requires non-empty reason before submit
 *  - Audit reel — last 15 entries from GET /api/audit-log?limit=15
 *  - Data ops sub-grid: export (scope picker → signed URL), wipe (phrase input)
 *  - Webhooks table: list, add, edit, test, delete
 */

import { useEffect, useState } from "react";

import {
  AlertTriangle,
  CheckCircle,
  ExternalLink,
  Loader2,
  Plus,
  RefreshCw,
  Shield,
  Trash2,
  Webhook,
  XCircle,
} from "lucide-react";

import { useAuditLog } from "@/hooks/use-audit-log";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { toast } from "sonner";

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

async function deleteWipe(phrase: string): Promise<void> {
  const resp = await fetch("/api/data/wipe", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ phrase }),
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    const err = body?.detail?.error ?? `DELETE /api/data/wipe failed: ${resp.status}`;
    throw new Error(err);
  }
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
          <DialogTitle>
            {currentGranted ? "Revoke" : "Grant"} permission
          </DialogTitle>
          <DialogDescription>
            <span className="font-mono text-sm">
              {butler} · {perm}
            </span>
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 py-2">
          <Label htmlFor="flip-reason">Reason (required)</Label>
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
      <p className="text-sm text-muted-foreground italic">
        No permissions or butlers found.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="text-sm border-collapse min-w-max">
        <thead>
          <tr>
            <th className="text-left font-mono text-xs text-muted-foreground p-2 border-b">
              permission
            </th>
            {matrix.butlers.map((b) => (
              <th
                key={b}
                className="font-mono text-xs text-muted-foreground p-2 border-b text-center"
              >
                {b}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {matrix.permissions.map((perm) => (
            <tr key={perm} className="border-b last:border-0">
              <td className="font-mono text-xs p-2 pr-6 whitespace-nowrap">{perm}</td>
              {matrix.butlers.map((butler) => {
                const cell = matrix.cells[butler]?.[perm];
                const inherited = cell?.inherited ?? true;
                const granted = cell?.granted ?? false;

                return (
                  <td key={butler} className="p-2 text-center">
                    <button
                      onClick={() => onCellFlip(butler, perm, granted)}
                      className={[
                        "w-12 rounded px-2 py-0.5 text-xs font-mono transition-colors",
                        inherited
                          ? "opacity-40 cursor-default"
                          : granted
                            ? "bg-green-100 text-green-800 hover:bg-green-200 dark:bg-green-900 dark:text-green-200"
                            : "bg-red-100 text-red-800 hover:bg-red-200 dark:bg-red-900 dark:text-red-200",
                      ].join(" ")}
                      title={cell?.reason ?? undefined}
                    >
                      {granted ? "on" : "off"}
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

function AuditReelSection() {
  const { data, isLoading } = useAuditLog({ limit: 15 });

  return (
    <div className="space-y-2">
      {isLoading && (
        <div className="space-y-1">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-6 w-full" />
          ))}
        </div>
      )}
      {!isLoading && (
        <>
          {(data?.data ?? []).map((entry) => (
            <div key={entry.id} className="flex gap-3 text-sm">
              <span className="font-mono text-xs text-muted-foreground whitespace-nowrap">
                {new Date(entry.created_at).toLocaleTimeString()}
              </span>
              <span className="text-xs text-muted-foreground">{entry.butler}</span>
              <span className="font-serif text-xs flex-1">{entry.operation}</span>
            </div>
          ))}
          {(data?.data ?? []).length === 0 && (
            <p className="text-xs text-muted-foreground italic">No recent audit entries.</p>
          )}
          <a
            href="/audit-log"
            className="text-xs text-primary hover:underline inline-flex items-center gap-1 mt-2"
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

const WIPE_PHRASE = "WIPE EVERYTHING IRREVERSIBLY";

function DataOpsSection() {
  const [exportScope, setExportScope] = useState<ExportScope>("all");
  const [exportLoading, setExportLoading] = useState(false);
  const [exportUrl, setExportUrl] = useState<string | null>(null);

  const [wipePhrase, setWipePhrase] = useState("");
  const [wipeLoading, setWipeLoading] = useState(false);
  const [wipeConfirmOpen, setWipeConfirmOpen] = useState(false);

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

  async function handleWipe() {
    setWipeLoading(true);
    try {
      await deleteWipe(wipePhrase);
      toast.success("All data wiped.");
      setWipePhrase("");
      setWipeConfirmOpen(false);
    } catch (err) {
      toast.error(`Wipe failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setWipeLoading(false);
    }
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
      {/* Export */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Export data</CardTitle>
          <CardDescription>Download an encrypted zip of your data.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
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
        </CardContent>
      </Card>

      {/* Wipe */}
      <Card className="border-destructive/40">
        <CardHeader>
          <CardTitle className="text-base text-destructive flex items-center gap-2">
            <AlertTriangle className="h-4 w-4" />
            Wipe all data
          </CardTitle>
          <CardDescription>
            Permanently deletes every butler schema and all cross-butler tables. This cannot be undone.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="wipe-phrase" className="text-xs font-mono">
              Type to confirm: <span className="text-destructive">{WIPE_PHRASE}</span>
            </Label>
            <Input
              id="wipe-phrase"
              value={wipePhrase}
              onChange={(e) => setWipePhrase(e.target.value)}
              placeholder="Type the phrase exactly"
              className="font-mono text-xs"
            />
          </div>
          <Button
            variant="destructive"
            disabled={wipePhrase !== WIPE_PHRASE || wipeLoading}
            onClick={() => setWipeConfirmOpen(true)}
          >
            <Trash2 className="mr-2 h-4 w-4" />
            Wipe everything
          </Button>
        </CardContent>
      </Card>

      {/* Wipe confirmation dialog */}
      <Dialog open={wipeConfirmOpen} onOpenChange={setWipeConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="text-destructive">Confirm wipe</DialogTitle>
            <DialogDescription>
              This will permanently delete all butler data. There is no undo. Are you sure?
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setWipeConfirmOpen(false)}
              disabled={wipeLoading}
            >
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleWipe} disabled={wipeLoading}>
              {wipeLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Yes, wipe everything
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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
          <DialogTitle>Add webhook</DialogTitle>
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
    <div className="space-y-3">
      <div className="flex justify-end">
        <Button size="sm" variant="outline" onClick={() => setAddOpen(true)}>
          <Plus className="mr-1 h-4 w-4" />
          Add webhook
        </Button>
      </div>

      {loading ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : webhooks.length === 0 ? (
        <p className="text-sm text-muted-foreground italic">No webhooks registered.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="font-mono text-xs">Endpoint</TableHead>
              <TableHead className="font-mono text-xs">Events</TableHead>
              <TableHead className="font-mono text-xs">Last test</TableHead>
              <TableHead className="font-mono text-xs text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {webhooks.map((wh) => (
              <TableRow key={wh.id}>
                <TableCell className="font-mono text-xs max-w-xs truncate">
                  {wh.endpoint}
                </TableCell>
                <TableCell className="text-xs">
                  {wh.events.length > 0 ? wh.events.join(", ") : "—"}
                </TableCell>
                <TableCell className="text-xs">
                  {wh.last_test_at ? (
                    <span className="flex items-center gap-1">
                      {wh.last_test_ok ? (
                        <CheckCircle className="h-3 w-3 text-green-600" />
                      ) : (
                        <XCircle className="h-3 w-3 text-red-600" />
                      )}
                      {new Date(wh.last_test_at).toLocaleString()}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => handleTest(wh.id)}
                      disabled={testingId === wh.id}
                      title="Test webhook"
                    >
                      {testingId === wh.id ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <RefreshCw className="h-3 w-3" />
                      )}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => handleDelete(wh.id)}
                      disabled={deletingId === wh.id}
                      title="Delete webhook"
                      className="text-destructive hover:text-destructive"
                    >
                      {deletingId === wh.id ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <Trash2 className="h-3 w-3" />
                      )}
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
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
    <div className="max-w-5xl mx-auto space-y-8 p-6">
      {/* Page header */}
      <div>
        <p className="font-mono text-xs text-muted-foreground uppercase tracking-wider">
          system · permissions
        </p>
        <h1 className="text-2xl font-semibold mt-1">Permissions & data</h1>
      </div>

      {/* Permissions matrix */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Shield className="h-4 w-4" />
            Permissions matrix
          </CardTitle>
          <CardDescription>
            Flip cells to grant or revoke per-butler permissions. A reason is required for every
            change and is recorded in the audit log.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {matrixLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-6 w-full" />
              ))}
            </div>
          ) : matrix ? (
            <PermissionsMatrixSection matrix={matrix} onCellFlip={handleCellFlip} />
          ) : (
            <p className="text-sm text-muted-foreground italic">Failed to load matrix.</p>
          )}
        </CardContent>
      </Card>

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
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Audit reel</CardTitle>
          <CardDescription>Last 15 entries from the audit log.</CardDescription>
        </CardHeader>
        <CardContent>
          <AuditReelSection />
        </CardContent>
      </Card>

      {/* Data ops */}
      <div>
        <h2 className="text-lg font-medium mb-4">Data operations</h2>
        <DataOpsSection />
      </div>

      {/* Webhooks */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Webhook className="h-4 w-4" />
            Webhooks
          </CardTitle>
          <CardDescription>
            Outbound webhook registrations. Events are signed with HMAC-SHA256.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <WebhooksSection />
        </CardContent>
      </Card>
    </div>
  );
}
