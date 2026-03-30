## 1. Backend: Owner entity_info endpoint

- [x] 1.1 Add `OwnerEntityInfoResponse` model to `roster/relationship/api/models.py`
- [x] 1.2 Add `GET /api/relationship/owner/entity-info` endpoint to `roster/relationship/api/router.py`
- [x] 1.3 Register `OwnerEntityInfoResponse` import in the router's lazy model loader

## 2. Home Assistant connector migration

- [x] 2.1 Update HA connector `_main()` to use `resolve_owner_entity_info()` instead of `CredentialStore`
- [x] 2.2 Update HA connector test `test_main_resolves_credentials_from_store` to verify entity_info resolution
- [ ] 2.3 Fix HA settings dashboard (`api/routers/home_assistant.py`) to write to `entity_info` instead of `butler_secrets`
- [ ] 2.4 Clean up orphaned `home_assistant:base_url` and `home_assistant:access_token` from `butler_secrets`

## 3. Frontend: Templates and adapter layer

- [x] 3.1 Create `frontend/src/lib/user-secret-templates.ts` with known entity_info types, categories, labels, secured set
- [x] 3.2 Create `frontend/src/lib/user-secrets-rows.ts` with `buildUserSecretRows()` adapter
- [x] 3.3 Add optional `entityInfoEntry` field to `SecretDisplayRow` in `frontend/src/lib/secrets-rows.ts`

## 4. Frontend: API client and hooks

- [x] 4.1 Add `OwnerEntityInfoResponse` type to `frontend/src/api/types.ts`
- [x] 4.2 Add `getOwnerEntityInfo()` function to `frontend/src/api/client.ts`
- [x] 4.3 Add re-exports to `frontend/src/api/index.ts`
- [x] 4.4 Create `frontend/src/hooks/use-owner-secrets.ts` with query + mutation hooks

## 5. Frontend: SecretsTable polymorphism

- [x] 5.1 Add `onReveal` callback and `plainValue` prop to `MaskedValue` component
- [x] 5.2 Add `GenericDeleteDialog` component for user-mode deletion
- [x] 5.3 Refactor `SecretRow` to support `mode`, `onEditRow`, `onRevealEntry`, `onDeleteRow` props
- [x] 5.4 Update `CategoryGroupRows` to pass through mode-aware props
- [x] 5.5 Update `SecretsTable` public interface with `mode`, `userRows`, and callback props

## 6. Frontend: UserSecretFormModal

- [x] 6.1 Create `frontend/src/components/secrets/UserSecretFormModal.tsx` with type selector, value input, secured handling, and create/update mutations

## 7. Frontend: SecretsPage with tabs

- [x] 7.1 Add System/User `Tabs` to `SecretsPage` (rename `GenericSecretsSection` to `SystemSecretsSection`)
- [x] 7.2 Create `UserSecretsSection` component with `useOwnerEntityInfo()`, adapter, and table wiring
- [x] 7.3 Wire up modal state management for add/edit in `UserSecretsSection`

## 8. Frontend: Cleanup

- [x] 8.1 Deduplicate `ENTITY_INFO_TYPES`, `SECURED_TYPES`, `entityInfoTypeLabel` from `EntityDetailPage.tsx` — import from `user-secret-templates.ts`

## 9. Quality gates

- [x] 9.1 Backend lint: `ruff check src/ roster/`
- [x] 9.2 Frontend type-check: `npx tsc --noEmit`
- [x] 9.3 Frontend build: `npx vite build`
- [x] 9.4 HA connector tests pass: `pytest tests/connectors/test_home_assistant_integration.py`

## 10. Future work (not in this change)

- [ ] 10.1 Migrate Spotify OAuth tokens (`SPOTIFY_ACCESS_TOKEN`, `SPOTIFY_REFRESH_TOKEN`, `SPOTIFY_TOKEN_EXPIRES_AT`, `SPOTIFY_GRANTED_SCOPES`) from `butler_secrets` to owner `entity_info`
- [ ] 10.2 Resolve `telegram_bot_token` dual identity (module reads entity_info, bot connector reads butler_secrets)
- [ ] 10.3 Add `email`, `email_password`, `whatsapp_phone` to frontend `ENTITY_INFO_TYPES` on entity detail page (now in user-secret-templates.ts)

## 11. Documentation

- [ ] 11.1 Update `about/heart-and-soul/security.md` with three-tier credential authority model
- [ ] 11.2 Update `about/law-and-lore/rfcs/0006-database-schema-and-isolation.md` with entity_info as Tier 2 authoritative source
