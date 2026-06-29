# Migrating an existing sish host to this repo

This is a one-time runbook for moving a server that runs the **old
`glueops/sish` fork** stack (cloned as `~/sish/glueops-docker-compose`) onto this
repo's upstream-image stack.

The migration is really just **copy runtime state into the new repo, then swap
stacks**. Nothing in the running setup is in git — the parts that matter are:

- `sish_users/` — the trust-on-first-use key database. **Irreplaceable**: losing
  it locks every user out until they re-register.
- `.env` — domain, ACME email, AWS credentials.
- `ssl/` — the current live certificate sish serves.
- the `letsencrypt` Docker volume — certbot's ACME account + renewal state.

> [!WARNING]
> Never run `docker compose down -v` against the old stack — `-v` deletes the
> `letsencrypt` volume. Plain `down` keeps all volumes.

Run everything on the server. Adjust paths if your old checkout lives elsewhere.

## 0. Back up the irreplaceable data first

```bash
cd ~
tar czf sish-backup-$(date +%F-%H%M).tgz \
  -C ~/sish/glueops-docker-compose sish_users ssl .env backup-keys
ls -lh sish-backup-*.tgz
```

## 1. Stop the old stack (keeps data + volumes)

```bash
cd ~/sish/glueops-docker-compose
docker compose down          # frees ports 80/443/2222; keeps sish_users, ssl, volume
```

## 2. Clone this repo

```bash
cd ~ && git clone https://github.com/GlueOps/cde-sish-tunnels.git
```

## 3. Copy runtime state into the new repo

```bash
OLD=~/sish/glueops-docker-compose
NEW=~/cde-sish-tunnels

cp    "$OLD/.env"         "$NEW/.env"
cp -a "$OLD/sish_users/." "$NEW/sish_users/"   # the TOFU key DB — critical
cp -a "$OLD/ssl/."        "$NEW/ssl/"          # current live certs
cp -a "$OLD/backup-keys"  "$NEW/"              # not used by the stack; kept for safety

# sanity: same number of users carried over, env vars present
echo "old: $(ls "$OLD/sish_users" | wc -l)  new: $(ls "$NEW/sish_users" | wc -l)"
grep -E 'DOMAIN|ACME_EMAIL|AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY' "$NEW/.env"
```

The new compose uses the same env-var names, so `.env` drops in unchanged.

## 4. Migrate the certbot volume (recommended)

Carries over the ACME account and renewal state so the new stack does **not**
request a brand-new certificate on first boot.

```bash
docker volume ls | grep letsencrypt   # confirm source (expected: glueops-docker-compose_letsencrypt)

docker volume create cde-sish-tunnels_letsencrypt
docker run --rm \
  -v glueops-docker-compose_letsencrypt:/from:ro \
  -v cde-sish-tunnels_letsencrypt:/to \
  alpine sh -c 'cp -a /from/. /to/'
```

Skippable: if omitted, certbot just requests a fresh cert via DNS-01 on first
boot. That works, but counts against Let's Encrypt rate limits if you redeploy
repeatedly. Either way tunnels stay up, because `ssl/` already holds a valid cert
that sish serves immediately.

## 5. Start the new stack

```bash
cd ~/cde-sish-tunnels
docker compose up -d --build
```

## 6. Verify

```bash
docker compose ps                       # expect 3 services up
docker compose logs sish | tail -30     # binds :2222/:80/:443, loads certs from /ssl
docker compose logs authenticator | tail -20
docker compose images sish              # confirm it's ghcr.io/antoniomika/sish now

# cert check (replace the domain)
echo | openssl s_client -connect <domain>:443 -servername x.<domain> 2>/dev/null \
  | openssl x509 -noout -subject -dates
```

Then prove the TOFU DB migrated: have an **existing** user connect with their
existing key — they should be allowed without re-registering.

```bash
# from a client machine
ssh -p 2222 -R test:80:localhost:8080 <domain>
```

## 7. Rollback

The old checkout is untouched, so reverting is instant:

```bash
cd ~/cde-sish-tunnels && docker compose down
cd ~/sish/glueops-docker-compose && docker compose up -d
```

> Never run both stacks at once — they bind the same host ports. Always `down`
> one before `up` the other.

## 8. Clean up (only once confident, e.g. the next day)

```bash
docker volume rm glueops-docker-compose_letsencrypt   # old certbot volume
# rm -rf ~/sish                                        # old repo + fork, if desired
```
