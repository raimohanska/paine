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
                 (80, 2, 18, 27), (106, 0, 14, 31), (128, 0, 14, 30))


def wheel_tile(crop, i, dx=0):
    x, y, w, h = WHEEL_WINDOWS[i]
    roi = crop[y:y+h, x+dx:x+dx+w]
    rgb = cv2.cvtColor(roi, cv2.COLOR_BGR2RGB)
    if i == 5:
        rgb = cv2.GaussianBlur(rgb, (3, 3), 0.55)
    lo, hi = np.percentile(rgb, [2, 98])
    scaled = np.uint8(np.clip((rgb-lo) * 255 / max(1, hi-lo), 0, 255))
    return cv2.resize(scaled, (20, 32), interpolation=cv2.INTER_AREA)


def prepare_strip(crop):
    """Prepare the six model tiles once, packed side by side as a lossless BGR image."""
    if crop is None or crop.shape != (40, 150, 3):
        raise ValueError('Expected an aligned 150x40 BGR register crop')
    # Glyph centers, not wheel centers: the dark hardware to the right of
    # each numeral looks like a vertical stroke when included in the ROI.
    return cv2.cvtColor(np.hstack([wheel_tile(crop, i) for i in range(6)]), cv2.COLOR_RGB2BGR)


def wheel_inputs(strip):
    if strip is not None and strip.shape == (40, 150, 3):  # historical offline captures
        strip = prepare_strip(strip)
    if strip is None or strip.shape != (32, 120, 3):
        raise ValueError('Expected six packed 20x32 wheel inputs')
    for tile in np.hsplit(strip, 6):
        yield cv2.cvtColor(tile, cv2.COLOR_BGR2RGB)[None].astype(np.float32)


# 8x12 normalized stroke signature from the physically verified 2108.43 burst.
FOUR = np.frombuffer(bytes.fromhex(
    '181c1e1e1e1f1f1d08070703fd060a00040505ffeafe08fb050804f5e2f809fe'
    '080903eee0f306fc0a07feefe3f401f10905fdf6e4f902df0a0600fae2fc11eb'
    '0c04f6edddf81d060b0700f9e3f4170007090902ec00f3d406080907050ffbec'),
    dtype=np.int8).astype(np.float32).reshape(12, 8)


def model_phase(model, tensor):
    inp = model.get_input_details()[0]['index']
    out = model.get_output_details()[0]['index']
    model.set_tensor(inp, tensor)
    model.invoke()
    return int(model.get_tensor(out).argmax())


def recognize(model, crop, audit=None):
    positions = []
    for tensor in wheel_inputs(crop):
        phase = model_phase(model, tensor)
        if len(positions) == 4 and 7 <= phase <= 13:
            tile = cv2.cvtColor(tensor[0], cv2.COLOR_RGB2GRAY)
            small = cv2.resize(tile, (8, 12), interpolation=cv2.INTER_AREA)
            score = float(cv2.matchTemplate(small, FOUR, cv2.TM_CCOEFF_NORMED)[0, 0])
            if audit is not None:
                audit.update(original_phase=phase, four_match=round(score, 3), template_applied=score >= 0.92)
            if score >= 0.92:
                phase = 40
        positions.append(phase)
    return positions  # integer tenths of a wheel revolution digit (0..99)


def shifted_fifth(model, crop, dx):
    return model_phase(model, wheel_tile(crop, 4, dx)[None].astype(np.float32))


def corroborate_fours(model, raw, rows, audits):
    """Resolve 1/4 splits, anchored by an original (non-template) centered 4."""
    if not any(38 <= row[4] <= 42 and not audit for row, audit in zip(rows, audits)):
        return
    for crop, row, audit in zip(raw, rows, audits):
        if 7 <= row[4] <= 13:
            phases = [shifted_fifth(model, crop, dx) for dx in (-1, -2)]
            audit['left_phases'] = phases
            if all(38 <= p <= 42 for p in phases):
                row[4] = phases[0]


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
        positions = [v/10 for v in recognize(model, cv2.imread(str(path)))]
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
