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


@pytest.mark.parametrize("angle", [240, 255, 270, 285, 350, 30, 61])
@pytest.mark.parametrize("shift", [(-1, 2), (2, -1)])
def test_day_night_alignment_with_shifted_low_contrast_needle(angle, shift):
    color = gauge_image(angle)
    red = (color[:, :, 2] > 100) & (color[:, :, 0] < 100)
    black = color.max(axis=2) < 100
    infrared = np.full((120, 120), 185, dtype=np.uint8)
    infrared[red] = 220
    infrared[black] = 150
    infrared = cv2.warpAffine(
        infrared, np.float32([[1, 0, shift[0]], [0, 1, shift[1]]]),
        (120, 120), borderValue=185,
    )
    # Small camera-mode translation must not be interpreted as needle rotation.
    day_angle, day_pressure = measure_pressure(color)
    night_angle, night_pressure = measure_pressure(cv2.cvtColor(infrared, cv2.COLOR_GRAY2BGR))
    assert abs((night_angle - day_angle + 180) % 360 - 180) < 1.5
    assert night_pressure == pytest.approx(day_pressure, abs=0.025)


def test_refined_angle_is_not_rounded_to_whole_degrees():
    angle, _ = measure_pressure(gauge_image(291))
    assert abs(angle - round(angle)) > 0.1
