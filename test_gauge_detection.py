import cv2
import numpy as np
import pytest

from paine.capture_camera import measure_pressure, save_gauge_crop
from paine.gauge_detection import extract_gauge
from test_capture_camera import gauge_image


def add_gauge(frame, center, radius, night=False):
    source = gauge_image(270)
    if night:
        red = (source[:, :, 2] > 100) & (source[:, :, 0] < 100)
        black = source.max(axis=2) < 100
        source[:] = 185
        source[red] = 220
        source[black] = 150
    scale = radius / 50
    matrix = np.float32([[scale, 0, center[0] - 60 * scale],
                         [0, scale, center[1] - 60 * scale]])
    dial = cv2.warpAffine(source, matrix, (frame.shape[1], frame.shape[0]))
    mask = np.zeros(frame.shape[:2], np.uint8)
    cv2.circle(mask, center, radius, 255, -1)
    frame[mask != 0] = dial[mask != 0]


@pytest.mark.parametrize("center,radius", [((100, 100), 30), ((490, 350), 42), ((320, 210), 36)])
@pytest.mark.parametrize("night", [False, True])
def test_gauge_location_and_size_are_not_fixed(center, radius, night):
    frame = np.full((480, 640, 3), 70, dtype=np.uint8)
    add_gauge(frame, center, radius, night)
    crop, (angle, pressure), circle = extract_gauge(frame, measure_pressure)
    assert crop.shape == (120, 120, 3)
    assert np.hypot(circle[0] - center[0], circle[1] - center[1]) < 3
    assert circle[2] == pytest.approx(radius, abs=3)
    assert angle == pytest.approx(270, abs=2)
    assert pressure == pytest.approx(2 - 86 / 65, abs=0.04)


def test_missing_gauge_is_rejected():
    with pytest.raises(RuntimeError, match="locate"):
        extract_gauge(np.full((480, 640, 3), 70, dtype=np.uint8), measure_pressure)


def test_round_object_without_needle_is_rejected():
    frame = np.full((480, 640, 3), 70, dtype=np.uint8)
    cv2.circle(frame, (300, 200), 40, (220, 220, 220), -1)
    with pytest.raises(RuntimeError, match="locate"):
        extract_gauge(frame, measure_pressure)


def test_ambiguous_gauges_are_rejected():
    frame = np.full((480, 640, 3), 70, dtype=np.uint8)
    add_gauge(frame, (150, 200), 40)
    add_gauge(frame, (480, 200), 40)
    with pytest.raises(RuntimeError, match="Multiple"):
        extract_gauge(frame, measure_pressure)


def test_strongly_oblique_dial_is_rejected():
    frame = np.full((480, 640, 3), 70, dtype=np.uint8)
    add_gauge(frame, (300, 220), 45)
    tilted = cv2.warpAffine(
        frame, np.float32([[0.6, 0, 120], [0, 1, 0]]), (640, 480),
        borderValue=(70, 70, 70),
    )
    with pytest.raises(RuntimeError, match="locate"):
        extract_gauge(tilted, measure_pressure)


def test_saves_normalized_crop(tmp_path):
    frame = np.full((480, 640, 3), 70, dtype=np.uint8)
    add_gauge(frame, (450, 300), 40)
    path = tmp_path / "gauge.png"
    _, pressure = save_gauge_crop(frame, path)
    assert cv2.imread(str(path)).shape[:2] == (120, 120)
    assert 0 < pressure < 1
