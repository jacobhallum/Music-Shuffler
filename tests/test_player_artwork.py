"""
tests/test_player_artwork.py - the player keeps the current cover (current_artwork_image) for the skins' cover
window, and finds its bundled files (bundle_dir). Real MusicPlayerGUI methods bound onto a small fake; Tk needed for
the default view's PhotoImages (run on the hidden desktop).

Run with:
    python scripts/run_hidden.py python -m unittest tests.test_player_artwork -v
"""

import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

try:
    import tkinter as tk
    # The player's PhotoImages have no master=, so they land on the default root: share it with other test modules.
    _root = getattr(tk, '_default_root', None) or tk.Tk()
    _root.withdraw()
except Exception:
    _root = None

from PIL import Image  # noqa: E402

import music_player  # noqa: E402
from music_player import MusicPlayerGUI  # noqa: E402


class _Player:
    _load_album_artwork = MusicPlayerGUI._load_album_artwork
    _show_placeholder_artwork = MusicPlayerGUI._show_placeholder_artwork

    def __init__(self):
        self.root = tk.Toplevel(_root)
        self.root.withdraw()
        self.artwork_label = tk.Label(self.root)
        self._default_icon_photos = None
        self._track_icon_photos = None
        self.current_artwork = None
        self.current_artwork_image = None
        self.themes = {'dark': {'placeholder': '#111111'}, 'light': {'placeholder': '#eeeeee'}}
        self.dark_mode = True


@unittest.skipIf(_root is None, "no display")
class CurrentArtworkTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.p = _Player()
        self.addCleanup(self.p.root.destroy)
        patcher = unittest.mock.patch.object(music_player, 'open_audio', return_value=object())
        self.open_audio = patcher.start()
        self.addCleanup(patcher.stop)

    def track(self, folder, art_size=None):
        os.makedirs(folder, exist_ok=True)
        if art_size:
            Image.new('RGB', art_size, (200, 10, 10)).save(os.path.join(folder, 'cover.jpg'))
        path = os.path.join(folder, 'song.mp3')
        return SimpleNamespace(meta_path=path, filepath=path)

    def test_folder_art_is_kept_capped(self):
        ok = self.p._load_album_artwork(self.track(os.path.join(self.dir, 'a'), (1500, 1500)))
        self.assertTrue(ok)
        self.assertEqual(max(self.p.current_artwork_image.size), 522)

    def test_no_art_clears_image(self):
        self.p._load_album_artwork(self.track(os.path.join(self.dir, 'a'), (600, 600)))
        self.assertIsNotNone(self.p.current_artwork_image)
        self.assertFalse(self.p._load_album_artwork(self.track(os.path.join(self.dir, 'empty'))))
        self.assertIsNone(self.p.current_artwork_image)

    def test_read_error_clears_image(self):
        self.p._load_album_artwork(self.track(os.path.join(self.dir, 'a'), (600, 600)))
        self.open_audio.side_effect = OSError('gone')
        self.p._load_album_artwork(self.track(os.path.join(self.dir, 'a')))
        self.assertIsNone(self.p.current_artwork_image)

    def test_theme_placeholder_leaves_image(self):
        self.p._load_album_artwork(self.track(os.path.join(self.dir, 'a'), (600, 600)))
        kept = self.p.current_artwork_image
        self.p._show_placeholder_artwork()          # what apply_theme and Stop call
        self.assertIs(self.p.current_artwork_image, kept)


class BundleDirTests(unittest.TestCase):
    def test_bundle_dir_frozen_and_source(self):
        here = Path('X:/app')
        with unittest.mock.patch.object(sys, 'frozen', True, create=True), \
                unittest.mock.patch.object(sys, '_MEIPASS', 'X:/bundle', create=True):
            self.assertEqual(music_player.bundle_dir(here), Path('X:/bundle'))
        with unittest.mock.patch.object(sys, 'frozen', False, create=True):
            self.assertEqual(music_player.bundle_dir(here), here)


if __name__ == '__main__':
    unittest.main()
