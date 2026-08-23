# Regression evidence

This record preserves the pre-implementation failures required by task 1.2. Each RED was observed
before its corresponding production change; later task reports retain the full command output.

## Canonical transport identity

```console
$ uv run pytest tests/core/test_whatsapp_identity.py::test_whatsapp_user_client_uses_jid_phone_fallback tests/core/test_identity.py::test_bulk_whatsapp_user_client_uses_jid_candidates tests/core/test_identity.py::test_canonical_identity_channel_type_maps_whatsapp_transport_alias -q --tb=short -n 0
ImportError: cannot import name 'canonical_identity_channel_type' from 'butlers.identity'
1 error in 0.25s
```

The RED established that the shared `whatsapp_user_client` to `whatsapp_jid` identity boundary did
not yet exist.

## LID history normalization

```console
$ uv run pytest tests/test_passive_interaction_sender_identity.py::TestWhatsAppBatchEnvelopeParticipants -q --tb=short
.....FFFF
```

The four failures showed mapped LIDs, unmapped LIDs, device-qualified phone JIDs, and distinct
unknown group speakers leaking raw transport values or missing the structured history projection.

## Authoritative excerpt identity

```console
$ uv run pytest tests/modules/test_module_pipeline.py tests/integration/test_decomposition_flow.py -q --tb=short
4 failed, 90 passed in 41.26s
```

Three failures were the absent authoritative-map interfaces; the integration failure showed that a
model-supplied sender, identity, entity UUID, text, and timestamp survived normalization.

## Fact-storage transport-name guard

```console
$ uv run pytest tests/modules/test_module_memory.py -q --tb=short
3 failed, 51 passed in 15.18s
```

Phone JID, device-qualified JID, and numeric LID fact-storage calls all delegated to entity creation
instead of returning the required content-blind rejection.

## Guarded reconciliation

```console
$ uv run pytest roster/relationship/tests/test_whatsapp_reconciliation.py -q --tb=short
ModuleNotFoundError: No module named 'butlers.tools.relationship.whatsapp_reconciliation'
1 error in 13.34s

$ uv run pytest tests/scripts/test_reconcile_whatsapp_entities.py -q --tb=short
1 failed, 10 errors in 13.81s
```

The first RED established that the guarded planner/apply core did not exist; the second established
that no dry-run-by-default, digest-authorized operator command existed.
