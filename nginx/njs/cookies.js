// Cookie header for tunnel apps: everything except oauth2-proxy's cookies
// (session, csrf), however many there are. A throw yields "" (fails closed).
function clean(r) {
  var c = r.headersIn.Cookie;
  if (!c) return '';
  return c.split(/;\s*/).filter(function (p) {
    return p && p.indexOf('_oauth2_proxy') !== 0;
  }).join('; ');
}

export default { clean };
