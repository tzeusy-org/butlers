/**
 * Elaboration -- serif Voice paragraph for the briefing elaboration.
 *
 * Font: --font-serif, 16px, leading 1.6, muted-foreground color, max-width 50ch.
 *
 * Motion: 200ms opacity cross-fade on briefing refresh.
 *   opacity 0.4 while isFetching, transitions back to 1 with
 *   transition-[opacity] duration-base ease-out-quart.
 *
 * Topology: about/lay-and-land/frontend.md §Editorial archetype layout
 * Doctrine: about/heart-and-soul/design-language.md §The Voice surface
 */

interface ElaborationProps {
  text: string;
  isFetching: boolean;
}

export function Elaboration({ text, isFetching }: ElaborationProps) {
  return (
    <p
      className="transition-[opacity] duration-base ease-out-quart"
      style={{
        fontFamily: "var(--font-serif)",
        fontSize: "16px",
        fontWeight: 400,
        lineHeight: 1.6,
        maxWidth: "50ch",
        color: "var(--muted-foreground)",
        opacity: isFetching ? 0.4 : 1,
      }}
    >
      {text}
    </p>
  );
}
