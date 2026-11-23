## Confirmation boundary

The preview takes explicit UUIDs and resolves one source through the existing Memory API pool registry. The server returns the ordered snapshots and a digest of their source identity and persisted content, authority, and provenance. The dashboard shows those snapshots before the owner confirms. Neither request accepts an actor or a schema. The owner entity comes from the authenticated dashboard route and the server's identity registry.

The commit takes the same UUIDs and the preview digest. The storage transaction locks rows in UUID order, reconstructs the digest in selection order, validates every row, and only then performs the existing endorsement effects. Endorsement stamps are excluded from the digest so a retry after commit can return original receipts and an overlapping batch can observe prior endorsements without duplicating them. An identity change, retirement, forget, source fault, or a content or provenance change fails before effects. Binding the selection on the server avoids client-dependent serialization and makes confirmation reusable after an ambiguous transport outcome.

## Compatibility and rollback

The existing single endorsement operation retains its behavior through a shared transaction-owned writer. The batch admits only legacy null-authority rules. Existing rule content, catalog exclusions, provenance, and LLM read policy remain in force. Removing the batch surface leaves committed endorsements and their audit history intact.
