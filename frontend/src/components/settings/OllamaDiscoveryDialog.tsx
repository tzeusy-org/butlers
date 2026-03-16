import { useState } from "react";
import { Loader2, Search } from "lucide-react";
import { toast } from "sonner";

import type { ComplexityTier, OllamaDiscoveredModel } from "@/api/types.ts";
import { COMPLEXITY_TIERS, complexityLabel } from "@/components/general/ComplexityBadge.tsx";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useDiscoverOllamaModels,
  useImportOllamaModels,
} from "@/hooks/use-providers.ts";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatBytes(bytes: number | null): string {
  if (bytes === null || bytes === undefined) return "--";
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(0)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb.toFixed(1)} MB`;
  const gb = mb / 1024;
  return `${gb.toFixed(1)} GB`;
}

interface SelectedModel {
  name: string;
  complexity_tier: ComplexityTier;
}

// ---------------------------------------------------------------------------
// OllamaDiscoveryDialog
// ---------------------------------------------------------------------------

interface OllamaDiscoveryDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function OllamaDiscoveryDialog({
  open,
  onOpenChange,
}: OllamaDiscoveryDialogProps) {
  const discoverMutation = useDiscoverOllamaModels();
  const importMutation = useImportOllamaModels();

  const [models, setModels] = useState<OllamaDiscoveredModel[]>([]);
  const [selection, setSelection] = useState<Map<string, ComplexityTier>>(new Map());
  const [discovered, setDiscovered] = useState(false);

  function handleDiscover() {
    setModels([]);
    setSelection(new Map());
    setDiscovered(false);
    discoverMutation.mutate(undefined, {
      onSuccess: (resp) => {
        const data = resp.data;
        setModels(data);
        setDiscovered(true);
        // Default: select all with "medium" tier
        const initial = new Map<string, ComplexityTier>();
        data.forEach((m) => initial.set(m.name, "medium"));
        setSelection(initial);
      },
      onError: (err) => {
        toast.error(
          `Discovery failed: ${err instanceof Error ? err.message : "Unknown error"}`,
        );
      },
    });
  }

  function toggleModel(name: string) {
    setSelection((prev) => {
      const next = new Map(prev);
      if (next.has(name)) {
        next.delete(name);
      } else {
        next.set(name, "medium");
      }
      return next;
    });
  }

  function setTier(name: string, tier: ComplexityTier) {
    setSelection((prev) => {
      const next = new Map(prev);
      next.set(name, tier);
      return next;
    });
  }

  function handleImport() {
    const items: SelectedModel[] = [];
    selection.forEach((tier, name) => {
      items.push({ name, complexity_tier: tier });
    });
    if (items.length === 0) {
      toast.error("No models selected");
      return;
    }
    importMutation.mutate(
      { models: items },
      {
        onSuccess: (resp) => {
          const data = resp.data;
          toast.success(
            `Imported ${data.imported} model(s)` +
              (data.skipped > 0 ? `, skipped ${data.skipped}` : ""),
          );
          onOpenChange(false);
        },
        onError: (err) => {
          toast.error(
            `Import failed: ${err instanceof Error ? err.message : "Unknown error"}`,
          );
        },
      },
    );
  }

  function handleOpenChange(val: boolean) {
    if (!val) {
      // Reset state on close
      setModels([]);
      setSelection(new Map());
      setDiscovered(false);
    }
    onOpenChange(val);
  }

  const selectedCount = selection.size;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-2xl max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>Discover Ollama Models</DialogTitle>
          <DialogDescription>
            Fetch available models from your Ollama instance and import them into the model catalog.
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-auto space-y-4">
          {!discovered && !discoverMutation.isPending && (
            <div className="flex flex-col items-center justify-center py-8 space-y-3">
              <Search className="h-10 w-10 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">
                Click "Discover" to fetch models from the configured Ollama instance.
              </p>
              <Button onClick={handleDiscover}>
                <Search className="h-4 w-4 mr-2" />
                Discover Models
              </Button>
            </div>
          )}

          {discoverMutation.isPending && (
            <div className="flex items-center justify-center py-8 gap-2">
              <Loader2 className="h-5 w-5 animate-spin" />
              <span className="text-sm text-muted-foreground">Discovering models...</span>
            </div>
          )}

          {discovered && models.length === 0 && (
            <div className="text-center py-8">
              <p className="text-sm text-muted-foreground">
                No models found on the Ollama instance.
              </p>
              <Button variant="outline" size="sm" className="mt-3" onClick={handleDiscover}>
                Retry
              </Button>
            </div>
          )}

          {discovered && models.length > 0 && (
            <>
              <div className="flex items-center justify-between">
                <p className="text-sm text-muted-foreground">
                  {models.length} model(s) found, {selectedCount} selected
                </p>
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      const all = new Map<string, ComplexityTier>();
                      models.forEach((m) => all.set(m.name, selection.get(m.name) ?? "medium"));
                      setSelection(all);
                    }}
                  >
                    Select all
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setSelection(new Map())}
                  >
                    Select none
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleDiscover}
                    disabled={discoverMutation.isPending}
                  >
                    Refresh
                  </Button>
                </div>
              </div>

              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-10"></TableHead>
                    <TableHead>Model</TableHead>
                    <TableHead>Size</TableHead>
                    <TableHead>Complexity Tier</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {models.map((model) => {
                    const isSelected = selection.has(model.name);
                    return (
                      <TableRow key={model.name}>
                        <TableCell>
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={() => toggleModel(model.name)}
                            className="h-4 w-4 rounded border-input"
                          />
                        </TableCell>
                        <TableCell>
                          <div className="space-y-0.5">
                            <p className="text-sm font-medium">{model.name}</p>
                            {model.digest && (
                              <p className="text-xs text-muted-foreground font-mono truncate max-w-48">
                                {model.digest.substring(0, 12)}
                              </p>
                            )}
                          </div>
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline">{formatBytes(model.size)}</Badge>
                        </TableCell>
                        <TableCell>
                          {isSelected ? (
                            <Select
                              value={selection.get(model.name) ?? "medium"}
                              onValueChange={(v) =>
                                setTier(model.name, v as ComplexityTier)
                              }
                            >
                              <SelectTrigger className="w-32 h-8">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {COMPLEXITY_TIERS.map((tier) => (
                                  <SelectItem key={tier} value={tier}>
                                    {complexityLabel(tier)}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          ) : (
                            <span className="text-xs text-muted-foreground">--</span>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={importMutation.isPending}
          >
            Cancel
          </Button>
          {discovered && models.length > 0 && (
            <Button
              onClick={handleImport}
              disabled={selectedCount === 0 || importMutation.isPending}
            >
              {importMutation.isPending
                ? "Importing..."
                : `Import ${selectedCount} Model${selectedCount !== 1 ? "s" : ""}`}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default OllamaDiscoveryDialog;
