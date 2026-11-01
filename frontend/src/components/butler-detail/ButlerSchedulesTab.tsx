import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import type { Schedule, ScheduleToggleResult } from "@/api/types.ts";
import { ScheduleForm } from "@/components/schedules/ScheduleForm";
import type { ScheduleFormValues } from "@/components/schedules/ScheduleForm";
import { ScheduleTable } from "@/components/schedules/ScheduleTable";
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
import {
  useCreateSchedule,
  useDeleteSchedule,
  useSchedules,
  useToggleSchedule,
  useTriggerSchedule,
  useUpdateSchedule,
} from "@/hooks/use-schedules";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ButlerSchedulesTabProps {
  butlerName: string;
}

// ---------------------------------------------------------------------------
// ButlerSchedulesTab
// ---------------------------------------------------------------------------

export default function ButlerSchedulesTab({ butlerName }: ButlerSchedulesTabProps) {
  // A cached sibling route can change the owner without unmounting this tab.
  // Retire its fields, mutation state and callback lifetime as one instance.
  return <ButlerSchedulesTabInstance key={butlerName} butlerName={butlerName} />;
}

function ButlerSchedulesTabInstance({ butlerName }: ButlerSchedulesTabProps) {
  const { data: schedulesResponse, isLoading, isError, error } = useSchedules(butlerName);
  const schedules = schedulesResponse?.data ?? [];

  // Form dialog state
  const [formOpen, setFormOpen] = useState(false);
  const formEpoch = useRef(0);
  const [formError, setFormError] = useState<string | null>(null);
  useEffect(() => () => { formEpoch.current++; }, []);
  const [editingSchedule, setEditingSchedule] = useState<Schedule | null>(null);

  // Delete confirmation dialog state
  const [deleteTarget, setDeleteTarget] = useState<Schedule | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  // Mutations
  const createMutation = useCreateSchedule(butlerName);
  const updateMutation = useUpdateSchedule(butlerName);
  const deleteMutation = useDeleteSchedule(butlerName);
  const toggleMutation = useToggleSchedule(butlerName);
  const triggerMutation = useTriggerSchedule(butlerName);

  // Track which schedule is currently being triggered
  const [triggeringId, setTriggeringId] = useState<string | null>(null);
  const [togglingIds, setTogglingIds] = useState<ReadonlySet<string>>(new Set());
  const [toggleReceipt, setToggleReceipt] = useState<ScheduleToggleResult | null>(null);

  // ---------------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------------

  function handleAddClick() {
    formEpoch.current++; setFormError(null);
    setEditingSchedule(null);
    setFormOpen(true);
  }

  function handleEdit(schedule: Schedule) {
    formEpoch.current++; setFormError(null);
    setEditingSchedule(schedule);
    setFormOpen(true);
  }

  async function handleToggle(schedule: Schedule) {
    const requestedEnabled = !schedule.enabled;
    setTogglingIds((pending) => new Set(pending).add(schedule.id));
    setToggleReceipt(null);
    try {
      const response = await toggleMutation.mutateAsync({
        scheduleId: schedule.id,
        enabled: requestedEnabled,
      });
      const receipt = response.data;
      setToggleReceipt(receipt);
      toast.success(
        `${receipt.observed_enabled ? "Event resumed" : "Event paused"} (Schedule "${schedule.name}" confirmed by server)`,
      );
    } catch (err) {
      toast.error(`Failed to toggle schedule: ${err instanceof Error ? err.message : "Unknown error"}`);
    } finally {
      setTogglingIds((pending) => {
        const remaining = new Set(pending);
        remaining.delete(schedule.id);
        return remaining;
      });
    }
  }

  function handleTrigger(schedule: Schedule) {
    setTriggeringId(schedule.id);
    triggerMutation.mutate(schedule.id, {
      onSuccess: () => {
        toast.success(`Schedule "${schedule.name}" triggered`);
        setTriggeringId(null);
      },
      onError: (err) => {
        toast.error(`Failed to trigger schedule: ${err instanceof Error ? err.message : "Unknown error"}`);
        setTriggeringId(null);
      },
    });
  }

  function handleDeleteClick(schedule: Schedule) {
    setDeleteTarget(schedule);
    setDeleteDialogOpen(true);
  }

  function handleDeleteConfirm() {
    if (!deleteTarget) return;
    deleteMutation.mutate(deleteTarget.id, {
      onSuccess: () => {
        toast.success(`Schedule "${deleteTarget.name}" deleted`);
        setDeleteDialogOpen(false);
        setDeleteTarget(null);
      },
      onError: (err) => {
        toast.error(`Failed to delete schedule: ${err instanceof Error ? err.message : "Unknown error"}`);
      },
    });
  }

  async function handleFormSubmit(values: ScheduleFormValues) {
    const epoch = formEpoch.current;
    setFormError(null);
    try {
      if (editingSchedule) {
        await updateMutation.mutateAsync({ scheduleId: editingSchedule.id, body: values });
        if (epoch === formEpoch.current) toast.success(`Schedule "${values.name}" updated`);
      } else {
        await createMutation.mutateAsync(values);
        if (epoch === formEpoch.current) toast.success(`Schedule "${values.name}" created`);
      }
    } catch (err) {
      if (epoch === formEpoch.current) {
        const message = err instanceof Error ? err.message : "Unknown error";
        setFormError(message);
        toast.error(`Failed to ${editingSchedule ? "update" : "create"} schedule: ${message}`);
      }
      throw err;
    }
  }

  // ---------------------------------------------------------------------------
  // Error state
  // ---------------------------------------------------------------------------

  if (isError) {
    return (
      <Section>
        <SectionHeader>
          <SectionTitle>Schedules</SectionTitle>
        </SectionHeader>
        <SectionContent>
          <p className="text-sm text-destructive">
            Failed to load schedules: {error instanceof Error ? error.message : "Unknown error"}
          </p>
        </SectionContent>
      </Section>
    );
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="space-y-4">
      <Section>
        <SectionHeader className="flex flex-row items-center justify-between">
          <div>
            <SectionTitle>Schedules</SectionTitle>
            <SectionDescription>Scheduled tasks for this butler</SectionDescription>
          </div>
          <Button onClick={handleAddClick}>Add schedule</Button>
        </SectionHeader>
        <SectionContent>
          <ScheduleTable
            schedules={schedules}
            isLoading={isLoading}
            onToggle={handleToggle}
            onTrigger={handleTrigger}
            onEdit={handleEdit}
            onDelete={handleDeleteClick}
            triggeringId={triggeringId}
            togglingIds={togglingIds}
          />
        </SectionContent>
      </Section>

      {toggleReceipt && (
        <p className="text-sm text-muted-foreground" role="status">
          Server confirmed schedule &quot;{toggleReceipt.name}&quot; is {toggleReceipt.observed_enabled ? "enabled" : "disabled"}.
          Audit receipt: {toggleReceipt.audit.action} ({toggleReceipt.audit.result}).
        </p>
      )}

      {/* Create / Edit form dialog */}
      <ScheduleForm
        butlerName={butlerName}
        schedule={editingSchedule}
        open={formOpen}
        onOpenChange={(open) => {
          formEpoch.current++; setFormError(null);
          setFormOpen(open);
          if (!open) setEditingSchedule(null);
        }}
        onSubmit={handleFormSubmit}
        isSubmitting={createMutation.isPending || updateMutation.isPending}
        error={formError}
      />

      {/* Delete confirmation dialog */}
      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Schedule</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete the schedule "{deleteTarget?.name}"? This action
              cannot be undone.
            </DialogDescription>
          </DialogHeader>
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
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? "Deleting..." : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
