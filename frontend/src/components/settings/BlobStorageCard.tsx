/**
 * BlobStorageCard — S3-compatible blob storage configuration card for the
 * settings page.
 *
 * Shows current config status with inline form fields for the 5 S3
 * parameters.  Includes a Test button that probes the configured endpoint
 * and a Save button that persists changes to shared secrets.
 */

import { useEffect, useState } from "react";

import { Eye, EyeOff, HardDrive, Loader2, Plug } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useBlobStorageStatus,
  useSaveBlobSecret,
  useTestBlobStorage,
} from "@/hooks/use-blob-storage";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface FormState {
  endpoint_url: string;
  bucket: string;
  region: string;
  access_key_id: string;
  secret_access_key: string;
}

const EMPTY_FORM: FormState = {
  endpoint_url: "",
  bucket: "",
  region: "",
  access_key_id: "",
  secret_access_key: "",
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function BlobStorageCard() {
  const statusQuery = useBlobStorageStatus();
  const status = statusQuery.data?.data;
  const testMutation = useTestBlobStorage();
  const saveMutation = useSaveBlobSecret();

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [showAccessKey, setShowAccessKey] = useState(false);
  const [showSecretKey, setShowSecretKey] = useState(false);

  // Populate form from server status on first load only (non-sensitive fields)
  const [initialized, setInitialized] = useState(false);
  useEffect(() => {
    if (status && !initialized) {
      setForm((prev) => ({
        ...prev,
        endpoint_url: status.endpoint_url ?? "",
        bucket: status.bucket ?? "",
        region: status.region ?? "",
      }));
      setInitialized(true);
    }
  }, [status, initialized]);

  function updateField(field: keyof FormState, value: string) {
    setForm((prev) => ({ ...prev, [field]: value }));
    setDirty(true);
  }

  async function handleSave() {
    setSaving(true);
    try {
      const fields: { key: string; value: string; isSensitive: boolean }[] = [];

      if (form.endpoint_url) {
        fields.push({ key: "BLOB_S3_ENDPOINT_URL", value: form.endpoint_url.trim(), isSensitive: false });
      }
      if (form.bucket) {
        fields.push({ key: "BLOB_S3_BUCKET", value: form.bucket.trim(), isSensitive: false });
      }
      // Always save region (even empty means default)
      fields.push({ key: "BLOB_S3_REGION", value: form.region.trim() || "us-east-1", isSensitive: false });

      if (form.access_key_id) {
        fields.push({ key: "BLOB_S3_ACCESS_KEY_ID", value: form.access_key_id.trim(), isSensitive: true });
      }
      if (form.secret_access_key) {
        fields.push({ key: "BLOB_S3_SECRET_ACCESS_KEY", value: form.secret_access_key.trim(), isSensitive: true });
      }

      for (const field of fields) {
        await saveMutation.mutateAsync(field);
      }
      toast.success("Blob storage configuration saved");
      setDirty(false);
    } catch (err) {
      toast.error(
        `Failed to save: ${err instanceof Error ? err.message : "Unknown error"}`,
      );
    } finally {
      setSaving(false);
    }
  }

  function handleTest() {
    testMutation.mutate(undefined, {
      onSuccess: (resp) => {
        const d = resp.data;
        if (d.success) {
          toast.success(`S3 connected (${d.latency_ms}ms)`);
        } else {
          toast.error(`S3 connection failed: ${d.error}`);
        }
      },
      onError: (err) => {
        toast.error(
          `Test failed: ${err instanceof Error ? err.message : "Unknown error"}`,
        );
      },
    });
  }

  // Badge
  const badgeInfo = status?.configured
    ? { variant: "default" as const, label: "Configured" }
    : { variant: "outline" as const, label: "Not configured" };

  if (statusQuery.isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <HardDrive className="h-5 w-5" />
            Blob Storage
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-32 w-full" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <HardDrive className="h-5 w-5" />
              Blob Storage
            </CardTitle>
            <CardDescription className="mt-1">
              S3-compatible object storage for media and file attachments.
            </CardDescription>
          </div>
          <Badge variant={badgeInfo.variant}>{badgeInfo.label}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Endpoint URL */}
        <div className="space-y-1.5">
          <Label htmlFor="blob-endpoint">Endpoint URL</Label>
          <Input
            id="blob-endpoint"
            value={form.endpoint_url}
            onChange={(e) => updateField("endpoint_url", e.target.value)}
            placeholder="http://nas:9000"
            disabled={saving}
          />
          <p className="text-xs text-muted-foreground">
            S3-compatible endpoint (Garage, MinIO, AWS S3)
          </p>
        </div>

        {/* Bucket + Region side by side */}
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="blob-bucket">Bucket</Label>
            <Input
              id="blob-bucket"
              value={form.bucket}
              onChange={(e) => updateField("bucket", e.target.value)}
              placeholder="butlers-blobs"
              disabled={saving}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="blob-region">Region</Label>
            <Input
              id="blob-region"
              value={form.region}
              onChange={(e) => updateField("region", e.target.value)}
              placeholder="us-east-1"
              disabled={saving}
            />
          </div>
        </div>

        {/* Access Key */}
        <div className="space-y-1.5">
          <Label htmlFor="blob-access-key">Access Key ID</Label>
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Input
                id="blob-access-key"
                type={showAccessKey ? "text" : "password"}
                value={form.access_key_id}
                onChange={(e) => updateField("access_key_id", e.target.value)}
                placeholder={status?.has_access_key ? "********** (saved)" : "Enter access key ID"}
                autoComplete="off"
                disabled={saving}
              />
              <button
                type="button"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                onClick={() => setShowAccessKey(!showAccessKey)}
                tabIndex={-1}
              >
                {showAccessKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </div>
        </div>

        {/* Secret Key */}
        <div className="space-y-1.5">
          <Label htmlFor="blob-secret-key">Secret Access Key</Label>
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Input
                id="blob-secret-key"
                type={showSecretKey ? "text" : "password"}
                value={form.secret_access_key}
                onChange={(e) => updateField("secret_access_key", e.target.value)}
                placeholder={status?.has_secret_key ? "********** (saved)" : "Enter secret access key"}
                autoComplete="off"
                disabled={saving}
              />
              <button
                type="button"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                onClick={() => setShowSecretKey(!showSecretKey)}
                tabIndex={-1}
              >
                {showSecretKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </div>
        </div>

        {/* Test result inline */}
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

        {/* Action buttons */}
        <div className="flex items-center gap-2 pt-2">
          <Button
            size="sm"
            onClick={handleSave}
            disabled={saving || !dirty}
          >
            {saving ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />
                Saving...
              </>
            ) : (
              "Save"
            )}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleTest}
            disabled={testMutation.isPending || !(status?.configured || (form.endpoint_url && form.bucket))}
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
        </div>
      </CardContent>
    </Card>
  );
}
