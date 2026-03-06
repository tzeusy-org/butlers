/**
 * OwnerSetupBanner
 *
 * Shown on the entity detail page when the owner entity is missing key
 * identity fields (name, email, or telegram handle). Prompts the user
 * to fill them in via an inline dialog so that external syncs (e.g. Google
 * Contacts) can match the owner correctly instead of creating duplicates.
 *
 * An expandable "Credentials" section lets the user optionally set credentials
 * (email password, Telegram API hash/ID/session, Home Assistant URL/token)
 * stored as entity_info entries.
 */

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { toast } from "sonner";

import type { EntityDetail } from "@/api/types";
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
  useCreateEntityInfo,
  useUpdateEntity,
} from "@/hooks/use-memory";

interface OwnerSetupBannerProps {
  entity: EntityDetail;
}

function hasInfoType(entity: EntityDetail, type: string): boolean {
  return (entity.entity_info ?? []).some((e) => e.type === type);
}

export function OwnerSetupBanner({ entity }: OwnerSetupBannerProps) {
  const createInfo = useCreateEntityInfo();
  const updateEntity = useUpdateEntity();

  const [open, setOpen] = useState(false);
  const [canonicalName, setCanonicalName] = useState("");
  const [email, setEmail] = useState("");
  const [telegram, setTelegram] = useState("");
  const [telegramChatId, setTelegramChatId] = useState("");

  // Credential fields (collapsible)
  const [showCredentials, setShowCredentials] = useState(false);
  const [emailPassword, setEmailPassword] = useState("");
  const [telegramApiHash, setTelegramApiHash] = useState("");
  const [telegramApiId, setTelegramApiId] = useState("");
  const [telegramUserSession, setTelegramUserSession] = useState("");
  const [homeAssistantUrl, setHomeAssistantUrl] = useState("");
  const [homeAssistantToken, setHomeAssistantToken] = useState("");

  // Don't render if not the owner entity
  if (!entity.roles?.includes("owner")) return null;

  // Check what's missing
  const nameIsPlaceholder =
    !entity.canonical_name?.trim() ||
    entity.canonical_name.trim().toLowerCase() === "owner";
  const hasEmail = hasInfoType(entity, "email");
  const hasTelegram = hasInfoType(entity, "telegram");
  const hasTelegramChatId = hasInfoType(entity, "telegram_chat_id");

  // Don't render if all core identity fields are configured
  if (!nameIsPlaceholder && hasEmail && hasTelegram && hasTelegramChatId) {
    return null;
  }

  const entityId = entity.id;
  const isSaving = createInfo.isPending || updateEntity.isPending;

  // Build a human-readable list of what's missing
  const missing: string[] = [];
  if (nameIsPlaceholder) missing.push("name");
  if (!hasEmail) missing.push("email");
  if (!hasTelegram) missing.push("Telegram handle");
  if (!hasTelegramChatId) missing.push("Telegram chat ID");

  async function handleSave() {
    const trimmedName = canonicalName.trim();
    const trimmedEmail = email.trim();
    const trimmedTelegram = telegram.trim();
    const trimmedChatId = telegramChatId.trim();
    const trimmedEmailPw = emailPassword.trim();
    const trimmedApiHash = telegramApiHash.trim();
    const trimmedApiId = telegramApiId.trim();
    const trimmedTelegramUserSession = telegramUserSession.trim();
    const trimmedHomeAssistantUrl = homeAssistantUrl.trim();
    const trimmedHomeAssistantToken = homeAssistantToken.trim();

    if (
      !trimmedName &&
      !trimmedEmail &&
      !trimmedTelegram &&
      !trimmedChatId &&
      !trimmedEmailPw &&
      !trimmedApiHash &&
      !trimmedApiId &&
      !trimmedTelegramUserSession &&
      !trimmedHomeAssistantUrl &&
      !trimmedHomeAssistantToken
    ) {
      toast.error("Please fill in at least one field.");
      return;
    }

    try {
      const promises: Promise<unknown>[] = [];

      if (trimmedName) {
        promises.push(
          updateEntity.mutateAsync({
            entityId,
            request: { canonical_name: trimmedName },
          }),
        );
      }

      // --- Identity fields (entity_info) ---

      if (trimmedEmail) {
        promises.push(
          createInfo.mutateAsync({
            entityId,
            request: { type: "email", value: trimmedEmail, is_primary: true },
          }),
        );
      }

      if (trimmedTelegram) {
        promises.push(
          createInfo.mutateAsync({
            entityId,
            request: { type: "telegram", value: trimmedTelegram, is_primary: true },
          }),
        );
      }

      if (trimmedChatId) {
        promises.push(
          createInfo.mutateAsync({
            entityId,
            request: { type: "telegram_chat_id", value: trimmedChatId, is_primary: true },
          }),
        );
      }

      // --- Secured credential fields ---

      if (trimmedEmailPw) {
        promises.push(
          createInfo.mutateAsync({
            entityId,
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
            entityId,
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
            entityId,
            request: {
              type: "telegram_api_id",
              value: trimmedApiId,
              is_primary: true,
              secured: true,
            },
          }),
        );
      }

      if (trimmedTelegramUserSession) {
        promises.push(
          createInfo.mutateAsync({
            entityId,
            request: {
              type: "telegram_user_session",
              value: trimmedTelegramUserSession,
              is_primary: true,
              secured: true,
            },
          }),
        );
      }

      if (trimmedHomeAssistantUrl) {
        promises.push(
          createInfo.mutateAsync({
            entityId,
            request: {
              type: "home_assistant_url",
              value: trimmedHomeAssistantUrl,
              is_primary: true,
            },
          }),
        );
      }

      if (trimmedHomeAssistantToken) {
        promises.push(
          createInfo.mutateAsync({
            entityId,
            request: {
              type: "home_assistant_token",
              value: trimmedHomeAssistantToken,
              is_primary: true,
              secured: true,
            },
          }),
        );
      }

      await Promise.all(promises);
      toast.success("Owner identity updated.");
      setOpen(false);
      setCanonicalName("");
      setEmail("");
      setTelegram("");
      setTelegramChatId("");
      setEmailPassword("");
      setTelegramApiHash("");
      setTelegramApiId("");
      setTelegramUserSession("");
      setHomeAssistantUrl("");
      setHomeAssistantToken("");
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
              {nameIsPlaceholder && (
                <div className="space-y-2">
                  <Label htmlFor="owner-name">Name</Label>
                  <Input
                    id="owner-name"
                    type="text"
                    placeholder="Jane Doe"
                    value={canonicalName}
                    onChange={(e) => setCanonicalName(e.target.value)}
                    disabled={isSaving}
                    autoFocus
                  />
                </div>
              )}
              {!hasEmail && (
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
              {!hasTelegram && (
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
              {!hasTelegramChatId && (
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
                      These are stored on your owner entity; sensitive values are secured.
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
                    <div className="space-y-2">
                      <Label htmlFor="owner-tg-user-session">Telegram user session</Label>
                      <Input
                        id="owner-tg-user-session"
                        type="password"
                        placeholder="••••••••"
                        value={telegramUserSession}
                        onChange={(e) => setTelegramUserSession(e.target.value)}
                        disabled={isSaving}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="owner-home-assistant-url">Home Assistant URL</Label>
                      <Input
                        id="owner-home-assistant-url"
                        type="url"
                        placeholder="http://homeassistant.local:8123"
                        value={homeAssistantUrl}
                        onChange={(e) => setHomeAssistantUrl(e.target.value)}
                        disabled={isSaving}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="owner-home-assistant-token">Home Assistant token</Label>
                      <Input
                        id="owner-home-assistant-token"
                        type="password"
                        placeholder="••••••••"
                        value={homeAssistantToken}
                        onChange={(e) => setHomeAssistantToken(e.target.value)}
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
