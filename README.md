# cde-sish-tunnels

Self-hosted [sish](https://github.com/antoniomika/sish) deployment for GlueOps
Cloud Development Environment (CDE) tunnels. sish exposes HTTP(S)/WS(S)/TCP
tunnels to a developer's localhost over plain SSH remote port forwarding — no
client agent required.

Version: 0.4.2 <!-- x-release-please-version -->

## What this is (and isn't)

This repo is **deployment only**. We run the **upstream sish image unmodified**,
pinned by digest — there is no fork to maintain. The only custom pieces are the
Docker Compose stack, a Let's Encrypt certificate sidecar, a small auth
service, and a Google SSO edge (nginx + oauth2-proxy) in front of every
browser request.

```
┌────────────┐  SSH :2222   ┌──────────────────────────────────────┐
│ CDE VM     │ ───────────► │ sish (ghcr.io/antoniomika/sish)      │
│ ssh -R ... │              │  - terminates tunnels                │
└────────────┘              │  - asks authenticator to allow a key │
                            └──▲──────────────────┬────────────────┘
┌────────────┐  :80 / :443     │ HTTP :80         │ POST key check
│ browser    │ ──► nginx ──────┘          ┌───────▼────────┐
└────────────┘     │ auth_request         │ authenticator  │
                   ▼                      │ (Flask, TOFU)  │
              oauth2-proxy ──► Google     └────────────────┘
              (company domain only)
                                 letsencrypt (certbot route53) → ./ssl
```

## Components

| Service         | Image / Build                                   | Role |
|-----------------|-------------------------------------------------|------|
| `sish`          | `ghcr.io/antoniomika/sish` (pinned by digest)   | The SSH tunnel server (upstream, unmodified). |
| `authenticator` | built from [`./auth`](./auth)                   | Trust-on-first-use SSH key authorizer. |
| `letsencrypt`   | `certbot/dns-route53`                           | Issues & renews the wildcard TLS cert via DNS-01. |
| `nginx`         | `nginx` (pinned by digest)                      | TLS edge on :80/:443; every request must pass oauth2-proxy before reaching sish. |
| `oauth2-proxy`  | `quay.io/oauth2-proxy/oauth2-proxy` (pinned)    | Google SSO check, restricted to `SSO_EMAIL_DOMAIN`. Never published. |

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
sish watches that directory and reloads automatically; nginx's
[`watch-cert.sh`](./nginx/watch-cert.sh) reloads nginx within 10 minutes of a
change. Renewal is checked every 12 hours. On first boot nginx waits for the
cert to exist before starting.

The IAM credentials need: `route53:ListHostedZones`, `route53:GetChange`,
`route53:ChangeResourceRecordSets`.

## Deployment model: one box per datacenter region

This repo deploys **regional** tunnel servers only: one instance per
datacenter, each serving `DOMAIN=<region>.tunnels.cde.glueopshosted.com`.
Subdomains are bound literally (`--force-requested-subdomains`): a codespace
VM binds its own hostname, so URLs are
`https://<hostname>.<region>.tunnels.cde.glueopshosted.com` with no prefix,
and a taken name fails the bind rather than silently going random. AWS creds
are access keys for the `acme-dns01-cde.glueopshosted.com` IAM user, which is
scoped to exactly the regional `_acme-challenge` record names.

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
SSH *and* HTTP/S and would block public browsers. The :2222 publish is
IPv4-only because docker-proxy rewrites IPv6 clients' sources to the bridge
gateway (a 172.x address inside the allowlist) — an unqualified publish would
let external IPv6 clients through the check.

Regional boxes sit behind a per-region CloudFront distribution: browsers hit
`https://<hostname>.<region>.tunnels.cde.glueopshosted.com` via the CDN, which
origin-fetches this box as `origin.<region>.tunnels.cde.glueopshosted.com` —
covered by the same wildcard cert, no extra config here. The box's 443 stays
directly reachable on purpose (CloudFront is an accelerator, not the security
boundary — the SSO edge on the box is). SSH (:2222) is always direct, never through the CDN. The full
region rollout runbook lives in GlueOps/slackbot-developer-workspaces#499.

### Browser access (Google SSO)

Every HTTP(S) request — through CloudFront or direct to the box — goes
through nginx, which asks oauth2-proxy whether the visitor has a session.
Without one, the browser is sent to Google; only accounts at exactly
`SSO_EMAIL_DOMAIN` with a verified email get in (others get a 403). One login
covers every tunnel in that region (cookie on `.${DOMAIN}`); each region logs
in separately. SSH tunnel creation (:2222) is unaffected.

- **Sessions last a hard 12h** from login, then Google's account picker and
  consent screen appear again. There is no background refresh on purpose: it
  would slide the expiry forward indefinitely. An open VS Code tab
  disconnects at expiry; reloading the tab signs back in.
- **Kill switch:** changing `OAUTH2_PROXY_COOKIE_SECRET` and running
  `docker compose up -d oauth2-proxy` signs out everyone on that box at once.
  Offboarded users otherwise keep an existing session for up to 12h.
- **What tunnel apps see:** `X-Forwarded-Email` / `X-Forwarded-User` set by
  nginx (client-sent copies are dropped). oauth2-proxy's cookies are stripped
  before requests reach sish, so a tunnel never sees visitors' sessions; a
  request carrying more of them than nginx can strip (e.g. junk ones a tunnel
  app planted) gets a 400 instead of being forwarded.
- **Reserved:** `/oauth2/*` on every tunnel host is handled by the proxy; the
  apex `${DOMAIN}` (the SSO callback) and `origin.${DOMAIN}` never route to a
  tunnel, even if a VM binds that name.
- **Browser only.** Webhooks, `curl` and other non-browser clients are
  redirected to Google and can't reach tunnels; there is no bypass.

One-time Google setup (Google Cloud console → APIs & Services):

1. OAuth consent screen: user type **Internal**, so Google itself rejects
   accounts outside the Workspace.
2. Credentials → OAuth client ID, type **Web application**. Add one
   authorized redirect URI per region:
   `https://<region>.tunnels.cde.glueopshosted.com/oauth2/callback`.
   New URIs can take a few minutes to apply.
3. Put the client ID/secret in each box's `.env`, with a per-box cookie
   secret (see [`.env.example`](./.env.example)).

CloudFront must not cache, must forward `Host`, cookies and query strings
(and return `Set-Cookie`), and must fetch the origin over HTTPS — nginx
redirects :80 to HTTPS. The OAuth callback itself hits the apex, which resolves
straight to the box, not through CloudFront.

To see why someone was denied: `docker compose logs oauth2-proxy | grep <email>`.
nginx logs one line per request with the signed-in email.

## Quick start

1. Configure environment:

   ```bash
   cp .env.example .env
   ```

   Edit `.env` and set every value for this deployment — the comments in
   [`.env.example`](./.env.example) explain each one.

2. Start the stack:

   ```bash
   docker compose up -d
   ```

   On first boot nginx starts only once certbot has issued the cert (up to a
   few minutes).

3. Smoke-test the tunnel and naming mode from any machine:

   ```bash
   ssh -p 2222 -R smoketest:80:localhost:3000 usfoobar1.tunnels.cde.glueopshosted.com
   # → https://smoketest.usfoobar1.tunnels.cde.glueopshosted.com
   # (no <user>- prefix in the URL sish prints — if there is one, the wrong
   #  stack version is deployed)
   curl -sI https://smoketest.usfoobar1.tunnels.cde.glueopshosted.com/ | grep -i location
   # → location: https://accounts.google.com/... (SSO is enforcing)
   ```

   Then open the URL in a browser, sign in with a company account, and check
   the app loads — once through CloudFront and once direct to the box.

## Upgrading to SSO (0.5.0) on an existing box

1. Add the region's redirect URI to the Google OAuth client.
2. Confirm the region's CloudFront distribution meets the requirements under
   "Browser access" (no caching; forwards `Host`, cookies, query strings;
   HTTPS origin) — otherwise logins through the CDN loop or fail.
3. Add the four SSO values to `.env` (`docker compose up` refuses to start
   without them).
4. `docker compose up -d` — this recreates sish, so every tunnel in the region
   drops for a few seconds; VMs reconnect on their own.
5. Run the smoke tests above.

Rollback: check out the previous tag and `docker compose up -d --remove-orphans`
so nginx releases :80/:443 back to sish.

## Testing

```bash
./test/run.sh
```

Runs the production compose stack layered with
[`test/compose.test.yml`](./test/compose.test.yml): a mock OIDC IdP stands in
for Google, a self-signed cert for certbot, and nothing is published or
written to `./ssl` / `./sish_users`. It checks login, denial, cookie
stripping, header spoofing, websockets, redirects, reserved hosts, `ssh -L`
bypass, the Google login URL, and cert reload. CI runs the same script on
every PR. Not covered: a real Google login and CloudFront.

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
