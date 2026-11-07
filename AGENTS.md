# AGENTS.md

Docker Compose deployment of upstream [sish](https://github.com/antoniomika/sish)
for GlueOps CDE tunnels: one box per region, SSH tunnel creation on :2222,
browser access through a Google SSO edge. Deployment only — no fork of sish.
See [README.md](./README.md) for the full picture.

## Repo map

- `docker-compose.yml` — the whole stack: letsencrypt, nginx, oauth2-proxy, sish, authenticator.
- `nginx/templates/default.conf.template` — TLS edge + `auth_request` to oauth2-proxy; `${DOMAIN}` is the only substituted variable.
- `nginx/watch-cert.sh` — reloads nginx when the cert changes.
- `auth/` — Flask TOFU SSH-key authenticator (sish's key-check webhook).
- `deploy-hook.sh` — certbot hook copying certs into `./ssl`.
- `test/` — end-to-end SSO test (`run.sh`, compose override, checks in `test_sso.py`).
- `.env.example` — every deploy-time setting, with comments.

## Build and test

```bash
./test/run.sh   # Docker only; same command CI runs (.github/workflows/test.yaml)
```

Run it after any change to `docker-compose.yml`, `nginx/` or `auth/`.

## Rules

- Pin every image by tag **and** digest; don't use unpinned images.
- Never publish sish's 80/443 or oauth2-proxy's 4180. Only nginx faces browsers.
- Published ports are IPv4-only (`0.0.0.0:`), see the comment on sish's ports.
- Never mount SSH keys into sish (see `/empty-pubkeys` comment).
- New required settings use `${VAR:?message}` so a box with an old `.env` fails at startup.
- `OAUTH2_PROXY_COOKIE_CSRF_PER_REQUEST_LIMIT` + 2 (pending csrf cookies + session) must not exceed nginx's 4 cookie-strip passes; add a pass if you raise it.
- Conventional Commits, release-please friendly (`feat!:` for breaking deploy changes).

## Decisions

Settled; don't re-raise in reviews.

- **nginx + oauth2-proxy** for SSO (not Pomerium/Traefik/Caddy, not oauth2-proxy as the edge — it only keeps a relative return path, which breaks the shared callback).
- **Hard 12h sessions, no `cookie-refresh`.** Refresh slides the expiry forward (it resets the session's creation time), which defeats the cap. Offboarded users keep an existing session up to 12h; the kill switch is rotating `OAUTH2_PROXY_COOKIE_SECRET`.
- **Account picker + consent on every login** (`prompt=select_account consent`).
- **Browser only, no bypass** for webhooks/API clients/public tunnels.
- **Domain-only authorization:** any company account can open any tunnel; no per-owner check.
- **Callback on the apex** `https://${DOMAIN}/oauth2/callback` (no tunnel can bind it; resolves straight to the box).
- **`.env` for secrets**, matching the existing AWS keys, not `*_FILE` secrets.
- **No `--trusted-proxy-ip` / split networks:** oauth2-proxy is unpublished and the trusted headers only affect its logs.
- **Accepted:** anonymous `curl`/XHR gets a 302 to Google, not a 401; websockets are checked only at upgrade; a tunnel app can set cookies on `.${DOMAIN}` (could log other users out); `/oauth2/*` is reserved on every tunnel host.
- **CI test runs on every PR but isn't a required check.**
