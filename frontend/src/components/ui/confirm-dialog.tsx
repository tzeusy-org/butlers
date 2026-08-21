/**
 * ConfirmDialog — shared evidence-optional confirm for consequential
 * dashboard actions (bu-ep4ks.11 — the safety envelope for destructive/bulk
 * actions).
 *
 * Generalizes the pattern QaOverviewPage's ResetBreakerDialog already
 * established (bu-533qx.2): open the dialog instead of firing the mutation on
 * click, keep it mounted through the mutation so its pending state stays
 * visible, and disable both actions while pending so a second click can never
 * double-fire. Replaces bare `window.confirm` call sites, which offer no
 * evidence, no visual consistency with the rest of the fleet, and cannot show
 * a pending state.
 *
 * Use for actions that are irreversible or bulk (no undo-window makes sense —
 * see useUndoWindow in use-undo-window.ts for the reversible-action sibling).
 */
import type { ReactNode } from "react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

export interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  /** Extra evidence content rendered between the header and the footer (e.g. a list of affected rows). */
  children?: ReactNode;
  confirmLabel?: string;
  /** Label shown on the confirm button while `pending` is true. Defaults to `confirmLabel`. */
  pendingLabel?: string;
  cancelLabel?: string;
  /** "destructive" for irreversible/dangerous actions; "default" otherwise. */
  variant?: "default" | "destructive";
  /** True while the confirmed action's mutation is in flight — disables both buttons and keeps the dialog mounted. */
  pending?: boolean;
  onConfirm: () => void;
  /**
   * Escape hatch for focus on close. Radix returns focus to whatever was
   * focused when the dialog opened, which is `<body>` when the trigger was
   * activated by a pointer click. Call `event.preventDefault()` and focus the
   * element yourself to override it.
   */
  onCloseAutoFocus?: (event: Event) => void;
  testId?: string;
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  confirmLabel = "Confirm",
  pendingLabel,
  cancelLabel = "Cancel",
  variant = "default",
  pending = false,
  onConfirm,
  onCloseAutoFocus,
  testId,
}: ConfirmDialogProps) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent data-testid={testId} onCloseAutoFocus={onCloseAutoFocus}>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          {description && <AlertDialogDescription>{description}</AlertDialogDescription>}
        </AlertDialogHeader>
        {children}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>{cancelLabel}</AlertDialogCancel>
          <AlertDialogAction
            variant={variant}
            disabled={pending}
            data-testid={testId ? `${testId}-confirm` : undefined}
            onClick={(event) => {
              // Keep the dialog mounted through the mutation so its pending
              // state is visible; the caller closes it on settle.
              event.preventDefault();
              onConfirm();
            }}
          >
            {pending ? (pendingLabel ?? confirmLabel) : confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
