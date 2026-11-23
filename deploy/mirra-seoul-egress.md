# Mirra Seoul egress

The gateway at `39.105.74.222` can use the existing Seoul Squid through
`http://127.0.0.1:18792`. Squid remains bound to `172.29.0.4:3128` with its
existing source and destination allowlists. OpenAI TLS terminates at OpenAI.

## Tunnel

Install `mirra-seoul-egress-tunnel.service` on the Seoul host. Its dedicated
`mirra-egress-tunnel` system user reads a newly generated SSH key from
`/etc/mirra-egress-tunnel/id_ed25519`. Never reuse or copy an operator key.
`known_hosts` in that directory contains the gateway's independently verified
ED25519 host key. Directory mode is 0750; key and known-hosts modes are 0640,
owned by root and readable only by the tunnel group.

The gateway has a separate `mirra-seoul-tunnel` account with a non-login shell.
The authorized key is restricted to the Seoul source IP `43.133.91.220`, remote
listener `127.0.0.1:18792`, and no interactive session. Local forwarding is
restricted to the unused `127.0.0.1:1` target. Keep `GatewayPorts` disabled.

The service reconnects automatically, uses SSH keepalives, and disables IPQoS.
`mirra-seoul-egress-route.service` enables BBR only for the gateway's /32 route
on Seoul. It does not change the default TCP congestion control or the routes
used by existing applications. Recheck the gateway and interface if moving the
Seoul instance to another network.

After installing both units, reload systemd and enable them. Readiness requires
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

After image transfers finish, stop and disable the tunnel on Seoul. Disable the
route unit and remove only its exact `39.105.74.222/32 via 172.29.0.1 dev eth0`
route to restore the default path. Remove only this tunnel's authorized-key
entry if retiring it. Do not restart Squid, existing Sub2API tunnels, workers,
or SSH during this maintenance.
