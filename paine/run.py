"""Home Assistant app scheduler; options are supplied by Supervisor, not .env."""

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import signal
import socket
import ssl
import subprocess
import sys
from threading import Event
import time

from .capture_camera import capture_picture
from .mqtt_pressure import discovery_config, publish_pressure


OPTION_KEYS = {
    "camera_host": "CAMERA_HOST", "camera_user": "CAMERA_USER",
    "camera_password": "CAMERA_PASSWORD", "mqtt_host": "MQTT_HOST",
    "mqtt_port": "MQTT_PORT", "mqtt_user": "MQTT_USER",
    "mqtt_password": "MQTT_PASSWORD", "sensor_name": "MQTT_SENSOR_NAME",
    "sensor_id": "MQTT_SENSOR_ID", "state_topic": "MQTT_STATE_TOPIC",
    "discovery_prefix": "MQTT_DISCOVERY_PREFIX", "expire_after": "MQTT_EXPIRE_AFTER",
    "device_id": "MQTT_DEVICE_ID", "device_name": "MQTT_DEVICE_NAME",
    "suggested_area": "MQTT_SUGGESTED_AREA",
}


def log(message: str) -> None:
    """Write timestamped app messages in UTC, including each multiline entry."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    for line in message.splitlines():
        print(f"[{timestamp}] {line}", flush=True)


def load_options(path: Path) -> tuple[dict[str, str], int, int, float, float, float]:
    """Validate app options without executing configuration or exposing secrets."""
    options = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(options, dict):
        raise ValueError("App configuration must be an object")
    config = {key: str(options[option]) for option, key in OPTION_KEYS.items() if option in options}
    for field in ("CAMERA_HOST", "CAMERA_USER", "CAMERA_PASSWORD", "MQTT_HOST"):
        if not config.get(field):
            raise ValueError(f"Configure {field.lower()} before starting the app")
    tls = options.get("mqtt_tls", False)
    if not isinstance(tls, bool):
        raise ValueError("mqtt_tls must be true or false")
    config["MQTT_TLS"] = "true" if tls else "false"
    try:
        interval = int(options.get("capture_interval", 60))
        stream = int(options.get("stream", 1))
        timeout = float(options.get("capture_timeout", 10))
        two = float(options.get("two_bar_angle", 356))
        three = float(options.get("three_bar_angle", 61))
    except (TypeError, ValueError):
        raise ValueError("Capture timing, stream, and calibration must be numeric") from None
    if not 15 <= interval <= 86400:
        raise ValueError("capture_interval must be between 15 and 86400 seconds")
    if stream not in (1, 2):
        raise ValueError("stream must be 1 or 2")
    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError("capture_timeout must be greater than zero and at most 300")
    if not (0 <= two < 360 and 0 <= three < 360 and 0 < (three - two) % 360 < 90):
        raise ValueError("Invalid 2-bar and 3-bar tick calibration")
    _, _, discovery = discovery_config(config)
    if discovery["expire_after"] <= interval:
        raise ValueError("expire_after must be longer than capture_interval")
    return config, interval, stream, timeout, two, three


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


def capture_once(options_path: Path, data_path: Path) -> int:
    """Capture and publish once; only controlled messages go to standard output."""
    try:
        config, _, stream, timeout, two, three = load_options(options_path)
    except Exception:
        log("Configuration loading failed; check app options")
        return 1
    log(f"Capturing camera stream {stream} and measuring gauge")
    try:
        angle, pressure = capture_picture(
            config, data_path / "snapshot.jpg", stream, timeout, two, three,
        )
    except Exception as error:
        log(f"Camera capture/measurement failed: {safe_failure(error)}")
        return 1
    log(f"Captured snapshot and crop; estimated pressure: {pressure:.2f} bar; needle: {angle:.1f} degrees")
    log("Publishing discovery and retained pressure to MQTT")
    try:
        publish_pressure(config, pressure, timeout=timeout)
    except Exception as error:
        log(f"MQTT publication failed: {safe_failure(error)}")
        return 1
    log(f"Pressure: {pressure:.2f} bar; needle: {angle:.1f} degrees; published to MQTT")
    return 0


def run_capture(options_path: Path, data_path: Path, timeout: float, stop: Event) -> bool:
    """Isolate native camera logs and terminate the worker on shutdown/timeout."""
    process = subprocess.Popen(
        [sys.executable, "-m", "paine.run", "--once", "--options", str(options_path),
         "--data", str(data_path)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    # Camera open/read plus MQTT connect and two delivery acknowledgements.
    deadline = time.monotonic() + 5 * timeout + 15
    try:
        while not stop.is_set() and time.monotonic() < deadline:
            try:
                output, _ = process.communicate(timeout=0.5)
            except subprocess.TimeoutExpired:
                continue
            if output.strip():
                # Worker lines already carry their event timestamps.
                print(output.strip(), flush=True)
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
        _, interval, _, timeout, _, _ = load_options(options_path)
    except ValueError as error:
        # Validation errors use fixed messages or field names, never values.
        log(f"Configuration error: {error}")
        return 1
    except Exception:
        log("Cannot load app configuration")
        return 1
    log(f"Paine started; capture interval: {interval} seconds")
    while not stop.is_set():
        started = time.monotonic()
        run_capture(options_path, data_path, timeout, stop)
        # Never overlap captures or spin on repeated failures.
        stop.wait(max(1, interval - (time.monotonic() - started)))
    log("Paine stopped")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--options", type=Path, default=Path("/data/options.json"))
    parser.add_argument("--data", type=Path, default=Path("/data"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.once:
        return capture_once(args.options, args.data)
    stop = Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    return run_loop(args.options, args.data, stop)


if __name__ == "__main__":
    sys.exit(main())
