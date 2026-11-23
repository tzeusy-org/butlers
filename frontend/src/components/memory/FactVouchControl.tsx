// ---------------------------------------------------------------------------
// FactVouchControl — the owner's Vouch action on a held fact (bu-jv1moa.14)
//
// A held fact was reported by someone other than the owner and has not been
// vouched for. Vouching is additive: the fact keeps its original authority, the
// owner just says they stand behind it. Rendered by the fact detail footer only
// while `fact.held`, so a vouched, owner-authored or retracted fact shows no
// button. Secondary (bordered) styling: Confirm stays the single commit pill.
// ---------------------------------------------------------------------------

import { Mono } from "@/components/ui/Mono";
import { Voice } from "@/components/ui/Voice";
import { useVouchFact } from "@/hooks/use-memory";
import { cn } from "@/lib/utils";
import type { Fact } from "@/api/types.ts";

export function FactVouchControl({ fact, disabled }: { fact: Fact; disabled?: boolean }) {
  const vouchMutation = useVouchFact();

  return (
    <div className="flex flex-wrap items-center gap-3">
      <button
        type="button"
        disabled={disabled || vouchMutation.isPending}
        onClick={() => vouchMutation.mutate(fact.id)}
        className={cn(
          "inline-flex h-7 items-center rounded-full px-3.5",
          "font-mono text-[11px] font-medium",
          "border border-[var(--border)] bg-transparent text-fg",
          "transition-colors hover:border-fg",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
          "disabled:pointer-events-none disabled:opacity-40",
        )}
      >
        Vouch
      </button>
      <Voice variant="italic" as="span" className="text-[13px] text-[var(--mfg)]">
        Reported by someone else. Vouching says you stand behind it.
      </Voice>
      {vouchMutation.isError && <Mono muted>Could not vouch. Try again.</Mono>}
    </div>
  );
}
