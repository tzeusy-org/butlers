/**
 * Headline -- Display tier two-line headline for the editorial archetype.
 *
 * Line 1: `greet`  -- muted color
 * Line 2: `body`   -- foreground color
 *
 * Sizing: 44px sans 500, tracking -0.025em, leading 1.08, max-width 14ch.
 * The 14ch constraint forces the dramatic line break that shapes the page.
 *
 * Topology: about/lay-and-land/frontend.md §Type tokens (Display tier)
 * Doctrine: about/heart-and-soul/design-language.md §Editorial archetype
 */

interface HeadlineProps {
  /** Greeting line, e.g. "Good morning." Rendered muted. */
  greet: string;
  /** Body line, e.g. "Everything is in hand." Rendered foreground. */
  body: string;
}

export function Headline({ greet, body }: HeadlineProps) {
  return (
    <div
      style={{
        fontFamily: "var(--font-sans)",
        fontSize: "44px",
        fontWeight: 500,
        letterSpacing: "-0.025em",
        lineHeight: 1.08,
        maxWidth: "14ch",
      }}
    >
      <p style={{ color: "var(--muted-foreground)" }}>{greet}</p>
      <p style={{ color: "var(--foreground)" }}>{body}</p>
    </div>
  );
}
