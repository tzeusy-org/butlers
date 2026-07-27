/**
 * Header bar inside the chat panel showing conversation title, total cost,
 * and keyboard navigation hints.
 */

import { MessageCircleIcon } from "lucide-react";
import type { ConversationSummary, Message, PricingMap } from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Cost calculation
// ---------------------------------------------------------------------------

function totalConversationCost(
  messages: Message[],
  pricingMap: PricingMap | null,
): string | null {
  if (!pricingMap) return null;

  let totalCost = 0;
  let hasCost = false;

  for (const msg of messages) {
    if (msg.role !== "assistant" || !msg.model) continue;
    const pricing = pricingMap[msg.model];
    if (!pricing) continue;
    const input = msg.input_tokens ?? 0;
    const output = msg.output_tokens ?? 0;
    totalCost +=
      (input / 1_000_000) * pricing.input_per_million +
      (output / 1_000_000) * pricing.output_per_million;
    hasCost = true;
  }

  return hasCost ? `~$${totalCost.toFixed(4)}` : null;
}

// ---------------------------------------------------------------------------
// ConversationHeader
// ---------------------------------------------------------------------------

export interface ConversationHeaderProps {
  butlerName: string;
  conversation: ConversationSummary | null;
  messages: Message[];
  pricingMap: PricingMap | null;
  /** Accepted route for the currently pending turn, if Switchboard supplied one. */
  routedButler?: string | null;
}

export function ConversationHeader({
  butlerName,
  conversation,
  messages,
  pricingMap,
  routedButler,
}: ConversationHeaderProps) {
  const title = conversation?.title ?? "New conversation";
  const costStr = totalConversationCost(messages, pricingMap);
  // `null` is an explicit targetless receipt for this turn; only an absent
  // receipt (`undefined`) may fall back to the conversation's older route.
  const accountableButler =
    routedButler !== undefined ? routedButler : (conversation?.routed_butler ?? null);

  return (
    <div className="flex items-center gap-2 px-4 py-3 border-b">
      <MessageCircleIcon className="size-4 text-muted-foreground shrink-0" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium truncate">{title}</p>
        {(conversation || accountableButler) && (
          <p className="text-xs text-muted-foreground">
            {accountableButler ? (
              <>
                Handled by{" "}
                <a
                  href={`/butlers/${encodeURIComponent(accountableButler)}`}
                  className="underline underline-offset-2 hover:text-foreground"
                >
                  {accountableButler}
                </a>
              </>
            ) : (
              butlerName
            )}
            {costStr && ` · ${costStr}`}
          </p>
        )}
      </div>
    </div>
  );
}
