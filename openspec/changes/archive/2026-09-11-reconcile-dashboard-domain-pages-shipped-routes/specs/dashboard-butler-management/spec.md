## MODIFIED Requirements

### Requirement: Tab Structures Reference (Non-Butler Pages)

The following tab structures and compatibility navigation behavior exist outside the butler
detail view and SHALL be documented here as a consolidated reference.

#### Scenario: Memory browser tabs
- **WHEN** the `/memory` page or the butler detail Memory tab is active
- **THEN** a tabbed browser shows three tabs: Facts, Rules, Episodes
- **AND** when opened inside a butler detail page, all queries are scope-filtered to that butler

#### Scenario: Contact detail tabs
- **WHEN** `/contacts/:contactId` is visited
- **THEN** the route MUST replace-navigate to `/entities/index?has=contact`
- **AND** it MUST NOT render the retired contact-detail page or its former tabs

#### Scenario: Approvals navigation integration
- **WHEN** the approvals section is accessed from the sidebar
- **THEN** two routes are available: `/approvals` (pending action queue with filters, metrics dashboard, and decision workflows) and `/approvals/rules` (standing rules list with detail, create, and revoke flows)
- **AND** the main approvals page provides: metrics dashboard with pending count and approval/rejection/auto-approval stats, filterable action queue by tool/status/butler, action detail dialog with approve/reject/rule creation, and stale action expiry management
- **AND** the rules page provides: filterable rules list by tool/active status/butler, rule detail dialog with constraint inspection, rule revocation capability, and use count and limit tracking
