# GoogleAuth 10.9.0 cached-ADC STS transport

This series prepares the complete `core/packages/google-auth-library-nodejs`
package from public monorepo commit
`1b010c501559ec8be815f870a340fc79cbe91730`. The official archive digest and
package Git tree are recorded in `series.json`. The source preparer selects
that complete package, retaining its tests, notices and fixture symlinks.

A native external-account client creates a separate STS Gaxios client. When
its parent has an explicitly supplied `fetchImplementation`, the constructor
passes that same terminal fetch into the STS client. Without an explicit fetch,
the original independent STS construction stays unchanged. The certificate
supplier's replacement STS client also retains the explicit fetch alongside
its existing agent. Parsers, token exchange fields, signing, audience, credential
suppliers, caches and native request policy remain upstream code. No private
client field or global Gaxios object is modified by production code.

The new lock records the complete upstream development build graph. It was
resolved from a metadata-only copy with identical dependency declarations;
the actual package retains every original lifecycle script. Run ordinary
`npm ci` and `npm pack --foreground-scripts` with the pinned Node/npm toolchain.
Both execute the original full `tsc -p .` prepare step. Public browser assets
used by upstream development dependencies remain part of that install.

The stock package comparison retained all 95 public package members and changed
only `build/src/auth/baseexternalclient.js` and
`build/src/auth/identitypoolclient.js`. The source build's package name, version,
public declarations and dependencies match the original. Gemini installs this
artifact only at its existing core GoogleAuth 10.9.0 boundary. Its nested SDK
GoogleAuth 10.5.0 and other dependency versions are separate inputs.

Contained controls use actual clients for external-account file-subject STS,
external-account-authorized-user, service-account JWT and impersonation, plus
off-mode survivors. Each runtime child must first verify its separate network
namespace, lack of external routes and exact CJS dependency identity/local
failure trap. Synthetic responses are never evidence of real credential use.
Actual certificate/mTLS, optional credential suppliers, complete native CLI
coverage, immutable images and production networking remain separate gates.
