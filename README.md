# cde-sish-tunnels

Self-hosted [sish](https://github.com/antoniomika/sish) deployment for GlueOps
Cloud Development Environment (CDE) tunnels. sish exposes HTTP(S)/WS(S)/TCP
tunnels to a developer's localhost over plain SSH remote port forwarding — no
client agent required.

Version: 0.5.0 <!-- x-release-please-version -->

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
  before requests reach sish, so a tunnel never sees visitors' sessions.
- **Signed-out background requests** (fetch/XHR, websockets, images) get a
  plain 401; only page loads go to Google, so a reload signs back in.
- **Reserved:** `/oauth2/*` on every tunnel host is handled by the proxy; the
  apex `${DOMAIN}` (the SSO callback) and `origin.${DOMAIN}` never route to a
  tunnel, even if a VM binds that name.
- **Browser only.** Webhooks, `curl` and other non-browser clients are
  redirected to Google and can't reach tunnels; there is no bypass.
- **TLS 1.3 only** on :443, with HSTS covering every subdomain. Clients that
  can't do TLS 1.3 (very old browsers, some TLS-inspecting proxies) can't
  connect. nginx logs `tls=` and `curve=` per request, and `cf=` (CloudFront's
  request ID) on requests that came through CloudFront.

One-time Google setup (Google Cloud console → APIs & Services):

1. OAuth consent screen: user type **Internal**, so Google itself rejects
   accounts outside the Workspace.
2. Credentials → OAuth client ID, type **Web application**. Add one
   authorized redirect URI per region:
   `https://<region>.tunnels.cde.glueopshosted.com/oauth2/callback`.
   New URIs can take a few minutes to apply.
3. Put the client ID/secret in each box's `.env`, with a per-box cookie
   secret (see [`.env.example`](./.env.example)).

CloudFront settings SSO depends on (the managed **CachingDisabled** cache
policy plus **AllViewer** origin request policy covers all the forwarding):

| Setting | If missing |
|---|---|
| No caching | Visitors could get other users' cached pages. |
| Forward `Host` | Tunnels don't route (already broken without SSO). |
| Forward query strings | Tunnel URLs lose their query (already broken without SSO). |
| Forward cookies and return `Set-Cookie` | Nobody can log in through CloudFront: the sign-in cookie is dropped, so login fails with a 403 or keeps bouncing to Google. |
| HTTPS to the origin | Every tunnel loops: nginx redirects HTTP to HTTPS, and CloudFront hands the redirect back to the browser. |
| Forward `Sec-Fetch-Mode` | Still works, but background requests start logins too, so a polling tab can occasionally break a login in progress (retrying fixes it). |
| TLS 1.3 to the origin | Every request through CloudFront gets a 502. CloudFront negotiates 1.3 to origins automatically (since Nov 2025); no setting controls it. |

To confirm CloudFront reaches the box, browse a tunnel through CloudFront, then
on the box count its requests (a refused handshake leaves no log line, so a
failure shows as zero, not as TLS 1.2):

```bash
docker compose logs --since 10m nginx | grep -c ' cf="[^-]'
```

**TLS 1.3 fallback:** if CloudFront 502s only because of TLS 1.3, allow TLS
1.2 rather than rolling back. In `nginx/templates/default.conf.template`
replace the `ssl_protocols` line with:

```nginx
ssl_protocols TLSv1.2 TLSv1.3;
ssl_ciphers ECDHE+AESGCM:ECDHE+CHACHA20;
```

Check it (`docker compose run --rm --no-deps -T nginx /docker-entrypoint.sh nginx -t -g 'load_module modules/ngx_http_js_module.so;'`),
then `docker compose restart nginx`; only nginx restarts, tunnels stay up. The
edit is a local change: the next deploy keeps it (or refuses the checkout if
the release changed that file). Undo it with
`git checkout -- nginx/templates/default.conf.template` only once CloudFront
negotiates TLS 1.3 or a release handles it, or the 502s come back.

The OAuth callback itself hits the apex, which resolves straight to the box,
not through CloudFront.

To see why someone was denied: `docker compose logs oauth2-proxy | grep <email>`.
nginx logs one line per request with the signed-in email.

## Quick start

1. Get the latest release:

   <!-- x-release-please-start-version -->
   ```bash
   git clone https://github.com/GlueOps/cde-sish-tunnels.git && cd cde-sish-tunnels
   git checkout v0.5.0
   ```
   <!-- x-release-please-end -->

2. Configure environment:

   ```bash
   cp .env.example .env
   ```

   Edit `.env` and set every value for this deployment — the comments in
   [`.env.example`](./.env.example) explain each one.

3. Start the stack:

   ```bash
   docker compose up -d --build --force-recreate --remove-orphans
   ```

   On first boot nginx starts only once certbot has issued the cert (up to a
   few minutes).

4. Smoke-test the tunnel and naming mode from any machine:

   ```bash
   ssh -p 2222 -R smoketest:80:localhost:3000 usfoobar1.tunnels.cde.glueopshosted.com
   # → https://smoketest.usfoobar1.tunnels.cde.glueopshosted.com
   # (no <user>- prefix in the URL sish prints — if there is one, the wrong
   #  stack version is deployed)
   curl -sI https://smoketest.usfoobar1.tunnels.cde.glueopshosted.com/ | grep -iE '^(HTTP|location|x-cache)'
   # → HTTP/2 302 + location: https://accounts.google.com/... (SSO is enforcing)
   # (through CloudFront, 502 + x-cache: Error from cloudfront = origin TLS failed)
   ```

   Then open the URL in a browser, sign in with a company account, and check
   the app loads — once through CloudFront and once direct to the box.

## Deploying

Every routine deploy, upgrade and rollback is one command: check out the
release tag, then bring the stack up from it. It shows the latest release; for
a rollback, put the previous tag in its place. Moving across a version listed
in [MIGRATION.md](./MIGRATION.md) (e.g. 0.4.x ↔ 0.5.0, either direction)
follows its steps instead.

<!-- x-release-please-start-version -->
```bash
git fetch --tags && git checkout v0.5.0 && docker compose up -d --build --force-recreate --remove-orphans
```
<!-- x-release-please-end -->

Run `git status` first and undo local edits to tracked files (e.g. an
uncommented `--debug`): git carries them into the new release, or, if the
release changed that file, refuses the checkout and the chain stops. Keep an
active TLS 1.3 fallback edit (see "Browser access") unless the release fixes
it.

- `--build`: the authenticator is built locally from `auth/`, and plain
  `up -d` keeps the old image forever once one exists.
- `--force-recreate`: config files mounted into containers (`nginx/templates/`,
  `nginx/njs/`, `nginx/watch-cert.sh`) are only read at container start, and
  compose doesn't recreate a container for them. Recreating everything means
  every change is picked up, at the cost of every deploy dropping all tunnels
  and browser access for about 10 seconds (VMs reconnect on their own;
  sessions survive, they live in the cookie).
- `--remove-orphans`: removes services a release dropped, so they can't keep
  holding ports.

Recreating a container deletes its logs. To keep them (e.g. mid-incident),
save them first: `(umask 077; docker compose logs -t > ~/pre-deploy-$(date +%F-%H%M).log)`
(they hold users' emails and IPs; delete the file when done).

Never delete the `sish-keys` volume (`docker compose down -v`): it holds sish's
SSH host key, and a new key disables every VM's tunnels until each VM runs
`ssh-keygen -R '[<region>.tunnels.cde.glueopshosted.com]:2222'`.


## Testing

```bash
./test/run.sh
```

Runs the production compose stack layered with
[`test/compose.test.yml`](./test/compose.test.yml): a mock OIDC IdP stands in
for Google, a self-signed cert for certbot, and nothing is published or
written to `./ssl` / `./sish_users`. It checks login, denial, cookie
stripping, header spoofing, websockets, redirects, reserved hosts, `ssh -L`
bypass, the Google login URL, TLS 1.3 only, HSTS, the SSH host key surviving
a recreate, and cert reload. CI runs the same script on
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
5. After the release, deploy it (see Deploying).

## Releases

This repo uses [release-please](https://github.com/googleapis/release-please)
with [Conventional Commits](https://www.conventionalcommits.org/). Merging the
release PR it maintains cuts a GitHub release, tags `vX.Y.Z`, and updates
`CHANGELOG.md`.
