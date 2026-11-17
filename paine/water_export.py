"""Opt-in HTTPS export of allowlisted water diagnostics, built in memory."""

import hmac
import io
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import re
import ssl
from threading import Thread
from zipfile import ZipFile, ZIP_DEFLATED


def archive(root):
    buf = io.BytesIO()
    size = 0
    with ZipFile(buf, 'w', compression=ZIP_DEFLATED, compresslevel=1) as z:
        for folder, allowed in [(root, r'state\.json|events\.(?:jsonl|[123])|(?:[01]\d|2[0-3])-(?:change|steady|reject)\.(?:jpg|png)'),
                                (root/'examples', r'(?:rolling|accepted|disputed)-(?:0\d|1[0-5])\.zip')]:
            if not folder.is_dir() or folder.is_symlink() or root.is_symlink():
                continue
            for p in sorted(folder.iterdir()):
                if not re.fullmatch(allowed, p.name) or p.is_symlink() or not p.is_file():
                    continue
                try:
                    with p.open('rb') as f:
                        data = f.read(1_000_001)
                except FileNotFoundError:  # Journal rotated during download
                    continue
                size += len(data)
                if len(data) > 1_000_000 or size > 10_000_000:
                    raise ValueError('Export exceeds water storage bounds')
                z.writestr(str(p.relative_to(root)), data)
    return buf.getvalue()


def handler(root, token):
    expected = ('Bearer '+token).encode('ascii')

    class Download(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never echo request URLs/headers into app logs.

        def do_GET(self):
            if not hmac.compare_digest(self.headers.get('Authorization','').encode(), expected):
                self.send_error(401)
                return
            if self.path != '/water/export.zip':
                self.send_error(404)
                return
            try:
                data = archive(root)
            except (OSError, ValueError):
                self.send_error(503)
                return
            self.send_response(200)
            self.send_header('Content-Type','application/zip')
            self.send_header('Content-Disposition','attachment; filename="paine-water.zip"')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    return Download


def start_export(options, data, log):
    token = json.loads(options.read_text()).get('water_export_key', '')
    if not token:
        return None
    if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}',token):
        raise ValueError('Use a random export key of 32–128 URL-safe characters')
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(Path('/ssl/fullchain.pem'), Path('/ssl/privkey.pem'))

    class Server(HTTPServer):
        def get_request(self):
            sock, addr = super().get_request()
            sock.settimeout(5)
            try:
                return ctx.wrap_socket(sock, server_side=True), addr
            except OSError:
                sock.close()
                raise

    server = Server(('', 8099), handler(data/'water-meter', token))
    Thread(target=server.serve_forever, daemon=True).start()
    log('Water data export enabled (HTTPS, port 8099)')
    return server
