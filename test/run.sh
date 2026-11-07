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
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT

echo "sso-test: starting stack ($project)"
compose build -q
compose up -d
echo "sso-test: running checks"
compose run --rm tester python -I /test_sso.py

echo "sso-test: checking nginx picks up a renewed certificate"
before=$(compose run --rm tester python -I /test_sso.py fingerprint)
# shellcheck disable=SC2016  # $0 expands inside the container
compose exec -T letsencrypt sh -c 'openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -subj "/CN=$0" -addext "subjectAltName=DNS:$0,DNS:*.$0" \
  -keyout "/ssl/$0.key" -out "/ssl/$0.crt" 2>/dev/null' "$(grep '^DOMAIN=' test/test.env | cut -d= -f2)"
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
