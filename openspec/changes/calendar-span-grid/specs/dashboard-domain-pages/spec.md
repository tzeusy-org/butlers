## ADDED Requirements

### Requirement: Calendar workspace discloses what it holds back

ID: REQ-dashboard-domain-pages-062
Source: bu-5ii4gj.4 (run 26 move 4), slice D1
Scope: v1-mandatory

The calendar workspace read SHALL NOT drop events silently. A title that repeats more than 20 times
on one display-timezone day on a page SHALL be left out of `entries` and returned in
`suppressed_groups` with its title, day, count and entries. `has_more` and `next_cursor` SHALL be
derived from the fetched page before suppression, so a page that loses rows to suppression still
reports the rows behind it. The page SHALL show each suppressed group as a counted chip that expands
to its entries, and SHALL say so when the fetch ended at its page cap with `has_more` still true,
rather than present the window as complete.

#### Scenario: A packed day shows what was hidden

- **WHEN** 25 events titled "Standup" fall on one day
- **THEN** `entries` omits them and `suppressed_groups` holds one group with count 25 and those entries
- **AND** the page shows "25 hidden: Standup x25" that expands to the 25 entries

#### Scenario: Suppression does not end pagination

- **WHEN** a full page is entirely one suppressed group and more rows follow
- **THEN** `has_more` is true and `next_cursor` is set

#### Scenario: A capped fetch says it is incomplete

- **WHEN** the client reaches its page cap and the last page still reports `has_more`
- **THEN** the page shows that later events are not shown and does not present the window as complete

## Source References

- docs/redesigns/pursuit-ledger.md north star: failure and filtering never impersonate calm
