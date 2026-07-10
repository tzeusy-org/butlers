## 1. Author relocation deltas

- [x] 1.1 Write `## REMOVED Requirements` delta for `contacts-identity` ("I/O model removal") with Reason + Migration pointing at `core-modules`
- [x] 1.2 Write `## ADDED Requirements` delta for `core-modules` ("Module Tool Naming Convention"), steady-state, dropping the dead "Legacy tool names rejected" scenario (see Archive Note)

## 2. Fix canonical prose and drift

- [x] 2.1 Update `contacts-identity` Purpose block: three → two surviving requirements; point at `core-modules` for the tool-naming contract
- [x] 2.2 Add SHALL/MUST keyword to the 11 pre-existing `core-modules` requirement statements lacking one (meaning-preserving; unblocks strict archive)

## 3. Validate and archive

- [x] 3.1 `openspec validate relocate-tool-naming-contract --strict` green
- [x] 3.2 `openspec archive relocate-tool-naming-contract -y` (merge deltas into canonical specs)
- [x] 3.3 `openspec validate contacts-identity --strict` green
- [x] 3.4 `openspec validate core-modules --strict` green
