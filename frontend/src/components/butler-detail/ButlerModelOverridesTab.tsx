import { useState } from "react";
import { toast } from "sonner";

import type { ButlerModelOverride, ComplexityTier, ModelCatalogEntry, PricingMap } from "@/api/types.ts";
import { ComplexityBadge, COMPLEXITY_TIERS, complexityLabel } from "@/components/general/ComplexityBadge.tsx";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Section,
  SectionContent,
  SectionDescription,
  SectionHeader,
  SectionTitle,
} from "@/components/ui/Section";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { usePricingMap } from "@/hooks/use-pricing-map.ts";
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
import {
  useButlerModelOverrides,
  useDeleteButlerModelOverride,
  useModelCatalog,
  useResolveModel,
  useUpsertButlerModelOverrides,
} from "@/hooks/use-model-catalog.ts";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ButlerModelOverridesTabProps {
  butlerName: string;
}

// ---------------------------------------------------------------------------
// Effective model per tier (read-only resolution table)
// ---------------------------------------------------------------------------

function EffectiveModelRow({ butlerName, tier }: { butlerName: string; tier: ComplexityTier }) {
  const { data, isLoading } = useResolveModel(butlerName, tier);
  const resolved = data?.data;

  return (
    <TableRow>
      <TableCell>
        <ComplexityBadge tier={tier} />
      </TableCell>
      <TableCell className="text-xs text-muted-foreground">
        {isLoading ? (
          <Skeleton className="h-3 w-24" />
        ) : resolved?.resolved ? (
          <code>{resolved.model_id}</code>
        ) : (
          <span className="text-[var(--amber-text)]">Not configured</span>
        )}
      </TableCell>
      <TableCell className="text-xs text-muted-foreground">
        {isLoading ? (
          <Skeleton className="h-3 w-16" />
        ) : resolved?.resolved ? (
          <code>{resolved.runtime_type}</code>
        ) : (
          "\u2014"
        )}
      </TableCell>
      <TableCell className="text-xs text-muted-foreground font-mono">
        {isLoading ? (
          <Skeleton className="h-3 w-32" />
        ) : resolved?.resolved && resolved.extra_args.length > 0 ? (
          resolved.extra_args.join(" ")
        ) : (
          "\u2014"
        )}
      </TableCell>
    </TableRow>
  );
}

function EffectiveModelsTable({ butlerName }: { butlerName: string }) {
  return (
    <Section>
      <SectionHeader>
        <SectionTitle>Effective Models per Tier</SectionTitle>
        <SectionDescription>
          Resolved model for each complexity tier (accounting for overrides).
        </SectionDescription>
      </SectionHeader>
      <SectionContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Complexity</TableHead>
              <TableHead>Model ID</TableHead>
              <TableHead>Runtime</TableHead>
              <TableHead>Extra Args</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {COMPLEXITY_TIERS.map((tier) => (
              <EffectiveModelRow key={tier} butlerName={butlerName} tier={tier} />
            ))}
          </TableBody>
        </Table>
      </SectionContent>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Override upsert dialog
// ---------------------------------------------------------------------------

interface PricePreviewProps {
  before?: ModelCatalogEntry;
  after?: ModelCatalogEntry;
  pricing?: PricingMap;
  pricingFailed: boolean;
  pricingPending: boolean;
}

function previewUnavailable({ before, after, pricing, pricingFailed }: PricePreviewProps) {
  return pricingFailed || ![before, after].every((entry) => {
    const rates = entry && pricing?.[entry.model_id];
    return rates && [rates.input_per_million, rates.output_per_million]
      .every((rate) => Number.isFinite(rate) && rate >= 0);
  });
}

function formatRate(rate: number) {
  return rate > 0 && rate < 0.01 ? String(rate) : rate.toFixed(2);
}

function OverridePricePreview(props: PricePreviewProps) {
  if (props.pricingPending) {
    return <p role="status" className="text-sm text-muted-foreground">Loading cost preview...</p>;
  }
  if (previewUnavailable(props)) {
    return (
      <p role="status" className="text-sm text-[var(--amber-text)]">
        Cost preview unavailable. You can still save with a reason.
      </p>
    );
  }
  return (
    <div className="space-y-2 text-sm" aria-label="Cost preview">
      <p className="font-medium">Catalog rates in USD per 1M tokens (base tier)</p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        {([["Before", props.before!], ["After", props.after!]] as const).map(([label, entry]) => {
          const rate = props.pricing![entry.model_id];
          return (
            <div key={label} className="contents">
              <dt>{label}</dt>
              <dd>
                <span className="block">{entry.alias} ({entry.model_id})</span>
                ${formatRate(rate.input_per_million)} input / ${formatRate(rate.output_per_million)} output
              </dd>
            </div>
          );
        })}
      </dl>
      <p className="text-xs text-muted-foreground">
        Overrides change routing settings, not provider rates. Actual model selection and spend
        depend on routing, context and usage.
      </p>
    </div>
  );
}

interface OverrideDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  catalogEntries: ModelCatalogEntry[];
  existingOverride?: ButlerModelOverride | null;
  onSubmit: (catalogEntryId: string, complexityTier: ButlerModelOverride["complexity_tier"], reason: string, unavailable: boolean) => void;
  price: Omit<PricePreviewProps, "before" | "after">;
  isSubmitting: boolean;
}

function OverrideDialog({
  open,
  onOpenChange,
  catalogEntries,
  existingOverride,
  onSubmit,
  price,
  isSubmitting,
}: OverrideDialogProps) {
  const formKey = open ? (existingOverride?.id ?? "new") : "closed";
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {existingOverride ? "Edit override" : "Add override"}
          </DialogTitle>
          <DialogDescription>
            Override the model catalog settings for this butler.
          </DialogDescription>
        </DialogHeader>
        <OverrideFormFields
          key={formKey}
          catalogEntries={catalogEntries}
          existingOverride={existingOverride}
          price={price}
          onSubmit={onSubmit}
          onCancel={() => onOpenChange(false)}
          isSubmitting={isSubmitting}
        />
      </DialogContent>
    </Dialog>
  );
}

function OverrideFormFields({
  catalogEntries,
  existingOverride,
  onSubmit,
  price,
  onCancel,
  isSubmitting,
}: {
  catalogEntries: ModelCatalogEntry[];
  existingOverride?: ButlerModelOverride | null;
  onSubmit: (catalogEntryId: string, complexityTier: ButlerModelOverride["complexity_tier"], reason: string, unavailable: boolean) => void;
  price: Omit<PricePreviewProps, "before" | "after">;
  onCancel: () => void;
  isSubmitting: boolean;
}) {
  const [catalogEntryId, setCatalogEntryId] = useState(
    existingOverride?.catalog_entry_id ?? "",
  );
  const [complexityTier, setComplexityTier] = useState<string>(
    existingOverride?.complexity_tier ?? "inherit",
  );

  const [reason, setReason] = useState("");
  const selected = catalogEntries.find((entry) => entry.id === catalogEntryId);
  const previous = existingOverride
    ? catalogEntries.find((entry) => entry.id === existingOverride.catalog_entry_id)
    : selected;
  const preview = { ...price, before: previous, after: selected };
  const isValid = catalogEntryId !== "" && reason.trim().length > 0;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!isValid) return;
    onSubmit(
      catalogEntryId,
      complexityTier === "inherit" ? null : complexityTier,
      reason.trim(),
      previewUnavailable(preview),
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="override-catalog-entry">Model</Label>
        <Select
          value={catalogEntryId}
          onValueChange={setCatalogEntryId}
          disabled={isSubmitting}
        >
          <SelectTrigger id="override-catalog-entry">
            <SelectValue placeholder="Select a model..." />
          </SelectTrigger>
          <SelectContent>
            {catalogEntries.map((entry) => (
              <SelectItem key={entry.id} value={entry.id}>
                <span className="flex items-center gap-2">
                  {entry.alias}
                  <ComplexityBadge tier={entry.complexity_tier} />
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-2">
        <Label htmlFor="override-complexity-tier">Complexity Override</Label>
        <Select
          value={complexityTier}
          onValueChange={(v) => setComplexityTier(v)}
          disabled={isSubmitting}
        >
          <SelectTrigger id="override-complexity-tier">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="inherit">Use catalog default</SelectItem>
            {COMPLEXITY_TIERS.map((tier) => (
              <SelectItem key={tier} value={tier}>
                {complexityLabel(tier)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-xs text-muted-foreground">
          Override the complexity tier for this model on this butler only.
        </p>
      </div>

      <OverridePricePreview {...preview} />
      {existingOverride && catalogEntryId !== existingOverride.catalog_entry_id && (
        <p className="text-sm text-muted-foreground">Choosing another catalog entry adds its override; the existing entry stays unchanged.</p>
      )}
      <div className="space-y-2">
        <Label htmlFor="override-reason">Reason</Label>
        <Textarea id="override-reason" value={reason} maxLength={2000} required
          onChange={(event) => setReason(event.target.value)} disabled={isSubmitting}
          placeholder="Why is this override needed?" />
      </div>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button type="submit" disabled={!isValid || isSubmitting}>
          {isSubmitting ? "Saving..." : existingOverride ? "Update" : "Add override"}
        </Button>
      </DialogFooter>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Overrides table
// ---------------------------------------------------------------------------

function OverridesTable({
  overrides,
  isLoading,
  onEdit,
  onDelete,
}: {
  overrides: ButlerModelOverride[];
  isLoading: boolean;
  onEdit: (override: ButlerModelOverride) => void;
  onDelete: (override: ButlerModelOverride) => void;
}) {
  if (!isLoading && overrides.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">
        No overrides configured. The butler uses the global model catalog.
      </p>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Alias</TableHead>
          <TableHead>Complexity Override</TableHead>
          <TableHead>Priority Override</TableHead>
          <TableHead>Enabled</TableHead>
          <TableHead className="text-right">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {isLoading ? (
          <>
            {Array.from({ length: 3 }, (_, i) => (
              <TableRow key={i}>
                <TableCell><Skeleton className="h-4 w-24" /></TableCell>
                <TableCell><Skeleton className="h-4 w-20" /></TableCell>
                <TableCell><Skeleton className="h-4 w-12" /></TableCell>
                <TableCell><Skeleton className="h-4 w-10" /></TableCell>
                <TableCell className="text-right"><Skeleton className="h-4 w-16 ml-auto" /></TableCell>
              </TableRow>
            ))}
          </>
        ) : (
          overrides.map((override) => (
            <TableRow key={override.id}>
              <TableCell className="font-medium">{override.alias}</TableCell>
              <TableCell>
                {override.complexity_tier ? (
                  <ComplexityBadge tier={override.complexity_tier} />
                ) : (
                  <Badge variant="outline">Inherited</Badge>
                )}
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">
                {override.priority != null ? override.priority : <span>&mdash;</span>}
              </TableCell>
              <TableCell>
                {override.enabled ? (
                  <Badge className="bg-[var(--green)] text-white hover:bg-[var(--green)]/90">
                    On
                  </Badge>
                ) : (
                  <Badge variant="secondary">Off</Badge>
                )}
              </TableCell>
              <TableCell className="text-right">
                <div className="flex justify-end gap-1">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => onEdit(override)}
                  >
                    Edit
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="text-destructive hover:bg-destructive/10"
                    onClick={() => onDelete(override)}
                  >
                    Remove
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          ))
        )}
      </TableBody>
    </Table>
  );
}

// ---------------------------------------------------------------------------
// Main tab
// ---------------------------------------------------------------------------

export default function ButlerModelOverridesTab({
  butlerName,
}: ButlerModelOverridesTabProps) {
  const { data: catalogData, isLoading: catalogLoading } = useModelCatalog();
  const {
    data: overridesData,
    isLoading: overridesLoading,
    isError,
  } = useButlerModelOverrides(butlerName);

  const catalogEntries = catalogData?.data ?? [];
  const overrides = overridesData?.data ?? [];

  const pricing = usePricingMap();
  const price = { pricing: pricing.data, pricingFailed: pricing.isError, pricingPending: pricing.isPending };
  const upsertMutation = useUpsertButlerModelOverrides(butlerName);
  const deleteMutation = useDeleteButlerModelOverride(butlerName);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingOverride, setEditingOverride] = useState<ButlerModelOverride | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ButlerModelOverride | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleteReason, setDeleteReason] = useState("");
  const deleteEntry = catalogEntries.find((entry) => entry.id === deleteTarget?.catalog_entry_id);
  const deletePreview = { ...price, before: deleteEntry, after: deleteEntry };

  function handleAddClick() {
    setEditingOverride(null);
    setDialogOpen(true);
  }

  function handleEditClick(override: ButlerModelOverride) {
    setEditingOverride(override);
    setDialogOpen(true);
  }

  function handleDeleteClick(override: ButlerModelOverride) {
    setDeleteTarget(override);
    setDeleteReason("");
    setDeleteDialogOpen(true);
  }

  function handleOverrideSubmit(
    catalogEntryId: string,
    complexityTier: ButlerModelOverride["complexity_tier"],
    reason: string,
    unavailable: boolean,
  ) {
    upsertMutation.mutate(
      { reason, price_preview_unavailable: unavailable,
        overrides: [{ catalog_entry_id: catalogEntryId, complexity_tier: complexityTier, enabled: true }] },
      {
        onSuccess: () => {
          toast.success("Model override saved");
          setDialogOpen(false);
          setEditingOverride(null);
        },
        onError: (err) =>
          toast.error(
            `Failed: ${err instanceof Error ? err.message : "Unknown error"}`,
          ),
      },
    );
  }

  function handleDeleteConfirm() {
    if (!deleteTarget || !deleteReason.trim()) return;
    deleteMutation.mutate({ overrideId: deleteTarget.id, reason: deleteReason.trim(),
      price_preview_unavailable: previewUnavailable(deletePreview) }, {
      onSuccess: () => {
        toast.success(`Override for "${deleteTarget.alias}" removed`);
        setDeleteDialogOpen(false);
        setDeleteTarget(null);
      },
      onError: (err) =>
        toast.error(
          `Failed: ${err instanceof Error ? err.message : "Unknown error"}`,
        ),
    });
  }

  if (isError) {
    return (
      <Section>
        <SectionContent className="py-8">
          <p className="text-sm text-destructive text-center">
            Failed to load model overrides.
          </p>
        </SectionContent>
      </Section>
    );
  }

  return (
    <div className="space-y-6">
      {/* Effective resolution table */}
      <EffectiveModelsTable butlerName={butlerName} />

      {/* Per-butler overrides */}
      <Section>
        <SectionHeader className="flex flex-row items-center justify-between">
          <div>
            <SectionTitle>Per-Butler Overrides</SectionTitle>
            <SectionDescription>
              Override specific catalog entries for this butler.
            </SectionDescription>
          </div>
          <Button
            size="sm"
            onClick={handleAddClick}
            disabled={catalogLoading || catalogEntries.length === 0}
          >
            Add override
          </Button>
        </SectionHeader>
        <SectionContent>
          <OverridesTable
            overrides={overrides}
            isLoading={overridesLoading}
            onEdit={handleEditClick}
            onDelete={handleDeleteClick}
          />
        </SectionContent>
      </Section>

      {/* Override dialog */}
      <OverrideDialog
        open={dialogOpen}
        onOpenChange={(open) => {
          setDialogOpen(open);
          if (!open) setEditingOverride(null);
        }}
        catalogEntries={catalogEntries}
        price={price}
        existingOverride={editingOverride}
        onSubmit={handleOverrideSubmit}
        isSubmitting={upsertMutation.isPending}
      />

      {/* Delete confirmation */}
      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove Override?</DialogTitle>
            <DialogDescription>
              Remove the override for{" "}
              <strong>{deleteTarget?.alias}</strong>? The butler will use the
              global catalog settings.
            </DialogDescription>
          </DialogHeader>
          <OverridePricePreview {...deletePreview} />
          <div className="space-y-2">
            <Label htmlFor="override-delete-reason">Reason</Label>
            <Textarea id="override-delete-reason" value={deleteReason} maxLength={2000} required
              onChange={(event) => setDeleteReason(event.target.value)} disabled={deleteMutation.isPending} />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteDialogOpen(false)}
              disabled={deleteMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDeleteConfirm}
              disabled={deleteMutation.isPending || !deleteReason.trim()}
            >
              {deleteMutation.isPending ? "Removing..." : "Remove"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
