## 1. Map the trigger outcome

- [x] 1.1 Return 404 for a nonexistent schedule, 409 for busy and 502 for any other non-triggered reply, with the reason in `detail`.
- [x] 1.2 Audit a refusal once as a success and a real failure once as an error.

## 2. Verification

- [x] 2.1 Route tests, including a real fastmcp Client, pin each status.
- [x] 2.2 Run strict OpenSpec validation and the overwrite, archive and citation guards.
