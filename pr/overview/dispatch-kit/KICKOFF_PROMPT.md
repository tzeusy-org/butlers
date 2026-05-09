# Kickoff Prompt — Dispatch redesign

> Paste this at the top of a fresh chat with Claude when starting a new
> page redesign. Replace `<PAGE_NAME>` with the page you're working on.

---

## Prompt

You are helping me redesign the **`<PAGE_NAME>`** page of the Butlers
dashboard. We have an established design language called **Dispatch**
that already shipped on the Overview page, and your job is to extend it
to this page consistently.

**Before writing any code or proposing layouts**, do these three things
in order:

1. **Read the language.** Open and read in full:
   - `frontend/docs/DESIGN_LANGUAGE.md` — the philosophy and rules.
   - `frontend/docs/PATTERNS.md` — concrete JSX snippets for every
     canonical primitive.
   - `frontend/docs/RECIPES.md` — per-page recipes. Find the
     `<PAGE_NAME>` recipe (or read the "When you can't find a recipe"
     section at the bottom).
   - `frontend/docs/tokens.css` — the token definitions.

2. **Read the existing page.** Open `frontend/src/pages/<PAGE_NAME>Page.tsx`
   (or wherever the current implementation lives) and the hooks/API
   modules it uses. Note what data is already available.

3. **Propose the layout in prose, no code.** Tell me:
   - Is this page **narrative** or **data**?
   - What's the **spine** of the left column? (headline → voice → list?)
   - What lives in the **right column**, if anything?
   - Where (if anywhere) does the system **speak in serif**?
   - What's the **empty state** sentence (one line, serif italic)?
   - Is there a **status pill**, and what does it report on?
   - Any **anti-patterns** the existing page falls into that we'll fix?

I'll review your proposal, push back where I disagree, and only then
will you write code.

**The five inviolable rules of Dispatch** (do not break these without
explicit permission):

1. Composure is the brand. The page reads calm even when things break.
2. Type is the system. Hierarchy comes from type and rules, not shadows
   or fills.
3. Surfaces, not cards. One elevation. Structure is rules and rhythm.
4. Every element earns its place against state. If nothing is happening,
   the section disappears its borders or shows a single serif-italic
   line. We do not decorate.
5. One affordance per signal. Status is one of: dot, sliver, numeral,
   color. Never a word like "active." Never two of the four together.

**Hard "do not"s** (compact list — full version in `DESIGN_LANGUAGE.md`
section 9):

- No purple/pink gradients. No glassmorphism. No drop shadows.
- No nested cards. No card-on-card. **Prefer no cards at all.**
- No italic-serif headlines as a brand move.
- No "Welcome back, ${user.name}!" — anywhere, ever.
- No emoji in chrome. No "" empty states.
- No invented colors. The palette is closed.
- No `font-bold` on display headlines — `font-medium` (500) only.
- No animation beyond a 200ms briefing fade and chevron rotation.
- No celebratory micro-interactions. Calm is the feature.
- No filling a quiet day with mock content because the screen looks empty.

**The north star.** Before merging anything, ask:

> If the system were a person handing me a sheet of paper, would I trust
> the typography of that sheet?

If the answer isn't yes, don't ship.

**Definition of done for this page:**

- [ ] Layout proposal reviewed and approved by me before code.
- [ ] Implementation passes the 12-item Dispatch checklist
      (`frontend/docs/CHECKLIST.md`).
- [ ] Both dark and light themes flip cleanly.
- [ ] Empty state is one serif-italic sentence.
- [ ] No new colors invented; no new font families introduced.
- [ ] Builds with no TypeScript errors and no a11y regressions.

Now: read the four docs, read the existing page, and come back with a
layout proposal.
