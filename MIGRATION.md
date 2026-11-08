# Migrations

One-time steps for moving a running box between specific versions, in either
direction. New boxes and routine deploys don't need anything here; see the
[README](./README.md).

## 0.4.x → 0.5.0 (Google SSO)

**Applies to:** a box running any 0.4.x release, upgrading to 0.5.0 or later.

0.5.0 puts nginx + oauth2-proxy (Google SSO) in front of sish and moves to
sish v4. It also keeps sish's SSH host key in the `sish-keys` volume; on 0.4.x
the key lived only inside the container, so the steps below carry it over.
Without that, every VM with a saved `known_hosts` entry loses its tunnels:
OpenSSH disables forwarding on a changed host key, even with
`StrictHostKeyChecking=no`.

1. Add the region's redirect URI to the Google OAuth client
   (`https://<region>.tunnels.cde.glueopshosted.com/oauth2/callback`).
2. Check the region's CloudFront distribution against the table under
   "Browser access" in the README. Otherwise logins through CloudFront loop,
   fail or get a 502.
3. Check out the release. `git status` must show no local edits to tracked
   files (undo them first), or the checkout is refused. The running 0.4.x
   stack isn't touched yet.

   ```bash
   git fetch --tags && git checkout v0.5.0
   ```

   Then add the four SSO values from `.env.example` to `.env`.
4. Save sish's host key from the running 0.4.x container, create the new
   containers without starting them, put the key in, and start. Tunnels and
   browser access drop for a few seconds; VMs reconnect on their own with the
   same host key.

   ```bash
   docker compose config --volumes | grep -qx sish-keys &&
     { [ -s ~/sish-host-key ] || docker cp sish:/app/deploy/keys/ssh_key ~/sish-host-key; } &&
     docker compose up --no-start --build --force-recreate --remove-orphans &&
     docker compose cp ~/sish-host-key sish:/keys/ssh_key &&
     docker compose start
   ```

   - The first line stops everything unless the 0.5.0 checkout from step 3
     worked.
   - An existing `~/sish-host-key` is never overwritten, so if the block
     stops partway, fix the cause and run it again.
   - Don't copy the key after sish has started: sish writes a newly generated
     key over it.
5. Run the README's smoke tests, through CloudFront and direct to the box.
   Then `rm ~/sish-host-key`.

### Rolling back 0.5.0 → 0.4.x

**Applies to:** a box on 0.5.0 or later going back to a 0.4.x tag.

0.4.x has no `sish-keys` volume, so sish would generate a new host key. As in
step 4, copy the key out, create the 0.4.x containers without starting them,
put it in, and start. Undo local edits first (`git status`; the TLS fallback
edit isn't needed on 0.4.x).

```bash
docker compose cp sish:/keys/ssh_key ~/sish-host-key &&
  git checkout v0.4.2 &&
  docker compose up --no-start --build --force-recreate --remove-orphans &&
  docker compose cp ~/sish-host-key sish:/app/deploy/keys/ssh_key &&
  docker compose start
```

- If the block stops after the checkout, fix the cause and run its last three
  lines (rerunning the whole block fails at its first line, safely).
- `--remove-orphans` removes nginx and oauth2-proxy, so sish gets :80/:443
  back. Tunnels are public again (no SSO).
- Keep `~/sish-host-key` while on 0.4.x. Every recreate of sish there makes a
  new key, so redeploy 0.4.x with the last three lines of the block above.
- To upgrade again, rerun steps 3–5 above (step 4 reuses the kept key). Leave
  the `sish-keys` volume in place.
- Tunnel apps can see session cookies while rolled back, so change
  `OAUTH2_PROXY_COOKIE_SECRET` when upgrading again.

If CloudFront 502s only because of TLS 1.3, use the TLS 1.3 fallback under
"Browser access" in the README instead of rolling back.
