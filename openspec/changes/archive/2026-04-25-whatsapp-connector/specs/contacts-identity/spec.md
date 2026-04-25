# Contacts Identity — WhatsApp Delta

## ADDED Requirements

### Requirement: WhatsApp-specific contact_info types

WhatsApp identity resolution uses phone-number-based JIDs as the canonical identifier. The `contact_info.type` column is an open string field with no CHECK constraint — new types like `whatsapp_jid` can be used by convention without schema changes.

#### Scenario: WhatsApp JID contact info type

- **WHEN** a WhatsApp sender is resolved to a contact
- **THEN** the `contact_info` row SHALL use `type = "whatsapp_jid"` with `value` set to the sender's WhatsApp JID (e.g., `"1234567890@s.whatsapp.net"` for individual chats)
- **AND** the JID SHALL be the permanent, stable identifier (phone-number-based, does not change)
- **AND** the UNIQUE constraint on `(type, value)` SHALL prevent duplicate entries

#### Scenario: WhatsApp phone number mapping

- **WHEN** a WhatsApp JID contains an E.164 phone number prefix (e.g., `1234567890` from `1234567890@s.whatsapp.net`)
- **THEN** it SHALL be cross-referenced against existing `contact_info` rows with `type = "phone"` for the same number
- **AND** if a matching phone contact exists, the WhatsApp JID SHALL be linked to the same `contact_id` (not a new contact)

#### Scenario: Group JID handling

- **WHEN** a WhatsApp group chat JID is encountered (e.g., `120363012345@g.us`)
- **THEN** it SHALL NOT be stored as a `contact_info` entry (groups are not contacts)
- **AND** group JIDs SHALL only appear in `event.external_thread_id` fields of ingest envelopes

### Requirement: WhatsApp identity in reverse-lookup

The `resolve_contact_by_channel` function SHALL support WhatsApp JID lookups.

#### Scenario: Reverse-lookup by WhatsApp JID

- **WHEN** `resolve_contact_by_channel(type="whatsapp_jid", value="1234567890@s.whatsapp.net")` is called
- **THEN** it SHALL return the contact linked to that JID
- **AND** if no direct match exists, it SHALL attempt phone-number fallback by extracting the number prefix and querying `type="phone"`

### Requirement: WhatsApp cross-provider contact disambiguation

WhatsApp contacts can be merged with existing contacts from other providers via phone number matching.

#### Scenario: WhatsApp + Google Contacts merge

- **WHEN** a Google Contacts sync provides a contact with phone `+1234567890`
- **AND** a WhatsApp JID `1234567890@s.whatsapp.net` is already linked to a contact
- **THEN** the Google contact SHALL be merged into the existing contact (same `contact_id`)
- **AND** provider-specific fields from both sources SHALL be preserved
