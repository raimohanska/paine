#!/usr/bin/env python3
"""Capture a median pressure reading using credentials in a local .env file.

Usage: python capture_camera.py [output.jpg] [--stream 1|2]
Requires opencv-python-headless (already listed in requirements.txt).
The config file accepts KEY=value lines, optionally with quoted values.
The gauge crop is saved beside the photo as <output-stem>_gauge.<extension>.
Three shots are captured by default; use --shots to change the count.
"""

import argparse
import os
import math
from statistics import median
from pathlib import Path
import sys
import time
from urllib.parse import quote

from .mqtt_pressure import publish_pressure
from .gauge_detection import cached_gauge_circle, extract_gauge, normalize_gauge, verify_gauge_crop


def load_credentials(path: Path) -> dict[str, str]:
    """Read simple assignments without executing shell code or expanding values."""
    required = {"CAMERA_HOST", "CAMERA_USER", "CAMERA_PASSWORD"}
    allowed = required | {
        "MQTT_HOST", "MQTT_PORT", "MQTT_USER", "MQTT_PASSWORD", "MQTT_TLS",
        "MQTT_SENSOR_NAME", "MQTT_SENSOR_ID", "MQTT_STATE_TOPIC",
        "MQTT_DISCOVERY_PREFIX", "MQTT_EXPIRE_AFTER",
        "MQTT_DEVICE_ID", "MQTT_DEVICE_NAME", "MQTT_SUGGESTED_AREA",
    }
    credentials = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or key not in allowed:
            continue
        value = value.strip()
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"Unmatched quotes for {key} in config file")
            value = value[1:-1]
        credentials[key] = value

    missing = sorted(key for key in required if not credentials.get(key))
    if missing:
        raise ValueError(f"Missing config values: {', '.join(missing)}")
    return credentials


def gauge_output_path(output: Path) -> Path:
    return output.with_name(f"{output.stem}_gauge{output.suffix}")


def _needle_pivot(gray, red) -> tuple[float, float]:
    """Locate the central colored hub or IR highlight in a normalized dial."""
    import cv2
    import numpy as np

    height, width = gray.shape
    yy, xx = np.mgrid[:height, :width]
    central = (xx * 120 / width - 60) ** 2 + (yy * 120 / height - 60) ** 2 < 12 ** 2
    if (red & central).sum() > 15 * width * height / 14400:
        distance = cv2.distanceTransform(red.astype(np.uint8), cv2.DIST_L2, 5)
        distance[~central] = 0
        _, peak, _, (x, y) = cv2.minMaxLoc(distance)
        if peak >= 2 * min(width, height) / 120:
            return x * 120 / width, y * 120 / height
    # The IR highlight must be close to the dial center. A wider bright-pixel
    # search intermittently selected the logo above the hub and displaced the
    # pivot by ~10 px, making the actual needle fail its confidence checks.
    ir_central = (xx * 120 / width - 60) ** 2 + (yy * 120 / height - 60) ** 2 < 8 ** 2
    threshold = max(float(np.median(gray[ir_central])) + 15, np.percentile(gray[ir_central], 95))
    mask = ((gray >= threshold) & ir_central).astype(np.uint8)
    count, _, stats, centers = cv2.connectedComponentsWithStats(mask)
    candidates = []
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] < 3 * width * height / 14400:
            continue
        x, y = centers[index] * (120 / width, 120 / height)
        candidates.append(((x - 60) ** 2 + (y - 60) ** 2, float(x), float(y)))
    if candidates:
        _, x, y = min(candidates)
        return x, y
    # Low-contrast hubs may not be resolvable; the shaft fit tolerates a small offset.
    return 58.0, 58.0


def _refine_needle_angle(gray, red, coarse_angle: float, pivot=(58.0, 58.0)) -> float:
    """Fit the shaft's direction independently of a slightly misplaced pivot.

    Measure subpixel dark-stripe centers across the inner shaft, excluding the
    hub and the faint tip/scale. A translated shaft changes the fit intercept,
    not its angle. Coordinates use the reference 120x120 crop in both streams.
    """
    import cv2
    import numpy as np

    height, width = gray.shape
    radians = np.deg2rad(coarse_angle)
    radii = np.linspace(15, 30, 31, dtype=np.float32)
    offsets = np.linspace(-5, 5, 41, dtype=np.float32)
    map_x = ((pivot[0] + np.sin(radians) * radii[:, None]
              + np.cos(radians) * offsets[None, :]) * width / 120).astype(np.float32)
    map_y = ((pivot[1] - np.cos(radians) * radii[:, None]
              + np.sin(radians) * offsets[None, :]) * height / 120).astype(np.float32)
    samples = cv2.remap(gray.astype(np.float32), map_x, map_y, cv2.INTER_LINEAR)
    # Estimate a sloping local background from either side of each cross-section.
    background = (samples[:, :3].mean(axis=1)[:, None] * (5 - offsets) / 10
                  + samples[:, -3:].mean(axis=1)[:, None] * (5 + offsets) / 10)
    weights = np.maximum(background - samples - 5, 0)
    red_samples = cv2.remap(red.astype(np.float32), map_x, map_y, cv2.INTER_LINEAR)
    weights[(red_samples > 0.25) | (np.abs(offsets)[None, :] > 4)] = 0
    strengths = weights.sum(axis=1)
    centers = (weights * offsets).sum(axis=1) / np.maximum(strengths, 1e-6)
    valid = (strengths > 20) & (weights.max(axis=1) > 8)
    if valid.sum() < 20:
        raise RuntimeError("Could not confidently detect the black gauge needle")
    slope, intercept = np.polyfit(
        radii[valid], centers[valid], 1, w=np.sqrt(strengths[valid]),
    )
    residual = centers[valid] - (slope * radii[valid] + intercept)
    rms = np.sqrt(np.average(residual ** 2, weights=strengths[valid]))
    correction = np.rad2deg(np.arctan(slope))
    if rms > 0.75 or abs(intercept) > 4 or abs(correction) > 12:
        raise RuntimeError("Could not confidently detect the black gauge needle")
    return float((coarse_angle + correction) % 360)


def measure_pressure(
    crop, two_bar_angle: float = 356.0, three_bar_angle: float = 61.0,
) -> tuple[float, float]:
    """Return clockwise needle degrees from 12 o'clock and estimated bar.

    Locate the hub in the normalized crop, then fit the shaft direction
    independently of small day/IR translations. The 2- and
    3-bar outer ticks are estimated at 356 and 61 degrees respectively.
    Extrapolate their spacing; do not use the ambiguous zero mark or red pointer.
    """
    import cv2
    import numpy as np

    height, width = crop.shape[:2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    red = ((hsv[:, :, 0] < 15) | (hsv[:, :, 0] > 165)) & (hsv[:, :, 1] > 60)
    pivot = _needle_pivot(gray, red)
    angles = np.arange(360, dtype=np.float32)
    radians = np.deg2rad(angles)[:, None]
    # Use the same inner shaft as the line fit: IR often washes out the tip.
    # Counting the faint outer tip as missing needle caused intermittent rejection.
    radii = np.linspace(0.30, 0.60, 40, dtype=np.float32)[None, :]
    map_x = (pivot[0] / 120 * width + np.sin(radians) * radii * 50 / 120 * width).astype(np.float32)
    map_y = (pivot[1] / 120 * height - np.cos(radians) * radii * 50 / 120 * height).astype(np.float32)
    samples = cv2.remap(gray.astype(np.float32), map_x, map_y, cv2.INTER_LINEAR)
    # Compare each ray with neighboring rays rather than an absolute black
    # threshold: infrared night mode renders the needle as low-contrast gray.
    background = (np.roll(samples, 8, axis=0) + np.roll(samples, -8, axis=0)) / 2
    contrast = background - samples
    red_samples = cv2.remap(red.astype(np.float32), map_x, map_y, cv2.INTER_LINEAR)
    contrast[red_samples > 0.25] = 0
    scores = np.maximum(contrast - 5, 0).mean(axis=1)
    scores = sum(np.roll(scores, offset) for offset in range(-2, 3)) / 5
    degrees_per_bar = (three_bar_angle - two_bar_angle) % 360
    if not 0 < degrees_per_bar < 90:
        raise ValueError("2-to-3-bar clockwise spacing must be between 0 and 90 degrees")
    offset = (angles - two_bar_angle + 180) % 360 - 180
    pressures = 2 + offset / degrees_per_bar
    # The visible dial runs from 0 to 4 bar.
    valid = (pressures >= 0) & (pressures <= 4)
    scores[~valid] = -1
    index = int(np.argmax(scores))
    if scores[index] < 10 or (contrast[index] > 5).mean() < 0.5:
        raise RuntimeError("Could not confidently detect the black gauge needle")
    angle = _refine_needle_angle(gray, red, float(angles[index]), pivot)
    offset = (angle - two_bar_angle + 180) % 360 - 180
    pressure = 2 + offset / degrees_per_bar
    if not -0.05 <= pressure <= 4.05:
        raise RuntimeError("Could not confidently detect the black gauge needle")
    # Small endpoint excursions are expected from pixel/tick calibration error.
    return angle, float(np.clip(pressure, 0, 4))


def save_gauge_crop(
    frame, output: Path, two_bar_angle: float = 356.0,
    three_bar_angle: float = 61.0, diagnostics=None, gauge_cache=None,
) -> tuple[float, float]:
    """Verify cached geometry first; search only if needed, then try legacy crop."""
    import cv2

    report = diagnostics or (lambda message: print(message, flush=True))
    height, width = frame.shape[:2]
    reading = None
    circle = cached_gauge_circle(gauge_cache, width, height)
    if circle is not None:
        crop = normalize_gauge(frame, circle)
        verified, rim_score = verify_gauge_crop(crop)
        if verified:
            try:
                reading = measure_pressure(crop, two_bar_angle, three_bar_angle)
            except RuntimeError:
                report("Cached crop needle verification failed; running full-frame gauge search")
        else:
            report(f"Cached crop rim verification failed (contrast={rim_score:.1f}); running full-frame gauge search")
        if reading is not None:
            x, y, radius = circle
            report(f"Gauge crop: verified last-known-good cache; full search skipped; center=({x:.1f}, {y:.1f}); diameter={2 * radius:.1f}px; rim contrast={rim_score:.1f}; frame={width}x{height}")
    try:
        if reading is None:
            crop, reading, (x, y, radius) = extract_gauge(
                frame, lambda image: measure_pressure(image, two_bar_angle, three_bar_angle),
                diagnostics=report,
            )
            report(f"Gauge crop: automatic; center=({x:.1f}, {y:.1f}); diameter={2 * radius:.1f}px; frame={width}x{height}")
            if gauge_cache is not None:
                gauge_cache.update(circle=[x, y, radius], frame=[width, height])
    except RuntimeError as error:
        reason = "ambiguous matches" if str(error) == "Multiple possible pressure gauges found" else "no confident match"
        report(f"WARNING: Automatic gauge detection failed ({reason}); trying original fixed crop")
        if reading is None:
            report("WARNING: Using legacy fixed-position crop; camera must remain in its original view")
            left, right = round(950 * width / 1920), round(1070 * width / 1920)
            top, bottom = round(725 * height / 1080), round(845 * height / 1080)
            crop = frame[top:bottom, left:right]
            report(f"Gauge crop: fixed fallback; rectangle=({left}, {top})-({right}, {bottom}); frame={width}x{height}")
            if not crop.size:
                raise RuntimeError("Gauge crop is empty") from None
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), crop):
        raise RuntimeError("Could not save the gauge crop")
    report(f"Gauge diagnostic crop saved; size={crop.shape[1]}x{crop.shape[0]}")
    if reading is None:
        known_circle = cached_gauge_circle(gauge_cache, width, height)
        if known_circle is not None:
            x, y, radius = known_circle
            if math.hypot(x - (left + right) / 2, y - (top + bottom) / 2) > radius / 2:
                report("Refusing legacy crop: last-known-good gauge is at a different location")
                raise RuntimeError("Legacy crop does not match the last-known-good gauge location")
        normalized = cv2.resize(crop, (120, 120))
        verified, rim_score = verify_gauge_crop(normalized)
        if not verified:
            report(f"Refusing legacy crop: dial rim verification failed (contrast={rim_score:.1f})")
            raise RuntimeError("Legacy crop failed gauge rim verification")
        reading = measure_pressure(crop, two_bar_angle, three_bar_angle)
        report("Fixed-position fallback rim and needle verification succeeded")
        # A dark line alone is never sufficient to accept or cache a legacy crop.
        if gauge_cache is not None and abs(crop.shape[0] / crop.shape[1] - 1) < 0.02:
            verified, _ = verify_gauge_crop(cv2.resize(crop, (120, 120)))
            if verified:
                gauge_cache.update(
                    circle=[(left + right) / 2, (top + bottom) / 2, (right - left) * 50 / 120],
                    frame=[width, height],
                )
                report("Fixed crop rim also verified; saving geometry for fast cached verification")
    return reading


def capture_picture(
    credentials: dict[str, str], output: Path, stream: int, timeout: float,
    two_bar_angle: float = 356.0, three_bar_angle: float = 61.0, diagnostics=None,
    gauge_cache=None,
) -> tuple[float, float]:
    # Set this before importing OpenCV; TCP works through Docker's NAT as well.
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
    import cv2

    host = credentials["CAMERA_HOST"].strip()
    if any(character in host for character in "/@?#") or any(
        character.isspace() for character in host
    ):
        raise ValueError("CAMERA_HOST must be a hostname or IP, optionally with a port")
    if ":" not in host:
        host = f"{host}:554"
    user = quote(credentials["CAMERA_USER"], safe="")
    password = quote(credentials["CAMERA_PASSWORD"], safe="")
    url = f"rtsp://{user}:{password}@{host}/stream{stream}"

    camera = cv2.VideoCapture()
    timeout_ms = max(1, int(timeout * 1000))
    try:
        opened = camera.open(
            url,
            cv2.CAP_FFMPEG,
            [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
                timeout_ms,
                cv2.CAP_PROP_READ_TIMEOUT_MSEC,
                timeout_ms,
            ],
        )
        if not opened:
            raise RuntimeError(
                "Could not open the camera stream. Check network access and the "
                "camera account configured in the Tapo app."
            )

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            success, frame = camera.read()
            if success and frame is not None and frame.size:
                output.parent.mkdir(parents=True, exist_ok=True)
                if not cv2.imwrite(str(output), frame):
                    raise RuntimeError("Could not save the image")
                return save_gauge_crop(
                    frame, gauge_output_path(output), two_bar_angle, three_bar_angle,
                    diagnostics=diagnostics,
                    gauge_cache=gauge_cache,
                )
        raise RuntimeError("Timed out waiting for a camera frame")
    finally:
        camera.release()


def capture_pressure_batch(
    credentials: dict[str, str], output: Path, stream: int, timeout: float,
    two_bar_angle: float = 356.0, three_bar_angle: float = 61.0,
    diagnostics=None, gauge_cache=None, shot_count: int = 3,
) -> tuple[float, float]:
    """Take independent captures and return their median pressure."""
    if not isinstance(shot_count, int) or isinstance(shot_count, bool) or not 1 <= shot_count <= 9:
        raise ValueError("shot_count must be an integer between 1 and 9")
    report = diagnostics or (lambda message: print(message, flush=True))
    # Tentative geometry is shared between shots, but only a consistent completed
    # batch may replace the scheduler's last-known-good geometry.
    cache = dict(gauge_cache) if gauge_cache is not None else {}
    pressures = []
    for shot in range(1, shot_count + 1):
        report(f"Capturing shot {shot}/{shot_count}")
        shot_output = output if shot == shot_count else output.with_name(f"{output.stem}_shot{shot}{output.suffix}")
        angle, pressure = capture_picture(
            credentials, shot_output, stream, timeout, two_bar_angle, three_bar_angle,
            diagnostics=report, gauge_cache=cache,
        )
        if not math.isfinite(pressure) or not 0 <= pressure <= 4:
            raise RuntimeError("Could not confidently detect the black gauge needle")
        pressures.append(pressure)
        report(f"Shot {shot}/{shot_count}: {pressure:.3f} bar; needle={angle:.2f} degrees")
        if shot < shot_count:
            time.sleep(1)
    spread = max(pressures) - min(pressures)
    report(f"{shot_count}-shot readings: {', '.join(f'{value:.3f}' for value in pressures)} bar; spread={spread:.3f} bar")
    # A median cannot protect against two unrelated bad readings out of three.
    # Require a strict majority to lie in some 0.15-bar-wide cluster before
    # publishing; do not compare against a historical pressure or hide real changes.
    support = max(sum(low <= value <= low + 0.15 + 1e-9 for value in pressures) for low in pressures)
    if support <= shot_count // 2:
        report(f"Rejecting batch: largest consistent group is {support}/{shot_count}; no reading published")
        raise RuntimeError("Pressure readings have no consistent majority")
    if spread > 0.15:
        report("WARNING: Shot spread exceeds 0.15 bar; using median and keeping previous last-known-good geometry")
    pressure = float(median(pressures))
    # Convert median pressure back to an angle without a 0/360 wraparound bug.
    angle = (two_bar_angle + (pressure - 2) * ((three_bar_angle - two_bar_angle) % 360)) % 360
    report(f"Median pressure from {shot_count} shots: {pressure:.3f} bar")
    if gauge_cache is not None and spread <= 0.15:
        gauge_cache.clear()
        gauge_cache.update(cache)
    return angle, pressure


def main(config_path: Path = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", type=Path, default=Path("snapshot.jpg"))
    parser.add_argument(
        "--config", type=Path,
        default=config_path or Path(__file__).resolve().parent.parent / ".env",
    )
    parser.add_argument(
        "--stream", type=int, choices=(1, 2), default=1,
        help="1: main/high-resolution stream (default); 2: lower-resolution stream",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="Timeout in seconds")
    parser.add_argument("--shots", type=int, choices=range(1, 10), default=3,
                        help="Number of captures per reading; publishes the median (default: 3)")
    parser.add_argument(
        "--two-bar-angle", type=float, default=356.0,
        help="2-bar tick angle clockwise from 12 o'clock (default: 356)",
    )
    parser.add_argument(
        "--three-bar-angle", type=float, default=61.0,
        help="3-bar tick angle clockwise from 12 o'clock (default: 61)",
    )
    args = parser.parse_args()
    if not 0 < args.timeout <= 300:
        parser.error("--timeout must be greater than zero and at most 300 seconds")
    if args.output.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        parser.error("output must end in .jpg, .jpeg, or .png")
    if not (0 <= args.two_bar_angle < 360 and 0 <= args.three_bar_angle < 360):
        parser.error("tick angles must be between 0 (inclusive) and 360 (exclusive)")
    if not 0 < (args.three_bar_angle - args.two_bar_angle) % 360 < 90:
        parser.error("2-to-3-bar clockwise spacing must be between 0 and 90 degrees")

    try:
        config = load_credentials(args.config)
        angle, pressure = capture_pressure_batch(
            config, args.output, args.stream, args.timeout,
            args.two_bar_angle, args.three_bar_angle,
            shot_count=args.shots,
        )
    except ImportError:
        print("Install dependencies with: python -m pip install -r requirements.txt", file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except Exception:
        # Backend errors can contain the authenticated URL; don't print them.
        print("Camera capture failed in OpenCV", file=sys.stderr)
        return 1

    print(f"Saved {args.output}")
    print(f"Saved {gauge_output_path(args.output)}")
    print(f"Needle angle: {angle:.1f} degrees clockwise from 12 o'clock")
    print(f"Estimated median pressure ({args.shots} shots): {pressure:.2f} bar")
    if config.get("MQTT_HOST"):
        try:
            publish_pressure(config, pressure, timeout=args.timeout)
        except ImportError:
            print("Install dependencies with: python -m pip install -r requirements.txt", file=sys.stderr)
            return 1
        except ValueError as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1
        except Exception:
            # Never expose connection details or credentials from library errors.
            print("MQTT publishing failed; check broker access, credentials, and TLS settings", file=sys.stderr)
            return 1
        print("Published pressure and Home Assistant discovery to MQTT")
    else:
        print("MQTT publishing disabled: configure MQTT_HOST to enable it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
