#!/usr/bin/env python3
"""Save one frame from a Tapo C100 using credentials in a local .env file.

Usage: python capture_camera.py [output.jpg] [--stream 1|2]
Requires opencv-python-headless (already listed in requirements.txt).
The config file accepts KEY=value lines, optionally with quoted values.
"""

import argparse
import os
from pathlib import Path
import sys
import time
from urllib.parse import quote


def load_credentials(path: Path) -> dict[str, str]:
    """Read simple assignments without executing shell code or expanding values."""
    required = {"CAMERA_HOST", "CAMERA_USER", "CAMERA_PASSWORD"}
    credentials = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or key not in required:
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


def capture_picture(
    credentials: dict[str, str], output: Path, stream: int, timeout: float
) -> None:
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
                return
        raise RuntimeError("Timed out waiting for a camera frame")
    finally:
        camera.release()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", type=Path, default=Path("snapshot.jpg"))
    parser.add_argument(
        "--config", type=Path, default=Path(__file__).resolve().with_name(".env")
    )
    parser.add_argument(
        "--stream", type=int, choices=(1, 2), default=1,
        help="1: main/high-resolution stream (default); 2: lower-resolution stream",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="Timeout in seconds")
    args = parser.parse_args()
    if not 0 < args.timeout <= 300:
        parser.error("--timeout must be greater than zero and at most 300 seconds")
    if args.output.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        parser.error("output must end in .jpg, .jpeg, or .png")

    try:
        capture_picture(load_credentials(args.config), args.output, args.stream, args.timeout)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
