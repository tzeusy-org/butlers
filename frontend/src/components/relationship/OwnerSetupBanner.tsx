/**
 * OwnerSetupBanner
 *
 * Shown at the top of the /contacts/ page when the owner contact is missing
 * key identity fields (name, email, or telegram handle). Prompts the user
 * to fill them in via an inline dialog so that external syncs (e.g. Google
 * Contacts) can match the owner correctly instead of creating duplicates.
 *
 * An expandable "Credentials" section lets the user optionally set secrets
 * (email password, Telegram API hash/ID) that were previously stored in
 * butler_secrets but now live on the owner contact_info.
 */

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
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
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [email, setEmail] = useState("");
  const [telegram, setTelegram] = useState("");
  const [telegramChatId, setTelegramChatId] = useState("");

  // Credential fields (collapsible)
  const [showCredentials, setShowCredentials] = useState(false);
  const [emailPassword, setEmailPassword] = useState("");
  const [telegramApiHash, setTelegramApiHash] = useState("");
  const [telegramApiId, setTelegramApiId] = useState("");

  // Don't render if loading or if all core identity fields are configured
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
    const trimmedFirst = firstName.trim();
    const trimmedLast = lastName.trim();
    const trimmedEmail = email.trim();
    const trimmedTelegram = telegram.trim();
    const trimmedChatId = telegramChatId.trim();
    const trimmedEmailPw = emailPassword.trim();
    const trimmedApiHash = telegramApiHash.trim();
    const trimmedApiId = telegramApiId.trim();

    if (
      !trimmedFirst &&
      !trimmedLast &&
      !trimmedEmail &&
      !trimmedTelegram &&
      !trimmedChatId &&
      !trimmedEmailPw &&
      !trimmedApiHash &&
      !trimmedApiId
    ) {
      toast.error("Please fill in at least your first name.");
      return;
    }

    try {
      const promises: Promise<unknown>[] = [];

      if (trimmedFirst || trimmedLast) {
        promises.push(
          patchContact.mutateAsync({
            contactId,
            request: {
              first_name: trimmedFirst || null,
              last_name: trimmedLast || null,
            },
          }),
        );
      }

      // --- Identity fields ---

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

      // --- Secured credential fields ---

      if (trimmedEmailPw) {
        promises.push(
          createInfo.mutateAsync({
            contactId,
            request: {
              type: "email_password",
              value: trimmedEmailPw,
              is_primary: true,
              secured: true,
            },
          }),
        );
      }

      if (trimmedApiHash) {
        promises.push(
          createInfo.mutateAsync({
            contactId,
            request: {
              type: "telegram_api_hash",
              value: trimmedApiHash,
              is_primary: true,
              secured: true,
            },
          }),
        );
      }

      if (trimmedApiId) {
        promises.push(
          createInfo.mutateAsync({
            contactId,
            request: {
              type: "telegram_api_id",
              value: trimmedApiId,
              is_primary: true,
              secured: true,
            },
          }),
        );
      }

      await Promise.all(promises);
      toast.success("Owner identity updated.");
      setOpen(false);
      setFirstName("");
      setLastName("");
      setEmail("");
      setTelegram("");
      setTelegramChatId("");
      setEmailPassword("");
      setTelegramApiHash("");
      setTelegramApiId("");
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
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-2">
                    <Label htmlFor="owner-first-name">First name</Label>
                    <Input
                      id="owner-first-name"
                      type="text"
                      placeholder="Jane"
                      value={firstName}
                      onChange={(e) => setFirstName(e.target.value)}
                      disabled={isSaving}
                      autoFocus
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="owner-last-name">Last name</Label>
                    <Input
                      id="owner-last-name"
                      type="text"
                      placeholder="Doe"
                      value={lastName}
                      onChange={(e) => setLastName(e.target.value)}
                      disabled={isSaving}
                    />
                  </div>
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
                    Numeric ID used for bot messaging. Send /start to @userinfobot to find
                    yours.
                  </p>
                </div>
              )}

              {/* Collapsible credentials section */}
              <div className="border-t pt-3">
                <button
                  type="button"
                  className="flex w-full items-center gap-1.5 text-sm font-medium text-muted-foreground hover:text-foreground"
                  onClick={() => setShowCredentials((v) => !v)}
                >
                  {showCredentials ? (
                    <ChevronDown className="h-4 w-4" />
                  ) : (
                    <ChevronRight className="h-4 w-4" />
                  )}
                  Credentials (optional)
                </button>
                {showCredentials && (
                  <div className="mt-3 space-y-4">
                    <p className="text-xs text-muted-foreground">
                      These were previously stored in /secrets. They are now managed as
                      secured entries on your owner identity.
                    </p>
                    <div className="space-y-2">
                      <Label htmlFor="owner-email-pw">Email password / app password</Label>
                      <Input
                        id="owner-email-pw"
                        type="password"
                        placeholder="••••••••"
                        value={emailPassword}
                        onChange={(e) => setEmailPassword(e.target.value)}
                        disabled={isSaving}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="owner-tg-api-id">Telegram API ID</Label>
                      <Input
                        id="owner-tg-api-id"
                        type="text"
                        placeholder="12345678"
                        value={telegramApiId}
                        onChange={(e) => setTelegramApiId(e.target.value)}
                        disabled={isSaving}
                      />
                      <p className="text-xs text-muted-foreground">
                        From my.telegram.org &mdash; used for user-client (MTProto) connections.
                      </p>
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="owner-tg-api-hash">Telegram API hash</Label>
                      <Input
                        id="owner-tg-api-hash"
                        type="password"
                        placeholder="••••••••"
                        value={telegramApiHash}
                        onChange={(e) => setTelegramApiHash(e.target.value)}
                        disabled={isSaving}
                      />
                    </div>
                  </div>
                )}
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
