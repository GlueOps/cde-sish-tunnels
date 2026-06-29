#!/bin/sh
# Deploy hook script for certbot
# Copies renewed certificates to the ssl directory for sish to pick up

# RENEWED_LINEAGE is set by certbot to the live directory path
# e.g., /etc/letsencrypt/live/ssh.example.com
LETSENCRYPT_LIVE="${RENEWED_LINEAGE}"
DOMAIN=$(basename "${RENEWED_LINEAGE}")
SSL_DIR="/ssl"

echo "Deploying certificates for ${DOMAIN}..."

# Copy certificates to ssl directory (sish watches for file changes)
cp -f "${LETSENCRYPT_LIVE}/fullchain.pem" "${SSL_DIR}/${DOMAIN}.crt"
cp -f "${LETSENCRYPT_LIVE}/privkey.pem" "${SSL_DIR}/${DOMAIN}.key"

# Set proper permissions
chmod 644 "${SSL_DIR}/${DOMAIN}.crt"
chmod 600 "${SSL_DIR}/${DOMAIN}.key"

echo "Certificates deployed successfully. Sish will reload automatically."
