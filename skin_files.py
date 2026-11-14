"""
skin_files.py - read the text parts of a classic Winamp skin (.wsz) safely
and leniently. Real skins are messy, so bad lines fall back to Winamp's
defaults instead of failing.
"""

import re

MAX_MEMBER_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 30 * 1024 * 1024

DEFAULT_PLEDIT = {
    'normal': '#00ff00',
    'current': '#ffffff',
    'normalbg': '#000000',
    'selectedbg': '#0000c6',
    'font': 'Arial',
}

DEFAULT_VISCOLORS = [
    (0, 0, 0), (24, 33, 41), (239, 49, 16), (206, 41, 16),
    (214, 90, 0), (214, 102, 0), (214, 115, 0), (198, 123, 8),
    (222, 165, 24), (214, 181, 33), (189, 222, 41), (148, 222, 33),
    (41, 206, 16), (50, 190, 16), (57, 181, 16), (49, 156, 8),
    (41, 148, 0), (24, 132, 8), (255, 255, 255), (214, 214, 222),
    (181, 189, 189), (160, 170, 175), (148, 156, 165), (150, 150, 150),
]

REGION_SECTIONS = ('normal', 'equalizer', 'windowshade', 'equalizerws')

_NUM_RE = re.compile(r'(\d+)\s*,\s*(\d+)\s*,\s*(\d+)')


def member_map(names):
    """Map lowercased basename -> original zip member name; first wins."""
    result = {}
    for name in names:
        if name.endswith('/') or name.endswith('\\'):
            continue
        base = re.split(r'[/\\]', name)[-1].lower()
        if base not in result:
            result[base] = name
    return result


def allowed_members(infos):
    """Keep members under the per-member cap, until the total exceeds the cap."""
    kept = set()
    total = 0
    for name, size in infos:
        if size > MAX_MEMBER_BYTES:
            continue
        if total + size > MAX_TOTAL_BYTES:
            break
        total += size
        kept.add(name)
    return kept


def parse_pledit(text):
    """Parse pledit.ini text over the Winamp defaults."""
    result = dict(DEFAULT_PLEDIT)
    color_keys = ('normal', 'current', 'normalbg', 'selectedbg')
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(';') or line.startswith('//') or line.startswith('['):
            continue
        if '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip().lower()
        value = value.strip()
        cut = len(value)
        for sep in (';', '//'):
            pos = value.find(sep)
            if pos != -1 and pos < cut:
                cut = pos
        value = value[:cut].strip()
        if not value:
            continue
        if key in color_keys:
            digits = value[1:] if value.startswith('#') else value
            if len(digits) == 6 and all(c in '0123456789abcdefABCDEF' for c in digits):
                result[key] = '#' + digits.lower()
        elif key == 'font':
            result[key] = value
    return result


def parse_viscolor(text):
    """Parse viscolor.txt text into exactly 24 (r, g, b) tuples."""
    colors = []
    for line in text.splitlines():
        match = _NUM_RE.search(line)
        if not match:
            continue
        colors.append(tuple(min(max(int(n), 0), 255) for n in match.groups()))
        if len(colors) == 24:
            break
    while len(colors) < 24:
        colors.append(DEFAULT_VISCOLORS[len(colors)])
    return colors


def parse_region(text):
    """Parse region.txt text into validated polygon lists per section."""
    sections = {}
    current = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(';'):
            continue
        if line.startswith('[') and line.endswith(']'):
            current = line[1:-1].strip().lower()
            sections.setdefault(current, {})
        elif '=' in line and current is not None:
            key, _, value = line.partition('=')
            sections[current][key.strip().lower()] = value.strip()

    result = {}
    for section in REGION_SECTIONS:
        data = sections.get(section)
        if not data or 'numpoints' not in data or 'pointlist' not in data:
            continue
        counts = [int(n) for n in re.findall(r'-?\d+', data['numpoints'])]
        nums = [int(n) for n in re.findall(r'-?\d+', data['pointlist'])]
        if any(c < 3 for c in counts) or len(nums) % 2 or sum(counts) != len(nums) // 2:
            continue
        points = list(zip(nums[::2], nums[1::2]))
        polygons = []
        index = 0
        for count in counts:
            polygons.append(points[index:index + count])
            index += count
        result[section] = polygons
    return result
