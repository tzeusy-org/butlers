# Fan-out Agent Prompt (verbatim)

Phase 1 hands one `general-purpose` agent per flow this prompt. Fill `{FLOW}`, `{GOAL}`,
`{STEPS}`, `{SURFACES}`, `{SPEC/BRIEF}`. Append `references/file-location-map.md`, and (if the dev
stack is up) `references/runtime-verification.md`. Keep the "investigate only" hard stop intact.

---

> You are running a **user-flow QC audit** of the Butlers dashboard (repo: /home/tze/gt/butlers).
> **Investigate and report only — do not edit any file, run any migration, or run quality gates.**
> You are read/trace-only.
>
> **Your flow:** {FLOW}
> **User goal (happy-path):** {GOAL}
> **Happy-path steps:** {STEPS}
> **Surfaces it spans:** {SURFACES}   **Intended end-state:** {SPEC/BRIEF}
>
> Walk this flow end to end as a real user would. For the flow as a whole AND for **each step**,
> answer the five-question rubric:
> 1. **Pleasant experience** — what is the happy-path here, grounded in the spec/brief? What should
>    the user see/feel after each action?
> 2. **What went wrong** — where does the implementation diverge? Which step, which control, what
>    the user sees instead.
> 3. **Maturity of every UX element along the way** — at each step: loading / empty / error /
>    degraded states (a `aggregates_available:false` envelope is "metrics unavailable", NOT an
>    error), action feedback (does the mutation toast success/failure?), sensible defaults,
>    recovery from trap cases.
> 4. **Misleading / poorly-designed elements** — success toast for work that didn't happen;
>    hardcoded status dressed as live; read-only control that looks editable; fake "Live" badge;
>    filter/search that silently drops results. Rank these high.
> 5. **Comprehensive backend support** — does the whole happy-path have real, *consumed*, non-stub
>    backing — including the unhappy branches the user WILL hit (revoked/expired token, empty
>    result, partial failure, permission denied)?
>
> **Method.** For every interactive step, trace handler → API client fn (`frontend/src/api/client.ts`)
> → hook (`frontend/src/hooks/use-*.ts`) → backend route (`src/butlers/api/routers/*.py` or
> `roster/*/api/router.py`) → then **`grep` the written table/column across `src/` to prove a
> runtime reader exists.** "Endpoint exists" ≠ "feature works"; "persists" ≠ "consumed." If the dev
> stack is up, *drive the step live* (curl the endpoint, query the DB, follow the request in
> `docker logs`) and label findings "confirmed"; static-only findings are "suspected." Verify
> against current `main` — re-read the live file before calling a control dead. Note any
> feature-flag gating and its prod default.
>
> Hunt the failure taxonomy you were given (decorative persistence; the lie/overpromise;
> data-contract break; fake/placeholder data; backend-ready-but-unwired; FE-wired-but-stub/404;
> orphaned routes/clipped search; missing states). If you find a shape that fits none, describe it.
>
> **Return (final message = data for the orchestrator, not chat):**
> 1. **Flow + scope** — the goal, the steps you walked, files/routes touched, stack-up? (live vs static).
> 2. **Step-by-step verdict** — for each step: Mature / Mostly / Partial / Skin-deep + one line.
> 3. **Findings table** — Severity (Critical/High/Med/Low, by user-trust damage) | Step | Element
>    (file:line) | What the user expects | What actually happens | Evidence (handler→client→route→
>    consumer trace; live evidence if any) | Confirmed/Suspected.
> 4. **Does the backend comprehensively support the happy-path?** — yes/no + the gaps, including
>    unhappy branches.
> 5. **Dead/decorative/misleading controls** and **orphaned routes/hooks** — explicit lists.
> 6. **Top gaps for this flow**, ranked by user-trust damage.
> Cite file:line everywhere. Do not edit anything.
