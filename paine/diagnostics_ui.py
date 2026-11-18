"""Read-only Supervisor ingress viewer. No user-supplied filesystem paths."""

import base64
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import shutil
from tempfile import TemporaryFile
from threading import Thread
from zipfile import ZipFile

from .water_export import diagnostic_files


WATER_IMAGES = ('overview.jpg', 'register.png', 'reading.png')
PRESSURE_IMAGE = re.compile(r'snapshot(?:_shot[1-8])?(?:_gauge)?\.jpg')


def folder(data, gauge):
    if gauge.water_flow_lpm is not None:
        return data/'water-meter'
    return data/'gauges'/gauge.storage_key if gauge.storage_key else data


def regular(path, data):
    return path.is_file() and path.resolve() == data.resolve()/path.relative_to(data)


def make_zip(output, data, gauges):
    with ZipFile(output, 'w') as archive:
        for gauge in gauges:
            root = folder(data, gauge)
            if gauge.water_flow_lpm is not None:
                paths = [*diagnostic_files(root), *(root/name for name in WATER_IMAGES)]
            else:
                paths = [root/'status.json', *(p for p in root.glob('snapshot*.jpg')
                                             if PRESSURE_IMAGE.fullmatch(p.name))]
            for p in paths:
                if regular(p, data):
                    try:
                        archive.write(p, str(p.relative_to(data)))
                    except FileNotFoundError:  # rotated between listing and opening
                        continue


def figure(path, label, data):
    content = 'Not available yet'
    if path and regular(path, data):
        try:
            encoded = base64.b64encode(path.read_bytes()).decode('ascii')
            mime = 'jpeg' if path.suffix == '.jpg' else 'png'
            content = f'<img alt="{label}" src="data:image/{mime};base64,{encoded}">'
        except FileNotFoundError:  # alignment failure retired the previous strip
            pass
    return f'<figure><figcaption>{label}</figcaption>{content}</figure>'


def summary(root, water, data):
    path = root/('events.jsonl' if water else 'status.json')
    if regular(path, data):
        try:
            with path.open('rb') as f:
                f.seek(max(0, path.stat().st_size-16384))
                event = json.loads(f.read().splitlines()[-1])
            when = datetime.fromtimestamp(event['time']).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')
            if water:
                ticks = event.get('candidate_ticks')
                value = 'No reading' if ticks is None else f'{ticks/100:.2f} m³'
                decision = event.get('decision', 'no decision')
                status = ('Accepted' if decision in ('confirmed', 'baseline_corrected') else
                          'Pending' if decision in ('bootstrap', 'confirm_change') else 'Not accepted')
                result = f'{value} · {status} ({decision.replace("_", " ")})'
                if 'error_stage' in event:
                    result += ' · failed: ' + event['error_stage']
            else:
                result = event['status']
            return escape(f'{when} — {result}')
        except (IndexError, ValueError, KeyError, OSError, TypeError):
            pass
    return 'Awaiting a completed reading'


def page(data, gauges, index):
    gauge = gauges[index]
    root = folder(data, gauge)
    water = gauge.water_flow_lpm is not None
    tabs = ''.join(f'<a href="?g={i}" aria-current="{"page" if i == index else "false"}">'
                   f'{escape(g.config["MQTT_SENSOR_NAME"])}</a>' for i, g in enumerate(gauges))
    if water:
        images = [(root/name, label) for name, label in zip(WATER_IMAGES,
                  ('Mirror-corrected meter area', 'Aligned register', 'Selected digits used for reading'))]
    else:
        captures = (p for p in root.glob('snapshot*.jpg') if PRESSURE_IMAGE.fullmatch(p.name)
                    and '_gauge' not in p.name and regular(p, data))
        latest = max(captures, key=lambda p: p.stat().st_mtime_ns, default=None)
        crop = latest.with_stem(latest.stem+'_gauge') if latest else None
        if crop and (not regular(crop, data) or crop.stat().st_mtime_ns < latest.stat().st_mtime_ns):
            crop = None
        images = [(latest, 'Latest capture'), (crop, 'Gauge crop')]
    return f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Paine diagnostics</title>
<style>:root{{color-scheme:light dark;font:1rem system-ui}}body{{max-width:75rem;margin:2rem auto;padding:0 1rem}}
nav,section{{display:flex;flex-wrap:wrap;gap:1rem}}nav a{{padding:.5rem}}[aria-current=page]{{font-weight:bold;border-bottom:3px solid}}
figure{{flex:1 1 15rem;margin:0}}figcaption{{font-weight:bold;margin-bottom:.5rem}}img{{width:100%;height:auto}}</style>
<h1>Paine diagnostics</h1><nav aria-label="Meters">{tabs}</nav><p>{summary(root, water, data)}</p>
<section>{''.join(figure(path, label, data) for path, label in images)}</section>
<p><a href="?g={index}">Refresh</a> · <a href="diagnostics.zip">Download all enabled meter diagnostics (ZIP)</a></p></html>'''.encode()


def start_ui(data, gauges):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.client_address[0] != '172.30.32.2':  # Supervisor ingress proxy
                self.send_error(403)
                return
            if self.path == '/diagnostics.zip':
                with TemporaryFile() as f:
                    make_zip(f, data, gauges)
                    self.headers_for('application/zip', f.tell(), 'paine-diagnostics.zip')
                    f.seek(0)
                    shutil.copyfileobj(f, self.wfile)
                return
            match = re.fullmatch(r'/(?:\?g=([0-9]{1,6}))?', self.path)
            if not match or (index := int(match[1] or 0)) >= len(gauges):
                self.send_error(404)
                return
            body = page(data, gauges, index)
            self.headers_for('text/html; charset=utf-8', len(body))
            self.wfile.write(body)

        def headers_for(self, mime, length, filename=None):
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(length))
            self.send_header('Cache-Control', 'no-store')
            if filename:
                self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.end_headers()

    server = ThreadingHTTPServer(('', 8100), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server
