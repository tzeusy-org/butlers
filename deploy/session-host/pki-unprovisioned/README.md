# Session host PKI: not provisioned

This tracked directory is the default `SESSION_HOST_PKI_DIR` for the dark
`session-host` Compose service. It holds no key material, so `butlers session-host`
refuses to start (`session_host_pki_unavailable`) until an operator points
`SESSION_HOST_PKI_DIR` at a provisioned directory. Never commit key material here.

A provisioned directory holds:

| File | Content |
| --- | --- |
| `tls.crt` | The host's leaf certificate (PEM, at most 64 KiB) |
| `tls.key` | Its private key, readable by its owner only (mode `0600` or `0400`) |
| `client-ca.crt` | The only CA a control-plane caller may chain to |
| `server.sha256` | SHA-256 of `tls.crt`'s DER, 64 hex characters |
| `caller.sha256` | SHA-256 of the control-plane client leaf's DER |

Production CA, workload identity, rotation and pin distribution are a separate gate;
see `docs/runtime/session-host.md`.
