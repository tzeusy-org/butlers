# Manifesto Writing Guide

The manifesto is the soul of a butler. It's not technical documentation — it's an identity document that guides every feature decision, tool design, and UX choice. When in doubt about scope or framing, consult the manifesto.

## Structure Template

```markdown
# The <Name> Butler

<Optional subtitle or metaphor>

## What We Believe

<2-3 paragraphs about the core philosophy. Why does this domain matter?
What problem exists in the world that this butler solves? What do most
people get wrong about this domain?>

## Our Promise / What It Does

**<Value 1>.** <One sentence explaining the core value proposition.>

**<Value 2>.** <One sentence explaining the second value proposition.>

**<Value 3>.** <One sentence explaining the third value proposition.>

## What You Can Do / What You Get

- **<Capability 1>**: <Brief description of a concrete thing the user can do>
- **<Capability 2>**: <Another concrete capability>
- ...

## Why It Matters

<1-2 paragraphs connecting the butler's purpose to the user's life.
Emotional resonance. How does this make them a better person, save them
time, or remove friction?>

## <Closing Section>

<A short, memorable closing. Can be a tagline, a philosophy statement,
or an invitation.>
```

## Tone and Style

- **Second person**: Address the user as "you"
- **Warm but not saccharine**: Genuine, not corporate. No exclamation marks.
- **Present tense**: "The butler remembers" not "The butler will remember"
- **Acknowledge friction**: Name the real-world problem honestly before offering the solution
- **Focus on outcomes**: "Never miss a birthday" not "Stores dates in a PostgreSQL table"
- **One bold word per promise**: Each value proposition starts with a single bold word (Flexibility, Thoughtfulness, Continuity)

## Examples of Good Opening Lines

- "Your health is not a snapshot — it's a story told over weeks, months, and years."
- "In a world of endless distractions and infinite inboxes, the people who matter most often get the leftover attention."
- "You shouldn't have to find the perfect app for every thought."

## Examples of Good Closings

- "Simple. Honest. Yours." (General)
- "Over time, the Health Butler becomes an extension of your memory. It remembers so you can focus on living." (Health)
- "And it all starts with never forgetting what matters." (Relationship)

## Anti-Patterns

- Don't list technical features (no mention of PostgreSQL, JSONB, MCP, etc.)
- Don't use marketing superlatives ("revolutionary", "game-changing", "best-in-class")
- Don't make it too long — 300-500 words is the sweet spot
- Don't repeat the same point in different words across sections
- Don't describe the butler in third person ("it will help you") — use "we" in philosophy sections and direct address in capability sections
