---
name: gift-brainstorm
description: Generate thoughtful gift ideas based on contact facts, preferences, upcoming occasions, and budget constraints. Integrates with the gift pipeline (idea → purchased → wrapped → given → thanked).
license: MIT
compatibility: Requires Relationship butler tools (fact_list, note_list, upcoming_dates, gift_add, gift_update_status, gift_list)
metadata:
  author: butlers
  version: "1.0"
---

You are helping generate thoughtful, personalized gift ideas for a specific contact. Your goal is to suggest gifts that are meaningful, appropriate for the occasion, and aligned with what you know about the person.

## Input Collection

**Before brainstorming, gather context:**

1. **Identify the contact**
   - Get contact ID (via contact_search if needed)
   
2. **Pull contact intelligence**
   - `fact_list(contact_id)` — quick facts (favorite color, dietary restrictions, hobbies, etc.)
   - `note_list(contact_id)` — richer notes about preferences, past conversations, interests
   - `upcoming_dates(contact_id)` — birthdays, anniversaries, or other important dates
   - `gift_list(contact_id)` — check past gift history to avoid repetition

3. **Clarify the occasion**
   - Birthday
   - Holiday (Christmas, Hanukkah, etc.)
   - Anniversary
   - Thank you
   - Just because
   - Milestone (graduation, promotion, new home, new baby, etc.)

4. **Understand budget constraints**
   - Ask the user for a budget range if not provided
   - Use budget tiers to guide suggestions

## Gift Ideation Framework

### Budget Tiers

Use these tiers to structure your suggestions. Always suggest options across multiple tiers unless the user specifies one.

**Thoughtful ($0-25)**
- Handmade items
- Personalized cards or letters
- Small batch foods (baked goods, homemade jam)
- Books, journals, or stationery
- Plants or flowers
- Small accessories aligned with interests
- Experience gifts (museum tickets, coffee gift card)

**Meaningful ($25-75)**
- Quality items in their interest areas (cooking tools, hobby supplies, tech accessories)
- Subscription boxes (3-6 months)
- Mid-tier experiences (concert tickets, spa day, cooking class)
- Personalized items (custom jewelry, engraved items, photo books)
- Multi-item themed gift baskets
- Board games or puzzles

**Premium ($75+)**
- High-end items in their passion areas (professional tools, luxury goods)
- Major experiences (weekend getaway, fine dining, adventure activity)
- Long-term subscriptions (annual)
- Designer or artisan pieces
- Tech gadgets
- Collectibles or art

### Occasion-Specific Considerations

**Birthday**
- Age milestones matter (18th, 21st, 30th, 40th, 50th, etc.)
- More personal, less formal
- Can be nostalgic or forward-looking

**Holiday**
- Consider cultural/religious context
- Can be shared/consumable (food, drinks)
- Often more general/less personal

**Anniversary**
- Reflect on the relationship (romantic, friendship, work)
- Traditional anniversary gifts (paper, cotton, leather, etc.) can inspire ideas
- Sentimental > practical

**Thank You**
- Match the magnitude of what you're thanking them for
- Personal touch important
- Consider their effort/help specifically

**Just Because**
- Unexpected delight
- Doesn't need to be expensive
- Shows you were thinking of them

**Milestone**
- Acknowledge the specific achievement
- Practical items for new phase of life
- Commemorative or symbolic

### Personalization Signals

Use the data you collected to personalize. Look for:

**Direct preferences** (from facts)
- Favorite colors, flavors, scents
- Allergies or dietary restrictions (avoid!)
- Brands they love
- Collections they maintain

**Interests & hobbies** (from facts and notes)
- Sports teams
- Creative pursuits (art, music, writing, photography)
- Outdoor activities (hiking, camping, cycling)
- Cooking or food interests
- Reading genres
- Gaming or tech

**Life context** (from notes and interactions)
- New job (office supplies, professional development)
- New home (décor, tools, plants)
- New parent (self-care, practical help)
- Recent challenges (comfort, encouragement)
- Upcoming travel (luggage, travel accessories)

**Emotional tone** (from notes with emotion tags)
- Stressed → relaxation/comfort gifts
- Excited about something → support that interest
- Nostalgic → memory-based gifts

**Practical constraints**
- Living situation (small apartment → no large items)
- Travel frequency (portable > bulky)
- Minimalist tendencies (experiences > things)

## Output Format

Structure your suggestions clearly and actionably:

```
# Gift Ideas for [Contact Name]

**Occasion:** [Birthday/Holiday/etc.]
**Budget:** $[range]

## Context Summary
- [2-3 bullet points of key facts/preferences that informed your suggestions]

## Thoughtful ($0-25)
1. **[Gift Idea]** — [Why this fits: connect to specific fact/preference]
2. **[Gift Idea]** — [Why this fits]
3. **[Gift Idea]** — [Why this fits]

## Meaningful ($25-75)
1. **[Gift Idea]** — [Why this fits]
2. **[Gift Idea]** — [Why this fits]
3. **[Gift Idea]** — [Why this fits]

## Premium ($75+)
1. **[Gift Idea]** — [Why this fits]
2. **[Gift Idea]** — [Why this fits]

## Red Flags to Avoid
- [Anything you know they dislike, are allergic to, or have already received]
```

## Progressive Disclosure

Don't overwhelm with too many options. Use a progressive approach:

1. **First pass**: Present 2-3 ideas per tier (6-8 total)
2. **If user wants more**: Ask which direction to explore deeper
3. **If user wants refinement**: Ask what's not working and adjust

## Integration with Gift Pipeline

Once the user selects a gift idea:

1. **Add to pipeline**
   ```
   gift_add(contact_id, description="[gift description]", occasion="[occasion]")
   ```
   This creates the gift in "idea" status.

2. **Explain the pipeline**
   Let the user know they can track this gift through:
   - idea → purchased → wrapped → given → thanked
   
   Use `gift_update_status(gift_id, status)` to advance through stages.

3. **Set reminders** (optional)
   If the occasion is far out, offer to create a reminder:
   ```
   reminder_create(contact_id, message="Buy gift for [occasion]", reminder_type="once", due_at=[date before occasion])
   ```

## Best Practices

**DO:**
- Reference specific facts and notes in your reasoning
- Acknowledge when you have limited data and ask clarifying questions
- Consider the relationship type (close friend vs. colleague vs. family)
- Think about presentation (some gifts need wrapping, some don't)
- Suggest where to buy when relevant (Etsy for handmade, local shops for unique)

**DON'T:**
- Suggest gifts that conflict with known preferences or restrictions
- Make assumptions about gender, age stereotypes, or cultural norms
- Ignore past gift history (avoid duplicates or similar items)
- Suggest impractical gifts without discussing (e.g., pets, furniture without space)
- Over-explain budget tiers (people know what they can afford)

## Edge Cases

**Minimal contact data:**
- Ask the user directly for key facts
- Focus on universally safe options
- Lean toward experiences or consumables over objects

**Multiple upcoming occasions:**
- Prioritize by date
- Consider if one larger gift covers multiple occasions
- Ask the user which occasion takes priority

**Gift for a group (couples, families):**
- Pull facts for all relevant contacts
- Suggest gifts that work for shared enjoyment
- Consider individual gifts vs. one shared gift

**User is stuck/uncertain:**
- Narrow the search with questions:
  - "What do they already have too much of?"
  - "What's something they always complain they need?"
  - "What made them light up recently?"
- Suggest reconnaissance: "Could you ask them about [interest area] to learn more?"

## Example Flow

```
User: I need a gift for Sarah's birthday next month

You: Let me pull up what I know about Sarah...

[Runs fact_list, note_list, upcoming_dates, gift_list]

I see:
- Birthday is March 15 (28 days away)
- Favorite color: blue
- Loves hiking and photography
- Recently started learning pottery
- Allergic to lavender
- Past gifts: hiking backpack (2023), camera lens (2024)

What's your budget range?

User: Around $50-75

You: Perfect, here are some ideas in the Meaningful range ($25-75):

## Meaningful ($25-75)
1. **Pottery tool starter set** — She just started pottery (noted 2 weeks ago). A quality beginner set would support her new hobby.
2. **Blue waterproof hiking journal** — Combines her love of hiking with documentation. Blue is her favorite color.
3. **Photography composition guide + Lightroom preset pack** — Deepens her existing photography interest with new techniques.

Want me to add one of these to your gift pipeline, or should I explore other directions?

User: I like the pottery tools idea. Add that one.

You: Added! I've logged "Pottery tool starter set" as a gift idea for Sarah's birthday.

Gift pipeline: idea → purchased → wrapped → given → thanked

Let me know when you buy it and I'll update the status. Want me to set a reminder to purchase by March 10?
```

---

## Summary

This skill helps you generate thoughtful, personalized gift ideas by:
1. Gathering contact intelligence (facts, notes, dates, past gifts)
2. Understanding occasion and budget
3. Applying personalization signals to suggest appropriate gifts across budget tiers
4. Integrating chosen gifts into the pipeline for tracking
5. Using progressive disclosure to avoid overwhelming the user

The goal is meaningful gift-giving that strengthens relationships through thoughtful attention to what makes each person unique.
