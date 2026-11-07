"""End-to-end checks for the SSO edge, run by test/run.sh inside the stack."""
import base64
import hashlib
import json
import os
import socket
import ssl
import subprocess
import sys
import time
from urllib.parse import parse_qs, quote, urlparse

import requests
import urllib3

urllib3.disable_warnings()
D = os.environ["DOMAIN"]
APP = f"https://app1.{D}/"
ECHO = "Request served by"
WS = {"Upgrade": "websocket", "Connection": "Upgrade", "Sec-WebSocket-Version": "13",
      "Sec-WebSocket-Key": base64.b64encode(os.urandom(16)).decode()}


def session():
    s = requests.Session()
    s.verify = False
    return s


def login(email, verified=True, target=APP):
    s = session()
    r = s.get(target)
    assert urlparse(r.url).hostname == "idp", f"expected IdP login page, got {r.url}"
    claims = json.dumps({"email": email, "email_verified": verified})
    return s, s.post(r.url, data={"username": email, "claims": claims})


def wait_ready(deadline=120):
    end = time.time() + deadline
    while True:
        try:
            s, r = login("alice@example.com")
            if ECHO in r.text:
                return
        except (requests.RequestException, AssertionError) as e:
            last = e
        else:
            last = f"{r.status_code} {r.url}"
        if time.time() > end:
            sys.exit(f"stack not ready after {deadline}s: {last}")
        time.sleep(2)


results = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}".rstrip(), flush=True)
    results.append(ok)


def ssh_local_forward_blocked():
    """A tunnel user must not reach sish's or oauth2-proxy's HTTP ports via ssh -L."""
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", "/tmp/id"], check=True)
    proc = subprocess.Popen(
        ["ssh", "-N", "-i", "/tmp/id", "-p", "2222", "-o", "StrictHostKeyChecking=no",
         "-L", "9000:sish:80", "-L", "9001:oauth2-proxy:4180", "-L", "9002:localhost:80",
         "vm2@sish"],
        stderr=subprocess.DEVNULL)
    try:
        time.sleep(3)
        if proc.poll() is not None:
            return ["ssh session never came up, so the check proved nothing"]
        reached = []
        for port, host in [(9000, f"app1.{D}"), (9001, f"app1.{D}"), (9002, f"app1.{D}")]:
            try:
                r = requests.get(f"http://127.0.0.1:{port}/oauth2/ping" if port == 9001 else
                                 f"http://127.0.0.1:{port}/", headers={"Host": host}, timeout=3)
                reached.append(f"{port}->{r.status_code}")
            except requests.RequestException:
                pass
        return reached
    finally:
        proc.terminate()


def main():
    wait_ready()

    s, r = login("alice@example.com", target=APP + "?tkn=abc123")
    check("allowed domain lands back on its tunnel URL (query kept)",
          r.status_code == 200 and r.url == APP + "?tkn=abc123" and "GET /?tkn=abc123" in r.text,
          f"{r.status_code} {r.url}")
    check("app receives X-Forwarded-Email", "X-Forwarded-Email: alice@example.com" in r.text)

    session_cookie = next(c for c in s.cookies if c.name == "_oauth2_proxy")
    left = session_cookie.expires - time.time()
    check("session cookie expires in 12h", 12 * 3600 - 300 < left <= 12 * 3600 + 60, f"{left:.0f}s")
    check("session cookie scoped to all tunnels", session_cookie.domain == f".{D}", session_cookie.domain)

    s.cookies.set("appcookie", "keepme", domain=f"app1.{D}")
    r = s.get(APP, headers={"X-Forwarded-Email": "ceo@example.com",
                            "X-Auth-Request-Email": "ceo@example.com",
                            "X-Forwarded-Groups": "admins"})
    cookie_line = next((l for l in r.text.splitlines() if l.startswith("Cookie:")), "")
    check("oauth2-proxy cookies stripped, app cookies kept", cookie_line == "Cookie: appcookie=keepme",
          repr(cookie_line))
    check("client-sent identity headers dropped",
          "ceo@example.com" not in r.text and "admins" not in r.text)

    ws = s.get(APP, headers=WS, stream=True, allow_redirects=False)
    check("websocket upgrade works when signed in", ws.status_code == 101, str(ws.status_code))
    ws.close()
    ws = session().get(APP, headers=WS, stream=True, allow_redirects=False)
    check("websocket upgrade refused when anonymous", ws.status_code != 101, str(ws.status_code))

    r = s.get(f"https://nope.{D}/", allow_redirects=False)
    check("one login covers other tunnel hosts", "idp" not in r.headers.get("Location", "") and ECHO not in r.text,
          str(r.status_code))
    for host in (f"origin.{D}", D):
        r = s.get(f"https://{host}/", allow_redirects=False)
        check(f"reserved host {host} never reaches a tunnel", r.status_code == 404 and ECHO not in r.text,
              str(r.status_code))

    for email, verified, label in [
        ("mallory@evil.example", True, "other domain"),
        ("mallory@example.com.evil.example", True, "suffix-spoofed domain"),
        ("mallory@sub.example.com", True, "subdomain of allowed domain"),
        ("alice@example.com", False, "unverified email"),
    ]:
        _, r = login(email, verified)
        check(f"denied with 403: {label}", r.status_code == 403 and ECHO not in r.text, str(r.status_code))

    anon = session().get(APP, allow_redirects=False)
    check("anonymous request redirected to IdP",
          anon.status_code == 302 and urlparse(anon.headers.get("Location", "")).hostname == "idp")
    r = requests.get(f"http://app1.{D}/x?y=1", allow_redirects=False)
    check("http redirected to https", r.status_code == 301 and r.headers["Location"] == f"https://app1.{D}/x?y=1",
          r.headers.get("Location", ""))
    r = session().get(APP, allow_redirects=False)
    check("HSTS header set", "max-age=31536000" in r.headers.get("Strict-Transport-Security", ""))

    s3 = session()
    r = s3.get(f"https://{D}/oauth2/start?rd=https://evil.example/")
    r = s3.post(r.url, data={"username": "alice@example.com",
                             "claims": json.dumps({"email": "alice@example.com", "email_verified": True})})
    hosts = [urlparse(h.headers.get("Location", "")).hostname for h in r.history] + [urlparse(r.url).hostname]
    check("rd to an outside domain is never followed", "evil.example" not in hosts, str(hosts))

    r = s.get(APP + "oauth2/sign_out", allow_redirects=False)
    check("sign_out redirects somewhere other than itself",
          r.status_code == 302 and "/oauth2/sign_out" not in r.headers.get("Location", ""),
          r.headers.get("Location", ""))
    r = s.get(APP, allow_redirects=False)
    check("signed out session is gone", r.status_code == 302 and "idp" in r.headers.get("Location", ""))

    # Production oauth2-proxy config (Google provider), as nginx would call it.
    r = requests.get("http://oauth2-proxy-google:4180/oauth2/start", allow_redirects=False, headers={
        "Host": f"app1.{D}", "X-Forwarded-Host": f"app1.{D}", "X-Forwarded-Proto": "https",
        "X-Auth-Request-Redirect": APP})
    loc = urlparse(r.headers.get("Location", ""))
    q = {k: v[0] for k, v in parse_qs(loc.query).items()}
    check("Google login URL: accounts.google.com", loc.hostname == "accounts.google.com", loc.hostname or "")
    check("Google login URL: callback on the apex", q.get("redirect_uri") == f"https://{D}/oauth2/callback",
          q.get("redirect_uri", ""))
    check("Google login URL: account picker + consent every login",
          q.get("prompt") == "select_account consent" and "approval_prompt" not in q, str(q.get("prompt")))
    check("Google login URL: returns to the tunnel", q.get("state", "").endswith(":" + quote(APP, safe="")),
          q.get("state", ""))

    reached = ssh_local_forward_blocked()
    check("ssh -L cannot reach sish/oauth2-proxy HTTP directly", not reached, str(reached))

    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


def fingerprint():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((f"app1.{D}", 443), timeout=5) as sock:
        with ctx.wrap_socket(sock, server_hostname=f"app1.{D}") as tls:
            print(hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest())


if __name__ == "__main__":
    fingerprint() if sys.argv[1:] == ["fingerprint"] else main()
