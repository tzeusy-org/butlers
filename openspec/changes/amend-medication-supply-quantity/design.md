## Design

The persisted source of truth remains the Health `medication` property fact.
The existing `medication_add` and `medication_update` paths already carry
`quantity` and `quantity_updated_at`; this change only completes the API and
dashboard seam.

`quantity` is represented as an optional strict integer in the FastAPI request
models. `StrictInt` prevents Pydantic from converting JSON strings, floats, or
booleans into counts, while `gt=0` rejects zero and negative values. FastAPI
returns its normal typed HTTP 422 validation response before the write tool is
called.

The dashboard form keeps the quantity input as text with numeric input mode so
malformed text is retained for an explicit validation message rather than
browser-sanitized into an empty value that could be mistaken for unknown. A
blank create value omits `quantity`; a blank edit value preserves an existing
recorded quantity. Entering a new positive value on an existing medication
forwards it as the existing refill/current-supply update, which stamps the
server timestamp. The list row renders only the server value: a null/absent
value is `Supply: unknown`, never `0` or a guessed standard supply.

The OpenSpec delta uses only `## ADDED Requirements` blocks. It does not
replace a whole baseline requirement, so it remains archive-safe if unrelated
medication scenarios are added to the canonical specs before this change is
archived.

## Verification Matrix

| Behavior | Evidence |
| --- | --- |
| Positive create quantity round-trips | API delegation test + MedicationForm test |
| Positive edit/refill quantity round-trips | API update test + MedicationForm edit test |
| Omitted quantity remains unknown | API list serialization test + row rendering test |
| Zero/negative/malformed API values refuse | Parametrized API 422 tests; tool not called |
| Zero/negative/malformed UI values refuse | MedicationTracker form test; mutation not called |
| Contract remains strict and archive-safe | `openspec validate ... --strict`, `make check-spec-overwrites`, `make check-guards` |
