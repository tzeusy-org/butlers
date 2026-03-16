import { useState } from "react";
import { Loader2, Plug, Search, Trash2 } from "lucide-react";
import { toast } from "sonner";

import type { ProviderConfig, ProviderConfigCreate, ProviderConfigUpdate } from "@/api/types.ts";
import { Badge } from "@/components/ui/badge";
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
  useCreateProvider,
  useDeleteProvider,
  useProviders,
  useTestProviderConnectivity,
  useUpdateProvider,
} from "@/hooks/use-providers.ts";
import { OllamaDiscoveryDialog } from "@/components/settings/OllamaDiscoveryDialog.tsx";

// ---------------------------------------------------------------------------
// Provider type constants
// ---------------------------------------------------------------------------

const PROVIDER_TYPES = [
  { value: "ollama", label: "Ollama" },
] as const;

function providerTypeLabel(type: string): string {
  const found = PROVIDER_TYPES.find((p) => p.value === type);
  return found?.label ?? type;
}

// ---------------------------------------------------------------------------
// Add/Edit Provider Dialog
// ---------------------------------------------------------------------------

interface ProviderFormValues {
  provider_type: string;
  display_name: string;
  base_url: string;
  enabled: boolean;
}

function defaultFormValues(provider?: ProviderConfig | null): ProviderFormValues {
  return {
    provider_type: provider?.provider_type ?? "ollama",
    display_name: provider?.display_name ?? "",
    base_url: (provider?.config?.base_url as string) ?? "",
    enabled: provider?.enabled ?? true,
  };
}

interface ProviderFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  provider?: ProviderConfig | null;
  onSubmit: (values: ProviderConfigCreate | { providerType: string; body: ProviderConfigUpdate }) => void;
  isSubmitting: boolean;
  error?: string | null;
}

function ProviderFormDialog({
  open,
  onOpenChange,
  provider,
  onSubmit,
  isSubmitting,
  error,
}: ProviderFormDialogProps) {
  const isEdit = !!provider;
  const formKey = open ? (provider?.provider_type ?? "new") : "closed";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit Provider" : "Add Provider"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Update the provider configuration."
              : "Configure a new model provider."}
          </DialogDescription>
        </DialogHeader>
        <ProviderFormFields
          key={formKey}
          provider={provider}
          onSubmit={onSubmit}
          onCancel={() => onOpenChange(false)}
          isSubmitting={isSubmitting}
          error={error ?? null}
        />
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Inner form fields
// ---------------------------------------------------------------------------

function ProviderFormFields({
  provider,
  onSubmit,
  onCancel,
  isSubmitting,
  error,
}: {
  provider?: ProviderConfig | null;
  onSubmit: (values: ProviderConfigCreate | { providerType: string; body: ProviderConfigUpdate }) => void;
  onCancel: () => void;
  isSubmitting: boolean;
  error: string | null;
}) {
  const isEdit = !!provider;
  const [values, setValues] = useState<ProviderFormValues>(defaultFormValues(provider));
  const testMutation = useTestProviderConnectivity();

  const isValid =
    values.provider_type.trim() !== "" &&
    values.display_name.trim() !== "" &&
    values.base_url.trim() !== "";

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!isValid || isSubmitting) return;

    if (isEdit) {
      onSubmit({
        providerType: provider!.provider_type,
        body: {
          display_name: values.display_name.trim(),
          config: { base_url: values.base_url.trim() },
          enabled: values.enabled,
        },
      });
    } else {
      onSubmit({
        provider_type: values.provider_type,
        display_name: values.display_name.trim(),
        config: { base_url: values.base_url.trim() },
        enabled: values.enabled,
      } satisfies ProviderConfigCreate);
    }
  }

  function handleTest() {
    // Test connectivity using the existing saved config (works only in edit mode)
    if (!isEdit || !provider) {
      toast.error("Save the provider first before testing connectivity.");
      return;
    }
    testMutation.mutate(provider.provider_type, {
      onSuccess: (resp) => {
        const data = resp.data;
        if (data.success) {
          toast.success(`Connected to ${data.url} (${data.latency_ms}ms)`);
        } else {
          toast.error(`Connection failed: ${data.error}`);
        }
      },
      onError: (err) => {
        toast.error(
          `Test failed: ${err instanceof Error ? err.message : "Unknown error"}`,
        );
      },
    });
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="provider-type">Provider Type</Label>
        <Select
          value={values.provider_type}
          onValueChange={(v) =>
            setValues((prev) => ({
              ...prev,
              provider_type: v,
              display_name: prev.display_name || providerTypeLabel(v),
            }))
          }
          disabled={isEdit || isSubmitting}
        >
          <SelectTrigger id="provider-type">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PROVIDER_TYPES.map((pt) => (
              <SelectItem key={pt.value} value={pt.value}>
                {pt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-2">
        <Label htmlFor="provider-display-name">Display Name</Label>
        <Input
          id="provider-display-name"
          value={values.display_name}
          onChange={(e) => setValues((v) => ({ ...v, display_name: e.target.value }))}
          placeholder="e.g. Local Ollama"
          disabled={isSubmitting}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="provider-base-url">Base URL</Label>
        <Input
          id="provider-base-url"
          value={values.base_url}
          onChange={(e) => setValues((v) => ({ ...v, base_url: e.target.value }))}
          placeholder="e.g. http://localhost:11434"
          disabled={isSubmitting}
        />
        <p className="text-xs text-muted-foreground">
          The base URL for the provider API (e.g. Ollama's default is http://localhost:11434).
        </p>
      </div>

      <div className="flex items-center gap-2">
        <input
          id="provider-enabled"
          type="checkbox"
          checked={values.enabled}
          onChange={(e) => setValues((v) => ({ ...v, enabled: e.target.checked }))}
          className="h-4 w-4 rounded border-input"
          disabled={isSubmitting}
        />
        <Label htmlFor="provider-enabled" className="text-sm">
          Enabled
        </Label>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      {testMutation.data && (
        <p
          className={`text-sm ${
            testMutation.data.data.success
              ? "text-green-600 dark:text-green-400"
              : "text-destructive"
          }`}
        >
          {testMutation.data.data.success
            ? `Connected (${testMutation.data.data.latency_ms}ms)`
            : `Failed: ${testMutation.data.data.error}`}
        </p>
      )}

      <DialogFooter className="gap-2">
        {isEdit && (
          <Button
            type="button"
            variant="outline"
            onClick={handleTest}
            disabled={testMutation.isPending}
          >
            {testMutation.isPending ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin mr-1" />
                Testing...
              </>
            ) : (
              <>
                <Plug className="h-4 w-4 mr-1" />
                Test
              </>
            )}
          </Button>
        )}
        <Button type="button" variant="outline" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button type="submit" disabled={!isValid || isSubmitting}>
          {isSubmitting
            ? isEdit
              ? "Updating..."
              : "Creating..."
            : isEdit
              ? "Update"
              : "Add Provider"}
        </Button>
      </DialogFooter>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Delete confirmation dialog
// ---------------------------------------------------------------------------

function DeleteProviderDialog({
  provider,
  open,
  onOpenChange,
  onConfirm,
  isDeleting,
}: {
  provider: ProviderConfig | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  isDeleting: boolean;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete Provider?</DialogTitle>
          <DialogDescription>
            Are you sure you want to remove{" "}
            <strong>{provider?.display_name ?? provider?.provider_type}</strong>?
            This cannot be undone.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isDeleting}
          >
            Cancel
          </Button>
          <Button variant="destructive" onClick={onConfirm} disabled={isDeleting}>
            {isDeleting ? "Deleting..." : "Delete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Provider row
// ---------------------------------------------------------------------------

function ProviderRow({
  provider,
  onEdit,
  onDelete,
  onDiscover,
}: {
  provider: ProviderConfig;
  onEdit: () => void;
  onDelete: () => void;
  onDiscover: () => void;
}) {
  const testMutation = useTestProviderConnectivity();

  function handleTest() {
    testMutation.mutate(provider.provider_type, {
      onSuccess: (resp) => {
        const data = resp.data;
        if (data.success) {
          toast.success(
            `${provider.display_name}: connected (${data.latency_ms}ms)`,
          );
        } else {
          toast.error(`${provider.display_name}: ${data.error}`);
        }
      },
      onError: (err) => {
        toast.error(
          `Test failed: ${err instanceof Error ? err.message : "Unknown error"}`,
        );
      },
    });
  }

  const baseUrl = (provider.config?.base_url as string) ?? null;

  return (
    <div className="space-y-2 py-4 border-b border-border last:border-0">
      <div className="flex items-center justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="text-sm font-medium">{provider.display_name}</p>
            <Badge variant="outline">{providerTypeLabel(provider.provider_type)}</Badge>
            <Badge
              variant={provider.enabled ? "default" : "secondary"}
            >
              {provider.enabled ? "Enabled" : "Disabled"}
            </Badge>
          </div>
          {baseUrl && (
            <p className="text-xs text-muted-foreground font-mono mt-0.5">
              {baseUrl}
            </p>
          )}
        </div>
      </div>

      {/* Test result inline feedback */}
      {testMutation.data && (
        <p
          className={`text-sm ${
            testMutation.data.data.success
              ? "text-green-600 dark:text-green-400"
              : "text-destructive"
          }`}
        >
          {testMutation.data.data.success
            ? `Connected (${testMutation.data.data.latency_ms}ms)`
            : `Failed: ${testMutation.data.data.error}`}
        </p>
      )}

      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={handleTest}
          disabled={testMutation.isPending}
        >
          {testMutation.isPending ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />
              Testing...
            </>
          ) : (
            <>
              <Plug className="h-3.5 w-3.5 mr-1" />
              Test
            </>
          )}
        </Button>
        {provider.provider_type === "ollama" && (
          <Button variant="outline" size="sm" onClick={onDiscover}>
            <Search className="h-3.5 w-3.5 mr-1" />
            Discover Models
          </Button>
        )}
        <Button variant="outline" size="sm" onClick={onEdit}>
          Edit
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="text-destructive hover:bg-destructive/10"
          onClick={onDelete}
        >
          <Trash2 className="h-3.5 w-3.5 mr-1" />
          Delete
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ProviderConfigCard
// ---------------------------------------------------------------------------

export function ProviderConfigCard() {
  const { data, isLoading, isError } = useProviders();
  const providers = data?.data ?? [];

  const createMutation = useCreateProvider();
  const updateMutation = useUpdateProvider();
  const deleteMutation = useDeleteProvider();

  const [formOpen, setFormOpen] = useState(false);
  const [editingProvider, setEditingProvider] = useState<ProviderConfig | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ProviderConfig | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [discoveryOpen, setDiscoveryOpen] = useState(false);

  function handleAddClick() {
    setEditingProvider(null);
    setFormOpen(true);
  }

  function handleEditClick(provider: ProviderConfig) {
    setEditingProvider(provider);
    setFormOpen(true);
  }

  function handleDeleteClick(provider: ProviderConfig) {
    setDeleteTarget(provider);
    setDeleteDialogOpen(true);
  }

  function handleFormSubmit(
    values: ProviderConfigCreate | { providerType: string; body: ProviderConfigUpdate },
  ) {
    if ("providerType" in values) {
      // Update
      updateMutation.mutate(
        { providerType: values.providerType, body: values.body },
        {
          onSuccess: () => {
            toast.success("Provider updated");
            setFormOpen(false);
            setEditingProvider(null);
          },
          onError: (err) =>
            toast.error(
              `Failed to update: ${err instanceof Error ? err.message : "Unknown error"}`,
            ),
        },
      );
    } else {
      // Create
      createMutation.mutate(values, {
        onSuccess: () => {
          toast.success(`Provider "${values.display_name}" created`);
          setFormOpen(false);
        },
        onError: (err) =>
          toast.error(
            `Failed to create: ${err instanceof Error ? err.message : "Unknown error"}`,
          ),
      });
    }
  }

  function handleDeleteConfirm() {
    if (!deleteTarget) return;
    deleteMutation.mutate(deleteTarget.provider_type, {
      onSuccess: () => {
        toast.success(`Provider "${deleteTarget.display_name}" deleted`);
        setDeleteDialogOpen(false);
        setDeleteTarget(null);
      },
      onError: (err) =>
        toast.error(
          `Failed to delete: ${err instanceof Error ? err.message : "Unknown error"}`,
        ),
    });
  }

  return (
    <>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle>Providers</CardTitle>
            <CardDescription>
              Configure external model providers (e.g. Ollama) for local LLM inference.
            </CardDescription>
          </div>
          <Button size="sm" onClick={handleAddClick}>
            Add Provider
          </Button>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          ) : isError ? (
            <p className="text-sm text-destructive">
              Failed to load providers. Ensure the dashboard API is running.
            </p>
          ) : providers.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No providers configured. Add one to get started with local LLM inference.
            </p>
          ) : (
            providers.map((provider) => (
              <ProviderRow
                key={provider.provider_type}
                provider={provider}
                onEdit={() => handleEditClick(provider)}
                onDelete={() => handleDeleteClick(provider)}
                onDiscover={() => setDiscoveryOpen(true)}
              />
            ))
          )}
        </CardContent>
      </Card>

      {/* Add/Edit dialog */}
      <ProviderFormDialog
        open={formOpen}
        onOpenChange={(open) => {
          setFormOpen(open);
          if (!open) setEditingProvider(null);
        }}
        provider={editingProvider}
        onSubmit={handleFormSubmit}
        isSubmitting={createMutation.isPending || updateMutation.isPending}
        error={
          createMutation.error instanceof Error
            ? createMutation.error.message
            : updateMutation.error instanceof Error
              ? updateMutation.error.message
              : null
        }
      />

      {/* Delete confirmation */}
      <DeleteProviderDialog
        provider={deleteTarget}
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        onConfirm={handleDeleteConfirm}
        isDeleting={deleteMutation.isPending}
      />

      {/* Ollama discovery dialog */}
      <OllamaDiscoveryDialog
        open={discoveryOpen}
        onOpenChange={setDiscoveryOpen}
      />
    </>
  );
}

export default ProviderConfigCard;
