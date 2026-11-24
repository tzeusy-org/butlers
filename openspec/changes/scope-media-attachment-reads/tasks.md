## 1. Implementation
- [x] 1.1 `get_attachment` and `attachment_view` in `butlers.tools.attachments` apply `check_storage_ref` before fetching and refuse out-of-scope refs with `attachment_forbidden`.
- [x] 1.2 Core media registration passes the daemon's butler name as `origin_butler`.

## 2. Verification
- [x] 2.1 Parametrised test over a foreign-butler ref, a connector-prefix ref and an empty origin, for both tools.
