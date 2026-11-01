import { useDraft } from "@/hooks/use-draft";
import { DraftNotice } from "@/components/ui/draft-notice";
/**
 * ButlerStateTab — state store tab for the butler detail page.
 *
 * Renders the StateBrowser with a "Set value" button to create new entries.
 * Uses useButlerState, useSetState, and useDeleteState hooks for data fetching
 * and mutations.
 */

import { useState } from "react";
import { toast } from "sonner";

import StateBrowser from "@/components/state/StateBrowser";
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
  butlerName,
  open,
  onOpenChange,
  onSave,
  isPending,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  butlerName: string;
  onSave: (key: string, value: unknown) => Promise<unknown>;
  isPending: boolean;
}) {
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);

  const draft = useDraft("butler-state.set", [butlerName, "create"], { key, value }, (v) => { setKey(v.key); setValue(v.value); }, open, { key: "", value: "" });

  const handleOpenChange = (nextOpen: boolean) => {
    if (nextOpen) {
      setKey("");
      setValue("");
      setParseError(null);
    }
    if (!nextOpen) draft.dismiss(() => onOpenChange(false)); else onOpenChange(true);
  };

  async function handleSave() {
    let parsed: unknown;
    try { parsed = JSON.parse(value); } catch {
      setParseError("Invalid JSON. Please check the value and try again.");
      return;
    }
    setParseError(null);
    const submission = draft.capture();
    const preparation = draft.prepare();
    if (preparation instanceof Promise ? !await preparation : !preparation) return;
    try {
      await onSave(key, parsed);
      if (draft.current(submission)) toast.success(`State key "${key}" saved`);
      await draft.submitted(submission, () => onOpenChange(false));
    } catch {
      if (draft.current(submission)) setParseError("Could not save the value. Your changes are still here. Try again.");
    } finally { draft.finish(); }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Set value</DialogTitle>
          <DialogDescription>
            Create or overwrite a key-value pair in the state store
          </DialogDescription>
        </DialogHeader>
        <DraftNotice session={draft.session} />
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
          <Button variant="outline" onClick={() => draft.dismiss(() => onOpenChange(false))}>
            Cancel
          </Button>
          <Button
            onClick={handleSave}
            disabled={!key.trim() || !value.trim() || isPending || draft.session?.presentation.submitting || draft.session?.presentation.cleanup}
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
  // A cached sibling route can change the owner without unmounting this tab.
  // Retire its fields, mutation state and callback lifetime as one instance.
  return <ButlerStateTabInstance key={butlerName} butlerName={butlerName} />;
}

function ButlerStateTabInstance({ butlerName }: ButlerStateTabProps) {
  const { data: stateResponse, isLoading, isError, error } = useButlerState(butlerName);
  const setStateMutation = useSetState(butlerName, { notify: false });
  const deleteStateMutation = useDeleteState(butlerName);
  const [setDialogOpen, setSetDialogOpen] = useState(false);

  const entries = stateResponse?.data ?? [];

  function handleEdit(key: string, value: unknown) {
    return setStateMutation.mutateAsync({ key, value });
  }

  function handleDelete(key: string) {
    deleteStateMutation.mutate(key);
  }

  function handleSetValue(key: string, value: unknown) {
    return setStateMutation.mutateAsync({ key, value });
  }

  if (isError) {
    return (
      <Section>
        <SectionHeader>
          <SectionTitle>State Store</SectionTitle>
        </SectionHeader>
        <SectionContent>
          <p className="text-sm text-destructive">
            Failed to load state: {error instanceof Error ? error.message : "Unknown error"}
          </p>
        </SectionContent>
      </Section>
    );
  }

  return (
    <div className="space-y-4">
      <Section>
        <SectionHeader className="flex flex-row items-center justify-between space-y-0">
          <div className="space-y-1">
            <SectionTitle>State Store</SectionTitle>
            <SectionDescription>
              Key-value state entries for this butler ({entries.length} entries)
            </SectionDescription>
          </div>
          <Button onClick={() => setSetDialogOpen(true)}>Set value</Button>
        </SectionHeader>
        <SectionContent>
          <StateBrowser
            butlerName={butlerName}
            entries={entries}
            isLoading={isLoading}
            onEdit={handleEdit}
            onDelete={handleDelete}
          />
        </SectionContent>
      </Section>

      {/* Set value dialog */}
      <SetValueDialog
        butlerName={butlerName}
        open={setDialogOpen}
        onOpenChange={setSetDialogOpen}
        onSave={handleSetValue}
        isPending={setStateMutation.isPending}
      />
    </div>
  );
}
