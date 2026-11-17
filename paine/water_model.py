"""Offline wheel-position probe for calibrated 150x40 water-register PNGs.

Usage: python probe_water_digits.py MODEL.tflite CROP.png [CROP.png ...]
Tested with AI-on-the-edge-device dig-class100-0182-s2_q and LiteRT.
Reports wheel positions only; a rotating wheel's position isn't a meter total.
"""

import argparse
from pathlib import Path

import cv2
import numpy as np


# Left, top, width, height in the 150x40 aligned register. Leave the
# stationary lower sill and the right-hand separator out of each model tile.
WHEEL_WINDOWS = ((4, 2, 18, 27), (28, 2, 18, 27), (54, 2, 18, 27),
                 (80, 2, 18, 27), (106, 0, 16, 31), (128, 0, 14, 30))


def wheel_inputs(crop):
    if crop is None or crop.shape != (40, 150, 3):
        raise ValueError('Expected an aligned 150x40 BGR register crop')
    # Glyph centers, not wheel centers: the dark hardware to the right of
    # each numeral looks like a vertical stroke when included in the ROI.
    for i, (x, y, w, h) in enumerate(WHEEL_WINDOWS):
        roi = crop[y:y+h, x:x+w]
        rgb = cv2.cvtColor(roi, cv2.COLOR_BGR2RGB)
        if i == 5:
            rgb = cv2.GaussianBlur(rgb, (3, 3), 0.55)
        lo, hi = np.percentile(rgb, [2, 98])
        scaled = np.uint8(np.clip((rgb-lo) * 255 / max(1, hi-lo), 0, 255))
        yield cv2.resize(scaled, (20, 32), interpolation=cv2.INTER_AREA)[None].astype(np.float32)


def recognize(model, crop):
    inp = model.get_input_details()[0]['index']
    out = model.get_output_details()[0]['index']
    positions = []
    for tensor in wheel_inputs(crop):
        model.set_tensor(inp, tensor)
        model.invoke()
        positions.append(int(model.get_tensor(out).argmax()))
    return positions  # integer tenths of a wheel revolution digit (0..99)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model', type=Path)
    parser.add_argument('crops', type=Path, nargs='+')
    parser.add_argument('--burst', action='store_true',
                        help='Report wheel agreement; supply only individual frames from one burst')
    args = parser.parse_args()
    from ai_edge_litert.interpreter import Interpreter

    model = Interpreter(str(args.model))
    model.allocate_tensors()
    inp, = model.get_input_details()
    out, = model.get_output_details()
    if tuple(inp['shape']) != (1, 32, 20, 3) or tuple(out['shape']) != (1, 100):
        parser.error('Expected the dig-class100 rolling-digit model')
    readings = []
    for path in args.crops:
        positions = []
        for tensor in wheel_inputs(cv2.imread(str(path))):
            model.set_tensor(inp['index'], tensor)
            model.invoke()
            positions.append(model.get_tensor(out['index']).argmax()/10)
        readings.append(positions)
        print(path, ' '.join(f'{v:.1f}' for v in positions))
    if args.burst:
        for i, column in enumerate(np.asarray(readings).T, 1):
            # Compare cyclic wheel positions: 9.9 is close to 0.0.
            delta = abs(column[:, None] - column)
            neighbors = np.minimum(delta, 10-delta) <= 0.35
            count = neighbors.sum(axis=1)
            j = int(np.argmax(count))
            status = 'consistent' if len(column) >= 3 and count[j] >= 0.8*len(column) else 'uncertain'
            print(f'Wheel {i}: {column[j]:.1f}, {count[j]}/{len(column)} agree ({status})')


if __name__ == '__main__':
    main()
