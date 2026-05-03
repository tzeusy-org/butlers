## ADDED Requirements

### Requirement: Fact detail page conforms to the detail-page archetype

The Fact detail page at `/memory/facts/:factId` SHALL conform to the detail-page
archetype defined in the `detail-page-archetype` spec.

**Changes from the existing requirement (dashboard-domain-pages §Requirement: Fact
detail page):**

1. **Shell adoption.** The page MUST use `<Page archetype="detail">` as its outer
   shell. The existing `breadcrumbs`, `isLoading`, and `error` handling MUST be
   delegated to the shell's `breadcrumbs`, `loading`, and `error` props respectively.
   The inline three-skeleton loading block and the inline destructive-text error block
   MUST be removed from the page body.

2. **Title.** The `title` prop on `<Page>` MUST be the fact's `subject` field (its
   record identity). The existing `<CardTitle>` is the correct source; it MUST be
   lifted to the `title` prop. (This was already specified as "Subject as page title"
   in the header sub-requirement — this requirement formalises the mechanism.)

3. **Subtitle.** The `description` prop on `<Page>` MUST carry the fact's `predicate`
   field, rendered as a plain-text subtitle below the H1.

4. **Body layout.** The existing card sections (Content, Status row, Metrics,
   Provenance, Tags, Metadata, Timestamps) become the `primary` body slot inside the
   shell.

#### Scenario: Fact detail page uses shell loading state

- **WHEN** `GET /api/memory/facts/:id` is in flight
- **THEN** the `<Page>` shell MUST show the `DetailSkeleton` (card + two block skeletons)
- **AND** the page MUST NOT render inline `<Skeleton>` blocks outside the shell
- **AND** breadcrumbs MUST still be visible during the loading state

#### Scenario: Fact detail page uses shell error state

- **WHEN** `GET /api/memory/facts/:id` fails
- **THEN** the `<Page>` shell MUST render the destructive error card
- **AND** the page MUST NOT render an inline `text-destructive text-center` block

#### Scenario: Fact detail page title shows subject

- **WHEN** a fact has `subject = "Tze"` and `predicate = "preferred contact channel"`
- **THEN** the `<h1>` MUST read "Tze"
- **AND** the subtitle line below the H1 MUST read "preferred contact channel"

---

### Requirement: Rule detail page conforms to the detail-page archetype

The Rule detail page at `/memory/rules/:ruleId` SHALL conform to the detail-page
archetype defined in the `detail-page-archetype` spec.

**Changes from the existing requirement (dashboard-domain-pages §Requirement: Rule
detail page):**

1. **Shell adoption.** Same as Fact: inline L/E blocks delegated to `<Page>` props.

2. **Title — record-identity correction.** The existing requirement specifies
   `"Rule" as page title`. This violates the archetype's record-identity requirement
   (detail-page-archetype spec §Requirement: Detail-page title is record-identity).
   The `title` prop on `<Page>` MUST be the first 80 characters of `rule.content`,
   truncated with an ellipsis (`…`) if the content exceeds 80 characters.
   `"Rule"` as a title is explicitly disallowed.

3. **Subtitle.** The `description` prop on `<Page>` MUST carry a `Maturity: {badge}`
   status summary or be omitted. The Maturity badge itself belongs in the `status`
   prop (see point 4).

4. **Status pills.** The Maturity badge MUST be passed via the `status` prop so it
   appears adjacent to the title row rather than inside `<CardContent>`.

5. **Body layout.** The existing card sections (Content, Status row, Effectiveness,
   Confidence, Provenance, Tags, Metadata, Timestamps) become the `primary` body slot.

#### Scenario: Rule detail page title shows content summary

- **WHEN** a rule has `content = "Always acknowledge messages within 24 hours of receipt"`
- **THEN** the `<h1>` MUST read "Always acknowledge messages within 24 hours of receipt"
- **AND** it MUST NOT read "Rule"

#### Scenario: Rule content truncated to 80 chars

- **WHEN** a rule has content longer than 80 characters
- **THEN** the `<h1>` MUST show the first 80 characters followed by "…"

#### Scenario: Rule detail page uses shell loading state

- **WHEN** `GET /api/memory/rules/:id` is in flight
- **THEN** the `<Page>` shell MUST show `DetailSkeleton`
- **AND** no inline skeleton blocks MUST be rendered by the page

#### Scenario: Rule detail page uses shell error state

- **WHEN** `GET /api/memory/rules/:id` fails
- **THEN** the `<Page>` shell MUST render the destructive error card
- **AND** no inline destructive-text error block MUST be rendered by the page

---

### Requirement: Episode detail page conforms to the detail-page archetype

The Episode detail page at `/memory/episodes/:episodeId` SHALL conform to the
detail-page archetype defined in the `detail-page-archetype` spec.

**Changes from the existing requirement (dashboard-domain-pages §Requirement: Episode
detail page):**

1. **Shell adoption.** Inline L/E blocks delegated to `<Page>` props.

2. **Title — record-identity correction.** The existing requirement specifies
   `"Episode" as page title`. This violates the archetype's record-identity requirement.
   The `title` prop on `<Page>` MUST be:
   - `episode.session_id` if the field is non-null; OR
   - `"Episode {episode.id.slice(0, 8)}"` if `session_id` is null.
   `"Episode"` as a standalone title is explicitly disallowed.

3. **Subtitle.** The butler name (as a plain string, not a badge) MUST be passed as
   the `description` prop on `<Page>` so it appears below the H1. The butler badge
   rendered in the body card is supplemental, not a replacement.

4. **Body layout.** The existing card sections (Content, Status row, Details, Metadata,
   Timestamps) become the `primary` body slot.

#### Scenario: Episode detail page title shows session ID

- **WHEN** an episode has `session_id = "sess-abc123def456"`
- **THEN** the `<h1>` MUST read "sess-abc123def456"
- **AND** it MUST NOT read "Episode"

#### Scenario: Episode detail page title falls back to ID prefix

- **WHEN** an episode has `session_id = null` and `id = "ep-12345678-abcd-..."`
- **THEN** the `<h1>` MUST read "Episode ep-123456" (first 8 chars of id)

#### Scenario: Episode detail page uses shell loading state

- **WHEN** `GET /api/memory/episodes/:id` is in flight
- **THEN** the `<Page>` shell MUST show `DetailSkeleton`
- **AND** no inline skeleton blocks MUST be rendered by the page

## Source References

- Non-Negotiable Rule 2 (The Page is a primitive)
- `detail-page-archetype` spec — archetype conformance requirements
- `about/lay-and-land/detail-page-audit.md` §6.1 (Episode), §6.2 (Fact), §6.3 (Rule)
  — migration guidance and title-quality analysis
- `frontend/src/components/ui/page.tsx` — `<Page archetype="detail">` implementation
