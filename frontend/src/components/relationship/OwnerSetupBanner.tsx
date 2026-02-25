/**
 * OwnerSetupBanner
 *
 * Shown at the top of the /contacts/ page when the owner contact is missing
 * key identity fields (name, email, or telegram handle). Prompts the user
 * to fill them in via an inline dialog so that external syncs (e.g. Google
 * Contacts) can match the owner correctly instead of creating duplicates.
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
import {
  useCreateContactInfo,
  useOwnerSetupStatus,
  usePatchContact,
} from "@/hooks/use-contacts";

export function OwnerSetupBanner() {
  const { data: status, isLoading } = useOwnerSetupStatus();
  const createInfo = useCreateContactInfo();
  const patchContact = usePatchContact();

  const [open, setOpen] = useState(false);
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [telegram, setTelegram] = useState("");
  const [telegramChatId, setTelegramChatId] = useState("");

  // Don't render if loading or if all identity fields are already configured
  if (isLoading) return null;
  if (!status) return null;
  if (
    status.has_name &&
    status.has_telegram &&
    status.has_telegram_chat_id &&
    status.has_email
  )
    return null;
  if (!status.contact_id) return null;

  const contactId = status.contact_id;
  const isSaving = createInfo.isPending || patchContact.isPending;

  // Build a human-readable list of what's missing
  const missing: string[] = [];
  if (!status.has_name) missing.push("name");
  if (!status.has_email) missing.push("email");
  if (!status.has_telegram) missing.push("Telegram handle");
  if (!status.has_telegram_chat_id) missing.push("Telegram chat ID");

  async function handleSave() {
    const trimmedName = fullName.trim();
    const trimmedEmail = email.trim();
    const trimmedTelegram = telegram.trim();
    const trimmedChatId = telegramChatId.trim();

    // Must provide at least a name or one identifier
    if (!trimmedName && !trimmedEmail && !trimmedTelegram && !trimmedChatId) {
      toast.error("Please fill in at least your name.");
      return;
    }

    try {
      const promises: Promise<unknown>[] = [];

      if (trimmedName) {
        promises.push(
          patchContact.mutateAsync({
            contactId,
            request: { full_name: trimmedName },
          }),
        );
      }

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

      if (trimmedChatId) {
        promises.push(
          createInfo.mutateAsync({
            contactId,
            request: { type: "telegram_chat_id", value: trimmedChatId, is_primary: true },
          }),
        );
      }

      await Promise.all(promises);
      toast.success("Owner identity updated.");
      setOpen(false);
      setFullName("");
      setEmail("");
      setTelegram("");
      setTelegramChatId("");
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
            Owner identity incomplete
          </p>
          <p className="mt-0.5 text-xs text-amber-700 dark:text-amber-300">
            Missing: {missing.join(", ")}. Fill these in so butlers can recognise you and
            contact syncs don&apos;t create duplicates.
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
                Fill in your real name and contact details so butlers can recognise you
                across channels and syncs match correctly.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-2">
              {!status.has_name && (
                <div className="space-y-2">
                  <Label htmlFor="owner-name">Full name</Label>
                  <Input
                    id="owner-name"
                    type="text"
                    placeholder="Jane Doe"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    disabled={isSaving}
                    autoFocus
                  />
                </div>
              )}
              {!status.has_email && (
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
              )}
              {!status.has_telegram && (
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
              )}
              {!status.has_telegram_chat_id && (
                <div className="space-y-2">
                  <Label htmlFor="owner-telegram-chat-id">Telegram chat ID</Label>
                  <Input
                    id="owner-telegram-chat-id"
                    type="text"
                    placeholder="123456789"
                    value={telegramChatId}
                    onChange={(e) => setTelegramChatId(e.target.value)}
                    disabled={isSaving}
                  />
                  <p className="text-xs text-muted-foreground">
                    Numeric ID used for bot messaging. Send /start to @userinfobot to find yours.
                  </p>
                </div>
              )}
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
