#!/usr/bin/env python3
"""Command-line entry point for capture, gauge measurement, and MQTT publishing."""

from pathlib import Path
import sys

from paine.capture_camera import (
    capture_picture,
    capture_pressure_batch,
    gauge_output_path,
    load_credentials,
    main,
    measure_pressure,
    save_gauge_crop,
)

__all__ = [
    "capture_picture", "capture_pressure_batch", "gauge_output_path", "load_credentials", "main",
    "measure_pressure", "save_gauge_crop",
]

if __name__ == "__main__":
    sys.exit(main(config_path=Path(__file__).resolve().with_name(".env")))
