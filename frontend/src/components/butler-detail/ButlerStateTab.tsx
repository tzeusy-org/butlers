/**
 * ButlerStateTab — state store tab for the butler detail page.
 *
 * Renders the StateBrowser with a "Set Value" button to create new entries.
 * Uses useButlerState, useSetState, and useDeleteState hooks for data fetching
 * and mutations.
 */

import { useState } from "react";

import StateBrowser from "@/components/state/StateBrowser";
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
import { Textarea } from "@/components/ui/textarea";
import { useButlerState, useDeleteState, useSetState } from "@/hooks/use-state";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ButlerStateTabProps {
  butlerName: string;
}

// ---------------------------------------------------------------------------
// Set Value Dialog
// ---------------------------------------------------------------------------

function SetValueDialog({
  open,
  onOpenChange,
  onSave,
  isPending,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (key: string, value: unknown) => void;
  isPending: boolean;
}) {
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);

  const handleOpenChange = (nextOpen: boolean) => {
    if (nextOpen) {
      setKey("");
      setValue("");
      setParseError(null);
    }
    onOpenChange(nextOpen);
  };

  function handleSave() {
    try {
      const parsed = JSON.parse(value) as unknown;
      setParseError(null);
      onSave(key, parsed);
      onOpenChange(false);
    } catch {
      setParseError("Invalid JSON. Please check the value and try again.");
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Set Value</DialogTitle>
          <DialogDescription>
            Create or overwrite a key-value pair in the state store
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <label htmlFor="new-state-key" className="text-sm font-medium">
              Key
            </label>
            <Input
              id="new-state-key"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="e.g. config.theme"
            />
          </div>
          <div className="space-y-2">
            <label htmlFor="new-state-value" className="text-sm font-medium">
              Value (JSON)
            </label>
            <Textarea
              id="new-state-value"
              value={value}
              onChange={(e) => {
                setValue(e.target.value);
                setParseError(null);
              }}
              placeholder='{"key": "value"}'
              className="min-h-32 font-mono text-sm"
            />
            {parseError && (
              <p className="text-sm text-destructive">{parseError}</p>
            )}
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={handleSave}
            disabled={!key.trim() || !value.trim() || isPending}
          >
            {isPending ? "Saving..." : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// ButlerStateTab
// ---------------------------------------------------------------------------

export default function ButlerStateTab({ butlerName }: ButlerStateTabProps) {
  const { data: stateResponse, isLoading, isError, error } = useButlerState(butlerName);
  const setStateMutation = useSetState(butlerName);
  const deleteStateMutation = useDeleteState(butlerName);
  const [setDialogOpen, setSetDialogOpen] = useState(false);

  const entries = stateResponse?.data ?? [];

  function handleEdit(key: string, value: unknown) {
    setStateMutation.mutate({ key, value });
  }

  function handleDelete(key: string) {
    deleteStateMutation.mutate(key);
  }

  function handleSetValue(key: string, value: unknown) {
    setStateMutation.mutate({ key, value });
  }

  if (isError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>State Store</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-destructive">
            Failed to load state: {error instanceof Error ? error.message : "Unknown error"}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div className="space-y-1">
            <CardTitle>State Store</CardTitle>
            <CardDescription>
              Key-value state entries for this butler ({entries.length} entries)
            </CardDescription>
          </div>
          <Button onClick={() => setSetDialogOpen(true)}>Set Value</Button>
        </CardHeader>
        <CardContent>
          <StateBrowser
            entries={entries}
            isLoading={isLoading}
            onEdit={handleEdit}
            onDelete={handleDelete}
          />
        </CardContent>
      </Card>

      {/* Set value dialog */}
      <SetValueDialog
        open={setDialogOpen}
        onOpenChange={setSetDialogOpen}
        onSave={handleSetValue}
        isPending={setStateMutation.isPending}
      />
    </div>
  );
}
