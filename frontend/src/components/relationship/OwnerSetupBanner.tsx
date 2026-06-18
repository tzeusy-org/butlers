/**
 * OwnerSetupBanner
 *
 * Shown on the entity detail page when the owner entity is missing key
 * identity fields (name, email, or telegram handle). Prompts the user
 * to fill them in via an inline dialog so that external syncs (e.g. Google
 * Contacts) can match the owner correctly instead of creating duplicates.
 *
 * Non-secret channel handles (Telegram handle + chat ID) are written to the
 * relationship graph as has-handle contact facts (entity_facts) so the owner
 * becomes resolvable via resolve_contact_by_channel — telegram values are
 * stored in the canonical "telegram:<bare>" form by the backend. ONLY secured
 * credentials (Telegram API hash/ID, Home Assistant token) and whitelisted
 * technical config (Home Assistant URL) go to the entity_info secret store.
 *
 * An expandable "Credentials" section lets the user optionally set those
 * credentials.
 */

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { toast } from "sonner";

import type { EntityDetail } from "@/api/types";
import { useAddEntityContact, useEntityLinkedContacts } from "@/hooks/use-entities";
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

export function OwnerSetupBanner({ entity }: OwnerSetupBannerProps) {
  const createInfo = useCreateEntityInfo();
  const updateEntity = useUpdateEntity();
  const addEntityContact = useAddEntityContact();
  // Telegram handles now live as has-handle contact facts (entity_facts), not
  // entity_info, so presence is detected from the entity's linked-contact
  // channels rather than entity.entity_info.
  const linkedContacts = useEntityLinkedContacts(entity.id);

  const [open, setOpen] = useState(false);
  const [canonicalName, setCanonicalName] = useState("");
  const [telegram, setTelegram] = useState("");
  const [telegramChatId, setTelegramChatId] = useState("");

  // Credential fields (collapsible)
  const [showCredentials, setShowCredentials] = useState(false);
  const [telegramApiHash, setTelegramApiHash] = useState("");
  const [telegramApiId, setTelegramApiId] = useState("");
  // telegram_user_session — now managed via the interactive Telegram Session Setup card
  const [homeAssistantUrl, setHomeAssistantUrl] = useState("");
  const [homeAssistantToken, setHomeAssistantToken] = useState("");

  // Don't render if not the owner entity
  if (!entity.roles?.includes("owner")) return null;

  // Check what's missing
  const nameIsPlaceholder =
    !entity.canonical_name?.trim() ||
    entity.canonical_name.trim().toLowerCase() === "owner";
  // Telegram has-handle facts surface as type "telegram_user_id" with the
  // "telegram:" prefix stripped from the display value. The deliverable chat ID
  // is numeric; a username handle is non-numeric — distinguish on that.
  const telegramValues = (linkedContacts.data ?? [])
    .flatMap((c) => c.contact_info)
    .filter((e) => e.type === "telegram_user_id" && e.value)
    .map((e) => e.value!.trim());
  const hasTelegramChatId = telegramValues.some((v) => /^\d+$/.test(v));
  const hasTelegram = telegramValues.some((v) => !/^\d+$/.test(v));

  // Don't render if all core identity fields are configured
  if (!nameIsPlaceholder && hasTelegram && hasTelegramChatId) {
    return null;
  }

  const entityId = entity.id;
  const isSaving =
    createInfo.isPending || updateEntity.isPending || addEntityContact.isPending;

  // Build a human-readable list of what's missing
  const missing: string[] = [];
  if (nameIsPlaceholder) missing.push("name");
  if (!hasTelegram) missing.push("Telegram handle");
  if (!hasTelegramChatId) missing.push("Telegram chat ID");

  async function handleSave() {
    const trimmedName = canonicalName.trim();
    const trimmedTelegram = telegram.trim();
    const trimmedChatId = telegramChatId.trim();
    const trimmedApiHash = telegramApiHash.trim();
    const trimmedApiId = telegramApiId.trim();
    const trimmedHomeAssistantUrl = homeAssistantUrl.trim();
    const trimmedHomeAssistantToken = homeAssistantToken.trim();

    if (
      !trimmedName &&
      !trimmedTelegram &&
      !trimmedChatId &&
      !trimmedApiHash &&
      !trimmedApiId &&
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

      // --- Identity channel facts (relationship.entity_facts) ---
      // Non-secret telegram handles go to the graph as has-handle contact facts
      // so the owner is resolvable via resolve_contact_by_channel. The backend
      // applies the canonical "telegram:<bare>" storage prefix (from channel_type)
      // and the owner self-identity exemption writes them directly (no approval).

      if (trimmedTelegram) {
        promises.push(
          addEntityContact.mutateAsync({
            entityId,
            request: {
              predicate: "has-handle",
              value: trimmedTelegram,
              primary: true,
              channel_type: "telegram",
            },
          }),
        );
      }

      if (trimmedChatId) {
        promises.push(
          addEntityContact.mutateAsync({
            entityId,
            request: {
              predicate: "has-handle",
              value: trimmedChatId,
              primary: true,
              channel_type: "telegram_chat_id",
            },
          }),
        );
      }

      // --- Secured credential fields ---

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
      setTelegram("");
      setTelegramChatId("");
      setTelegramApiHash("");
      setTelegramApiId("");
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
              Set up identity
            </Button>
          </DialogTrigger>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Set up owner identity</DialogTitle>
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
                    <p className="text-xs text-muted-foreground">
                      Telegram user session is generated interactively via the Telegram Session
                      Setup card on this page.
                    </p>
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
