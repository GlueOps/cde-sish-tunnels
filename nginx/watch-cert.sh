#!/bin/sh
# Reload nginx when certbot replaces the cert (sish reloads on its own).
# Hash-gated so long-lived websockets don't pin old workers on every tick;
# `nginx -t` first so a half-copied crt/key pair is retried next tick
# instead of being marked as handled.
crt="/ssl/${DOMAIN}.crt"
# Same as the server's -g: both -t and -s reload parse the config.
mods="load_module modules/ngx_http_js_module.so;"
last=$(sha256sum "$crt" 2>/dev/null)
while sleep "${CERT_CHECK_SECONDS:-600}"; do
  cur=$(sha256sum "$crt" 2>/dev/null)
  if [ "$cur" != "$last" ] && nginx -t -q -g "$mods" && nginx -s reload -g "$mods"; then
    echo "watch-cert: reloaded nginx for new certificate"
    last=$cur
  fi
done
