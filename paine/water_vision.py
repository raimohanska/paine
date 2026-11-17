"""Capture aligned water-register PNGs. Run from the repository with Python."""

import argparse
from collections import Counter
from datetime import datetime
import math
from pathlib import Path
import time

import cv2
import numpy as np

from paine.capture_camera import load_prefixed_credentials, open_camera_stream
from paine.water_model import prepare_strip


ROOT = Path(__file__).resolve().parent.parent
WARMUP_SECONDS = 20
REFERENCE = ROOT / 'snapshots/water-meter-night-light-20261117-182653/shot-1.jpg'
# In the mirrored 1080p calibration frame: three printed labels, then the
# four corners of the first six wheel windows (exclude the litre wheel).
LABELS = [(800, 335, 105, 48), (762, 439, 125, 47), (805, 482, 97, 39)]
WINDOW = np.float32([[784, 386], [934, 398], [932, 436], [782, 426]])
DIGIT_EDGES = tuple(range(0, 121, 20))


def extract_register(frame, reference_gray):
    img = cv2.flip(frame, 1)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if gray.shape != reference_gray.shape or gray.shape != (1080, 1920):
        raise ValueError('Calibration requires the 1920x1080 main stream')
    centers, matches = [], []
    for x, y, w, h in LABELS:
        template = reference_gray[y:y+h, x:x+w]
        radius = 150
        search = gray[y-radius:y+h+radius, x-radius:x+w+radius]
        scores = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, (dx, dy) = cv2.minMaxLoc(scores)
        if score < 0.65:
            raise RuntimeError(f'Meter label not found reliably (score {score:.2f})')
        center = np.array([x+w/2, y+h/2])
        centers.append(center)
        matches.append(center + [dx-radius, dy-radius])
    # Fit translation, rotation and uniform scale. Require all labels to agree.
    src, dst = np.float32(centers), np.float32(matches)
    m, _ = cv2.estimateAffinePartial2D(src, dst)
    if m is None or np.max(np.linalg.norm(src @ m[:, :2].T + m[:, 2] - dst, axis=1)) > 3:
        raise RuntimeError('Meter alignment labels disagree')
    corners = np.float32(WINDOW @ m[:, :2].T + m[:, 2])
    rect = np.float32([[0, 0], [149, 0], [149, 39], [0, 39]])
    return cv2.warpPerspective(img, cv2.getPerspectiveTransform(corners, rect), (150, 40))


def select_digits(crops, positions=None):
    """Prefer clear strokes, using the latest frame as a wheel-position reference."""
    winners = []
    for wheel, (left, right) in enumerate(zip(DIGIT_EDGES, DIGIT_EDGES[1:])):
        # Ignore the dark window borders when scoring. Smooth before measuring
        # gradients so isolated compression speckles don't win on sharpness.
        tiles = [cv2.GaussianBlur(cv2.cvtColor(c[4:-4, left+2:right-2], cv2.COLOR_BGR2GRAY),
                                  (3, 3), 0).astype(np.float32) for c in crops]
        anchor = tiles[-1] - tiles[-1].mean()
        candidates = []
        for i, tile in enumerate(tiles):
            centered = tile - tile.mean()
            norm = np.linalg.norm(anchor) * np.linalg.norm(centered)
            if i != len(tiles)-1 and (not norm or (anchor * centered).sum() / norm < 0.85):
                continue  # Wheel looks different now; don't splice an earlier position.
            dx = cv2.Sobel(tile, cv2.CV_32F, 1, 0)
            dy = cv2.Sobel(tile, cv2.CV_32F, 0, 1)
            score = np.percentile(cv2.magnitude(dx, dy), 85)
            candidates.append((score, i))
        if positions is not None:
            phases = [row[wheel] for row in positions]
            digits = [((p+5)//10) % 10 if p % 10 not in (4, 5, 6) else None for p in phases]
            digit, count = Counter(digits).most_common(1)[0]
            if digit is not None and count > len(crops)//2:
                supported = [item for item in candidates if digits[item[1]] == digit]
                if supported:
                    candidates = supported
        _, winner = max(candidates)
        winners.append(winner)
    return np.hstack([crops[i][:, left:right] for i, left, right in
                      zip(winners, DIGIT_EDGES, DIGIT_EDGES[1:])]), winners


def features(gray):
    # Descriptor coordinates are in the full mirrored frame. Restrict searches
    # to the installation's meter face and allow modest camera displacement.
    roi = gray[180:680, 650:1100]
    detector = cv2.ORB_create(nfeatures=1200, fastThreshold=5, edgeThreshold=12)
    points, desc = detector.detectAndCompute(cv2.equalizeHist(roi), None)
    return np.float32([p.pt for p in points]).reshape(-1, 2) + [650, 180], desc


def make_landmarks(reference, path):
    pts, desc = features(reference)
    fixed = np.zeros(len(pts), dtype=bool)
    for x, y, w, h in LABELS:
        fixed |= ((pts[:, 0] >= x) & (pts[:, 0] < x+w) &
                  (pts[:, 1] >= y) & (pts[:, 1] < y+h))
    np.savez_compressed(path, points=pts[fixed], descriptors=desc[fixed])


def registered_crop(frame, landmarks):
    if frame.shape[:2] != (1080, 1920):
        raise ValueError('water stream resolution')
    img = cv2.flip(frame, 1)
    pts, desc = features(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    matches = [] if desc is None or len(desc) < 2 else cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(
        landmarks['descriptors'], desc, k=2)
    good = [a for a, b in matches if a.distance < 0.78*b.distance]
    if len(good) < 10:
        return None
    src = landmarks['points'][[m.queryIdx for m in good]]
    dst = pts[[m.trainIdx for m in good]]
    m, mask = cv2.estimateAffinePartial2D(src, dst, ransacReprojThreshold=1.8)
    if m is None or mask.sum() < 10 or np.ptp(src[mask.ravel() != 0, 1]) < 70:
        return None
    if not 0.9 < np.linalg.norm(m[:, 0]) < 1.1 or abs(m[1, 0]) > 0.15:
        return None
    corners = np.float32(WINDOW @ m[:, :2].T + m[:, 2])
    rect = np.float32([[0, 0], [149, 0], [149, 39], [0, 39]])
    return cv2.warpPerspective(img, cv2.getPerspectiveTransform(corners, rect), (150, 40))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--warmup', type=float, default=WARMUP_SECONDS,
                        help='Seconds to drain after first frame (default: %(default)s)')
    parser.add_argument('--reference', type=Path, default=REFERENCE,
                        help='Original unmirrored frame matching the calibrated label/window coordinates')
    parser.add_argument('--compare-first', action='store_true', help='Include first frame as a baseline')
    args = parser.parse_args()
    if not math.isfinite(args.warmup) or not 0 <= args.warmup <= 60:
        parser.error('--warmup must be 0 to 60 seconds')
    reference = cv2.imread(str(args.reference), cv2.IMREAD_GRAYSCALE)
    if reference is None:
        parser.error(f'Missing calibration frame: {args.reference}')
    reference = cv2.flip(reference, 1)
    creds = load_prefixed_credentials(ROOT / '.env', 'WATER_METER_')
    out = ROOT / 'snapshots' / datetime.now().strftime('water-register-%Y%m%d-%H%M%S-%f')
    out.mkdir(parents=True)
    times = [args.warmup + i * 0.25 for i in range(9)]
    if args.compare_first and args.warmup:
        times.insert(0, 0)
    frames = []
    with open_camera_stream(creds, 1, 10) as camera:
        start = None
        while times:
            ok, frame = camera.read()
            if not ok:
                raise RuntimeError('Water meter stream stopped delivering frames')
            now = time.monotonic()
            if start is None:
                start = now
            if now - start >= times[0]:
                frames.append((now - start, frame))
                times.pop(0)
    preview, crops, numbers = [], [], []
    for i, (elapsed, frame) in enumerate(frames):
        crop = prepare_strip(extract_register(frame, reference))
        path = out / f'{i+1}-register.png'
        if not cv2.imwrite(str(path), crop):
            raise OSError(path)
        row = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
        row = cv2.copyMakeBorder(row, 25, 0, 0, 0, cv2.BORDER_CONSTANT)
        cv2.putText(row, f'+{elapsed:.1f} s', (6, 18), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (255, 255, 255), 1, cv2.LINE_AA)
        preview.append(row)
        if elapsed >= args.warmup:
            crops.append(crop)
            numbers.append(i + 1)
    selected, winners = select_digits(crops)
    if not cv2.imwrite(str(out / 'best-per-digit.png'), selected):
        raise OSError('Could not save selected digits')
    sources = ' '.join(str(numbers[i]) for i in winners)
    print(f'Source frames (left to right): {sources}')
    row = cv2.resize(selected, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
    row = cv2.copyMakeBorder(row, 25, 0, 0, 0, cv2.BORDER_CONSTANT)
    cv2.putText(row, f'Best per digit: {sources}', (6, 18), cv2.FONT_HERSHEY_SIMPLEX,
                0.5, (255, 255, 255), 1, cv2.LINE_AA)
    preview.insert(0, row)
    if not cv2.imwrite(str(out / 'preview.png'), np.vstack(preview)):
        raise OSError('Could not save preview')
    print(out.relative_to(ROOT))


if __name__ == '__main__':
    main()
