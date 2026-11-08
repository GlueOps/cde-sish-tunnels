#!/usr/bin/env bash
# End-to-end SSO test: the production compose stack layered with
# test/compose.test.yml (mock IdP for Google, self-signed cert). Same command
# locally and in CI. Exit code is the result.
set -euo pipefail
cd "$(dirname "$0")/.."

OAUTH2_PROXY_COOKIE_SECRET=$(head -c 32 /dev/urandom | base64 | tr -d '\n' | tr '+/' '-_')
export OAUTH2_PROXY_COOKIE_SECRET
project="sish-sso-test-$$"
compose() {
  docker compose -p "$project" --env-file test/test.env \
    -f docker-compose.yml -f test/compose.test.yml --profile test "$@"
}

# shellcheck disable=SC2317  # invoked via trap
cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "--- failed; recent logs ---"
    compose logs --tail=40 nginx oauth2-proxy sish authenticator tunnel || true
  fi
  compose down -v --rmi local --remove-orphans >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT

echo "sso-test: starting stack ($project)"
compose build -q
compose up -d
echo "sso-test: running checks"
compose run --rm tester python -I /test_sso.py

echo "sso-test: checking sish keeps its SSH host key across a recreate"
hostkey() { compose run --rm tester ssh-keyscan -p 2222 -t ed25519 sish 2>/dev/null || true; }
key_before=$(hostkey)
compose up -d --force-recreate sish
key_after=""
for _ in $(seq 10); do
  sleep 1
  key_after=$(hostkey)
  [ -n "$key_after" ] && break
done
if [ -z "$key_before" ] || [ "$key_after" != "$key_before" ]; then
  echo "FAIL  sish host key changed on recreate (before=${key_before:-none} after=${key_after:-none})"
  exit 1
fi
echo "PASS  sish host key survives a recreate"

echo "sso-test: checking nginx picks up a renewed certificate"
before=$(compose run --rm tester python -I /test_sso.py fingerprint)
# The test letsencrypt service issues a fresh self-signed cert on every start.
compose restart letsencrypt
for _ in $(seq 15); do
  sleep 2
  after=$(compose run --rm tester python -I /test_sso.py fingerprint)
  if [ "$after" != "$before" ]; then
    echo "PASS  renewed certificate served without restart"
    exit 0
  fi
done
echo "FAIL  renewed certificate not served after 30s"
exit 1
