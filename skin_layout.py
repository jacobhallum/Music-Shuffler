"""
skin_layout.py - geometry of the classic Winamp main and equalizer windows,
plus the small conversions the skinned windows need (slider positions, sprite
frames, time digits, font cells, scrolling title).
"""

from skin_sprites import FONT_LOOKUP

MAIN_LAYOUT = {
    'menu': (6, 3, 9, 9), 'minimize': (244, 3, 9, 9), 'shade': (254, 3, 9, 9),
    'close': (264, 3, 9, 9),
    'clutter_o': (10, 25, 8, 8), 'clutter_a': (10, 33, 8, 7),
    'clutter_i': (10, 40, 8, 7), 'clutter_d': (10, 47, 8, 8),
    'clutter_v': (10, 55, 8, 7), 'playstate': (26, 28, 9, 9),
    'time': (39, 26, 59, 13), 'heart': (100, 28, 9, 8),
    'visualizer': (24, 43, 76, 16), 'marquee': (111, 27, 154, 6),
    'kbps': (111, 43, 15, 6), 'khz': (156, 43, 10, 6),
    'mono': (212, 41, 27, 12), 'stereo': (239, 41, 29, 12),
    'volume': (107, 57, 68, 13), 'balance': (177, 57, 38, 13),
    'eq_button': (219, 58, 23, 12), 'pl_button': (242, 58, 23, 12),
    'position': (16, 72, 248, 10),
    'previous': (16, 88, 23, 18), 'play': (39, 88, 23, 18),
    'pause': (62, 88, 23, 18), 'stop': (85, 88, 23, 18),
    'next': (108, 88, 22, 18), 'eject': (136, 89, 22, 16),
    'shuffle': (164, 89, 47, 15), 'repeat': (210, 89, 28, 15),
    'about': (253, 91, 13, 15), 'titlebar': (0, 0, 275, 14),
}

EQ_LAYOUT = {
    'close': (264, 3, 9, 9), 'shade': (254, 3, 9, 9),
    'on': (14, 18, 26, 12), 'auto': (40, 18, 32, 12),
    'presets': (217, 18, 44, 12), 'graph': (86, 17, 113, 19),
    'preamp': (21, 38, 14, 63),
}
for _i in range(10):
    EQ_LAYOUT['band%d' % _i] = (78 + 18 * _i, 38, 14, 63)
EQ_LAYOUT['titlebar'] = (0, 0, 275, 14)

TIME_DIGIT_X = (48, 60, 78, 90)

MINUS_SIGN = (38, 32, 5, 1)

MARQUEE_SEPARATOR = '  ***  '


def hit_test(layout, x, y, scale=1):
    px = x // scale
    py = y // scale
    for name, (rx, ry, w, h) in layout.items():
        if rx <= px < rx + w and ry <= py < ry + h:
            return name
    return None


def slider_value(x, track_x, track_w, thumb_w, lo, hi):
    span = track_w - thumb_w
    if span <= 0:
        return lo
    pos = min(max(x - track_x - thumb_w / 2, 0), span)
    return lo + (hi - lo) * pos / span


def slider_thumb_x(value, track_x, track_w, thumb_w, lo, hi):
    value = min(max(value, lo), hi)
    return track_x + round((value - lo) / (hi - lo) * (track_w - thumb_w))


def volume_frame(percent):
    percent = min(max(percent, 0), 100)
    return min(27, int(percent * 28 / 100))


def balance_frame(balance):
    balance = min(max(balance, -100), 100)
    return min(27, int(abs(balance) * 28 / 100))


def eq_frame(db):
    db = min(max(db, -12), 12)
    return min(27, int((db + 12) / 24 * 28))


def eq_thumb_y(db):
    db = min(max(db, -12), 12)
    return round((12 - db) / 24 * 51)


def time_digits(seconds):
    s = max(0, int(seconds or 0))
    m, sec = divmod(s, 60)
    if m > 99:
        m, sec = 99, 59
    return (m // 10, m % 10, sec // 10, sec % 10)


def text_cells(text):
    cells = []
    for ch in text:
        for key in (ch, ch.lower(), ch.upper()):
            if key in FONT_LOOKUP:
                cells.append(FONT_LOOKUP[key])
                break
        else:
            cells.append(FONT_LOOKUP[' '])
    return cells


def marquee_text(artist, title, duration):
    artist = (artist or '').strip()
    title = (title or '').strip()
    head = '%s - %s' % (artist, title) if artist else title
    if duration and duration > 0:
        m, s = divmod(int(duration), 60)
        return '%s (%d:%02d)' % (head, m, s)
    return head


def marquee_slice(text, offset, chars):
    if len(text) <= chars:
        return text.ljust(chars)
    loop = text + MARQUEE_SEPARATOR
    o = offset % len(loop)
    return (loop + loop)[o:o + chars]
