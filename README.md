# cde-sish-tunnels

Self-hosted [sish](https://github.com/antoniomika/sish) deployment for GlueOps
Cloud Development Environment (CDE) tunnels. sish exposes HTTP(S)/WS(S)/TCP
tunnels to a developer's localhost over plain SSH remote port forwarding — no
client agent required.

Version: 0.2.1 <!-- x-release-please-version -->

## What this is (and isn't)

This repo is **deployment only**. We run the **upstream sish image unmodified**,
pinned by digest — there is no fork to maintain. The only custom pieces are the
Docker Compose stack, a Let's Encrypt certificate sidecar, and a small auth
service.

```
┌────────────┐   SSH :2222    ┌───────────────────────────────────────┐
│ developer  │ ─────────────► │ sish (ghcr.io/antoniomika/sish)       │
│ ssh -R ... │   HTTP  :80    │  - terminates tunnels                 │
└────────────┘   HTTPS :443   │  - asks authenticator to allow a key  │
                              │  - serves TLS from /ssl               │
                              └───────┬───────────────────┬───────────┘
                                      │ POST key check    │ reads certs
                              ┌───────▼────────┐  ┌────────▼─────────┐
                              │ authenticator  │  │ letsencrypt      │
                              │ (Flask, TOFU)  │  │ (certbot route53)│
                              └────────────────┘  └──────────────────┘
```

## Components

| Service         | Image / Build                                   | Role |
|-----------------|-------------------------------------------------|------|
| `sish`          | `ghcr.io/antoniomika/sish` (pinned by digest)   | The SSH tunnel server (upstream, unmodified). |
| `authenticator` | built from [`./auth`](./auth)                   | Trust-on-first-use SSH key authorizer. |
| `letsencrypt`   | `certbot/dns-route53`                           | Issues & renews the wildcard TLS cert via DNS-01. |

### Authentication model

`authenticator` is a ~40-line Flask app. sish calls it via
`--authentication-key-request-url` on every connection. The service implements
**trust-on-first-use (TOFU)**:

- The first time a username connects, its public key is recorded under
  `sish_users/<username>` and allowed.
- Subsequent connections for that username are allowed **only** if the key
  matches the stored one.

> [!IMPORTANT]
> There is no expiry or revocation. The first key seen for a username wins
> permanently. To rotate or revoke a user, delete or edit their file in
> `sish_users/`. This is intentional for the CDE use case — review it before
> using this in a higher-trust context.

### TLS certificates

`letsencrypt` runs certbot with the Route53 DNS-01 plugin to obtain `${DOMAIN}`
and `*.${DOMAIN}`. On issuance/renewal, [`deploy-hook.sh`](./deploy-hook.sh)
copies the cert into the shared `ssl/` volume as `<domain>.crt` / `<domain>.key`.
sish watches that directory and reloads automatically. Renewal is checked every
12 hours.

The IAM credentials need: `route53:ListHostedZones`, `route53:GetChange`,
`route53:ChangeResourceRecordSets`.

## Quick start

1. Configure environment:

   ```bash
   cp .env.example .env
   ```

   ```
   DOMAIN=ssh.example.com
   ACME_EMAIL=admin@example.com
   AWS_ACCESS_KEY_ID=your_access_key_id
   AWS_SECRET_ACCESS_KEY=your_secret_access_key
   ```

2. Start the stack:

   ```bash
   docker compose up -d
   ```

3. Create a tunnel from a developer machine:

   ```bash
   ssh -p 2222 -R myapp:80:localhost:3000 ssh.example.com
   # → https://<user>-myapp.ssh.example.com
   ```

> Migrating a server from the old `glueops/sish` fork stack? See
> [`MIGRATION.md`](./MIGRATION.md).

## Upgrading sish

We pin the upstream image by digest in [`docker-compose.yml`](./docker-compose.yml).
To bump:

1. Pick a release from
   [antoniomika/sish releases](https://github.com/antoniomika/sish/releases).
2. Resolve its digest:

   ```bash
   gh api users/antoniomika/packages/container/sish/versions \
     --jq '.[] | select(.metadata.container.tags[]? | test("^vX.Y.Z$")) | .name'
   ```

3. Update the `image:` line to `ghcr.io/antoniomika/sish:vX.Y.Z@sha256:...`.
4. Confirm any sish flags we pass still exist in that release before merging.

## Releases

This repo uses [release-please](https://github.com/googleapis/release-please)
with [Conventional Commits](https://www.conventionalcommits.org/). Merging the
release PR it maintains cuts a GitHub release, tags `vX.Y.Z`, and updates
`CHANGELOG.md`.
