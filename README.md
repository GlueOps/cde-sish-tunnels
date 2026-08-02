# cde-sish-tunnels

Self-hosted [sish](https://github.com/antoniomika/sish) deployment for GlueOps
Cloud Development Environment (CDE) tunnels. sish exposes HTTP(S)/WS(S)/TCP
tunnels to a developer's localhost over plain SSH remote port forwarding — no
client agent required.

Version: 0.3.0 <!-- x-release-please-version -->

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

## Deployment model: one box per datacenter region

This repo deploys **regional** tunnel servers only: one instance per
datacenter, each serving `DOMAIN=<region>.tunnels.cde.glueopshosted.com`.
Subdomains are bound literally (`--force-requested-subdomains`): a codespace
VM binds its own hostname, so URLs are
`https://<hostname>.<region>.tunnels.cde.glueopshosted.com` with no prefix,
and a taken name fails the bind rather than silently going random. AWS creds
come from the `acme-dns01-cde.glueopshosted.com` IAM user (the `cde_acme_*`
outputs of the CDE assets workspace).

### Restricting who can create tunnels

`TUNNEL_ALLOWED_CIDRS` (comma-separated CIDRs, default: all RFC 1918 private
ranges plus `100.64.0.0/10` — the CGNAT range Tailscale uses) is enforced by
the **authenticator** on every SSH auth attempt using the client address sish
reports — so it restricts tunnel creation only. Browsers and CloudFront
(:80/:443) never touch the authenticator and are unaffected. Outside-the-list
clients get a logged 403 (`denied (source ... not allowlisted)`) and can
never register or connect a tunnel. Empty value disables the restriction; a
malformed CIDR fails the authenticator at startup.

Deliberately NOT used: sish's own `--whitelisted-ips`, which is global across
SSH *and* HTTP/S and would block public browsers.

Regional boxes sit behind a per-region CloudFront distribution: browsers hit
`https://<hostname>.<region>.tunnels.cde.glueopshosted.com` via the CDN, which
origin-fetches this box as `origin.<region>.tunnels.cde.glueopshosted.com` —
covered by the same wildcard cert, no extra config here. The box's 443 stays
directly reachable on purpose (CloudFront is an accelerator, not the security
boundary). SSH (:2222) is always direct, never through the CDN. The full
region rollout runbook lives in GlueOps/slackbot-developer-workspaces#499.

## Quick start

1. Configure environment:

   ```bash
   cp .env.example .env
   ```

   ```
   DOMAIN=uswest1.tunnels.cde.glueopshosted.com
   ACME_EMAIL=admin@example.com
   AWS_ACCESS_KEY_ID=your_access_key_id
   AWS_SECRET_ACCESS_KEY=your_secret_access_key
   ```

2. Start the stack (the `cde.glueopshosted.com` NS delegation must be live
   for certbot's DNS-01 validation to succeed):

   ```bash
   docker compose up -d
   ```

3. Smoke-test the tunnel and naming mode from any machine:

   ```bash
   ssh -p 2222 -R smoketest:80:localhost:3000 uswest1.tunnels.cde.glueopshosted.com
   # → https://smoketest.uswest1.tunnels.cde.glueopshosted.com
   # (no <user>- prefix in the URL sish prints — if there is one, the wrong
   #  stack version is deployed)
   ```

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
