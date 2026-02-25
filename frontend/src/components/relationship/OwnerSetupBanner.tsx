/**
 * OwnerSetupBanner
 *
 * Shown at the top of the /contacts/ page when the owner contact has no
 * identifiers configured (no telegram handle, no email address). Prompts
 * the user to set up their identity via an inline dialog.
 */

import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useCreateContactInfo, useOwnerSetupStatus } from "@/hooks/use-contacts";

export function OwnerSetupBanner() {
  const { data: status, isLoading } = useOwnerSetupStatus();
  const createInfo = useCreateContactInfo();

  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [telegram, setTelegram] = useState("");

  // Don't render if loading or if identifiers are configured
  if (isLoading) return null;
  if (!status) return null;
  if (status.has_telegram || status.has_email) return null;
  if (!status.contact_id) return null;

  const contactId = status.contact_id;
  const isSaving = createInfo.isPending;

  async function handleSave() {
    const trimmedEmail = email.trim();
    const trimmedTelegram = telegram.trim();

    if (!trimmedEmail && !trimmedTelegram) {
      toast.error("Please enter at least an email or Telegram handle.");
      return;
    }

    try {
      const promises: Promise<unknown>[] = [];

      if (trimmedEmail) {
        promises.push(
          createInfo.mutateAsync({
            contactId,
            request: { type: "email", value: trimmedEmail, is_primary: true },
          }),
        );
      }

      if (trimmedTelegram) {
        promises.push(
          createInfo.mutateAsync({
            contactId,
            request: { type: "telegram", value: trimmedTelegram, is_primary: true },
          }),
        );
      }

      await Promise.all(promises);
      toast.success("Identity configured successfully.");
      setOpen(false);
      setEmail("");
      setTelegram("");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error";
      toast.error(`Failed to save identity: ${message}`);
    }
  }

  return (
    <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 dark:border-amber-700 dark:bg-amber-950">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-amber-900 dark:text-amber-100">
            Owner identity not configured
          </p>
          <p className="mt-0.5 text-xs text-amber-700 dark:text-amber-300">
            Your owner contact has no identifiers (email or Telegram handle). Configure your
            identity so butlers can recognize you across channels.
          </p>
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button
              size="sm"
              variant="outline"
              className="shrink-0 border-amber-400 text-amber-900 hover:bg-amber-100 dark:border-amber-600 dark:text-amber-100"
            >
              Set Up Identity
            </Button>
          </DialogTrigger>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Set Up Owner Identity</DialogTitle>
              <DialogDescription>
                Add your email and/or Telegram handle so butlers can recognize you across channels.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-2">
              <div className="space-y-2">
                <Label htmlFor="owner-email">Email</Label>
                <Input
                  id="owner-email"
                  type="email"
                  placeholder="you@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  disabled={isSaving}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="owner-telegram">Telegram handle</Label>
                <Input
                  id="owner-telegram"
                  type="text"
                  placeholder="@username"
                  value={telegram}
                  onChange={(e) => setTelegram(e.target.value)}
                  disabled={isSaving}
                />
              </div>
            </div>
            <DialogFooter>
              <Button onClick={handleSave} disabled={isSaving}>
                {isSaving ? "Saving..." : "Save"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
}
