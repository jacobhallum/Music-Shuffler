"""
skin.py - load a classic Winamp skin (.wsz) for the skinned windows.

A .wsz is a zip of BMP sprite sheets plus a few text files. The zip is read in memory (never extracted), under the
size limits in skin_files, and every sheet the skin lacks, or a piece that falls outside a short sheet, is filled with
FALLBACK_RGBA instead of failing: an odd skin degrades piece by piece. Only a file that isn't a zip, or a skin without
a readable main.bmp, can't be loaded (SkinError).
"""

import io
import os
import struct
import zipfile

from PIL import Image, ImageFile

import skin_files
from skin_sprites import SPRITES

FALLBACK_RGBA = (0, 0, 0, 0)   # pieces a skin doesn't have are see-through, so its background art shows
EXTRA_SHEETS = ('text.bmp',)  # sheets read directly by the windows (the font), not via SPRITES
TEXT_FILES = ('pledit.txt', 'viscolor.txt', 'region.txt')
_SHEET_OF = {name: sheet for sheet, sprites in SPRITES.items() for name in sprites}


class SkinError(Exception):
    """The file can't be used as a skin; str(error) says why."""


class Skin:
    def __init__(self, name, sheets, pledit, viscolors, regions):
        self.name = name
        self._sheets = sheets            # sheet file name -> RGBA image
        self.pledit = pledit
        self.viscolors = viscolors
        self.regions = regions
        self._cache = {}

    def has(self, sheet):
        return sheet in self._sheets

    def sheet_size(self, sheet):
        """(width, height) of a sheet the skin has, else None."""
        img = self._sheets.get(sheet)
        return img.size if img is not None else None

    def crop(self, sheet, x, y, w, h):
        """A w x h RGBA image cut from a sheet at (x, y); pixels the sheet doesn't have get FALLBACK_RGBA."""
        key = (sheet, x, y, w, h)
        img = self._cache.get(key)
        if img is None:
            img = Image.new('RGBA', (w, h), FALLBACK_RGBA)
            src = self._sheets.get(sheet)
            if src is not None:
                sw, sh = src.size
                right, bottom = min(x + w, sw), min(y + h, sh)
                if right > x and bottom > y:
                    img.paste(src.crop((x, y, right, bottom)), (0, 0))
            self._cache[key] = img
        return img

    def sprite(self, name):
        """A named sprite from skin_sprites.SPRITES."""
        sheet = _SHEET_OF[name]
        return self.crop(sheet, *SPRITES[sheet][name])

    def scaled(self, img, scale):
        """img enlarged by an integer factor, nearest neighbour (crisp pixels)."""
        if scale == 1:
            return img
        return img.resize((img.width * scale, img.height * scale), Image.NEAREST)


def _decode_rle8(data):
    """Decode an 8-bit RLE-compressed BMP (BI_RLE8) by hand, or None. Pillow rejects some valid ones (Winamp's own
    base skin's TEXT.BMP), so this follows the format: runs, end-of-line, end-of-bitmap, delta and absolute runs,
    rows stored bottom-up, pixels never written are palette index 0."""
    try:
        if data[:2] != b'BM':
            return None
        off, hdr = struct.unpack_from('<II', data, 10)
        w, h, _planes, bpp, comp = struct.unpack_from('<iiHHI', data, 18)
        ncolors = struct.unpack_from('<I', data, 46)[0] or 256
        if bpp != 8 or comp != 1 or not (0 < w <= 4096) or not (0 < abs(h) <= 4096):
            return None
        top_down, h = h < 0, abs(h)
        pal = data[14 + hdr:14 + hdr + 4 * min(ncolors, 256)]
        palette = []
        for i in range(0, len(pal) - 3, 4):
            palette += [pal[i + 2], pal[i + 1], pal[i]]
        pixels = bytearray(w * h)
        x, row, i, n = 0, 0, off, len(data)
        while i + 1 < n and row < h:
            count, value = data[i], data[i + 1]
            i += 2
            y = row if top_down else h - 1 - row
            if count:
                end = min(x + count, w)
                if x < w:
                    pixels[y * w + x:y * w + end] = bytes([value]) * (end - x)
                x += count
            elif value == 0:
                x, row = 0, row + 1
            elif value == 1:
                break
            elif value == 2:
                if i + 1 >= n:
                    break
                x, row, i = x + data[i], row + data[i + 1], i + 2
            else:
                run = data[i:i + value]
                end = min(x + len(run), w)
                if x < w:
                    pixels[y * w + x:y * w + end] = run[:end - x]
                x += value
                i += value + (value & 1)
        img = Image.frombytes('P', (w, h), bytes(pixels))
        img.putpalette(palette + [0] * (768 - len(palette)))
        return img.convert('RGBA')
    except (struct.error, ValueError, IndexError):
        return None


def _open_bitmap(data):
    """An RGBA image from bitmap bytes, or None. A truncated bitmap is read as far as it goes and an RLE8 one Pillow
    rejects is decoded by hand, like Winamp does; anything else broken or hostile gives None so that sheet falls back."""
    def pillow(truncated_ok):
        old = ImageFile.LOAD_TRUNCATED_IMAGES
        try:
            ImageFile.LOAD_TRUNCATED_IMAGES = truncated_ok
            img = Image.open(io.BytesIO(data))
            img.load()
            return img.convert('RGBA')
        except Exception:
            return None
        finally:
            ImageFile.LOAD_TRUNCATED_IMAGES = old

    return pillow(False) or _decode_rle8(data) or pillow(True)


def _read_text(z, members, name):
    member = members.get(name)
    if member is None:
        return ''
    try:
        return z.read(member).decode('latin-1')
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError):
        return ''


def load_skin(path):
    """Load a .wsz file. Raises SkinError(reason) when it can't be used."""
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise SkinError(f"not a skin file ({e.__class__.__name__})") from None
    with z:
        infos = [(i.filename, i.file_size) for i in z.infolist()]
        allowed = skin_files.allowed_members(infos)
        members = skin_files.member_map([n for n, _ in infos if n in allowed])
        sheets = {}
        for sheet in (*SPRITES, *EXTRA_SHEETS):
            member = members.get(sheet)
            if member is None:
                continue
            img = _open_bitmap(z.read(member))
            if img is not None:
                sheets[sheet] = img
        if 'main.bmp' not in sheets:
            raise SkinError("main.bmp is missing or unreadable")
        pledit = skin_files.parse_pledit(_read_text(z, members, 'pledit.txt'))
        viscolors = skin_files.parse_viscolor(_read_text(z, members, 'viscolor.txt'))
        regions = skin_files.parse_region(_read_text(z, members, 'region.txt'))
    return Skin(os.path.basename(path), sheets, pledit, viscolors, regions)


def list_skins(folder):
    """The .wsz file names in folder, sorted case-insensitively; [] if the folder is missing."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted((n for n in names if n.lower().endswith('.wsz')), key=str.casefold)
