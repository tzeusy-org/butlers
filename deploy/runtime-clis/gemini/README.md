# Gemini CLI 0.66.0

The source series binds public commit
`970413207732f5d4d8c50540c7fc359c44fdaaf2`. Prepare this source with
`prepare_vendor.py`, then supply the separately built Google GenAI SDK archive
using `materialize_inputs.py` and `artifact-inputs.json`. Supply the separately
built core GoogleAuth 10.9.0 archive from its sibling source series as well. The SDK’s digest and
integrity must match before running ordinary `npm ci`; no registry SDK fallback
is permitted. Keep upstream lifecycle scripts and run `npm run build`.

The core package and stock lock both name the verified SDK artifact. Existing
nested GoogleAuth/Gaxios versions stay intact. The isolated transport alias adds
one package without replacing the core or SDK’s original Undici graph. The
selected OAuth/ADC factories inject fetch through their actual transporter
options. Cached ADC parsing passes those options to the actual `fromJSON`
factory and keeps an instance-local hook for recursive impersonation source
clients. The returned client's native transporter keeps its interceptors and
receives the same selected fetch default, including the outer impersonated
client whose parser ignores constructor options. Off mode calls the original
factory unchanged. Real core-resolved client controls cover external-account,
service-account JWT and impersonated refresh with synthetic responses; they do
not claim real ADC credentials or every optional credential subtype. The core's
private, same-version GoogleAuth artifact propagates explicit fetch into the
constructor-created external-account STS transport. Existing other lock nodes
and the SDK's distinct auth versions stay unchanged. The materializer verifies
both archive bytes and declared input modes before a new build. Web Fetch preserves its native permission/timeout handling and routes
its terminal request before ambient proxy selection; the gateway performs the
public-address decision. API-key and Vertex mode detection stay unchanged.
Do not use `GOOGLE_GEMINI_BASE_URL` to select this transport: Gemini treats that
as a gateway authentication mode, which changes behavior.

The SDK source series separately owns API, upload/download and native Live
WebSocket execution. Model names, prompts, stream-json terminal events, MCP
settings and the adapter’s working-directory confinement remain with Gemini.
An actual native turn and its recorded tool inventory do not prove every tool
or authentication mode. Preserve full native-operation coverage, plugin/assets,
immutable installation and offline image closure as separate delivery gates.
