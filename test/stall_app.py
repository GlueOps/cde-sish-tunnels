"""Tunnel app for test_sso.py: /stall pauses mid-body, /app.js is a compressible script."""
import http.server
import time

STALL = b"x" * 2_000_000
JS = b"console.log('hello');\n" * 2000


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        body, ctype = (STALL, "application/octet-stream") if self.path == "/stall" else (JS, "application/javascript")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        half = len(body) // 2
        self.wfile.write(body[:half])
        self.wfile.flush()
        if self.path == "/stall":
            time.sleep(8)
        self.wfile.write(body[half:])


http.server.ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
