/**
 * Section -- eyebrow + bottom-rule wrapper for the right-column index lists.
 *
 * Used by ButlerIndex and NextList. Renders a mono uppercase eyebrow label
 * above the list body, separated by a hairline border at the bottom.
 *
 * Topology: about/lay-and-land/frontend.md §Editorial archetype layout §Row anatomies
 * Doctrine: about/heart-and-soul/design-language.md §Editorial archetype
 */

interface SectionProps {
  eyebrow: string;
  children: React.ReactNode;
}

export function Section({ eyebrow, children }: SectionProps) {
  return (
    <div className="space-y-2">
      <p
        className="tnum uppercase"
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "10px",
          letterSpacing: "0.14em",
          lineHeight: 1,
          color: "var(--muted-foreground)",
        }}
      >
        {eyebrow}
      </p>
      {children}
    </div>
  );
}
