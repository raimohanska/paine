"""Find mechanical wheel banks. Geometry is in the oriented image's coordinates."""

import json
from pathlib import Path

import cv2
import numpy as np


def load_reference():
    with np.load(Path(__file__).with_name("water_register.npz")) as archive:
        return dict(
            points=archive["points"],
            descriptors=archive["descriptors"].astype(np.float32),
            plane=archive["plane"],
        )


def rectangle(w, h):
    return np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])


def warp(image, quad, width):
    matrix = cv2.getPerspectiveTransform(np.float32(quad), rectangle(width, 40))
    return cv2.warpPerspective(image, matrix, (width, 40))


def sift(gray):
    # Local contrast also brings out weak etched edges under the night light.
    gray = cv2.createCLAHE(
        3, (max(1, gray.shape[1] // 48), max(1, gray.shape[0] // 48))
    ).apply(gray)
    points, desc = cv2.SIFT_create(contrastThreshold=0.012).detectAndCompute(gray, None)
    return np.float32([p.pt for p in points]).reshape(-1, 2), desc


def plausible(q, shape):
    h, w = shape[:2]
    if (
        not np.isfinite(q).all()
        or (q < 0).any()
        or (q[:, 0] >= w).any()
        or (q[:, 1] >= h).any()
        or not cv2.isContourConvex(q)
        or cv2.contourArea(q, oriented=True) <= 0
    ):
        return False
    lengths = np.linalg.norm(q - np.roll(q, -1, axis=0), axis=1)
    return (
        min(lengths) >= 14
        and 1.8 < (lengths[0] + lengths[2]) / (lengths[1] + lengths[3]) < 10
        and max(lengths[::2]) / min(lengths[::2]) < 1.8
        and max(lengths[1::2]) / min(lengths[1::2]) < 1.8
    )


def reference_bank(gray, reference):
    # SIFT's image pyramid is large. Tile the long full-frame search on small
    # hosts; overlap gives descriptors context but each point has one owner.
    if gray.size > 700_000:
        points, descriptors = [], []
        for y in range(0, gray.shape[0], 360):
            top = max(0, y - 48)
            p, d = sift(gray[top : y + 408])
            p[:, 1] += top
            take = (p[:, 1] >= y) & (p[:, 1] < y + 360)
            if take.any():
                points.append(p[take])
                descriptors.append(d[take])
        if not points:
            return None
        pts, desc = np.vstack(points), np.vstack(descriptors)
    else:
        pts, desc = sift(gray)
    if desc is None or len(desc) < 2:
        return None
    pairs = cv2.BFMatcher().knnMatch(reference["descriptors"], desc, k=2)
    good = sorted(
        (a for a, b in pairs if a.distance < 0.80 * b.distance),
        key=lambda m: m.distance,
    )
    # Features at the same destination (e.g. repeated numerals) are one observation.
    unique = {}
    for m in good:
        unique.setdefault(tuple(pts[m.trainIdx]), m)
    good = list(unique.values())
    if len(good) < 9:
        return None
    src = reference["points"][[m.queryIdx for m in good]]
    dst = pts[[m.trainIdx for m in good]]
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 2.0)
    if H is None:
        return None
    inliers = mask.ravel().astype(bool)
    support = src[inliers]
    if (
        len(support) < 9
        or np.ptp(support, axis=0)[0] < 85
        or cv2.contourArea(cv2.convexHull(support)) < 174 * 40 * 0.11
    ):
        return None
    # With little foreshortening a full homography can overfit sub-pixel noise
    # on the inner wheels and move the outer glyphs several pixels off center.
    affine, _ = cv2.estimateAffine2D(support, dst[inliers], ransacReprojThreshold=1.5)
    if affine is not None:
        error = np.linalg.norm(
            support @ affine[:, :2].T + affine[:, 2] - dst[inliers], axis=1
        )
        if np.percentile(error, 85) < 1.25:
            H = np.vstack([affine, [0, 0, 1]])
    # For the known register, prefer its calibrated perspective when rotation,
    # translation and scale already explain the measured points to pixel accuracy.
    plane = reference["plane"]
    native = cv2.perspectiveTransform(support[None], plane)[0]
    rigid, _ = cv2.estimateAffinePartial2D(
        native, dst[inliers], ransacReprojThreshold=1.5
    )
    if rigid is not None:
        error = np.linalg.norm(
            native @ rigid[:, :2].T + rigid[:, 2] - dst[inliers], axis=1
        )
        if np.percentile(error, 85) < 1.3:
            H = np.vstack([rigid, [0, 0, 1]]) @ plane
    q = cv2.perspectiveTransform(rectangle(174, 40)[None], H)[0]
    return (q, 7, "reference") if plausible(q, gray.shape) else None


def wheel_count(strip):
    """Require recurring dividers and textured glyph interiors in a rectified bezel."""
    best = []
    for n in range(4, 10):
        if not 0.39 < strip.shape[1] / (strip.shape[0] * n) < 0.94:
            continue
        cells = cv2.resize(strip, (24 * n, 40), interpolation=cv2.INTER_AREA)
        grad = np.abs(cv2.Sobel(cells, cv2.CV_32F, 1, 0, ksize=3))[5:35]
        # A separator persists through most of the height, unlike digit strokes.
        profile = np.percentile(grad, 25, axis=0)
        peaks = [profile[x - 3 : x + 4].max() for x in range(24, 24 * n, 24)]
        floor = max(12, np.percentile(profile, 60) * 1.7)
        if min(peaks) < floor or any(
            np.std(cells[5:34, x + 4 : x + 20]) < 8 for x in range(0, 24 * n, 24)
        ):
            continue
        best.append((np.mean(peaks) / max(10, np.mean(profile)), n))
    best.sort(reverse=True)
    if not best or (len(best) > 1 and best[1][0] > 0.8 * best[0][0]):
        return None
    return best[0][1]


def other_bank(gray):
    # Propose closed bezels, then test the contents; printed lines of text
    # alone cannot supply both the enclosing quadrilateral and wheel dividers.
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 25, 75)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    hits = []
    for c in contours:
        if cv2.contourArea(c) < 700:
            continue
        poly = cv2.approxPolyDP(c, 0.022 * cv2.arcLength(c, True), True)
        if len(poly) != 4:
            continue
        q = poly[:, 0].astype(np.float32)
        if cv2.contourArea(q, oriented=True) < 0:
            q = q[::-1].copy()
        # Upright, possibly tilted register. Start at the left end of its upper long edge.
        start = np.argmax(np.roll(q, -1, axis=0)[:, 0] - q[:, 0])
        q = np.roll(q, -start, axis=0)
        if not plausible(q, gray.shape):
            continue
        lengths = np.linalg.norm(q - np.roll(q, -1, axis=0), axis=1)
        width = round(40 * (lengths[0] + lengths[2]) / (lengths[1] + lengths[3]))
        n = wheel_count(warp(gray, q, width))
        if n:
            if any(
                n != count
                or np.max(np.linalg.norm(q - other, axis=1)) > min(lengths) * 0.22
                for other, count, _ in hits
            ):
                return None  # multiple distinct plausible registers: do not guess
            hits.append((q, n, "dividers"))
    return hits[0] if hits else None


def locate(image, reference, hint=None):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    regions = []
    if hint is not None:
        q = np.float32(hint)
        if q.shape == (4, 2) and plausible(q, (h, w)):
            pad = np.ptp(q, axis=0) * 0.45 + 25
            x, y = np.maximum(0, q.min(axis=0) - pad).astype(int)
            r, b = np.minimum([w, h], q.max(axis=0) + pad).astype(int)
            regions.append((x, y, r, b, "local"))
    regions.append((0, 0, w, h, "full"))
    for x, y, r, b, search in regions:
        roi = gray[y:b, x:r]
        scale = min(1.0, 1920 / max(roi.shape), 1080 / min(roi.shape))
        if scale < 1:
            roi = cv2.resize(
                roi, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
            )
        hit = reference_bank(roi, reference) or other_bank(roi)
        if hit:
            q, n, kind = hit
            if kind == "reference":
                # The known meter's red wheels disappear under monochrome IR.
                # A convincing outline is not evidence that the digits are readable.
                sample = warp(image, q / scale + np.float32([x, y]), 174)
                if np.max(np.ptp(sample, axis=2)) == 0:
                    return None
            return q / scale + np.float32([x, y]), n, kind, search
    return None


def load_hint(path, shape, mirror):
    try:
        if path.stat().st_size < 2048:
            data = json.loads(path.read_bytes())
            if data["frame"] == list(shape[:2]) and data["mirror"] is mirror:
                q = np.asarray(data["corners"], dtype=float)
                if (
                    q.shape == (4, 2)
                    and np.all((q >= 0) & (q < max(shape[:2])))
                    and plausible(q.astype("f"), shape)
                ):
                    return q.astype("f")
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        pass
    return None
