/**
 * Elaboration -- serif Voice paragraph for the briefing elaboration.
 *
 * Font: --font-serif, 16px, leading 1.6, muted-foreground color, max-width 50ch.
 *
 * Motion: FetchingDim applies the shared 200ms opacity cross-fade on briefing
 * refresh.
 *
 * Topology: about/lay-and-land/frontend.md §Editorial archetype layout
 * Doctrine: about/heart-and-soul/design-language.md §The Voice surface
 */

import { FetchingDim } from "@/components/ui/fetching-dim";

interface ElaborationProps {
  text: string;
  isFetching: boolean;
}

export function Elaboration({ text, isFetching }: ElaborationProps) {
  return (
    <FetchingDim isFetching={isFetching}>
      <p
        style={{
          fontFamily: "var(--font-serif)",
          fontSize: "16px",
          fontWeight: 400,
          lineHeight: 1.6,
          maxWidth: "50ch",
          color: "var(--muted-foreground)",
        }}
      >
        {text}
      </p>
    </FetchingDim>
  );
}
