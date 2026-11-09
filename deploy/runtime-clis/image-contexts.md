# Source-owned complete image build contexts

`scripts/runtime_clis/produce_image_contexts.py` runs the existing pinned public
producer recipes serially, with one heavy producer at a time:

```sh
python3 scripts/runtime_clis/produce_image_contexts.py \
  --project "$PWD" --work /absolute/new/work \
  --output /absolute/new/closed-contexts
```

The three paths must be real, absolute and disjoint. Work and output must not
exist. The caller needs ordinary Docker/Buildx access; the script neither
escalates privileges nor changes the daemon. It uses the default local builder,
Linux/amd64, an empty task-owned Docker configuration and no ambient credentials,
proxy or remote-daemon variables. HOME retains its caller value. Builder recipes
keep their existing empty private HOME mounts and actual networkless final
layers. No runtime egress or deployment option is enabled.

A finite copy of tracked current source bytes excludes Git metadata, untracked
files/caches and dotenv files. Tracked actual environment files refuse before
copying; the harmless tracked `.env.example` and `.envrc` are excluded. The
Python source snapshot is captured by its existing strict producer API. Both
copies are checked against source changes before further production. The
frozen Docker source remains checked through final publication.

The pipeline fetches both exact Debian profiles, then runs every existing
complete Docker producer: Node/uv; full Codex; Google Auth and GenAI SDKs;
complete default plugin; Gemini; OpenCode; original native packages; all four
Python profiles; and the full Go module closure. Named-context dependencies
are explicit and ordered. Normal lifecycle scripts, native/platform packages,
embedded UI, models, plugins, authentication and tool behavior remain owned by
those recipes. No graph is reduced to satisfy a build.

The six final runtime components go through the unchanged `runtime_artifacts.py
close` API and its exact committed tree anchors. A directory or successful
Docker exit alone cannot publish a runtime context: its closed manifest must
equal the source-owned manifest. Mismatched actual artifacts refuse; expected
digests are never calculated and silently substituted by this pipeline.

Only all-complete output is atomically published. It contains named context
directories `system`, `compiler`, `toolchains`, `codex`, `google-auth`, `genai`,
`plugin`, `gemini`, `opencode`, `native`, `python-source`, `python`, `go`, `runtime`,
plus content-only `source-inputs.json` and `verification.json`. Python contains
both default and live-listener variants and their actual four-profile
verification. The final app consumes its selected variant, plus the complete
Go context; the base consumes system and runtime contexts.

Commands are bounded and use the existing producer owner-signal/process-group
settlement implementation. Logs and command outcomes remain in the work
directory. They are execution evidence, never reproducible artifact inputs.
The three-hour per-producer watchdog accommodates the measured full Codex
release build (about 85 minutes); it is a bound, not a timing-success claim.

Source-shaped orchestration tests use synthetic Docker exports and establish
ordering, refusal and publication behavior only. Actual Docker producer,
assembled images, exact-image sandbox/nightly execution and remaining native
conformance must still earn their own terminal evidence. Building contexts
does not publish or deploy an application image.
