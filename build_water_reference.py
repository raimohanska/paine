"""Rebuild numeric wheel-bank landmarks from installation captures (no OCR labels)."""

from pathlib import Path
from zipfile import ZipFile

import cv2
import numpy as np

from paine.water_locator import sift, rectangle
from paine.water_vision import WINDOW, REFERENCE, features


def main():
    assets = Path(__file__).parent / "paine"
    points, descriptors = [], []
    with np.load(assets / "water_landmarks.npz") as old:
        for path in [
            REFERENCE,
            REFERENCE.parent.parent
            / "water-meter-lights-on-20261117-181900/shot-2.jpg",
        ]:
            gray = cv2.flip(cv2.imread(str(path), cv2.IMREAD_GRAYSCALE), 1)
            corners = WINDOW
            if path != REFERENCE:
                pts, des = features(gray)
                pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(
                    old["descriptors"], des, k=2
                )
                good = [a for a, b in pairs if a.distance < 0.78 * b.distance]
                m, _ = cv2.estimateAffinePartial2D(
                    old["points"][[m.queryIdx for m in good]],
                    pts[[m.trainIdx for m in good]],
                    ransacReprojThreshold=1.8,
                )
                corners = np.float32(WINDOW @ m[:, :2].T + m[:, 2])
            # Clip to register before computing descriptors: no label or face pixels.
            H = cv2.getPerspectiveTransform(corners, rectangle(150, 40))
            crop = cv2.warpPerspective(gray, H, (174, 40))
            pts, des = sift(crop)
            points.extend(pts)
            descriptors.extend(des.astype(np.uint8))
            # Also sample in native camera orientation, blanking everything
            # outside the wheel bank before descriptor extraction.
            quad = cv2.perspectiveTransform(rectangle(174, 40)[None], np.linalg.inv(H))[
                0
            ]
            mask = np.zeros_like(gray)
            cv2.fillConvexPoly(mask, np.int32(quad), 255)
            masked = np.where(mask, gray, np.uint8(np.median(crop)))
            pts, des = sift(masked)
            mapped = cv2.perspectiveTransform(pts[None], H)[0]
            use = ((mapped > [2, 2]) & (mapped < [172, 38])).all(axis=1)
            points.extend(mapped[use])
            descriptors.extend(des[use].astype(np.uint8))
    # Two later six-wheel crops provide different drum positions. Never invent
    # coordinates beyond their 150px canvas. All other review crops are held out.
    examples = assets.parent / "downloads/paine-diagnostics/water-meter/examples"
    for name in ("accepted-00.zip", "accepted-02.zip"):
        with ZipFile(examples / name) as z:
            crop = cv2.imdecode(np.frombuffer(z.read("register.png"), np.uint8), 0)
        assert crop.shape == (40, 150)
        pts, des = sift(crop)
        points.extend(pts)
        descriptors.extend(des.astype(np.uint8))
    plane = cv2.getPerspectiveTransform(rectangle(150, 40), WINDOW)
    np.savez_compressed(
        assets / "water_register.npz",
        points=np.float32(points),
        descriptors=descriptors,
        plane=plane,
    )


if __name__ == "__main__":
    main()
