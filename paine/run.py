"""Home Assistant app scheduler; options are supplied by Supervisor, not .env."""

import argparse
import json
import math
from pathlib import Path
import signal
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


def capture_once(options_path: Path, data_path: Path) -> int:
    """Capture and publish once; only controlled messages go to standard output."""
    try:
        config, _, stream, timeout, two, three = load_options(options_path)
        angle, pressure = capture_picture(
            config, data_path / "snapshot.jpg", stream, timeout, two, three,
        )
        publish_pressure(config, pressure, timeout=timeout)
    except Exception:
        # OpenCV/MQTT exception messages can contain credentials.
        print("Capture or MQTT publication failed; check camera, broker, and calibration settings", flush=True)
        return 1
    print(f"Pressure: {pressure:.2f} bar; needle: {angle:.1f} degrees; published to MQTT", flush=True)
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
                print(output.strip(), flush=True)
            elif process.returncode != 0:
                print("Capture worker failed; will retry on the next interval", flush=True)
            return process.returncode == 0
        if not stop.is_set():
            print("Capture cycle timed out; will retry on the next interval", flush=True)
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
        print(f"Configuration error: {error}", flush=True)
        return 1
    except Exception:
        print("Cannot load app configuration", flush=True)
        return 1
    print(f"Paine started; capture interval: {interval} seconds", flush=True)
    while not stop.is_set():
        started = time.monotonic()
        run_capture(options_path, data_path, timeout, stop)
        # Never overlap captures or spin on repeated failures.
        stop.wait(max(1, interval - (time.monotonic() - started)))
    print("Paine stopped", flush=True)
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
