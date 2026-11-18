"""Offline fifth-wheel model experiment. Usage: python probe_water_verifier.py EXAMPLES_DIR

Train a small ridge classifier on visually labeled, centered 4s and 5s plus
the second (integer) wheel's 1s as proxy negatives. Compare on later bursts.
This evaluates digit shape only, not rolling phase or publication decisions.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
from zipfile import ZipFile

import cv2
import numpy as np

from paine.water_model import wheel_tile


# Visual labels keyed by capture time, not the reusable example-slot filename.
# Omit unclear and rolling crops rather than inferring a label from the total.
LABELS = {
    4: {1794987455, 1795015415, 1795004915, 1794953912, 1794975995,
        1794980675, 1795013135, 1795013676, 1795000776, 1794980375,
        1794981335, 1794973655, 1794977255, 1794980615, 1794973174,
        1794954092, 1794988355, 1795015356, 1794954152, 1794981035,
        1794958353, 1794969454, 1795001675, 1795015176},
    5: {1795018056, 1795015715, 1795017815, 1795018236},
}


def features(crop, wheel):
    gray = cv2.cvtColor(wheel_tile(crop, wheel), cv2.COLOR_RGB2GRAY)
    x = cv2.resize(gray, (8, 12), interpolation=cv2.INTER_AREA).astype(float).ravel()
    x -= x.mean()
    return x / max(np.linalg.norm(x), 1e-6)


def experiment(folder):
    data = []
    for path in folder.glob('*.zip'):
        with ZipFile(path) as z:
            meta = json.loads(z.read('review.json'))
            t = meta['time']
            label = next((digit for digit, times in LABELS.items() if t in times), None)
            if label is None:
                continue
            crop = cv2.imdecode(np.frombuffer(z.read('register.png'), np.uint8), cv2.IMREAD_COLOR)
            data.append((t, label, crop, meta['positions']))
    data.sort(key=lambda row: row[0])
    if not data:
        raise ValueError('No visually labeled bursts from this export')
    train, test = data[:len(data)//2], data[len(data)//2:]
    # Later 5s are unseen challenge cases: the binary model must not call them 4.
    pairs = [(features(crop, wheel), target) for _, label, crop, _ in train if label == 4
             for wheel, target in ((1, -1), (4, 1))]
    if not pairs:
        raise ValueError('No labeled training pairs')
    x, y = map(np.asarray, zip(*pairs))
    weights = np.linalg.solve(x.T @ x + np.eye(x.shape[1]), x.T @ y)
    counts = Counter()
    for _, label, crop, phases in test:
        for wheel, digit in ((1, 1), (4, label)):
            p = phases[wheel]
            archived_four = 37 <= p <= 43
            model_four = features(crop, wheel) @ weights > 0
            counts[digit, 'total'] += 1
            counts[digit, 'archived'] += int(archived_four)
            counts[digit, 'learned'] += int(model_four)
    print(f'{len(pairs)//2} training pairs; {len(test)} later test bursts')
    print('Actual digit | test images | archived calls of 4 | learned calls of 4')
    for digit in (1, 4, 5):
        print(digit, *(counts[digit, k] for k in ('total', 'archived', 'learned')), sep=' | ')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('examples', type=Path)
    experiment(parser.parse_args().examples)
