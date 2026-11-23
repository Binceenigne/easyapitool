# Mirra Seoul egress

The gateway at `39.105.74.222` can use the existing Seoul Squid through
`http://127.0.0.1:18794`. Squid remains bound to `172.29.0.4:3128` with its
existing source and destination allowlists. OpenAI TLS terminates at OpenAI.

```
Mirra (127.0.0.1:18794)
  -> encrypted SSH tunnel to Nanjing relay (146.56.217.193)
  -> encrypted SSH tunnel to Seoul (172.29.0.4)
  -> existing Squid -> api.openai.com
```

## Tunnel

Install `mirra-seoul-egress-tunnel.service` on the Seoul host. Its dedicated
`mirra-egress-tunnel` system user reads a newly generated SSH key from
`/etc/mirra-egress-tunnel/id_ed25519`. Never reuse or copy an operator key.
`known_hosts` in that directory contains the gateway's independently verified
ED25519 host key. Directory mode is 0750; key and known-hosts modes are 0640,
owned by root and readable only by the tunnel group.

Install `mirra-seoul-egress-relay.service` on the existing Nanjing relay. It
connects to Mirra and exposes only `127.0.0.1:18794` there. Its local target is
the same loopback port on Nanjing, provided by the separate Seoul tunnel. It
also uses a newly generated dedicated key; operator keys stay on their host.

The gateway and Nanjing relay each have a `mirra-seoul-tunnel` account with a
non-login shell. The gateway accepts only the relay's source IP; the relay
accepts only Seoul's source IP. Both authorized keys allow only remote listener
`127.0.0.1:18794`, with no interactive session or useful local forwarding.
Keep `GatewayPorts` disabled.

Install `mirra-seoul-egress-sshd.conf` as a drop-in on the two receiving SSH
hosts. If sshd does not already include that directory, append an explicit
Include for this file. Back up the main configuration, validate with `sshd -t`,
and reload. Check the effective settings for `mirra-seoul-tunnel` before
reconnecting the new services. Other SSH users retain their configuration.

Both services reconnect automatically, use client and server keepalives, and
disable IPQoS. Their listeners remain on loopback; neither public proxy ports
nor changes to Squid or CCN are required.

After installing the units, reload systemd and enable them. Readiness requires
an established listener on the gateway and a TLS-verified response from
`https://api.openai.com/v1/models` through it. A no-key probe normally returns
401. Validate complete image transfer and concurrent connections before making
the proxy the gateway default. Save the setting through the existing admin
proxy API with its revision to preserve concurrent-edit protection.

## Recovery and rollback

The gateway retries an image request once only when the TLS connection has not
been obtained and its upload has not been read. Once connected, an interrupted
POST is never automatically replayed. A proven unsent request releases its USD
reservation with zero cost; an ambiguous submitted request retains the existing
usage-reconciliation behavior.

To revert the egress, restore the previous mode and address from the encrypted
proxy configuration backup using the admin API. The prior managed pool listens
on `127.0.0.1:18789`. Existing in-flight requests retain their transport.

After image transfers finish, stop and disable the new services on Seoul and
Nanjing. Remove only their authorized-key entries and dedicated SSH drop-ins
if retiring them. Do not restart Squid, existing Sub2API tunnels, workers, or
SSH during this maintenance.
