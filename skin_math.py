"""
skin_math.py - math for Winamp-style skinned windows: snapping, docking and
the classic visualizer (19-bar spectrum and oscilloscope line, 76x16 pixels).

Pure functions, standard library only.
"""

import cmath
import math

SNAP_PX = 10


def snap(rect, others, screen):
    """Return the (x, y) of `rect` after snapping to `screen` edges and to
    each rect in `others` (x and y snap independently)."""
    x, y, w, h = rect
    sx, sy, sw, sh = screen

    # --- X candidates ---
    x_candidates = [sx, sx + sw - w]
    for ox, oy, ow, oh in others:
        if y < oy + oh + SNAP_PX and y + h > oy - SNAP_PX:
            x_candidates.extend([ox - w, ox + ow, ox, ox + ow - w])
    best_x = None
    best_dx = SNAP_PX
    for cand in x_candidates:
        d = abs(cand - x)
        if d <= SNAP_PX and (best_x is None or d < best_dx):
            best_x = cand
            best_dx = d
    if best_x is not None:
        x = best_x

    # --- Y candidates ---
    y_candidates = [sy, sy + sh - h]
    for ox, oy, ow, oh in others:
        if x < ox + ow + SNAP_PX and x + w > ox - SNAP_PX:
            y_candidates.extend([oy - h, oy + oh, oy, oy + oh - h])
    best_y = None
    best_dy = SNAP_PX
    for cand in y_candidates:
        d = abs(cand - y)
        if d <= SNAP_PX and (best_y is None or d < best_dy):
            best_y = cand
            best_dy = d
    if best_y is not None:
        y = best_y

    return (int(x), int(y))


def docked(a, b):
    """True when rects a and b touch edge to edge and overlap along that edge."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    vertical_touch = (ay + ah == by or by + bh == ay) and ax < bx + bw and bx < ax + aw
    horizontal_touch = (ax + aw == bx or bx + bw == ax) and ay < by + bh and by < ay + ah
    return vertical_touch or horizontal_touch


def dock_group(index, rects):
    """Set of indices reachable from `index` through `docked` pairs (BFS)."""
    group = {index}
    frontier = [index]
    while frontier:
        current = frontier.pop()
        for other in range(len(rects)):
            if other not in group and docked(rects[current], rects[other]):
                group.add(other)
                frontier.append(other)
    return group


def fft(values):
    """Iterative radix-2 FFT: bit-reversal permutation, then butterflies."""
    n = len(values)
    a = list(values)

    # Bit-reversal permutation
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]

    # Butterflies
    size = 2
    while size <= n:
        step = size // 2
        twiddle = cmath.exp(-2j * math.pi / size)
        for start in range(0, n, size):
            w = 1 + 0j
            for k in range(start, start + step):
                t = w * a[k + step]
                a[k + step] = a[k] - t
                a[k] = a[k] + t
                w *= twiddle
        size *= 2
    return a


def spectrum(samples, rate, bars=19, height=16):
    """19-bar spectrum magnitudes as ints in 0..height."""
    n = 1
    limit = min(len(samples), 512)
    while n * 2 <= limit:
        n *= 2
    if n < 32:
        return [0] * bars

    x = [
        samples[i] * (0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1)))
        for i in range(n)
    ]
    X = fft(x)
    half = n // 2
    mags = [abs(X[b]) for b in range(half)]

    fmax = min(16000.0, rate / 2)
    result = []
    for k in range(bars):
        f_lo = 60.0 * (fmax / 60.0) ** (k / bars)
        f_hi = 60.0 * (fmax / 60.0) ** ((k + 1) / bars)
        lo = max(1, math.ceil(f_lo * n / rate))
        hi = min(half - 1, math.ceil(f_hi * n / rate) - 1)
        if lo <= hi:
            mag = max(mags[lo:hi + 1])
        else:
            fc = math.sqrt(f_lo * f_hi)
            mag = mags[min(max(round(fc * n / rate), 1), half - 1)]
        db = 20 * math.log10(mag / (n / 4) + 1e-9)
        bar = round((db + 60) / 60 * height)
        bar = max(0, min(height, bar))
        result.append(bar)
    return result


def scope(samples, width=76, height=16):
    """Oscilloscope line: list of ints in 0..height-1 (0 = top)."""
    if not samples:
        return [round((height - 1) / 2)] * width
    result = []
    for i in range(width):
        s = samples[i * len(samples) // width]
        s = max(-1.0, min(1.0, s))
        value = round((1 - s) / 2 * (height - 1))
        result.append(value)
    return result
