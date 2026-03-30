import { useState } from "react";

import type { SecretEntry } from "@/api/index.ts";
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
import { GoogleOAuthSection } from "@/components/settings/GoogleOAuthSection";
import { SpotifySection } from "@/components/settings/SpotifySetupCard";
import { HomeAssistantSection } from "@/components/settings/HomeAssistantSetupCard";
import { OwnTracksSection } from "@/components/settings/OwnTracksSetupCard";
import { SteamSection } from "@/components/settings/SteamSetupCard";
import { WhatsAppSection } from "@/components/settings/WhatsAppSetupCard";
import { useButlers } from "@/hooks/use-butlers";
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

function UserSecretsSection() {
  const { data: ownerData, isLoading, isError, error } = useOwnerEntityInfo();
  const deleteMutation = useDeleteOwnerEntityInfo();

  const [editRow, setEditRow] = useState<SecretDisplayRow | null>(null);
  const [addOpen, setAddOpen] = useState(false);

  const entityId = ownerData?.entity_id ?? "";
  const entries = ownerData?.entries ?? [];
  const userRows = buildUserSecretRows(entries);

  async function handleRevealEntry(row: SecretDisplayRow): Promise<string | null> {
    if (!row.entityInfoEntry || !entityId) return null;
    const resp = await revealEntitySecret(entityId, row.entityInfoEntry.id);
    return resp.value ?? null;
  }

  async function handleDeleteRow(row: SecretDisplayRow): Promise<void> {
    if (!row.entityInfoEntry || !entityId) return;
    await deleteMutation.mutateAsync({ entityId, infoId: row.entityInfoEntry.id });
  }

  function handleEditRow(row: SecretDisplayRow) {
    setEditRow(row);
  }

  return (
    <div className="space-y-6">
      {/* ------------------------------------------------------------------ */}
      {/* Integrations section                                                */}
      {/* ------------------------------------------------------------------ */}
      <div>
        <h2 className="text-lg font-semibold mb-1">Integrations</h2>
        <p className="text-sm text-muted-foreground mb-4">
          Account-login integrations that bind external services to your identity.
        </p>
        <Card>
          <CardContent className="pt-6">
            <div className="divide-y">
              <div className="pb-6">
                <GoogleOAuthSection />
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

      {/* ------------------------------------------------------------------ */}
      {/* Credentials section                                                  */}
      {/* ------------------------------------------------------------------ */}
      <div>
        <h2 className="text-lg font-semibold mb-1">
          Credentials
          {ownerData?.entity_name ? (
            <span className="text-muted-foreground font-normal text-base ml-2">
              ({ownerData.entity_name})
            </span>
          ) : null}
        </h2>
        <p className="text-sm text-muted-foreground mb-4">
          Identity-bound credentials on the owner entity.
          Telegram API keys, Home Assistant tokens, and other personal credentials.
        </p>
        <Card>
          <CardHeader>
            <div className="flex items-start justify-between gap-4">
              <div>
                <CardTitle className="text-base">Stored Credentials</CardTitle>
                <CardDescription>
                  Raw credential entries managed on the owner entity record.
                </CardDescription>
              </div>
              <Button
                size="sm"
                onClick={() => {
                  setEditRow(null);
                  setAddOpen(true);
                }}
                disabled={!entityId}
              >
                Add Credential
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {isError ? (
              <p className="text-sm text-destructive">
                {(error as Error)?.message?.includes("404")
                  ? "No owner entity found. Create one in the Entities page first."
                  : "Failed to load user credentials."}
              </p>
            ) : (
              <SecretsTable
                mode="user"
                butlerName=""
                secrets={[]}
                userRows={userRows}
                isLoading={isLoading}
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
        entityId={entityId}
        open={addOpen}
        onOpenChange={(open) => {
          setAddOpen(open);
        }}
      />

      {/* Edit modal */}
      <UserSecretFormModal
        entityId={entityId}
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
