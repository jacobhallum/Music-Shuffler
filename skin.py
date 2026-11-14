"""
skin.py - load a classic Winamp skin (.wsz) for the skinned windows.

A .wsz is a zip of BMP sprite sheets plus a few text files. The zip is read in memory (never extracted), under the
size limits in skin_files, and every sheet the skin lacks, or a piece that falls outside a short sheet, is filled with
FALLBACK_RGBA instead of failing: an odd skin degrades piece by piece. Only a file that isn't a zip, or a skin without
a readable main.bmp, can't be loaded (SkinError).
"""

import io
import os
import zipfile

from PIL import Image

import skin_files
from skin_sprites import SPRITES

FALLBACK_RGBA = (38, 40, 48, 255)   # a neutral dark fill for pieces a skin doesn't have
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
        for sheet in SPRITES:
            member = members.get(sheet)
            if member is None:
                continue
            try:
                img = Image.open(io.BytesIO(z.read(member)))
                img.load()
                sheets[sheet] = img.convert('RGBA')
            except Exception:   # a broken or hostile bitmap: that sheet falls back
                continue
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
