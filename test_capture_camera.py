import cv2
import numpy as np
import pytest

from capture_camera import measure_pressure


def gauge_image(angle, size=120):
    image = np.full((120, 120, 3), 255, dtype=np.uint8)
    pivot = (58, 58)
    # A red limit indicator must not win over the black measurement needle.
    cv2.line(image, pivot, (30, 30), (0, 0, 200), 4)
    radians = np.deg2rad(angle)
    tip = (round(58 + 40 * np.sin(radians)), round(58 - 40 * np.cos(radians)))
    cv2.line(image, pivot, tip, (20, 20, 20), 3)
    cv2.circle(image, pivot, 8, (0, 0, 200), -1)
    return cv2.resize(image, (size, size), interpolation=cv2.INTER_AREA)


@pytest.mark.parametrize("angle, pressure", [(226, 0), (270, 2 - 86 / 65), (291, 1), (356, 2), (61, 3)])
def test_pressure_calibration(angle, pressure):
    detected, measured = measure_pressure(gauge_image(angle))
    assert abs((detected - angle + 180) % 360 - 180) <= 3
    assert measured == pytest.approx(pressure, abs=0.05)


def test_reduced_resolution():
    angle, pressure = measure_pressure(gauge_image(270, size=40))
    assert angle == pytest.approx(270, abs=3)
    assert pressure == pytest.approx(2 - 86 / 65, abs=0.05)


def test_no_needle():
    with pytest.raises(RuntimeError, match="confidently detect"):
        measure_pressure(np.full((120, 120, 3), 255, dtype=np.uint8))


def test_night_mode_gray_needle():
    image = np.full((120, 120, 3), 185, dtype=np.uint8)
    # IR renders the red indicator bright and the black needle gray.
    cv2.line(image, (58, 58), (30, 30), (220, 220, 220), 4)
    cv2.line(image, (58, 58), (18, 58), (150, 150, 150), 3)
    cv2.circle(image, (58, 58), 8, (200, 200, 200), -1)
    angle, pressure = measure_pressure(image)
    assert angle == pytest.approx(270, abs=3)
    assert pressure == pytest.approx(2 - 86 / 65, abs=0.05)


def test_red_indicator_without_black_needle():
    image = np.full((120, 120, 3), 255, dtype=np.uint8)
    cv2.line(image, (58, 58), (30, 30), (0, 0, 200), 4)
    with pytest.raises(RuntimeError, match="confidently detect"):
        measure_pressure(image)


def test_custom_tick_calibration():
    _, pressure = measure_pressure(gauge_image(270), two_bar_angle=0, three_bar_angle=60)
    assert pressure == pytest.approx(0.5, abs=0.05)
