"""
cover_art.py - Pillow helpers for the album-cover window: shrink the current cover, draw it letterboxed in a
square filled with the skin's background colour, and load the app's own "Deep Cuts" art as the fallback.

No Tk.
"""

import os

from PIL import Image

import skin_math

ART = 174        # the picture's side at 1x: the cover window's inside height (main + EQ, less the frame)
MAX_ART = 522    # the largest kept cover (ART x 3, the biggest skin size)


def shrink(image, limit=MAX_ART):
    """A copy of image that fits inside limit x limit (LANCZOS); the mode is kept and image is not changed."""
    out = image.copy()
    out.thumbnail((limit, limit), Image.LANCZOS)
    return out


def render_square(image, size, bg):
    """An RGB size x size square filled with bg ('#rrggbb'), with image (any mode, or None) scaled to fit and
    centered, its transparency showing bg."""
    out = Image.new('RGB', (size, size), bg)
    if image is None:
        return out
    src = image.convert('RGBA')
    x, y, w, h = skin_math.fit_box(src.width, src.height, size)
    src = src.resize((w, h), Image.LANCZOS)
    out.paste(src, (x, y), src)
    return out


def load_fallback(bundle_dir):
    """The app's Deep Cuts art from bundle_dir/assets: the 1024 px PNG, else the ICO's largest frame, shrunk to
    MAX_ART; None when neither loads."""
    for name in ('app_icon_1024.png', 'app_icon.ico'):
        path = os.path.join(str(bundle_dir), 'assets', name)
        if not os.path.exists(path):
            continue
        try:
            with Image.open(path) as img:
                img.load()
                return shrink(img)
        except Exception as e:
            print(f"⚠ Could not load fallback cover {name}: {e}")
    return None
