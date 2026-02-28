import { useState } from "react";

import type { SecretEntry } from "@/api/index.ts";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { SecretFormModal } from "@/components/secrets/SecretFormModal";
import { SecretsTable } from "@/components/secrets/SecretsTable";
import { useButlers } from "@/hooks/use-butlers";
import { useSecrets } from "@/hooks/use-secrets";
import { buildSecretsTargets, SHARED_SECRETS_TARGET } from "@/pages/secretsTargets";

function formatSecretsTargetLabel(target: string): string {
  if (target.trim().toLowerCase() === SHARED_SECRETS_TARGET) {
    return SHARED_SECRETS_TARGET;
  }
  return target;
}

// ---------------------------------------------------------------------------
// Generic secrets section (shared + per-butler)
// ---------------------------------------------------------------------------

function GenericSecretsSection() {
  interface SecretPrefill {
    key: string;
    category: string;
    description: string | null;
  }

  const { data: butlersResponse, isLoading: butlersLoading } = useButlers();
  const butlerNames = butlersResponse?.data?.map((b) => b.name) ?? [];
  const secretTargets = buildSecretsTargets(butlerNames);

  const [selectedTarget, setSelectedTarget] = useState<string>("");
  const [addModalOpen, setAddModalOpen] = useState(false);
  const [addPrefill, setAddPrefill] = useState<SecretPrefill | null>(null);
  const [editSecret, setEditSecret] = useState<SecretEntry | null>(null);

  // Pick first available target by default once loaded.
  const activeTarget = selectedTarget || (secretTargets[0] ?? "");

  const { data: secretsResponse, isLoading, isError } = useSecrets(activeTarget);
  const secrets = secretsResponse?.data ?? [];

  function handleEdit(secret: SecretEntry) {
    setEditSecret(secret);
  }

  function handleCreateOverride(prefill: SecretPrefill) {
    setAddPrefill(prefill);
    setAddModalOpen(true);
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle>Secrets</CardTitle>
            <CardDescription>
              Known secret requirements plus resolved values, grouped by category.
              Manage shared defaults and local per-butler overrides.
            </CardDescription>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {/* Butler selector */}
            {butlersLoading ? (
              <Skeleton className="h-9 w-36" />
            ) : secretTargets.length > 1 ? (
              <Select
                value={activeTarget}
                onValueChange={setSelectedTarget}
              >
                <SelectTrigger className="w-36">
                  <SelectValue placeholder="Select target" />
                </SelectTrigger>
                <SelectContent>
                  {secretTargets.map((name) => (
                    <SelectItem key={name} value={name}>
                      {formatSecretsTargetLabel(name)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : null}
            <Button
              size="sm"
              onClick={() => {
                setAddPrefill(null);
                setAddModalOpen(true);
              }}
              disabled={!activeTarget}
            >
              Add Secret
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {!activeTarget ? (
          <p className="text-sm text-muted-foreground">
            No secret target available. Check dashboard DB configuration.
          </p>
        ) : (
          <SecretsTable
            butlerName={activeTarget}
            secrets={secrets}
            isLoading={isLoading}
            isError={isError}
            onEdit={handleEdit}
            onCreateOverride={handleCreateOverride}
          />
        )}
      </CardContent>

      {/* Add modal */}
      <SecretFormModal
        butlerName={activeTarget}
        prefill={addPrefill}
        open={addModalOpen}
        onOpenChange={(open) => {
          setAddModalOpen(open);
          if (!open) setAddPrefill(null);
        }}
      />

      {/* Edit modal */}
      <SecretFormModal
        butlerName={activeTarget}
        editSecret={editSecret}
        open={!!editSecret}
        onOpenChange={(open) => {
          if (!open) setEditSecret(null);
        }}
      />
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function SecretsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Secrets</h1>
        <p className="text-muted-foreground mt-1">
          Manage secrets stored in the database.
          Suggested keys, inherited sources, and local overrides are shown without exposing values.
        </p>
      </div>

      {/* Generic secrets management */}
      <GenericSecretsSection />
    </div>
  );
}
