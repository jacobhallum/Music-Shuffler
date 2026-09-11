"""Pure image helpers for building window/taskbar icon frames from arbitrary
album artwork. No Tkinter/GUI dependency - takes and returns PIL Images."""

from PIL import Image

DEFAULT_ICON_SIZES = (16, 32, 48)


def make_icon_images(image, sizes=DEFAULT_ICON_SIZES):
    """Center-crop `image` to a square (using its shorter side) and return a
    list of RGBA copies resized to each size in `sizes`, same order as `sizes`."""
    rgba = image.convert('RGBA')
    width, height = rgba.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    square = rgba.crop((left, top, left + side, top + side))

    return [square.resize((size, size), Image.Resampling.LANCZOS) for size in sizes]
