"""Small, ingress-only camera editor. No public ports or additional dependencies."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread

from .supervisor_config import ConfigurationError


MAX_BODY = 256 * 1024
ASSETS = {"/": ("config_ui.html", "text/html; charset=utf-8"),
          "/editor.js": ("editor.js", "text/javascript; charset=utf-8"),
          "/editor.css": ("editor.css", "text/css; charset=utf-8")}


def make_handler(config):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # No URLs, bodies, or secrets in access logs.

        def setup(self):
            super().setup()
            self.connection.settimeout(20)

        def respond(self, status, body, content_type="application/json"):
            if content_type == "application/json":
                body = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def trusted(self):
            # HA ingress authenticates the browser; only Supervisor may reach us.
            # Never trust a browser-supplied X-Forwarded-For header instead.
            if self.client_address[0] != "172.30.32.2":
                self.respond(403, {"error": "Home Assistant ingress access required"})
                return False
            return True

        def do_GET(self):
            if not self.trusted():
                return
            if self.path in ASSETS:
                name, content_type = ASSETS[self.path]
                self.respond(200, Path(__file__).with_name(name).read_bytes(), content_type)
            elif self.path == "/api/config":
                try:
                    self.respond(200, config.read())
                except Exception:
                    self.respond(503, {"error": "Cannot load settings from Supervisor. Try again"})
            else:
                self.respond(404, {"error": "Not found"})

        def do_POST(self):
            if not self.trusted():
                return
            # A cross-origin HTML form cannot set this header or JSON content type.
            # No CORS/OPTIONS support: browsers cannot preflight cross-site saves.
            if (self.headers.get("X-Paine-Editor") != "1"
                    or self.headers.get_content_type() != "application/json"):
                self.respond(403, {"error": "Invalid editor request"})
                return
            if self.path != "/api/config":
                self.respond(404, {"error": "Not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    raise ValueError()
                data = json.loads(self.rfile.read(length))
                config.save(data["options"], data["revision"])
                self.respond(200, {"message": "Saved. Restart Paine from its Info page to apply the changes."})
            except ConfigurationError as error:
                self.respond(400, {"error": str(error)})
            except Exception:
                self.respond(400, {"error": "Cannot save settings; check the form and try again"})

    return Handler


def start_editor(config):
    server = ThreadingHTTPServer(("0.0.0.0", 8099), make_handler(config))
    thread = Thread(target=server.serve_forever, name="camera-editor", daemon=True)
    thread.start()
    return server
