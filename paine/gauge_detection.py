"""Find this installation's near-front-on, light-faced circular gauge."""


def normalize_gauge(frame, circle):
    import cv2
    import numpy as np

    x, y, radius = circle
    scale = 50 / radius
    matrix = np.float32([[scale, 0, 60 - x * scale], [0, scale, 60 - y * scale]])
    return cv2.warpAffine(frame, matrix, (120, 120), flags=cv2.INTER_LINEAR)


def cached_gauge_circle(cache, width, height):
    """Validate and scale cached geometry, rejecting changed aspect ratios."""
    import math

    try:
        x, y, radius = map(float, cache["circle"])
        old_width, old_height = map(float, cache["frame"])
        if not all(math.isfinite(value) for value in (x, y, radius, old_width, old_height)):
            return None
        if min(old_width, old_height, width, height) <= 0:
            return None
        scale = width / old_width
        if abs(scale / (height / old_height) - 1) > 0.01:
            return None
        x, y, radius = x * scale, y * scale, radius * scale
        if radius < 3 or min(x, y, width - 1 - x, height - 1 - y) < 1.1 * radius:
            return None
        return x, y, radius
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def verify_gauge_crop(crop):
    """Cheap 120x120 dial/rim check; no contours or center/diameter search."""
    import cv2
    import numpy as np

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    yy, xx = np.mgrid[:120, :120]
    face_brightness = float(np.median(gray[(xx - 60) ** 2 + (yy - 60) ** 2 < 35 ** 2]))
    angles = np.deg2rad(np.arange(0, 360, 3, dtype=np.float32))[None, :]

    def sample(radius):
        return cv2.remap(gray, (60 + np.sin(angles) * radius).astype(np.float32),
                         (60 - np.cos(angles) * radius).astype(np.float32),
                         cv2.INTER_LINEAR).astype(np.float32)

    contrast = sample(46) - sample(54)
    score = float(np.median(contrast))
    return face_brightness >= 120 and score >= 20 and (contrast > 10).mean() >= 0.6, score


def _refine_circle(gray, x, y, radius):
    """Find the bright-to-dark dial boundary with broad angular support."""
    import cv2
    import numpy as np

    # Search a small neighborhood of the contour estimate. Scale the grid with
    # dial size, so reduced camera streams use the same reference geometry.
    unit = radius / 50
    offsets = np.arange(-6, 6.1, 1, dtype=np.float32) * unit
    radii = np.arange(0.88 * radius, 1.12 * radius, 0.5 * unit, dtype=np.float32)
    xx, yy, rr = np.meshgrid(x + offsets, y + offsets, radii, indexing="ij")
    xx, yy, rr = (values.ravel()[:, None] for values in (xx, yy, rr))
    angles = np.deg2rad(np.arange(0, 360, 3, dtype=np.float32))[None, :]

    def sample(delta):
        return cv2.remap(
            gray,
            (xx + np.sin(angles) * (rr + delta)).astype(np.float32),
            (yy - np.cos(angles) * (rr + delta)).astype(np.float32),
            cv2.INTER_LINEAR,
        ).astype(np.float32)

    contrast = sample(-2 * unit) - sample(2 * unit)
    scores = np.median(contrast, axis=1)
    index = int(np.argmax(scores))
    return (float(xx[index, 0]), float(yy[index, 0]), float(rr[index, 0])), float(scores[index])


def extract_gauge(frame, measure, diagnostics=None):
    """Return normalized crop, measurement, and (center x/y, radius).

    Search the whole image, keeping near-circular bright dial candidates with a
    coherent rim and detectable needle. Ambiguous separate gauges are rejected.
    This is intentionally not a detector for arbitrary gauges or large tilts.
    """
    import cv2
    import numpy as np

    height, width = frame.shape[:2]
    if min(height, width) < 100:
        raise RuntimeError("Could not confidently locate the pressure gauge")
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 1)
    scale = width / 1920
    min_radius, max_radius = max(10, 30 * scale), 160 * scale
    kernel_size = max(1, round(3 * scale)) | 1
    kernel = np.ones((kernel_size, kernel_size), np.uint8)
    candidates = []
    shape_matches = 0
    rim_matches = 0
    for threshold in (100, 120, 140, 160, 180, 200, 220):
        mask = (blurred > threshold).astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area = cv2.contourArea(contour)
            if not np.pi * min_radius ** 2 < area < np.pi * max_radius ** 2:
                continue
            hull = cv2.convexHull(contour)
            hull_area = cv2.contourArea(hull)
            perimeter = cv2.arcLength(hull, True)
            if (len(hull) < 5 or area / hull_area < 0.8
                    or 4 * np.pi * hull_area / perimeter ** 2 < 0.8):
                continue
            (x, y), (minor, major), _ = cv2.fitEllipse(hull)
            if min(minor, major) / max(minor, major) < 0.8:
                continue
            shape_matches += 1
            radius = np.sqrt(minor * major) / 2
            circle, score = _refine_circle(gray, x, y, radius)
            cx, cy, radius = circle
            if score < 20 or min(cx, cy, width - 1 - cx, height - 1 - cy) < 1.15 * radius:
                continue
            rim_matches += 1
            crop = normalize_gauge(frame, circle)
            crop_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            yy, xx = np.mgrid[:120, :120]
            if np.median(crop_gray[(xx - 60) ** 2 + (yy - 60) ** 2 < 35 ** 2]) < 120:
                continue
            candidates.append((score, circle, crop))

    accepted = []
    for score, circle, crop in sorted(candidates, key=lambda item: item[0], reverse=True):
        # Threshold sweeps often detect the same bezel several times.
        if any(np.hypot(circle[0] - other[1][0], circle[1] - other[1][1])
               < min(circle[2], other[1][2]) for other in accepted):
            continue
        try:
            reading = measure(crop)
        except RuntimeError:
            continue
        accepted.append((score, circle, crop, reading))
    if diagnostics:
        diagnostics(f"Gauge search: frame={width}x{height}; circular candidates={shape_matches}; rim matches={rim_matches}; light faces={len(candidates)}; validated gauges={len(accepted)}")
    if not accepted:
        raise RuntimeError("Could not confidently locate the pressure gauge")
    if len(accepted) > 1 and accepted[1][0] >= 0.75 * accepted[0][0]:
        raise RuntimeError("Multiple possible pressure gauges found")
    _, circle, crop, reading = accepted[0]
    return crop, reading, circle
