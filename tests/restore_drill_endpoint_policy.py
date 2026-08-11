"""Canonical acceptance matrix for restore-drill relay endpoint tests.

Every supported entry point has an independently implemented validator: the
shell launcher, root-owned firewall, deploy command, and relay process. Keep
this matrix exhaustive so their shared grammar and remote-unicast contract
cannot drift.
"""

# These are accepted only as the separately resolved relay/firewall target.
# They are not valid executor connection identities because Docker cannot
# resolve a numeric literal through the internal relay alias.
REMOTE_IPV4_ACCEPTED = (
    "10.23.4.5",  # RFC1918
    "172.20.4.5",  # RFC1918
    "192.168.4.5",  # RFC1918
    "100.64.4.5",  # tailnet / CGNAT
    "192.0.1.1",  # ordinary public unicast outside 192.0.0.0/24
    "8.8.8.8",  # public unicast
)

EXECUTOR_DNS_IDENTITIES_ACCEPTED = (
    "postgres.example.test",
    "postgres",
)

LEGACY_NUMERIC_IPV4_REJECTED = (
    # These are all accepted by libc's legacy inet_aton-compatible resolver,
    # but must never be treated as a DNS/TLS identity or a firewall target.
    "169280517",  # decimal 10.23.4.5
    "01205602005",  # octal 10.23.4.5
    "0x0a170405",  # hexadecimal 10.23.4.5
    "012.027.004.005",  # octal dotted decimal 10.23.4.5
    "0x0a.0x17.0x04.0x05",  # hexadecimal dotted decimal 10.23.4.5
)

# Ports travel through the elevated firewall wrapper as literal iptables
# arguments.  Leading zeros are therefore forbidden: iptables may otherwise
# interpret the value as octal while the Python relay interprets it as decimal.
NONCANONICAL_PORT_REJECTED = (
    "05432",
    "0005432",
    "010",
    "+5432",
    "5_432",
    "５４３２",
    " 5432",
)

REMOTE_IPV4_REJECTED = (
    "0.0.0.0",  # unspecified
    "0.0.0.1",  # 0/8
    "127.0.0.1",  # loopback
    "169.254.1.1",  # link-local
    "192.0.0.9",  # IETF protocol assignment
    "192.0.2.1",  # TEST-NET-1
    "192.31.196.1",  # AS112-v4
    "192.52.193.1",  # AMT
    "192.88.99.1",  # deprecated 6to4 relay
    "192.175.48.1",  # Direct Delegation AS112
    "198.18.1.1",  # benchmark range
    "198.51.100.1",  # TEST-NET-2
    "203.0.113.1",  # TEST-NET-3
    "224.0.0.1",  # multicast
    "240.0.0.1",  # reserved
    "255.255.255.255",  # limited broadcast
    # Noncanonical dotted decimal must be rejected before iptables can treat
    # any octet as octal and silently select a different target.
    "010.23.4.5",
    "198.022.001.001",  # iptables normalizes this to benchmark 198.18.1.1
    "192.037.196.1",  # iptables normalizes this to AS112 192.31.196.1
    "1.2.3.4.",  # a trailing delimiter is not canonical dotted decimal
    " 10.23.4.5 ",  # endpoint literals are not trimmed at any boundary
    *LEGACY_NUMERIC_IPV4_REJECTED,
)

# Every numeric spelling is invalid as the executor's connect/TLS identity.
EXECUTOR_NUMERIC_IDENTITIES_REJECTED = (
    *REMOTE_IPV4_ACCEPTED,
    *REMOTE_IPV4_REJECTED,
)
