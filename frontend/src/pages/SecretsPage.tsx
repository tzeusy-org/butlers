import { useMemo, useState } from "react";

import type { EntitySummary, SecretEntry } from "@/api/index.ts";
import { revealEntitySecret } from "@/api/index.ts";
import type { SecretDisplayRow } from "@/lib/secrets-rows";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SecretFormModal } from "@/components/secrets/SecretFormModal";
import { UserSecretFormModal } from "@/components/secrets/UserSecretFormModal";
import { SecretsTable } from "@/components/secrets/SecretsTable";
import { GoogleHealthStatusCard } from "@/components/settings/GoogleHealthStatusCard";
import { GoogleOAuthSection } from "@/components/settings/GoogleOAuthSection";
import { SpotifySection } from "@/components/settings/SpotifySetupCard";
import { HomeAssistantSection } from "@/components/settings/HomeAssistantSetupCard";
import { OwnTracksSection } from "@/components/settings/OwnTracksSetupCard";
import { SteamSection } from "@/components/settings/SteamSetupCard";
import { WhatsAppSection } from "@/components/settings/WhatsAppSetupCard";
import { useButlers } from "@/hooks/use-butlers";
import { useEntities, useEntity } from "@/hooks/use-memory";
import { useSecrets } from "@/hooks/use-secrets";
import {
  useOwnerEntityInfo,
  useDeleteOwnerEntityInfo,
} from "@/hooks/use-owner-secrets";
import { buildSecretsTargets, SHARED_SECRETS_TARGET } from "@/pages/secretsTargets";
import { buildUserSecretRows } from "@/lib/user-secrets-rows";
import { userCategoryLabel } from "@/lib/user-secret-templates";

function formatSecretsTargetLabel(target: string): string {
  if (target.trim().toLowerCase() === SHARED_SECRETS_TARGET) {
    return SHARED_SECRETS_TARGET;
  }
  return target;
}

// ---------------------------------------------------------------------------
// System secrets section (existing behavior, unchanged)
// ---------------------------------------------------------------------------

function SystemSecretsSection() {
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
            <CardTitle>System Secrets</CardTitle>
            <CardDescription>
              Ecosystem-wide credentials stored in the database.
              Manage shared defaults and local per-butler overrides.
            </CardDescription>
          </div>
          <div className="flex items-center gap-2 shrink-0">
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

      <SecretFormModal
        butlerName={activeTarget}
        prefill={addPrefill}
        open={addModalOpen}
        onOpenChange={(open) => {
          setAddModalOpen(open);
          if (!open) setAddPrefill(null);
        }}
      />

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
// User secrets section (entity_info on owner entity)
// ---------------------------------------------------------------------------

// Lightweight searchable entity picker for the User tab. Defaults to the
// owner entity; switching loads that entity's credentials.
function EntityPicker({
  ownerId,
  ownerName,
  selectedId,
  selectedName,
  onSelect,
}: {
  ownerId: string | null;
  ownerName: string | null;
  selectedId: string;
  selectedName: string | null;
  onSelect: (id: string, name: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const { data } = useEntities(
    open ? { q: search || undefined, entity_type: "person", limit: 25 } : undefined,
  );
  const entities: EntitySummary[] = data?.data ?? [];

  const isOwnerSelected = ownerId !== null && selectedId === ownerId;
  const displayName = isOwnerSelected
    ? `${ownerName ?? "Owner"} (you)`
    : (selectedName ?? "Select identity");

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={
          "border-input bg-background hover:bg-accent flex h-9 w-full items-center " +
          "justify-between gap-2 rounded-md border px-3 text-left text-sm " +
          "transition-colors focus:outline-none focus:ring-2 focus:ring-ring sm:w-72"
        }
      >
        <span className="truncate font-medium">{displayName}</span>
        <span className="text-muted-foreground text-xs">change</span>
      </button>

      {open && (
        <div className="bg-popover absolute z-20 mt-1 w-full max-w-md rounded-md border p-2 shadow-md sm:w-96">
          <Input
            autoFocus
            placeholder="Search identities..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="mb-2 h-8 text-sm"
          />
          {ownerId && !isOwnerSelected && (
            <button
              type="button"
              onClick={() => {
                onSelect(ownerId, ownerName);
                setOpen(false);
                setSearch("");
              }}
              className="hover:bg-muted block w-full rounded px-2 py-1.5 text-left text-sm"
            >
              <span className="font-medium">{ownerName ?? "Owner"}</span>
              <span className="text-muted-foreground"> (you)</span>
            </button>
          )}
          <div className="max-h-64 overflow-y-auto">
            {entities
              .filter((e) => e.id !== ownerId)
              .map((e) => (
                <button
                  key={e.id}
                  type="button"
                  onClick={() => {
                    onSelect(e.id, e.canonical_name);
                    setOpen(false);
                    setSearch("");
                  }}
                  className="hover:bg-muted block w-full rounded px-2 py-1.5 text-left text-sm"
                >
                  <span className="font-medium">{e.canonical_name}</span>
                  {e.dunbar_tier != null && (
                    <span className="text-muted-foreground text-xs">
                      {" "}· tier {e.dunbar_tier}
                    </span>
                  )}
                </button>
              ))}
            {entities.filter((e) => e.id !== ownerId).length === 0 && search && (
              <p className="text-muted-foreground px-2 py-2 text-xs">
                No matches.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function UserSecretsSection() {
  const { data: ownerData, isError: ownerError } = useOwnerEntityInfo();
  const deleteMutation = useDeleteOwnerEntityInfo();

  const ownerId = ownerData?.entity_id ?? null;
  const ownerName = ownerData?.entity_name ?? null;

  // Override is null until the user explicitly picks a non-owner identity.
  // Effective selection falls back to the owner — no useEffect needed for the
  // default-to-owner behavior, which is what react-hooks/set-state-in-effect
  // is rightly cranky about.
  const [override, setOverride] = useState<{ id: string; name: string | null } | null>(
    null,
  );
  const selectedId = override?.id ?? ownerId ?? "";
  const selectedName = override?.name ?? ownerName ?? null;

  const {
    data: entityResp,
    isLoading: entityLoading,
    isError: entityError,
    error,
  } = useEntity(selectedId || undefined, { facts_limit: 1 });
  const entity = entityResp?.data ?? null;
  const userRows = useMemo(
    () => buildUserSecretRows(entity?.entity_info ?? []),
    [entity?.entity_info],
  );

  const [editRow, setEditRow] = useState<SecretDisplayRow | null>(null);
  const [addOpen, setAddOpen] = useState(false);

  const isOwner = !!ownerId && selectedId === ownerId;
  const displayName = entity?.canonical_name ?? selectedName ?? "—";

  async function handleRevealEntry(row: SecretDisplayRow): Promise<string | null> {
    if (!row.entityInfoEntry || !selectedId) return null;
    const resp = await revealEntitySecret(selectedId, row.entityInfoEntry.id);
    return resp.value ?? null;
  }

  async function handleDeleteRow(row: SecretDisplayRow): Promise<void> {
    if (!row.entityInfoEntry || !selectedId) return;
    await deleteMutation.mutateAsync({
      entityId: selectedId,
      infoId: row.entityInfoEntry.id,
    });
  }

  function handleEditRow(row: SecretDisplayRow) {
    setEditRow(row);
  }

  return (
    <div className="space-y-6">
      {/* ------------------------------------------------------------------ */}
      {/* Integrations section — owner-only; only renders for the owner       */}
      {/* identity since the underlying providers bind to the owner record.   */}
      {/* ------------------------------------------------------------------ */}
      {isOwner && (
        <div>
          <h2 className="text-lg font-semibold mb-1">Integrations</h2>
          <p className="text-sm text-muted-foreground mb-4">
            Account-login integrations that bind external services to your identity.
          </p>
          <Card>
            <CardContent className="pt-6">
              <div className="divide-y">
                <div className="pb-6 space-y-4">
                  <GoogleOAuthSection />
                  <GoogleHealthStatusCard />
                </div>
                <div className="py-6">
                  <SpotifySection />
                </div>
                <div className="py-6">
                  <HomeAssistantSection />
                </div>
                <div className="py-6">
                  <WhatsAppSection />
                </div>
                <div className="py-6">
                  <OwnTracksSection />
                </div>
                <div className="pt-6">
                  <SteamSection />
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* Credentials section                                                  */}
      {/* ------------------------------------------------------------------ */}
      <div>
        <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">Credentials</h2>
            <p className="text-sm text-muted-foreground">
              Identity-bound credentials. API keys, tokens, and other secrets
              attached to a specific person's entity record.
            </p>
          </div>
          <EntityPicker
            ownerId={ownerId}
            ownerName={ownerName}
            selectedId={selectedId}
            selectedName={selectedName}
            onSelect={(id, name) => {
              // Reset override when picking the owner so future owner-name
              // changes from useOwnerEntityInfo flow through.
              if (id === ownerId) {
                setOverride(null);
              } else {
                setOverride({ id, name });
              }
            }}
          />
        </div>

        <Card>
          <CardHeader>
            <div className="flex items-start justify-between gap-4">
              <div>
                <CardTitle className="text-base">
                  {displayName}
                  {isOwner && (
                    <span className="text-muted-foreground ml-2 text-sm font-normal">
                      (you)
                    </span>
                  )}
                </CardTitle>
                <CardDescription>
                  Raw credential entries managed on this entity's record.
                </CardDescription>
              </div>
              <Button
                size="sm"
                onClick={() => {
                  setEditRow(null);
                  setAddOpen(true);
                }}
                disabled={!selectedId}
              >
                Add Credential
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {ownerError && !selectedId ? (
              <p className="text-sm text-destructive">
                No owner entity found. Create one in the Entities page first.
              </p>
            ) : entityError ? (
              <p className="text-sm text-destructive">
                Failed to load credentials. {(error as Error)?.message ?? ""}
              </p>
            ) : (
              <SecretsTable
                mode="user"
                butlerName=""
                secrets={[]}
                userRows={userRows}
                isLoading={entityLoading}
                isError={false}
                onEdit={() => {}}
                onCreateOverride={() => {}}
                onEditRow={handleEditRow}
                onRevealEntry={handleRevealEntry}
                onDeleteRow={handleDeleteRow}
                categoryLabelFn={userCategoryLabel}
              />
            )}
          </CardContent>
        </Card>
      </div>

      {/* Add modal */}
      <UserSecretFormModal
        entityId={selectedId}
        open={addOpen}
        onOpenChange={(open) => {
          setAddOpen(open);
        }}
      />

      {/* Edit modal */}
      <UserSecretFormModal
        entityId={selectedId}
        editRow={editRow}
        open={!!editRow}
        onOpenChange={(open) => {
          if (!open) setEditRow(null);
        }}
      />
    </div>
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
          Manage system-wide and user-specific credentials.
        </p>
      </div>

      <Tabs defaultValue="system">
        <TabsList>
          <TabsTrigger value="system">System</TabsTrigger>
          <TabsTrigger value="user">User</TabsTrigger>
        </TabsList>
        <TabsContent value="system">
          <SystemSecretsSection />
        </TabsContent>
        <TabsContent value="user">
          <UserSecretsSection />
        </TabsContent>
      </Tabs>
    </div>
  );
}
