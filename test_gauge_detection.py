import cv2
import numpy as np
import pytest

from paine.capture_camera import measure_pressure, save_gauge_crop
from paine.gauge_detection import extract_gauge
from test_capture_camera import gauge_image
from paine import capture_camera as camera
from paine.gauge_detection import cached_gauge_circle
from unittest.mock import Mock


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


def test_last_good_geometry_fallback(tmp_path, monkeypatch):
    frame = np.full((480, 640, 3), 70, dtype=np.uint8)
    add_gauge(frame, (450, 300), 40)
    cache = {}
    messages = []
    first = save_gauge_crop(frame, tmp_path / "first.png", gauge_cache=cache, diagnostics=messages.append)
    assert "circle" in cache
    original = dict(cache)
    search = Mock(side_effect=AssertionError("A valid cached crop must skip full search"))
    monkeypatch.setattr(camera, "extract_gauge", search)
    second = save_gauge_crop(frame, tmp_path / "second.png", gauge_cache=cache, diagnostics=messages.append)
    assert second == pytest.approx(first)
    assert cache == original
    assert any("verified last-known-good cache" in message for message in messages)
    assert not any("Gauge crop: fixed fallback" in message for message in messages)
    search.assert_not_called()


def test_moved_gauge_invalidates_cache_and_triggers_search(tmp_path, monkeypatch):
    frame = np.full((480, 640, 3), 70, dtype=np.uint8)
    add_gauge(frame, (450, 300), 40)
    cache = {}
    save_gauge_crop(frame, tmp_path / "first.png", gauge_cache=cache)
    moved = np.full((480, 640, 3), 70, dtype=np.uint8)
    add_gauge(moved, (200, 140), 40)
    search = Mock(wraps=camera.extract_gauge)
    monkeypatch.setattr(camera, "extract_gauge", search)
    save_gauge_crop(moved, tmp_path / "moved.png", gauge_cache=cache)
    search.assert_called_once()
    assert cache["circle"][0] == pytest.approx(200, abs=3)
    assert cache["circle"][1] == pytest.approx(140, abs=3)


def test_legacy_fixed_fallback_without_cache(tmp_path, monkeypatch):
    frame = np.full((1080, 1920, 3), 70, dtype=np.uint8)
    add_gauge(frame, (1010, 785), 50)
    monkeypatch.setattr(camera, "extract_gauge", Mock(side_effect=RuntimeError("not found")))
    messages = []
    _, pressure = save_gauge_crop(frame, tmp_path / "gauge.png", diagnostics=messages.append)
    assert pressure == pytest.approx(2 - 86 / 65, abs=0.05)
    assert any("fixed fallback" in message for message in messages)


def test_stale_legacy_crop_cannot_read_other_objects(tmp_path, monkeypatch):
    frame = np.full((1080, 1920, 3), 185, dtype=np.uint8)
    cache = {"circle": [991.8, 918.1, 52.55], "frame": [1920, 1080]}
    monkeypatch.setattr(camera, "extract_gauge", Mock(side_effect=RuntimeError("not found")))
    measurement = Mock(return_value=(76.32, 3.236))
    monkeypatch.setattr(camera, "measure_pressure", measurement)
    path = tmp_path / "stale.png"
    with pytest.raises(RuntimeError, match="last-known-good gauge location"):
        save_gauge_crop(frame, path, gauge_cache=cache)
    measurement.assert_not_called()
    assert path.exists()


def test_legacy_crop_requires_rim_even_without_cached_geometry(tmp_path, monkeypatch):
    frame = np.full((1080, 1920, 3), 185, dtype=np.uint8)
    cv2.line(frame, (990, 790), (1040, 760), (20, 20, 20), 3)
    monkeypatch.setattr(camera, "extract_gauge", Mock(side_effect=RuntimeError("not found")))
    measurement = Mock(return_value=(103.06, 3.647))
    monkeypatch.setattr(camera, "measure_pressure", measurement)
    with pytest.raises(RuntimeError, match="rim verification"):
        save_gauge_crop(frame, tmp_path / "not-a-gauge.png")
    measurement.assert_not_called()


def test_failed_fallback_still_saves_diagnostic_crop(tmp_path, monkeypatch):
    frame = np.full((1080, 1920, 3), 185, dtype=np.uint8)
    monkeypatch.setattr(camera, "extract_gauge", Mock(side_effect=RuntimeError("not found")))
    path = tmp_path / "failed-gauge.png"
    with pytest.raises(RuntimeError):
        save_gauge_crop(frame, path)
    assert path.exists()


def test_cached_geometry_scales_but_rejects_changed_aspect():
    cache = {"circle": [1000, 780, 50], "frame": [1920, 1080]}
    assert cached_gauge_circle(cache, 640, 360) == pytest.approx((1000 / 3, 260, 50 / 3))
    assert cached_gauge_circle(cache, 640, 480) is None


@pytest.mark.parametrize("cache", [None, {}, {"circle": [1, 2, float("nan")], "frame": [640, 480]}])
def test_invalid_cached_geometry_is_not_used(cache):
    assert cached_gauge_circle(cache, 640, 480) is None
