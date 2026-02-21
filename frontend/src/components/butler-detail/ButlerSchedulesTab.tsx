import { useState } from "react";
import { toast } from "sonner";

import type { Schedule } from "@/api/types.ts";
import { ScheduleForm } from "@/components/schedules/ScheduleForm";
import type { ScheduleFormValues } from "@/components/schedules/ScheduleForm";
import { ScheduleTable } from "@/components/schedules/ScheduleTable";
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
import {
  useCreateSchedule,
  useDeleteSchedule,
  useSchedules,
  useToggleSchedule,
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
  const { data: schedulesResponse, isLoading, isError, error } = useSchedules(butlerName);
  const schedules = schedulesResponse?.data ?? [];

  // Form dialog state
  const [formOpen, setFormOpen] = useState(false);
  const [editingSchedule, setEditingSchedule] = useState<Schedule | null>(null);

  // Delete confirmation dialog state
  const [deleteTarget, setDeleteTarget] = useState<Schedule | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  // Mutations
  const createMutation = useCreateSchedule(butlerName);
  const updateMutation = useUpdateSchedule(butlerName);
  const deleteMutation = useDeleteSchedule(butlerName);
  const toggleMutation = useToggleSchedule(butlerName);

  // ---------------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------------

  function handleAddClick() {
    setEditingSchedule(null);
    setFormOpen(true);
  }

  function handleEdit(schedule: Schedule) {
    setEditingSchedule(schedule);
    setFormOpen(true);
  }

  function handleToggle(schedule: Schedule) {
    toggleMutation.mutate(schedule.id, {
      onSuccess: () => {
        toast.success(`Schedule "${schedule.name}" ${schedule.enabled ? "disabled" : "enabled"}`);
      },
      onError: (err) => {
        toast.error(`Failed to toggle schedule: ${err instanceof Error ? err.message : "Unknown error"}`);
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

  function handleFormSubmit(values: ScheduleFormValues) {
    if (editingSchedule) {
      updateMutation.mutate(
        { scheduleId: editingSchedule.id, body: values },
        {
          onSuccess: () => {
            toast.success(`Schedule "${values.name}" updated`);
            setFormOpen(false);
            setEditingSchedule(null);
          },
          onError: (err) => {
            toast.error(`Failed to update schedule: ${err instanceof Error ? err.message : "Unknown error"}`);
          },
        },
      );
    } else {
      createMutation.mutate(values, {
        onSuccess: () => {
          toast.success(`Schedule "${values.name}" created`);
          setFormOpen(false);
        },
        onError: (err) => {
          toast.error(`Failed to create schedule: ${err instanceof Error ? err.message : "Unknown error"}`);
        },
      });
    }
  }

  // ---------------------------------------------------------------------------
  // Error state
  // ---------------------------------------------------------------------------

  if (isError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Schedules</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-destructive">
            Failed to load schedules: {error instanceof Error ? error.message : "Unknown error"}
          </p>
        </CardContent>
      </Card>
    );
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle>Schedules</CardTitle>
            <CardDescription>Scheduled tasks for this butler</CardDescription>
          </div>
          <Button onClick={handleAddClick}>Add Schedule</Button>
        </CardHeader>
        <CardContent>
          <ScheduleTable
            schedules={schedules}
            isLoading={isLoading}
            onToggle={handleToggle}
            onEdit={handleEdit}
            onDelete={handleDeleteClick}
          />
        </CardContent>
      </Card>

      {/* Create / Edit form dialog */}
      <ScheduleForm
        schedule={editingSchedule}
        open={formOpen}
        onOpenChange={(open) => {
          setFormOpen(open);
          if (!open) setEditingSchedule(null);
        }}
        onSubmit={handleFormSubmit}
        isSubmitting={createMutation.isPending || updateMutation.isPending}
        error={
          createMutation.error
            ? createMutation.error instanceof Error
              ? createMutation.error.message
              : "Failed to create schedule"
            : updateMutation.error
              ? updateMutation.error instanceof Error
                ? updateMutation.error.message
                : "Failed to update schedule"
              : null
        }
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
