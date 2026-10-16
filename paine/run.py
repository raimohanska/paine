"""Home Assistant app scheduler; options are supplied by Supervisor, not .env."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import socket
import ssl
import subprocess
import sys
from threading import Event
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .capture_camera import capture_pressure_batch
from .app_config import STREAM, load_gauges, load_options as load_options
from .gauge_detection import cached_gauge_circle
from .mqtt_pressure import publish_pressure


GEOMETRY_PREFIX = "PAINE_GAUGE_GEOMETRY "


def decode_geometry(message: str):
    """Accept only finite, in-frame non-secret worker geometry."""
    try:
        geometry = json.loads(message)
        width, height = geometry["frame"]
        circle = cached_gauge_circle(geometry, width, height)
        if circle is None:
            return None
        return {"circle": list(circle), "frame": [width, height]}
    except (TypeError, ValueError, KeyError):
        return None

def log(message: str) -> None:
    """Write messages in local time (Supervisor's TZ), with zone and UTC offset."""
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z%z")
    for line in message.splitlines():
        print(f"[{timestamp}] {line}", flush=True)


def configure_timezone():
    """Honor and validate the IANA timezone injected by Home Assistant Supervisor."""
    name = os.environ.get("TZ")
    if name:
        try:
            ZoneInfo(name)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("TZ must identify an installed IANA timezone") from None
    # Refresh libc's local timezone as well as datetime's local conversions.
    if hasattr(time, "tzset"):
        time.tzset()
    return name


def safe_failure(error: Exception) -> str:
    """Return only known-safe hints, never backend exception text or URLs."""
    known = {
        "Could not confidently detect the black gauge needle": "Cannot confidently detect the needle; check crop, lighting, and camera position",
        "Could not open the camera stream. Check network access and the camera account configured in the Tapo app.": "Cannot open RTSP stream; check camera IP, port, network access, and camera account",
        "Timed out waiting for a camera frame": "Timed out waiting for a camera frame",
        "Could not save the image": "Cannot save the snapshot",
        "Could not save the gauge crop": "Cannot save the gauge crop",
        "Gauge crop is empty": "Gauge crop is empty; check stream resolution",
        "Could not confidently locate the pressure gauge": "Cannot locate a near-front-on light-faced gauge with a clear needle; check framing, size, and lighting",
        "Multiple possible pressure gauges found": "Multiple possible gauges detected; cannot safely choose a pressure reading",
        "Legacy crop does not match the last-known-good gauge location": "Original fixed crop is stale after camera movement; refusing to read unrelated objects",
        "Legacy crop failed gauge rim verification": "Original fixed crop has no verified gauge rim; no pressure reading accepted",
        "Pressure readings have no consistent majority": "Shot batch has no consistent majority within 0.15 bar; no pressure published",
        "Gauge detection failed and legacy fallback is disabled": "Cannot verify this gauge; original garage fallback is disabled for app profiles",
        "MQTT broker connection failed": "Broker connection timed out or was rejected; check MQTT credentials and permissions",
        "MQTT publish failed": "Broker publication failed",
        "MQTT delivery acknowledgement timed out": "Broker delivery acknowledgement timed out",
        "CAMERA_HOST must be a hostname or IP, optionally with a port": "Invalid camera_host; use a hostname/IP, optionally with a port, not a URL",
    }
    if str(error) in known:
        return known[str(error)]
    if isinstance(error, ssl.SSLCertVerificationError):
        return "TLS certificate or hostname verification failed"
    if isinstance(error, ssl.SSLError):
        return "TLS connection failed; check broker TLS settings"
    if isinstance(error, socket.gaierror):
        return "Hostname lookup failed; check host configuration and DNS"
    if isinstance(error, ConnectionRefusedError):
        return "Connection refused; check broker port and whether the service is running"
    if isinstance(error, TimeoutError):
        return "Network operation timed out; check connectivity"
    if isinstance(error, PermissionError):
        return "Permission denied; check app storage access"
    if isinstance(error, ValueError):
        return "Invalid configuration; check MQTT options and calibration"
    return "Backend failure; connection details suppressed"


def capture_once(options_path: Path, data_path: Path, gauge_cache=None, gauge_id=None) -> int:
    """Capture a shot batch and publish its median once, with sanitized output."""
    try:
        gauges = load_gauges(options_path)
        if gauge_id is None:
            if len(gauges) != 1:
                raise ValueError("Select a gauge when more than one is configured")
            gauge = gauges[0]
        else:
            gauge = next(item for item in gauges if item.sensor_id == gauge_id)
        config, _, stream, timeout, two, three, shot_count = gauge.as_tuple()
    except Exception:
        log("Configuration loading failed; check app options")
        return 1
    cache = gauge_cache if gauge_cache is not None else {}
    def report(message):
        log(f"[{gauge.sensor_id}] {message}")
    if gauge.storage_key is not None:
        data_path = data_path / "gauges" / gauge.storage_key
    report(f"Capturing {shot_count} shots from camera stream {stream} and measuring gauge")
    try:
        angle, pressure = capture_pressure_batch(
            config, data_path / "snapshot.jpg", stream, timeout, two, three,
            diagnostics=report, gauge_cache=cache, shot_count=shot_count,
            allow_legacy_fallback=gauge.storage_key is None,
        )
    except Exception as error:
        report(f"Camera capture/measurement failed: {safe_failure(error)}")
        return 1
    finally:
        if cache:
            # Internal worker/scheduler protocol, stripped from user-facing logs.
            # Kept even when later shots or MQTT fail; only auto-detected geometry
            # with a valid needle can update this cache.
            print(GEOMETRY_PREFIX + json.dumps(cache), flush=True)
    report(f"Captured {shot_count} shots; estimated pressure: {pressure:.2f} bar (median); needle: {angle:.1f} degrees")
    report("Publishing discovery and retained pressure to MQTT")
    try:
        publish_pressure(config, pressure, timeout=timeout)
    except Exception as error:
        report(f"MQTT publication failed: {safe_failure(error)}")
        return 1
    report(f"Pressure: {pressure:.2f} bar; needle: {angle:.1f} degrees; published to MQTT")
    return 0


def run_capture(options_path: Path, data_path: Path, timeout: float, stop: Event,
                gauge_cache=None, shot_count: int = 3, gauge_id=None) -> bool:
    """Isolate native camera logs and terminate the worker on shutdown/timeout."""
    command = [sys.executable, "-m", "paine.run", "--once", "--options", str(options_path),
               "--data", str(data_path)]
    if gauge_id is not None:
        command.extend(["--gauge", gauge_id])
    if gauge_cache:
        command.extend(["--last-gauge", json.dumps(gauge_cache)])
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    # Camera open/read cycles plus MQTT and small inter-shot delays.
    deadline = time.monotonic() + (3 * shot_count + 3) * timeout + shot_count + 15
    try:
        while not stop.is_set() and time.monotonic() < deadline:
            try:
                output, _ = process.communicate(timeout=0.5)
            except subprocess.TimeoutExpired:
                continue
            if output.strip():
                # Worker lines already carry their event timestamps. Geometry is
                # kept in scheduler memory, never written to disk or logged.
                for line in output.splitlines():
                    if line.startswith(GEOMETRY_PREFIX):
                        geometry = decode_geometry(line[len(GEOMETRY_PREFIX):])
                        if geometry is not None and gauge_cache is not None:
                            gauge_cache.clear()
                            gauge_cache.update(geometry)
                    else:
                        print(line, flush=True)
            elif process.returncode != 0:
                log("Capture worker failed; will retry on the next interval")
            return process.returncode == 0
        if not stop.is_set():
            log("Capture cycle timed out; will retry on the next interval")
        return False
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()


def run_loop(options_path: Path, data_path: Path, stop: Event) -> int:
    try:
        timezone_name = configure_timezone()
        gauges = load_gauges(options_path)
    except ValueError as error:
        # Validation errors use fixed messages or field names, never values.
        log(f"Configuration error: {error}")
        return 1
    except Exception:
        log("Cannot load app configuration")
        return 1
    if timezone_name:
        log(f"Log timezone: {timezone_name} (TZ supplied to the app)")
    else:
        log("WARNING: TZ is not set; using container local time, which may be UTC")
    log(f"Paine started; gauges: {len(gauges)}; captures run sequentially; aggregation: median")
    for gauge in gauges:
        log(f"[{gauge.sensor_id}] Interval: {gauge.interval} seconds; shots: {gauge.shot_count}; stream: {STREAM}; expiry: {gauge.config['MQTT_EXPIRE_AFTER']} seconds")
    states = [{"settings": gauge, "cache": {}, "next_due": 0.0} for gauge in gauges]
    while not stop.is_set():
        state = min(states, key=lambda item: item["next_due"])
        wait = state["next_due"] - time.monotonic()
        if wait > 0:
            stop.wait(wait)
            continue
        gauge = state["settings"]
        started = time.monotonic()
        try:
            run_capture(options_path, data_path, gauge.timeout, stop, state["cache"],
                        gauge.shot_count, gauge_id=gauge.sensor_id)
        except Exception:
            # One camera/worker must not stop scheduling the remaining gauges.
            log(f"[{gauge.sensor_id}] Capture worker failed; retrying at its next interval")
        # Independent per-gauge schedules, bounded retries, and no overlapping workers.
        state["next_due"] = max(started + gauge.interval, time.monotonic() + 1)
    log("Paine stopped")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--options", type=Path, default=Path("/data/options.json"))
    parser.add_argument("--data", type=Path, default=Path("/data"))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--last-gauge", help=argparse.SUPPRESS)
    parser.add_argument("--gauge", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.once:
        try:
            configure_timezone()
        except ValueError as error:
            log(f"Configuration error: {error}")
            return 1
        cache = decode_geometry(args.last_gauge) if args.last_gauge else {}
        return capture_once(args.options, args.data, cache, gauge_id=args.gauge)
    stop = Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    return run_loop(args.options, args.data, stop)


if __name__ == "__main__":
    sys.exit(main())
