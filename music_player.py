"""
Custom Shuffle Music Player V2 - Unified Metadata Edition

V2 CHANGES (2025-01):
- Unified metadata system: All 8,812 tracks tracked equally
- Single XML file: music_player_metadata.xml (no iTunes dependency)
- Fresh start: Build metadata from actual usage
- Simpler codebase: Removed ~200 lines of dual-system complexity
- All tracks: play_count, skip_count, rating, loved, last_played, date_added

FEATURES:
- Smart Shuffle v2 with novelty curve fix (0-play tracks = 0.5, not 1.0)
- Sound engine: FFmpeg decodes every format; crossfade/smart transitions, 10-band EQ, loudness leveling, gapless
- Media keys: F9-F11 global (volume); F5-F8 + hardware dial arrive as WM_APPCOMMAND from fnlock, even unfocused
- Search & filter (8K+ library)
- Up Next queue system
- Album artwork display
- Path-based metadata parsing for M4P Downloads FLACs
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import re
import queue
from collections import deque
from shuffle_core import CustomShuffleAlgorithm
import playlist_queue
import appcommand
import audio_settings
import library_index
import listening_log
import transitions
from audio_engine import AudioEngine, PlayItem
from audio_backend import FfmpegDecoder, PygameChannel, analyze_file
from eq import BUILTIN_PRESETS, filter_chain, parse_eqf
from sound_ui import EqualizerWindow, SoundDialog
from loudness import album_gain_db, leveling_gain_db
from loudness_service import LoudnessService
from icon_utils import make_icon_images
from mutagen import File as MutagenFile
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.easymp4 import EasyMP4
from mutagen.flac import FLAC

# Suppress pygame pkg_resources deprecation warning
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning, module="pkg_resources")

import pygame
import threading
import time
from datetime import datetime
import sys
import json
from pathlib import Path
import subprocess  # For calling FFmpeg
import hashlib  # For creating unique cache filenames
from PIL import Image, ImageTk  # For album artwork
import io
import struct
import plistlib  # For parsing iTunes XML library
from urllib.parse import unquote, urlparse  # For decoding iTunes file URLs


# =============================================================================
# Constants
# =============================================================================
MIN_DISK_SPACE_MB = 50  # Minimum disk space for cache operations
MIN_DISK_SPACE_PRECONV_MB = 500  # Minimum disk space for pre-conversion
MIN_VALID_CACHE_SIZE = 1024  # Minimum bytes for valid converted file
END_DETECTION_BUFFER_SEC = 2.0  # Buffer seconds for track end detection
VOLUME_SYNC_INTERVAL_SEC = 2.0  # How often to sync volume slider with system
METADATA_SAVE_DEBOUNCE_SEC = 0.1  # Debounce delay for metadata saves
HISTORY_SAVE_DEBOUNCE_MS = 4000  # Debounce delay for listening-history saves
SEARCH_DEBOUNCE_MS = 100  # Debounce delay for search filtering
ALBUMS_SEARCH_OPEN_CAP = 50   # Albums search: at most this many albums open with their matching songs
TYPE_JUMP_RESET_MS = 800      # Albums tab type-to-jump: typing pause that starts a new prefix
MAIN_CALLS_POLL_MS = 20  # How often the main thread runs callbacks posted by worker threads
# The settings each window edits. Each emits its whole snapshot from when it opened, so only these
# keys are taken from it; the rest may be stale if the other window changed them meanwhile.
SOUND_DIALOG_KEYS = ('transition_mode', 'transition_seconds', 'leveling_enabled')
EQ_WINDOW_KEYS = ('eq_enabled', 'eq_preset', 'eq_bands_db', 'eq_preamp_db')
ARTWORK_SIZE = 500  # Album artwork thumbnail size in pixels
ICON_SIZES = (16, 32, 48, 64, 128, 256)  # Window/taskbar icon sizes to generate


def open_audio(path, easy=False):
    """mutagen.File, trusting the file's content when its extension lies.

    mutagen weighs a .flac extension above the header, so MP4 audio saved with a .flac name
    fails to open there and the track would lose its duration, tags and artwork.
    """
    try:
        return MutagenFile(path, easy=easy)
    except Exception:
        with open(path, 'rb') as fh:
            header = fh.read(12)
        if header[4:8] == b'ftyp':
            return EasyMP4(path) if easy else MP4(path)
        raise


def _mp4_boxes(fh, start, end):
    """(type, payload_start, box_end) for each MP4 box between start and end; stops at a torn box."""
    pos = start
    while pos + 8 <= end:
        fh.seek(pos)
        size, typ = struct.unpack('>I4s', fh.read(8))
        payload = pos + 8
        if size == 1:
            size = struct.unpack('>Q', fh.read(8))[0]
            payload = pos + 16
        elif size == 0:
            size = end - pos
        if size < payload - pos or pos + size > end:
            return
        yield typ, payload, pos + size
        pos += size


def fragmented_mp4_duration(path):
    """Seconds from a fragmented MP4's mvex/mehd box, or None.

    mutagen reports 0 for fragmented (DASH) MP4 because the main header has no samples; the
    total length is in mehd, in mvhd's timescale.
    """
    try:
        with open(path, 'rb') as fh:
            end = os.fstat(fh.fileno()).st_size
            moov = next(((p, e) for t, p, e in _mp4_boxes(fh, 0, end) if t == b'moov'), None)
            if moov is None:
                return None
            timescale = fragment = None
            for typ, p, e in _mp4_boxes(fh, *moov):
                if typ == b'mvhd' and e - p >= 24:
                    fh.seek(p)
                    v = fh.read(1)[0]
                    fh.seek(p + (20 if v == 1 else 12))
                    timescale = struct.unpack('>I', fh.read(4))[0]
                elif typ == b'mvex':
                    for t2, p2, e2 in _mp4_boxes(fh, p, e):
                        if t2 == b'mehd' and e2 - p2 >= 8:
                            fh.seek(p2)
                            v = fh.read(1)[0]
                            fh.seek(p2 + 4)
                            fragment = struct.unpack('>Q' if v == 1 else '>I', fh.read(8 if v == 1 else 4))[0]
            if timescale and fragment:
                return fragment / timescale
    except (OSError, struct.error, IndexError):
        pass
    return None


class MusicTrack:
    """Represents a music track with all its metadata"""
    
    def __init__(self, filepath, meta_path=None, player_metadata=None):
        self.filepath = filepath  # Used for playback
        self.meta_path = meta_path or filepath  # Used for metadata (original or decrypted)
        self.filename = os.path.basename(self.meta_path)
        self.original_filepath = filepath  # Always initialize - prevents crashes with old cache
        
        # Default values
        self.title = self.filename
        self.artist = "Unknown Artist"
        self.album = "Unknown Album"
        self.genre = ""
        self.year = ""
        self.duration = 0
        self.rating = 0
        self.play_count = 0
        self.bpm = 0
        self.track_number = None  # Track number within album
        
        # Optional metadata (only if stored in-file). None = unknown.
        self.loved = None        # bool|None
        self.loved_source = None  # 'user' | 'migrated' | None (Task 7a)
        self.last_played = None  # epoch seconds|None
        self.skips = None        # int|None (legacy heuristic counter)
        self.date_added = None   # epoch seconds|None
        # Task 8: honest listening-outcome counters (versioned, additive - do
        # NOT reinterpret the legacy `skips`/`play_count`).
        self.qualified_play_count = None  # int|None
        self.manual_skip_count = None     # int|None
        self.last_skipped_at = None       # epoch seconds|None
        
        self._load_metadata()
        
        # Fallback: Parse path/filename for M4P Downloads files with missing tags
        if "m4p downloads" in self.filepath.lower():
            self._parse_m4p_path_metadata()
        
        # V2: Apply player metadata if provided (takes precedence over file metadata)
        if player_metadata:
            self._apply_player_metadata(player_metadata)
    
    def _apply_player_metadata(self, metadata):
        """Apply metadata from player metadata file"""
        self.play_count = metadata.get('play_count', self.play_count)
        self.skips = metadata.get('skip_count', self.skips)
        self.rating = metadata.get('rating', self.rating)
        self.bpm = metadata.get('bpm', self.bpm)
        
        # Last played - handle datetime object or epoch
        last_played = metadata.get('last_played')
        if last_played:
            if hasattr(last_played, 'timestamp'):
                self.last_played = int(last_played.timestamp())
            elif isinstance(last_played, (int, float)):
                self.last_played = int(last_played)
        
        # Date added - handle datetime object or epoch
        date_added = metadata.get('date_added')
        if date_added:
            if hasattr(date_added, 'timestamp'):
                self.date_added = int(date_added.timestamp())
            elif isinstance(date_added, (int, float)):
                self.date_added = int(date_added)
        
        # Loved status (tri-state) + provenance.  A None in the player metadata
        # means "no opinion recorded" - don't let it clobber a real loved tag
        # already read from the file itself.
        loved = metadata.get('loved')
        if loved is not None:
            self.loved = loved
            self.loved_source = metadata.get('loved_source') or 'user'

        # Task 8 listening-outcome counters
        for attr, key in (('qualified_play_count', 'qualified_play_count'),
                          ('manual_skip_count', 'manual_skip_count'),
                          ('last_skipped_at', 'last_skipped_at')):
            v = metadata.get(key)
            if v is not None:
                setattr(self, attr, v)

    def _load_metadata(self):
        """Load metadata from the meta_path"""
        try:
            audio = open_audio(self.meta_path, easy=True)
            
            if audio is None:
                return
            
            # Common tags across formats
            self.title = self._get_tag(audio, 'title') or self.filename
            self.artist = self._get_tag(audio, 'artist') or "Unknown Artist"
            self.album = self._get_tag(audio, 'album') or "Unknown Album"
            self.genre = self._get_tag(audio, 'genre') or ""
            self.year = self._get_tag(audio, 'date') or ""
            
            # Track number (may be "5" or "5/12" format)
            track_num = self._get_tag(audio, 'tracknumber')
            if track_num:
                self.track_number = track_num  # Keep original format (string)
            
            # Get duration
            if hasattr(audio.info, 'length'):
                self.duration = int(audio.info.length)
            if not self.duration and isinstance(audio, MP4):
                # fragmented (DASH) MP4: mutagen sees no samples, the length is in mvex/mehd
                self.duration = int(fragmented_mp4_duration(self.meta_path) or 0)
            
            # Try to get additional metadata from full tags
            try:
                full_audio = open_audio(self.meta_path)
                
                # MP3 specific tags
                if isinstance(full_audio, MP3):
                    # Rating (POPM frame)
                    if 'POPM:Windows Media Player 9 Series' in full_audio:
                        rating_data = full_audio['POPM:Windows Media Player 9 Series']
                        self.rating = rating_data.rating
                    
                    # Play count
                    if 'PCNT' in full_audio:
                        self.play_count = int(full_audio['PCNT'].count)
                    
                    # BPM
                    if 'TBPM' in full_audio:
                        try:
                            self.bpm = int(str(full_audio['TBPM']))
                        except:
                            pass
                    
                    # Additional: scan custom text frames for love/last played/skips/date added
                    try:
                        if full_audio.tags:
                            for key, frame in full_audio.tags.items():
                                if not key.startswith("TXXX"):
                                    continue

                                try:
                                    desc = (getattr(frame, "desc", "") or "").strip().lower()
                                    texts = getattr(frame, "text", []) or []
                                    val = self._to_text(texts[0]) if texts else ""
                                    val_l = val.strip().lower()
                                except (AttributeError, IndexError, TypeError):
                                    continue

                                # Loved
                                if self.loved is None and ("love" in desc or "loved" in desc):
                                    self.loved = val_l in ("1", "true", "yes", "y", "on")

                                # Last played
                                if self.last_played is None and ("last played" in desc or "lastplayed" in desc):
                                    self.last_played = self._parse_epoch(val, default=None)

                                # Skips
                                if self.skips is None and ("skip" in desc or "skipped" in desc):
                                    self.skips = self._parse_int(val, default=None)

                                # Date added
                                if self.date_added is None and ("date added" in desc or "dateadded" in desc or desc == "added"):
                                    self.date_added = self._parse_epoch(val, default=None)
                    except:
                        pass
                
                # MP4/M4A specific tags
                elif isinstance(full_audio, MP4):
                    # Rating
                    if '----:com.apple.iTunes:rating' in full_audio:
                        self.rating = int(full_audio['----:com.apple.iTunes:rating'][0])
                    
                    # Play count
                    if '----:com.apple.iTunes:play count' in full_audio:
                        self.play_count = int(full_audio['----:com.apple.iTunes:play count'][0])
                    
                    # BPM
                    if 'tmpo' in full_audio:
                        self.bpm = int(full_audio['tmpo'][0])
                    
                    # Additional: scan tags (including freeform atoms) heuristically
                    try:
                        tags = full_audio.tags or {}
                        for k, v in tags.items():
                            ktxt = self._to_text(k).strip().lower()

                            # MP4 tags often store lists
                            val0 = v[0] if isinstance(v, list) and v else v
                            vtxt = self._to_text(val0).strip()
                            vtxt_l = vtxt.lower()

                            # Loved
                            if self.loved is None and ("love" in ktxt or "loved" in ktxt):
                                self.loved = vtxt_l in ("1", "true", "yes", "y", "on")

                            # Last played
                            if self.last_played is None and ("last played" in ktxt or "lastplayed" in ktxt):
                                self.last_played = self._parse_epoch(vtxt, default=None)

                            # Skips
                            if self.skips is None and ("skip" in ktxt or "skipped" in ktxt):
                                self.skips = self._parse_int(vtxt, default=None)

                            # Date added
                            if self.date_added is None and ("date added" in ktxt or "dateadded" in ktxt):
                                self.date_added = self._parse_epoch(vtxt, default=None)
                    except:
                        pass
                
                # FLAC specific tags (Vorbis comments)
                elif isinstance(full_audio, FLAC):
                    # FLAC uses simple key-value Vorbis comments
                    try:
                        # Play count
                        if 'PLAYCOUNT' in full_audio:
                            self.play_count = self._parse_int(full_audio['PLAYCOUNT'][0], default=0)
                        
                        # Last played
                        if 'LASTPLAYED' in full_audio:
                            self.last_played = self._parse_epoch(full_audio['LASTPLAYED'][0], default=None)
                        
                        # Skip count
                        if 'SKIPCOUNT' in full_audio:
                            self.skips = self._parse_int(full_audio['SKIPCOUNT'][0], default=None)
                        
                        # Loved
                        if 'LOVED' in full_audio:
                            loved_val = self._to_text(full_audio['LOVED'][0]).strip()
                            self.loved = loved_val == '1'
                    except:
                        pass
            except:
                pass
                
        except Exception as e:
            # Suppress FLAC validation errors for corrupted/mislabeled files
            error_msg = str(e).lower()
            if "not a valid flac file" in error_msg:
                # Silently skip - these files will still load with basic filename metadata
                pass
            else:
                print(f"Error loading metadata for {self.meta_path}: {e}")
    
    def _parse_m4p_path_metadata(self):
        r"""
        Parse artist/album from M4P Downloads path structure when tags are missing.
        Expected structure: M4P Downloads\{Artist}\{Album}\{Artist} - {Album} - {Track} {Title}.ext
        Example: M4P Downloads\Future\FUTURE\Future - FUTURE - 01-10 Scrape.flac
        """
        try:
            # Only parse if tags are actually missing
            if self.artist != "Unknown Artist" and self.album != "Unknown Album":
                return
            
            # Split path into parts (cross-platform)
            # Note: Path is already imported at module level
            parts = Path(self.filepath).parts
            
            # Find "M4P Downloads" in the path
            try:
                m4p_idx = next(i for i, p in enumerate(parts) if p.lower() == "m4p downloads")
            except StopIteration:
                return
            
            # Extract artist and album from directory structure
            # Structure: M4P Downloads\{Artist}\{Album}\filename
            if len(parts) > m4p_idx + 2:
                path_artist = parts[m4p_idx + 1].strip()
                path_album = parts[m4p_idx + 2].strip()
                
                # Only use if not empty
                if path_artist and self.artist == "Unknown Artist":
                    self.artist = path_artist
                if path_album and self.album == "Unknown Album":
                    self.album = path_album
            
            # Try to parse filename: {Artist} - {Album} - {Track} {Title}
            filename_no_ext = os.path.splitext(self.filename)[0]
            parts_filename = filename_no_ext.split(' - ')
            
            if len(parts_filename) >= 3:
                # Format: Artist - Album - Track Title
                file_artist = parts_filename[0].strip()
                file_album = parts_filename[1].strip()
                
                # Prefer filename over directory if both available
                if file_artist and self.artist == "Unknown Artist":
                    self.artist = file_artist
                if file_album and self.album == "Unknown Album":
                    self.album = file_album
                
                # Extract track number and title from remaining parts
                track_title = parts_filename[2].strip()
                if self.title == self.filename:  # Only if we don't have a better title
                    # Remove track number prefix (e.g., "01-10 Scrape" -> "Scrape")
                    title_match = re.match(r'^[\d\-]+\s+(.+)', track_title)
                    if title_match:
                        self.title = title_match.group(1).strip()
                    else:
                        self.title = track_title
        except Exception as e:
            # Silently fail - we still have the filename as fallback
            pass
    
    def _get_tag(self, audio, tag_name):
        """Safely get a tag value"""
        try:
            value = audio.get(tag_name, [''])[0]
            return str(value) if value else ''
        except (KeyError, IndexError, TypeError):
            return ''
    
    def _to_text(self, v):
        """Best-effort convert tag value to a plain string."""
        try:
            if v is None:
                return ""
            if isinstance(v, (bytes, bytearray)):
                return v.decode("utf-8", errors="ignore")
            return str(v)
        except (AttributeError, TypeError, UnicodeDecodeError):
            return ""

    def _parse_int(self, s, default=None):
        """Parse int. Returns default (None recommended) if not parseable."""
        try:
            return int(float(str(s).strip()))
        except (ValueError, TypeError, AttributeError):
            return default

    def _parse_epoch(self, s, default=None):
        """
        Best-effort epoch parser:
          - epoch seconds (int/float)
          - epoch millis
          - ISO-ish timestamps (rare)
        Returns default (None recommended) on failure.
        """
        txt = str(s).strip() if s is not None else ""
        if not txt:
            return default

        # epoch seconds/millis
        try:
            n = float(txt)
            if n > 10_000_000_000:  # likely ms
                n = n / 1000.0
            return int(n)
        except (ValueError, TypeError):
            pass

        # ISO-ish timestamp (rare)
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                # keep minimal; clamp length
                return int(datetime.strptime(txt[:19], fmt).timestamp())
            except ValueError:
                continue

        return default
    
    def format_duration(self):
        """Format duration as MM:SS"""
        minutes = int(self.duration // 60)
        seconds = int(self.duration % 60)
        return f"{minutes}:{seconds:02d}"

    def to_dict(self):
        """Serialize track to JSON-safe dictionary"""
        return {
            'filepath': self.filepath,
            'meta_path': self.meta_path,
            'original_filepath': getattr(self, 'original_filepath', self.filepath),
            'filename': self.filename,
            'title': self.title,
            'artist': self.artist,
            'album': self.album,
            'genre': self.genre,
            'year': self.year,
            'duration': self.duration,
            'rating': self.rating,
            'play_count': self.play_count,
            'bpm': self.bpm,
            'track_number': self.track_number,
            'loved': self.loved,
            'loved_source': self.loved_source,
            'last_played': self.last_played,
            'skips': self.skips,
            'date_added': self.date_added,
            'qualified_play_count': self.qualified_play_count,
            'manual_skip_count': self.manual_skip_count,
            'last_skipped_at': self.last_skipped_at,
        }

    @classmethod
    def from_dict(cls, data, player_metadata=None):
        """Deserialize track from dictionary (skips file I/O)"""
        # Create instance without calling __init__ to avoid file I/O
        track = cls.__new__(cls)
        track.filepath = data.get('filepath', '')
        track.meta_path = data.get('meta_path', track.filepath)
        track.original_filepath = data.get('original_filepath', track.filepath)
        track.filename = data.get('filename', os.path.basename(track.meta_path))
        track.title = data.get('title', track.filename)
        track.artist = data.get('artist', 'Unknown Artist')
        track.album = data.get('album', 'Unknown Album')
        track.genre = data.get('genre', '')
        track.year = data.get('year', '')
        track.duration = data.get('duration', 0)
        track.rating = data.get('rating', 0)
        track.play_count = data.get('play_count', 0)
        track.bpm = data.get('bpm', 0)
        track.track_number = data.get('track_number')
        track.loved = data.get('loved')
        track.loved_source = data.get('loved_source')
        track.last_played = data.get('last_played')
        track.skips = data.get('skips')
        track.date_added = data.get('date_added')
        track.qualified_play_count = data.get('qualified_play_count')
        track.manual_skip_count = data.get('manual_skip_count')
        track.last_skipped_at = data.get('last_skipped_at')

        # Apply player metadata if provided (takes precedence)
        if player_metadata:
            track._apply_player_metadata(player_metadata)

        return track

    def __repr__(self):
        return f"<Track: {self.artist} - {self.title}>"


class MusicPlayerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Music Player V2")
        self.root.geometry("550x750")
        self.root.minsize(550, 650)
        self.root.resizable(False, True)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # WM_APPCOMMAND window subclass state (set up in _setup_wm_appcommand_hook)
        self._appcmd_hwnd = None
        self._appcmd_old_proc = None
        self._appcmd_wndproc = None
        self._appcmd_set_window_long = None
        # Small deque, not a single slot: a burst of dial presses within one
        # 50ms poll window must not silently overwrite/drop earlier presses.
        self._appcmd_pending = deque()
        self._appcmd_poll_job = None

        # Music library
        self.library_tracks = []       # Full scanned library (source of truth)
        self.all_tracks = []           # Active working subset (filtered view)
        self.current_playlist = []
        self.filtered_playlist = []    # For search filtering
        self._playlist_query = ''      # Search text the Playlist tab shows (None: stale, filter again)
        self._search_job = None        # For search debouncing
        self.current_index = 0
        self.queued_count = 0          # Manually-queued tracks right after current_index
        self.is_temp_view = False      # True when current_playlist is a taken-over search view
        self.library_index = None      # Artists/albums of library_tracks (library_index.py)
        self._play_context = None      # ('album'|'artist', key) while that list plays in order

        # Recent play history for the shuffle history guard.  The persisted
        # source of truth is self._history_entries (list of shuffle_history
        # entry dicts), loaded during cache setup below; this deque is kept only
        # as a defensive default in case that load has not run yet.
        self._recent_play_history = deque(maxlen=200)  # legacy; unused once history loads

        # Debug flag: set MUSIC_SHUFFLER_DEBUG_SCAN=1 to enable scan spam (Task 8)
        self.debug_scan_logging = os.getenv('MUSIC_SHUFFLER_DEBUG_SCAN', '0') == '1'

        # Thread locks for shared mutable state
        self._metadata_lock = threading.Lock()  # Protects self.player_metadata
        self._tracks_lock = threading.Lock()  # Protects self.all_tracks during scan

        # Library stats
        self.total_tracks = 0
        self.total_artists = 0
        self.total_albums = 0
        
        # Theme settings
        self.dark_mode = True
        self.themes = {
            'dark': {
                'bg': '#1e1e1e',
                'fg': '#ffffff',
                'secondary_fg': '#b0b0b0',
                'frame_bg': '#2d2d2d',
                'button_bg': '#3d3d3d',
                'placeholder': '#404040'
            },
            'light': {
                'bg': '#ffffff',
                'fg': '#000000',
                'secondary_fg': '#666666',
                'frame_bg': '#f0f0f0',
                'button_bg': '#e0e0e0',
                'placeholder': '#d0d0d0'
            }
        }
        
        # Music directories - load from local config or use sensible defaults
        self.music_dirs = self._load_music_dirs()
        
        # V2: Unified metadata system (no more iTunes XML dependency)
        self.player_metadata = {}  # Will hold normalized_filepath -> metadata mapping
        
        # Cache file location - detect if running as exe or script
        try:
            # Check if running as PyInstaller exe
            if getattr(sys, 'frozen', False):
                # Running as compiled exe - use exe location
                self.script_dir = Path(sys.executable).parent
                print(f"Running as EXE from: {self.script_dir}")
            else:
                # Running as script - use script location
                self.script_dir = Path(__file__).parent
                print(f"Running as script from: {self.script_dir}")
        except (NameError, AttributeError, OSError):
            self.script_dir = Path.cwd()
            print(f"Using current directory: {self.script_dir}")

        # Window/taskbar icon - default (overridden per-track once artwork loads).
        # Best-effort: the app must still start if the icon file is missing/corrupt.
        self._default_icon_photos = None
        self._track_icon_photos = None
        try:
            # PyInstaller onefile builds extract bundled `datas` (assets/app_icon.ico)
            # to sys._MEIPASS at runtime, NOT next to sys.executable - script_dir is
            # the exe's own folder (correct for persistent cache/config, wrong here).
            bundle_dir = Path(getattr(sys, '_MEIPASS', self.script_dir)) if getattr(sys, 'frozen', False) else self.script_dir
            icon_path = bundle_dir / "assets" / "app_icon.ico"
            self.root.iconbitmap(default=str(icon_path))
            default_icon_source = Image.open(io.BytesIO(icon_path.read_bytes()))
            self._default_icon_photos = [
                ImageTk.PhotoImage(im) for im in make_icon_images(default_icon_source, sizes=ICON_SIZES)
            ]
            self.root.iconphoto(True, *self._default_icon_photos)
        except Exception as e:
            print(f"⚠ Could not set app icon: {e}")

        # First try: D:\iTunes Shuffler\music_shuffler_cache (next to exe/script)
        cache_dir = self.script_dir / "music_shuffler_cache"
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, IOError) as e:
            print(f"⚠ Cannot create cache in script directory: {e}")
            base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            cache_dir = base / "CustomShuffleMusicPlayer" / "music_shuffler_cache"
            try:
                cache_dir.mkdir(parents=True, exist_ok=True)
                print(f"Using fallback cache location: {cache_dir}")
            except (OSError, IOError) as e2:
                # Last resort: use temp directory with subdirectory
                import tempfile
                cache_dir = Path(tempfile.gettempdir()) / "CustomShuffleMusicPlayer" / "cache"
                try:
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    print(f"⚠ Using temporary cache location: {cache_dir}")
                    print(f"  Cache will be lost on system restart")
                except (OSError, IOError) as e3:
                    # Absolute last resort: show error and exit gracefully
                    error_msg = (
                        f"Cannot create cache directory anywhere:\n"
                        f"• Script dir: {self.script_dir / 'music_shuffler_cache'}\n"
                        f"• AppData: {base / 'CustomShuffleMusicPlayer'}\n"
                        f"• Temp: {Path(tempfile.gettempdir()) / 'CustomShuffleMusicPlayer'}\n\n"
                        f"The application cannot run without a cache directory.\n"
                        f"Please check your file system permissions."
                    )
                    messagebox.showerror("Fatal Error", error_msg)
                    sys.exit(1)
        
        self.cache_file = cache_dir / "library_cache.json"
        self.volume_pref_file = cache_dir / "volume_pref.txt"
        self.player_metadata_file = cache_dir / "music_player_metadata.xml"
        self.shuffle_config_file = cache_dir / "shuffle_config.json"
        self.shuffle_history_file = cache_dir / "shuffle_history.json"
        # Favorites store: sibling of the cache dir, NOT inside it, so
        # "Clear Cache" cannot destroy the shuffle taste signal (Task 7c).
        self.preferences_file = cache_dir.parent / "shuffler_preferences.json"
        # Sound engine: settings and the listening log sit beside the cache dir so Clear Cache keeps them;
        # the loudness cache can be rebuilt, so it lives inside the cache dir.
        self.audio_settings_file = cache_dir.parent / "audio_settings.json"
        self.listening_log_file = cache_dir.parent / "listening_log.jsonl"
        self.loudness_cache_file = cache_dir / "loudness_cache.json"
        self.audio_settings = audio_settings.load(str(self.audio_settings_file))
        print(f"Cache directory: {cache_dir}")

        # Persisted ordered listening history (Task 3) - survives restarts so
        # Smart Shuffle keeps session continuity.
        try:
            import shuffle_history as _sh
            self._history_entries = _sh.load(str(self.shuffle_history_file))
            if self._history_entries:
                print(f"✓ Loaded {len(self._history_entries)} listening-history entries")
        except Exception as e:
            print(f"⚠ Could not load shuffle history: {e}")
            self._history_entries = []
        self._history_dirty = False
        self._history_save_job = None
        self._active_algo = "Smart Shuffle"
        self._last_wrap_mono = 0.0   # cycle-wrap loop guard (Task 5)
        # Playback lifecycle, tracked independently of is_playing so a *paused*
        # track still counts as "active" for reshuffle prefix preservation.
        self._playback_state = 'stopped'   # 'playing' | 'paused' | 'stopped'
        
        # Initialize shuffle configuration
        self._init_shuffle_config()
        
        # System volume control (Windows)
        self._init_system_volume_control()
        
        # Convert cache directory
        self.convert_cache_dir = cache_dir / "convert_cache"
        try:
            # The old M4A->MP3 cache is retired (the sound engine plays M4A directly); it is no longer
            # created, only leftover temp files are cleaned up. Settings > Delete Old MP3 Cache removes it.
            
            # Cleanup orphaned temp files from crashed conversions
            # Format: {hash}.{pid}.{thread_id}.mp3
            # Only delete files older than 5 minutes to avoid interfering with other instances
            import time
            current_time = time.time()
            for temp_file in self.convert_cache_dir.glob("*.*.*.mp3"):
                try:
                    # Check if file is stale (older than 5 minutes)
                    if (current_time - temp_file.stat().st_mtime) > 300:
                        temp_file.unlink()
                        print(f"Cleaned up stale temp file: {temp_file.name}")
                except Exception:
                    # Specific exception - file might be in use or deleted by another instance
                    pass
        except:
            pass
        
        # Locate FFmpeg
        self.ffmpeg_path = self.locate_ffmpeg()
        
        # Metadata save worker (single background thread to avoid thread spam)
        self._metadata_save_queue = queue.Queue()
        self._metadata_save_stop = threading.Event()
        self._metadata_save_thread = None
        
        # Playback state
        self.is_playing = False
        self.current_position = 0
        # Task 8: audible-time tracker for the current playback instance
        self._pb_tracker = None
        self._pb_track = None
        self._pb_seq = 0
        self.audio_available = False
        
        
        # Sound engine: FFmpeg decodes every song into float32 48 kHz chunks that play on two reserved
        # pygame channels (audio_engine.py). Transitions, EQ and leveling ride on this path.
        self._engine = None
        self._engine_item = None
        self._engine_thread = None
        self._engine_lock = threading.RLock()
        self._engine_stop = threading.Event()
        # Next-song preparation and crossfade bookkeeping (plan Task 10)
        self._next_item = None       # PlayItem prepared to follow the current song
        self._next_tr = 'cut'        # how it will follow: gapless | crossfade | smart
        self._outgoing_item = None   # during an overlap: the song fading out
        self._outgoing_tr = 'cut'
        self._pb_outgoing = None     # (tracker, track) of the outgoing song until its audio stops
        self._sound_apply_job = None  # debounce for Sound/Equalizer window changes
        # Worker threads (sound engine, loudness) post zero-arg callables here; the main thread drains
        # it. The engine thread emits while holding _engine_lock, so it must never wait on Tk.
        self._main_calls = queue.SimpleQueue()
        self._main_calls_job = None
        self._play_token = 0          # bumped by every pick/Stop/Pause: a stale pending start is dropped
        self._start_pending = False   # a picked song is waiting for its loudness before it starts
        self._sound_dialog = None
        self._eq_window = None
        try:
            pygame.mixer.init(frequency=48000, size=32, channels=2, buffer=2048)
            pygame.mixer.set_reserved(2)
            self._engine = AudioEngine(
                time.monotonic,
                (PygameChannel(pygame.mixer.Channel(0)), PygameChannel(pygame.mixer.Channel(1))),
                self._open_decoder, self._engine_emit)
            self.audio_available = True
        except Exception as e:
            print(f"⚠ Audio initialization failed: {e}")
            messagebox.showerror(
                "Audio Unavailable",
                f"Could not initialize audio device:\n{e}\n\n"
                "The app will continue but playback will be disabled.\n\n"
                "Common causes:\n"
                "• No audio device available\n"
                "• Audio device in exclusive mode\n"
                "• Running over Remote Desktop"
            )
        
        self.setup_ui()
        self.setup_keyboard_shortcuts()
        self._set_dark_title_bar()
        self.check_ffmpeg()
        # Loudness analysis (leveling + smart transitions); its cache loads off the main thread.
        self._loudness = LoudnessService(self.ffmpeg_path or "ffmpeg", str(self.loudness_cache_file),
                                         analyze=self._analyze_loudness) \
            if self._engine is not None else None
        self._drain_main_calls()
        self._start_engine_loop()
        
        # V2: Load unified player metadata
        self._load_player_metadata()

        # Task 7c: favorites live in a durable store outside the cache; they are
        # authoritative for loved/loved_source and survive a cache wipe.
        try:
            import preferences as _pf
            self._favorites = _pf.load(str(self.preferences_file))
            with self._metadata_lock:
                _pf.merge_into_metadata(self._favorites, self.player_metadata)
            if self._favorites:
                print(f"✓ Loaded {len(self._favorites)} favorites")
        except Exception as e:
            print(f"⚠ Could not load favorites: {e}")
            self._favorites = {}

        # Auto-load library on startup
        self.root.after(100, self.scan_library)
    
    def _load_music_dirs(self):
        """Load music directories from config.local.json, falling back to platform defaults."""
        config_path = Path(__file__).parent / "config.local.json" if not getattr(sys, 'frozen', False) else Path(sys.executable).parent / "config.local.json"
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    cfg = json.load(f)
                dirs = cfg.get("music_dirs", [])
                if dirs:
                    return dirs
            except (json.JSONDecodeError, OSError) as e:
                print(f"Warning: could not read {config_path}: {e}")
        # Platform-aware defaults
        home = Path.home()
        defaults = [
            str(home / "Music" / "iTunes" / "iTunes Media" / "Music"),
            str(home / "Music" / "M4P Downloads"),
        ]
        return defaults

    def setup_ui(self):
        # Custom menu bar with search (replaces native menubar)
        menubar_frame = ttk.Frame(self.root, padding=(10, 6))
        menubar_frame.grid(row=0, column=0, sticky=(tk.W, tk.E))
        menubar_frame.columnconfigure(1, weight=1)  # Spacer between menus and search
        
        # Left side: Menu buttons
        menu_container = ttk.Frame(menubar_frame)
        menu_container.grid(row=0, column=0, sticky=tk.W)
        
        # Shuffle menu
        shuffle_btn = ttk.Menubutton(menu_container, text="Shuffle")
        shuffle_menu = tk.Menu(shuffle_btn, tearoff=0)
        shuffle_btn['menu'] = shuffle_menu
        shuffle_btn.grid(row=0, column=0, padx=(0, 5))
        
        shuffle_menu.add_command(label="Smart Shuffle", command=lambda: self.apply_shuffle("Smart Shuffle"))
        shuffle_menu.add_command(label="Truly Random", command=lambda: self.apply_shuffle("Truly Random"))
        
        # Settings menu
        settings_btn = ttk.Menubutton(menu_container, text="Settings")
        settings_menu = tk.Menu(settings_btn, tearoff=0)
        settings_btn['menu'] = settings_menu
        settings_btn.grid(row=0, column=1, padx=(0, 5))
        
        settings_menu.add_command(label="Library Info", command=self.show_library_info)
        settings_menu.add_separator()
        settings_menu.add_command(label="Shuffle Settings", command=self.edit_shuffle_settings)
        settings_menu.add_command(label="Sound…", command=self.open_sound_dialog)
        settings_menu.add_command(label="Equalizer…", command=self.open_equalizer)
        settings_menu.add_command(label="Import iTunes Ratings as Favorites…", command=self.migrate_ratings_to_favorites)
        settings_menu.add_separator()
        settings_menu.add_command(label="Toggle Dark/Light Mode", command=self.toggle_theme)
        settings_menu.add_separator()
        settings_menu.add_command(label="Scan Library", command=self.scan_library)
        settings_menu.add_command(label="Force Full Rescan", command=self.force_rescan)
        settings_menu.add_command(label="Change Folders", command=self.change_folders)
        settings_menu.add_separator()
        settings_menu.add_command(label="Set FFmpeg Path", command=self.set_ffmpeg_path)
        settings_menu.add_command(label="Clear Cache", command=self.clear_cache)
        settings_menu.add_command(label="Delete Old MP3 Cache…", command=self.delete_old_mp3_cache)
        settings_menu.add_command(label="Clear Metadata (Reset All)", command=self.clear_metadata)
        settings_menu.add_separator()
        settings_menu.add_command(label="Show Library Diagnostics", command=self.show_diagnostics)
        settings_menu.add_command(label="Show Metadata", command=self.show_metadata)
        settings_menu.add_command(label="Check File Path", command=self.check_file_path)
        settings_menu.add_separator()
        settings_menu.add_command(label="Filter Library", command=self.filter_library)
        settings_menu.add_command(label="Reload Library", command=self.scan_library)
        settings_menu.add_separator()
        settings_menu.add_command(label="View Up Next Queue", command=self.view_up_next)
        settings_menu.add_command(label="Clear Up Next Queue", command=self.clear_up_next)
        
        # Help menu
        help_btn = ttk.Menubutton(menu_container, text="Help")
        help_menu = tk.Menu(help_btn, tearoff=0)
        help_btn['menu'] = help_menu
        help_btn.grid(row=0, column=2)
        
        help_menu.add_command(label="Keyboard Shortcuts", command=self.show_shortcuts)
        help_menu.add_command(label="Test Hotkeys", command=self.test_hotkeys)
        help_menu.add_command(label="About", command=self.show_about)
        
        # Right side: Search box
        search_frame = ttk.Frame(menubar_frame)
        search_frame.grid(row=0, column=2, sticky=tk.E)
        
        ttk.Label(search_frame, text="🔍").grid(row=0, column=0, padx=(0, 5))
        
        self.search_var = tk.StringVar()
        self.search_var.trace_add('write', lambda *args: self._schedule_filter())
        
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=28)
        self.search_entry.grid(row=0, column=1)
        
        self.clear_search_btn = ttk.Button(search_frame, text="✕", width=3, 
                                           command=self.clear_search)
        self.clear_search_btn.grid(row=0, column=2, padx=(5, 0))
        
        # Keyboard shortcuts for search
        self.search_entry.bind("<Escape>", lambda e: (self.clear_search(), "break"))
        self.search_entry.bind("<Return>", lambda e: self._play_first_result())
        self.root.bind("<Control-f>", lambda e: (self.search_entry.focus(), self.search_entry.select_range(0, tk.END)))
        
        # Main container
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        main_frame.columnconfigure(0, weight=1)
        
        # Now playing section
        now_playing_frame = ttk.Frame(main_frame, padding="10")
        now_playing_frame.grid(row=0, column=0, sticky=(tk.W, tk.E))
        
        # Album artwork
        self.artwork_label = ttk.Label(now_playing_frame)
        self.artwork_label.pack(pady=(0, 6))
        self.current_artwork = None
        
        # Progress bar and time
        progress_frame = ttk.Frame(now_playing_frame)
        progress_frame.pack(fill=tk.X, padx=6, pady=(0, 10))
        
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, pady=(0, 4))
        self.progress_bar.bind('<Button-1>', self.on_progress_click)
        
        self.time_label = ttk.Label(progress_frame, text="0:00 / 0:00", font=('Arial', 8))
        self.time_label.pack()
        
        # Info block
        self.info_frame = ttk.Frame(now_playing_frame)
        self.info_frame.pack(pady=(6, 0), fill=tk.X)
        
        self.now_playing_label = ttk.Label(self.info_frame, text="Not Playing", 
                                          font=('Arial', 14, 'bold'))
        self.now_playing_label.pack()
        
        self.now_playing_artist = ttk.Label(self.info_frame, text="", 
                                           font=('Arial', 10))
        self.now_playing_artist.pack()
        
        # Transport controls
        self.transport_frame = ttk.Frame(now_playing_frame, padding="10")
        
        self.prev_button = ttk.Button(self.transport_frame, text="⏮", command=self.previous_track, width=6)
        self.prev_button.grid(row=0, column=0, padx=5)
        
        self.play_pause_button = ttk.Button(self.transport_frame, text="▶ / ⏸", command=self.toggle_play_pause, width=8)
        self.play_pause_button.grid(row=0, column=1, padx=5)
        
        self.stop_button = ttk.Button(self.transport_frame, text="⏹", command=self.stop, width=6)
        self.stop_button.grid(row=0, column=2, padx=5)
        
        self.next_button = ttk.Button(self.transport_frame, text="⏭", command=self.next_track, width=6)
        self.next_button.grid(row=0, column=3, padx=5)
        
        # Love button - using tk.Button for color support
        self.love_button = tk.Button(self.transport_frame, text="♡", command=self.toggle_loved, 
                                      width=3, font=('Arial', 14), foreground="gray")
        self.love_button.grid(row=0, column=4, padx=(15, 5))
        self.love_button.grid_remove()  # Hidden by default
        
        # Volume control
        ttk.Label(self.transport_frame, text="🔊", font=('Arial', 10)).grid(row=0, column=5, padx=(20, 5))
        
        # Get current system volume or fall back to saved preference
        current_volume = self.get_system_volume()
        if current_volume is None:
            # Fallback to saved pygame volume if system volume unavailable
            current_volume = 70
            try:
                if self.volume_pref_file.exists():
                    with open(self.volume_pref_file, 'r') as f:
                        current_volume = float(f.read().strip())
            except:
                pass
        
        self._updating_volume = False  # Flag to prevent callback loops
        self.volume_var = tk.DoubleVar(value=current_volume)
        self.volume_slider = ttk.Scale(self.transport_frame, from_=0, to=100, 
                                       variable=self.volume_var, 
                                       orient=tk.HORIZONTAL, length=180,
                                       command=self.on_volume_change)
        self.volume_slider.grid(row=0, column=6, padx=5)
        
        # Set initial volume
        if self.volume_interface:
            # System volume control available
            self.set_system_volume(current_volume)
        elif self.audio_available and self._engine is not None:
            # No Windows volume control: the engine's master volume is the fallback
            self._engine.set_master_volume(current_volume / 100.0)
        
        self.transport_frame.place_forget()
        self._transport_hide_job = None
        
        # Playlist and Albums tabs
        self.playlist_notebook = ttk.Notebook(main_frame)
        self.playlist_notebook.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(5, 10))
        main_frame.rowconfigure(1, weight=1)
        playlist_frame = ttk.Frame(self.playlist_notebook, padding="5")
        playlist_frame.columnconfigure(0, weight=1)
        playlist_frame.rowconfigure(1, weight=1)  # Treeview is on row 1
        self.playlist_notebook.add(playlist_frame, text="Playlist")
        
        columns = ('Artist', 'Title', 'Album', 'Duration')
        self.playlist_tree = ttk.Treeview(playlist_frame, columns=columns, 
                                         show='tree headings', height=5)
        
        self.playlist_tree.heading('#0', text='#')
        self.playlist_tree.heading('Artist', text='Artist')
        self.playlist_tree.heading('Title', text='Title')
        self.playlist_tree.heading('Album', text='Album')
        self.playlist_tree.heading('Duration', text='Duration')
        
        self.playlist_tree.column('#0', width=50, stretch=False)
        self.playlist_tree.column('Artist', width=120)
        self.playlist_tree.column('Title', width=180)
        self.playlist_tree.column('Album', width=120)
        self.playlist_tree.column('Duration', width=50, stretch=False)
        
        scrollbar = ttk.Scrollbar(playlist_frame, orient=tk.VERTICAL, 
                                 command=self.playlist_tree.yview)
        self.playlist_tree.configure(yscrollcommand=scrollbar.set)
        
        self.playlist_tree.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scrollbar.grid(row=1, column=1, sticky=(tk.N, tk.S))
        
        self.playlist_tree.bind('<Double-1>', self.on_track_double_click)
        
        # Up Next context menu
        self.playlist_menu = tk.Menu(self.root, tearoff=0)
        self.playlist_menu.add_command(label="Play Next", command=self._ctx_play_next)
        self.playlist_menu.add_command(label="Add to Up Next", command=self._ctx_add_up_next)
        self.playlist_menu.add_separator()
        self.playlist_menu.add_command(label="Play album from start",
                                       command=lambda: self._ctx_album_action('album'))
        self.playlist_menu.add_command(label="Play artist", command=lambda: self._ctx_album_action('artist'))
        self.playlist_menu.add_command(label="Add album to Up Next",
                                       command=lambda: self._ctx_album_action('queue'))

        # Right-click support (Windows and Mac)
        self.playlist_tree.bind("<Button-3>", self._on_playlist_right_click)  # Windows/Linux
        self.playlist_tree.bind("<Control-Button-1>", self._on_playlist_right_click)  # Mac

        self.albums_tab = ttk.Frame(self.playlist_notebook, padding="5")
        self.playlist_notebook.add(self.albums_tab, text="Albums")
        self._setup_albums_tab(self.albums_tab)
        # The one search box filters whichever tab is showing.
        self.playlist_notebook.bind('<<NotebookTabChanged>>', lambda e: self._run_filter())
        
        # Status bar
        self.status_label = ttk.Label(main_frame, text="Ready", 
                                     font=('Arial', 8), foreground='gray')
        self.status_label.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=(5, 0))
        
        self.start_update_thread()
        
        # Hover bindings
        def bind_hover(widget):
            widget.bind("<Enter>", lambda e: self._show_transport())
            widget.bind("<Leave>", lambda e: self._schedule_hide_transport())
        
        bind_hover(self.info_frame)
        bind_hover(self.now_playing_label)
        bind_hover(self.now_playing_artist)
        bind_hover(self.transport_frame)
        for child in self.transport_frame.winfo_children():
            bind_hover(child)
        
        self.apply_theme()
    
    def apply_theme(self):
        """Apply current theme to all UI elements"""
        theme = self.themes['dark'] if self.dark_mode else self.themes['light']
        
        self.root.configure(bg=theme['bg'])
        
        style = ttk.Style()
        style.theme_use('default')
        
        style.configure('TFrame', background=theme['bg'])
        style.configure('TLabelframe', background=theme['bg'], foreground=theme['fg'])
        style.configure('TLabelframe.Label', background=theme['bg'], foreground=theme['fg'])
        style.configure('TLabel', background=theme['bg'], foreground=theme['fg'])
        style.configure('TButton', background=theme['button_bg'], foreground=theme['fg'])
        style.map('TButton', background=[('active', theme['frame_bg'])])
        
        style.configure('Treeview',
                       background=theme['frame_bg'],
                       foreground=theme['fg'],
                       fieldbackground=theme['frame_bg'])
        style.configure('Treeview.Heading',
                       background=theme['button_bg'],
                       foreground=theme['fg'])
        style.map('Treeview', background=[('selected', theme['button_bg'])])

        style.configure('TNotebook', background=theme['bg'], borderwidth=0)
        style.configure('TNotebook.Tab', background=theme['button_bg'], foreground=theme['fg'],
                        padding=(12, 3))
        style.map('TNotebook.Tab',
                  background=[('selected', theme['frame_bg']), ('active', theme['frame_bg'])],
                  foreground=[('selected', theme['fg'])])

        style.configure('TProgressbar',
                       background=theme['fg'],
                       troughcolor=theme['frame_bg'],
                       bordercolor=theme['button_bg'],
                       lightcolor=theme['fg'],
                       darkcolor=theme['fg'])
        
        self._show_placeholder_artwork()
        
        if hasattr(self, 'status_label'):
            self.status_label.configure(foreground=theme['secondary_fg'])
        
        if hasattr(self, 'time_label'):
            self.time_label.configure(foreground=theme['secondary_fg'])
    
    def toggle_theme(self):
        """Toggle between dark and light mode"""
        self.dark_mode = not self.dark_mode
        self.apply_theme()
        self._set_dark_title_bar()  # Update title bar to match theme
        mode = "Dark" if self.dark_mode else "Light"
        print(f"Switched to {mode} mode")
    
    
    
    def _init_shuffle_config(self):
        """Initialize shuffle configuration from the shuffle_core defaults."""
        from shuffle_core import normalize_shuffle_config

        # Single source of truth lives in shuffle_core.
        self.shuffle_config = normalize_shuffle_config(None)

        # Load and validate saved configuration if it exists
        if self.shuffle_config_file.exists():
            try:
                import json
                with open(self.shuffle_config_file, 'r') as f:
                    saved_config = json.load(f)
                if not isinstance(saved_config, dict):
                    raise ValueError("shuffle config is not a JSON object")
                # normalize_shuffle_config clamps/validates every field and
                # falls back per-key for malformed persisted values.
                self.shuffle_config = normalize_shuffle_config(saved_config)
                print("✓ Loaded shuffle configuration")
            except Exception as e:
                print(f"⚠ Could not load shuffle config: {e}")
                print("  Using default settings")
    
    def _save_shuffle_config(self):
        """Save current shuffle configuration"""
        try:
            import json
            with open(self.shuffle_config_file, 'w') as f:
                json.dump(self.shuffle_config, f, indent=2)
            return True
        except Exception as e:
            print(f"Error saving shuffle config: {e}")
            return False

    # ---- Persisted listening history (Task 3) -----------------------------

    def _record_history_start(self, track):
        """Append a playback start to the history and schedule a debounced save."""
        try:
            import shuffle_history as _sh
            entry = _sh.make_entry(
                track.filepath,   # matches what smart_shuffle compares against
                artist=getattr(track, 'artist', None),
                album=getattr(track, 'album', None),
                genre=getattr(track, 'genre', None),
            )
            self._history_entries = _sh.append(self._history_entries, entry)
            self._history_dirty = True
            if self._history_save_job is not None:
                try:
                    self.root.after_cancel(self._history_save_job)
                except Exception:
                    pass
            self._history_save_job = self.root.after(
                HISTORY_SAVE_DEBOUNCE_MS, self._flush_history)
        except Exception as e:
            print(f"⚠ Could not record listening history: {e}")

    def _flush_history(self):
        """Write the listening history to disk if it changed (atomic)."""
        self._history_save_job = None
        if not self._history_dirty:
            return
        try:
            import shuffle_history as _sh
            _sh.save(str(self.shuffle_history_file), list(self._history_entries))
            self._history_dirty = False
        except Exception as e:
            print(f"⚠ Could not save listening history: {e}")

    def _shuffle_context(self):
        """Build (recent_history, context) for a smart_shuffle call."""
        try:
            import shuffle_history as _sh
            entries = list(self._history_entries)
            recent = _sh.to_recent_history(entries)
            context = {'protected_paths': _sh.protected_paths(entries),
                       'protect_positions': 3}
            return recent, context
        except Exception:
            return [], None
    
    def edit_shuffle_settings(self):
        """Edit shuffle algorithm parameters"""
        dialog = tk.Toplevel(self.root)
        dialog.title("Shuffle Settings")
        dialog.geometry("550x650")
        dialog.resizable(False, False)
        
        dialog.transient(self.root)
        dialog.grab_set()
        
        main_frame = ttk.Frame(dialog, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(main_frame, text="Smart Shuffle Parameters", 
                font=('Arial', 14, 'bold')).pack(pady=(0, 15))
        
        ttk.Label(main_frame, text="Adjust how Smart Shuffle prioritizes tracks:", 
                font=('Arial', 9)).pack(pady=(0, 20))
        
        # Create a canvas with scrollbar for parameters
        canvas = tk.Canvas(main_frame, height=400)
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Dictionary to hold variable references
        vars_dict = {}
        
        # Weight parameters
        ttk.Label(scrollable_frame, text="Weight Parameters", 
                font=('Arial', 11, 'bold')).grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=(5, 10))
        
        weight_params = [
            ('random_min', 'Random Minimum', 0.0, 1.0, 0.05, 'Base randomness level'),
            ('random_range', 'Random Range', 0.0, 1.0, 0.05, 'Additional randomness variation'),
            ('rating_weight', 'Rating Weight', 0.0, 2.0, 0.05, 'Importance of track ratings'),
            ('novelty_weight', 'Novelty Weight', 0.0, 2.0, 0.05, 'Favor less-played tracks'),
            ('recency_weight', 'Recency Weight', 0.0, 2.0, 0.05, 'Favor recently unplayed tracks'),
            ('newness_weight', 'Newness Weight', 0.0, 2.0, 0.05, 'Favor recently added tracks'),
            ('loved_weight', 'Loved Weight', 0.0, 2.0, 0.05, 'Bonus for loved tracks'),
            ('skip_penalty_weight', 'Skip Penalty', 0.0, 2.0, 0.05, 'Penalty for skipped tracks'),
            ('artist_skip_weight', 'Artist Skip Penalty', 0.0, 1.0, 0.05, 'Penalty for skipped artists'),
            ('bpm_bonus', 'BPM Bonus', 0.0, 0.5, 0.01, 'Bonus for tracks with BPM data'),
        ]
        
        row = 1
        for param, label, min_val, max_val, increment, tooltip in weight_params:
            ttk.Label(scrollable_frame, text=f"{label}:").grid(row=row, column=0, sticky=tk.W, padx=(10, 5), pady=3)
            
            var = tk.DoubleVar(value=self.shuffle_config.get(param, 0.0))
            vars_dict[param] = var
            
            spinbox = ttk.Spinbox(scrollable_frame, from_=min_val, to=max_val, 
                                increment=increment, textvariable=var, width=8)
            spinbox.grid(row=row, column=1, sticky=tk.W, padx=5, pady=3)
            
            ttk.Label(scrollable_frame, text=f"({tooltip})", 
                    font=('Arial', 8), foreground='gray').grid(row=row, column=2, sticky=tk.W, padx=5, pady=3)
            
            row += 1
        
        # Constraint parameters
        ttk.Label(scrollable_frame, text="Constraint Parameters", 
                font=('Arial', 11, 'bold')).grid(row=row, column=0, columnspan=3, sticky=tk.W, pady=(15, 10))
        row += 1
        
        constraint_params = [
            ('recent_artists', 'Recent Artists', 1, 10, 1, 'Avoid repeating artists'),
            ('recent_albums', 'Recent Albums', 1, 10, 1, 'Avoid repeating albums'),
            ('recent_genres', 'Recent Genres', 1, 10, 1, 'Avoid repeating genres'),
            ('lookahead', 'Lookahead', 50, 1000, 50, 'How far ahead to search for swaps'),
        ]

        for param, label, min_val, max_val, increment, tooltip in constraint_params:
            ttk.Label(scrollable_frame, text=f"{label}:").grid(row=row, column=0, sticky=tk.W, padx=(10, 5), pady=3)

            var = tk.IntVar(value=self.shuffle_config.get(param, 0))
            vars_dict[param] = var

            spinbox = ttk.Spinbox(scrollable_frame, from_=min_val, to=max_val,
                                  increment=increment, textvariable=var, width=8)
            spinbox.grid(row=row, column=1, sticky=tk.W, padx=5, pady=3)

            ttk.Label(scrollable_frame, text=f"({tooltip})",
                      font=('Arial', 8), foreground='gray').grid(row=row, column=2, sticky=tk.W, padx=5, pady=3)
            row += 1

        # Adaptive & History parameters (Tasks 4, 5, 6)
        ttk.Label(scrollable_frame, text="Adaptive & History Parameters",
                  font=('Arial', 11, 'bold')).grid(row=row, column=0, columnspan=3, sticky=tk.W, pady=(15, 10))
        row += 1

        # adaptive_constraints checkbox
        adapt_var = tk.BooleanVar(value=bool(self.shuffle_config.get('adaptive_constraints', True)))
        vars_dict['adaptive_constraints'] = adapt_var
        ttk.Checkbutton(scrollable_frame, text="Adaptive Constraints",
                        variable=adapt_var).grid(row=row, column=0, columnspan=2, sticky=tk.W, padx=(10, 5), pady=3)
        ttk.Label(scrollable_frame, text="(auto-scale spacing from library size)",
                  font=('Arial', 8), foreground='gray').grid(row=row, column=2, sticky=tk.W, padx=5, pady=3)
        row += 1

        history_params = [
            ('history_guard_size',    'History Guard Size',   5, 200, 5,    'Recent tracks checked for penalty'),
            ('history_track_penalty', 'Track Penalty',        0.0, 1.0, 0.05, 'Weight penalty for recently played tracks'),
            ('history_artist_penalty','Artist Penalty',       0.0, 1.0, 0.05, 'Weight penalty for recently played artists'),
        ]
        for param, label, min_val, max_val, increment, tooltip in history_params:
            ttk.Label(scrollable_frame, text=f"{label}:").grid(row=row, column=0, sticky=tk.W, padx=(10, 5), pady=3)
            if isinstance(self.shuffle_config.get(param, 0), int) or increment == 5:
                var = tk.IntVar(value=int(self.shuffle_config.get(param, 0)))
            else:
                var = tk.DoubleVar(value=self.shuffle_config.get(param, 0.0))
            vars_dict[param] = var
            ttk.Spinbox(scrollable_frame, from_=min_val, to=max_val,
                        increment=increment, textvariable=var, width=8).grid(
                row=row, column=1, sticky=tk.W, padx=5, pady=3)
            ttk.Label(scrollable_frame, text=f"({tooltip})",
                      font=('Arial', 8), foreground='gray').grid(row=row, column=2, sticky=tk.W, padx=5, pady=3)
            row += 1
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Buttons
        button_frame = ttk.Frame(dialog)
        button_frame.pack(pady=15)
        
        def save_settings():
            from shuffle_core import normalize_shuffle_config
            # Update + validate config (clamps out-of-range typed values)
            candidate = dict(self.shuffle_config)
            for param, var in vars_dict.items():
                candidate[param] = var.get()
            self.shuffle_config = normalize_shuffle_config(candidate)

            if self._save_shuffle_config():
                messagebox.showinfo("Saved", "Shuffle settings saved successfully!")
                dialog.destroy()
                # Apply immediately so the change is audible next shuffle.
                try:
                    if getattr(self, 'all_tracks', None):
                        self.apply_shuffle()
                except Exception as e:
                    print(f"⚠ Could not re-apply shuffle after settings change: {e}")
            else:
                messagebox.showerror("Error", "Failed to save shuffle settings")

        def reset_defaults():
            result = messagebox.askyesno("Reset to Defaults",
                                        "Reset all shuffle parameters to default values?")
            if result:
                from shuffle_core import DEFAULT_SHUFFLE_CONFIG
                for param, var in vars_dict.items():
                    var.set(DEFAULT_SHUFFLE_CONFIG.get(param, 0))
    
        ttk.Button(button_frame, text="Save", command=save_settings, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Reset Defaults", command=reset_defaults, width=15).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=dialog.destroy, width=12).pack(side=tk.LEFT, padx=5)
    
    def _init_system_volume_control(self):
        """Initialize Windows system volume control"""
        self.system_volume = None
        self.volume_interface = None
        
        if sys.platform != 'win32':
            print("System volume control only available on Windows")
            return
        
        try:
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL, CoCreateInstance
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume, IMMDeviceEnumerator
            from pycaw.constants import CLSID_MMDeviceEnumerator
            
            # Create device enumerator
            deviceEnumerator = CoCreateInstance(
                CLSID_MMDeviceEnumerator,
                IMMDeviceEnumerator,
                CLSCTX_ALL
            )
            
            # Get default audio endpoint (0 = eRender, 0 = eConsole)
            defaultDevice = deviceEnumerator.GetDefaultAudioEndpoint(0, 0)
            
            # Activate the volume interface
            interface = defaultDevice.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            self.volume_interface = cast(interface, POINTER(IAudioEndpointVolume))
            
            print("✓ System volume control enabled")
                
        except ImportError:
            print("⚠ System volume control unavailable")
            print("  Install with: pip install pycaw comtypes")
        except Exception as e:
            print(f"⚠ Could not initialize system volume: {e}")
            print("  Volume slider will control pygame only")
    
    def get_system_volume(self):
        """Get current system volume (0-100)"""
        if not self.volume_interface:
            return None
        try:
            # Get volume as scalar (0.0 to 1.0)
            volume_scalar = self.volume_interface.GetMasterVolumeLevelScalar()
            return int(volume_scalar * 100)
        except (AttributeError, OSError):
            # COM interface may be disconnected
            return None
        except Exception as e:
            print(f"Unexpected error getting volume: {e}")
            return None
    
    def set_system_volume(self, volume_percent):
        """Set system volume (0-100)"""
        if not self.volume_interface:
            return False
        try:
            # Set volume as scalar (0.0 to 1.0)
            volume_scalar = max(0.0, min(1.0, volume_percent / 100.0))
            self.volume_interface.SetMasterVolumeLevelScalar(volume_scalar, None)
            return True
        except Exception as e:
            print(f"Error setting system volume: {e}")
            return False
    
    def volume_up(self):
        """Increase system volume by 5%"""
        current = self.get_system_volume()
        if current is not None:
            new_volume = min(100, current + 5)
            self.set_system_volume(new_volume)
            try:
                self._updating_volume = True
                self.volume_var.set(new_volume)
            finally:
                self._updating_volume = False
        else:
            print("⚠ System volume control not available")
            print("  Install: pip install pycaw comtypes")
    
    def volume_down(self):
        """Decrease system volume by 5%"""
        current = self.get_system_volume()
        if current is not None:
            new_volume = max(0, current - 5)
            self.set_system_volume(new_volume)
            try:
                self._updating_volume = True
                self.volume_var.set(new_volume)
            finally:
                self._updating_volume = False
        else:
            print("⚠ System volume control not available")
            print("  Install: pip install pycaw comtypes")
    
    def toggle_mute(self):
        """Toggle system mute"""
        if not self.volume_interface:
            print("⚠ System volume control not available")
            print("  Install: pip install pycaw comtypes")
            return
        try:
            is_muted = self.volume_interface.GetMute()
            self.volume_interface.SetMute(not is_muted, None)
        except Exception as e:
            print(f"Error toggling mute: {e}")
    
    def _sync_volume_slider(self):
        """Sync volume slider with system volume (called periodically)"""
        if not self.volume_interface:
            return
        try:
            current_volume = self.get_system_volume()
            if current_volume is not None:
                # Only update if significantly different (avoid slider jitter)
                slider_value = self.volume_var.get()
                if abs(current_volume - slider_value) > 1:
                    try:
                        self._updating_volume = True
                        self.volume_var.set(current_volume)
                    finally:
                        self._updating_volume = False
        except (AttributeError, TypeError, tk.TclError):
            # Volume interface disconnected or Tk destroyed
            pass
    
    def setup_keyboard_shortcuts(self):
        """Setup keyboard shortcuts for playback controls"""
        # Volume keys (fnlock-translated F9-F11) handle volume globally, even unfocused
        self._setup_global_media_keys()
        # Transport keys (F5-F8) and the hardware media dial both arrive as a real
        # WM_APPCOMMAND — fnlock owns the physical-key-to-action mapping, this app
        # just reacts to whatever command it actually sends. Delivery (global vs.
        # foreground-only) depends entirely on which window fnlock targets, not on
        # anything here — see _setup_wm_appcommand_hook's docstring.
        self._setup_wm_appcommand_hook()

        print("Keyboard shortcuts enabled:")
        print("  Volume keys work globally (out of focus); transport keys/dial: reacts to whatever fnlock sends")

    def _setup_global_media_keys(self):
        """Enable the volume keys even when the window is not focused.

        Transport keys (F5-F8) and the dial are handled separately, via the real
        WM_APPCOMMAND message (_setup_wm_appcommand_hook) rather than raw keys here
        — see that method's docstring for why.
        """
        try:
            import keyboard
        except ImportError:
            print("⚠ Global media keys disabled")
            print("  Install with: pip install keyboard")
            print("  Then restart the app")
            return

        def ui_call(fn, label):
            print(f"[MEDIA KEY] {label} triggered → dispatching to UI thread")
            self.root.after(0, fn)

        # Only the volume keys are handled here. F5-F8 used to be intercepted as
        # raw F-keys too, but that raced fnlock's own remapping (this hook installs
        # after fnlock's, so it fires first in the Windows LIFO hook chain and
        # swallows the key before fnlock can translate it) — meaning F5-F8 always
        # did the hardcoded Previous/Next/Play-Pause/Stop below no matter how the
        # user remapped them in fnlock's GUI. Fixed by not intercepting them here at
        # all: fnlock sends the real WM_APPCOMMAND for whatever action it resolved
        # to, and _setup_wm_appcommand_hook reacts to that instead.
        hotkeys = [
            ('volume mute', self.toggle_mute,       'Mute (F9)',       False),
            ('volume down', self.volume_down,       'Vol Down (F10)',  False),
            ('volume up',   self.volume_up,         'Vol Up (F11)',    False),
        ]

        registered_keys = []
        failed_keys = []

        for key_name, fn, label, suppress in hotkeys:
            try:
                keyboard.add_hotkey(key_name, lambda fn=fn, label=label: ui_call(fn, label), suppress=suppress)
                registered_keys.append(f"{key_name.upper()} = {label}")
            except Exception as e:
                failed_keys.append(f"{key_name.upper()} ({e})")
        
        if registered_keys:
            print("✓ Global media keys enabled:")
            for key in registered_keys:
                print(f"  {key}")
            if not failed_keys:
                print("  Works globally, even when window not focused")
        
        if failed_keys:
            print("⚠ Some keys failed to register:")
            for key in failed_keys:
                print(f"  {key}")
            print("  Try running as administrator")
            print("  Or close other apps using these keys")

    def _setup_wm_appcommand_hook(self):
        """Subclass this window's WNDPROC to receive WM_APPCOMMAND.

        Handles both F5-F8 (transport keys, remapped by fnlock) and the hardware
        media dial — fnlock forwards both the same way, as a real WM_APPCOMMAND,
        so this app doesn't need to know or duplicate fnlock's physical-key mapping.

        WM_APPCOMMAND has no inherent foreground requirement — it's just a message
        sent to whatever window handle the sender chooses, via SendMessage. It's not
        this app that decides whether that's global or foreground-only; fnlock is
        the sender, and its SendAppCommand() (fn-lock.ahk, a separate companion repo)
        is what decides the target. fnlock currently targets this app's window
        directly whenever it's running (falling back to the foreground window only
        when this app isn't open), so in practice this fires regardless of focus —
        but that's a property of fnlock's current code, not a guarantee this app can
        make on its own. (An earlier version of this comment claimed WM_APPCOMMAND
        was inherently foreground-only and that fixing it would need Windows' System
        Media Transport Controls — that was wrong; SMTC is a real alternative but was
        never actually necessary here.)
        """
        try:
            import ctypes
            from ctypes import wintypes
        except ImportError:
            return

        try:
            self.root.update_idletasks()
            raw_hwnd = self.root.winfo_id()

            LRESULT = ctypes.c_ssize_t
            LONG_PTR = ctypes.c_ssize_t
            GWLP_WNDPROC = -4
            GA_ROOT = 2

            WNDPROC = ctypes.WINFUNCTYPE(
                LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
            )

            user32 = ctypes.WinDLL('user32', use_last_error=True)

            user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            user32.GetAncestor.restype = wintypes.HWND

            user32.CallWindowProcW.argtypes = [
                LONG_PTR, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
            ]
            user32.CallWindowProcW.restype = LRESULT

            set_window_long = getattr(user32, 'SetWindowLongPtrW', None) or user32.SetWindowLongW
            set_window_long.argtypes = [wintypes.HWND, ctypes.c_int, LONG_PTR]
            set_window_long.restype = LONG_PTR

            # winfo_id() can return a child HWND; WM_APPCOMMAND bubbles up to the
            # top-level via DefWindowProc, so a child-window subclass would never see it.
            hwnd = user32.GetAncestor(raw_hwnd, GA_ROOT) or raw_hwnd

            # Labels are generic (not "(dial)") because this same WM_APPCOMMAND path
            # now handles both the hardware dial and F5-F8 (fnlock forwards both the
            # same way) — nothing here can tell which physical control sent it.
            actions = {
                'play_pause': (self.toggle_play_pause, 'Play/Pause'),
                'next': (self.next_track, 'Next'),
                'previous': (self.previous_track, 'Previous'),
                'stop': (self.stop, 'Stop'),
            }

            def wndproc_callback(hwnd_arg, msg, wparam, lparam):
                # WM_APPCOMMAND arrives via a cross-process SendMessage, so Windows
                # calls this callback reentrantly, nested inside Tcl's own blocked
                # GetMessage wait (Tcl releases the GIL while waiting for messages).
                # Reacquiring the GIL to run this Python code is fine (that's what
                # ctypes' callback trampoline is for), but calling back into
                # Tkinter/Tcl itself (e.g. root.after) from this nested context
                # corrupts Tcl's own saved thread-state and crashes the process with
                # an unrecoverable "GIL is released" fatal error the moment control
                # returns to Tcl's notifier. So: touch nothing Tcl-related here.
                # Just record the action and return — a root.after-scheduled poller
                # (running in Tcl's own normal, non-reentrant loop) does the dispatch.
                try:
                    if msg == appcommand.WM_APPCOMMAND:
                        cmd = appcommand.appcommand_from_lparam(lparam)
                        action_name = appcommand.action_for_cmd(cmd)
                        print(f"[APPCOMMAND] cmd={cmd} action={action_name}", flush=True)
                        entry = actions.get(action_name)
                        if entry is not None:
                            self._appcmd_pending.append(entry)
                            return 1
                    if self._appcmd_old_proc:
                        return user32.CallWindowProcW(self._appcmd_old_proc, hwnd_arg, msg, wparam, lparam)
                except Exception as e:
                    print(f"⚠ Error handling window message (msg={msg}): {e}", flush=True)
                return 0

            # Keep a strong reference before installing — a GC'd ctypes callback
            # crashes the app on the next message delivered to this window.
            self._appcmd_wndproc = WNDPROC(wndproc_callback)
            callback_ptr = ctypes.cast(self._appcmd_wndproc, ctypes.c_void_p).value

            ctypes.set_last_error(0)
            old_proc = set_window_long(hwnd, GWLP_WNDPROC, callback_ptr)
            if old_proc == 0 and ctypes.get_last_error() != 0:
                print("⚠ Media transport hook disabled: SetWindowLong failed")
                self._appcmd_wndproc = None
                return

            self._appcmd_hwnd = hwnd
            self._appcmd_old_proc = old_proc
            self._appcmd_set_window_long = set_window_long
            print("✓ Media transport hook enabled — F5-F8 + dial (reacts to whatever fnlock sends)")
            self._appcmd_poll_job = self.root.after(50, self._poll_appcommand_pending)
        except Exception as e:
            print(f"⚠ Media transport hook disabled: {e}")
            self._appcmd_wndproc = None
            self._appcmd_old_proc = None

    def _poll_appcommand_pending(self):
        """Dispatch media-dial actions recorded by the WNDPROC hook.

        Runs entirely within Tk's own normal event loop turn (scheduled via
        root.after), never inside the reentrant WNDPROC callback itself — see
        the comment in _setup_wm_appcommand_hook for why that split is required.
        """
        while self._appcmd_pending:
            fn, label = self._appcmd_pending.popleft()
            print(f"[APPCOMMAND] {label} triggered → dispatching to UI thread")
            try:
                fn()
            except Exception as e:
                print(f"⚠ Error running dial action '{label}': {e}")
        try:
            self._appcmd_poll_job = self.root.after(50, self._poll_appcommand_pending)
        except tk.TclError:
            pass

    def _set_dark_title_bar(self):
        """Set title bar color to match theme (Windows 10/11)"""
        try:
            self.root.update()  # Ensure window is created
            hwnd = self.root.winfo_id()
            
            import ctypes
            # 1 = dark, 0 = light
            use_dark = 1 if self.dark_mode else 0
            
            # Try Windows 11 attribute (20) first, then Windows 10 (19)
            for attribute in [20, 19]:
                try:
                    value = ctypes.c_int(use_dark)
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
                    )
                    break
                except (AttributeError, OSError):
                    continue  # Attribute not supported or API call failed
        except (AttributeError, ImportError):
            pass  # Not Windows or ctypes not available
        except Exception:
            # Don't crash for cosmetic features
            pass
    
    def _show_transport(self):
        """Show transport controls on hover"""
        if getattr(self, "_transport_hide_job", None):
            try:
                self.root.after_cancel(self._transport_hide_job)
            except:
                pass
            self._transport_hide_job = None
        
        if not self.transport_frame.winfo_ismapped():
            self.transport_frame.place(in_=self.info_frame, relx=0.5, rely=0.5, anchor="center")
    
    def _schedule_hide_transport(self):
        """Schedule hiding transport controls after small delay"""
        if getattr(self, "_transport_hide_job", None):
            return
        self._transport_hide_job = self.root.after(200, self._hide_transport)
    
    def _hide_transport(self):
        """Hide transport controls"""
        self._transport_hide_job = None
        if self.transport_frame.winfo_ismapped():
            self.transport_frame.place_forget()
    
    def _set_navigation_enabled(self, enabled):
        """Enable or disable navigation buttons"""
        state = tk.NORMAL if enabled else tk.DISABLED
        self.prev_button.config(state=state)
        self.next_button.config(state=state)
    
    def _validate_playlist_index(self):
        """Validate current playlist and index are in valid state"""
        if not self.current_playlist:
            return False
        if self.current_index < 0 or self.current_index >= len(self.current_playlist):
            return False
        return True
    
    def _get_safe_duration(self, track):
        """Get track duration with validation to prevent division by zero"""
        try:
            duration = float(getattr(track, 'duration', 0))
            return max(duration, 0.01)  # Minimum 0.01s to prevent division by zero
        except (ValueError, TypeError):
            return 0.01
    
    def _check_disk_space(self, required_mb=100):
        """
        Check if sufficient disk space is available
        
        Args:
            required_mb: Minimum MB required
            
        Returns:
            bool: True if sufficient space, False otherwise
        """
        try:
            import shutil
            stats = shutil.disk_usage(self.cache_file.parent)
            available_mb = stats.free / (1024 * 1024)
            
            if available_mb < required_mb:
                print(f"⚠ Low disk space: {available_mb:.0f}MB available, {required_mb}MB required")
                return False
            
            return True
        except Exception as e:
            print(f"⚠ Could not check disk space: {e}")
            return True  # Assume OK if we can't check
    
    def toggle_loved(self):
        """V2: Toggle loved status for ANY track"""
        if not self._validate_playlist_index():
            return
        
        track = self.current_playlist[self.current_index]

        # Toggle loved status. A button press is always an explicit user
        # opinion, so record provenance (Task 7a) - distinguishes a real
        # "not a favourite" from "never touched".
        track.loved = not track.loved if track.loved is not None else True
        track.loved_source = 'user'

        # Persist to the durable favorites store (Task 7c)
        try:
            import preferences as _pf
            self._favorites = _pf.set_favorite(
                getattr(self, '_favorites', {}),
                getattr(track, 'original_filepath', track.filepath),
                track.loved, source='user')
            _pf.save(str(self.preferences_file), self._favorites)
        except Exception as e:
            print(f"⚠ Could not save favorite: {e}")

        # Update button - use unicode hearts that respond to foreground color
        if track.loved:
            self.love_button.config(text="♥", foreground="red", font=('Arial', 14, 'bold'))
        else:
            self.love_button.config(text="♡", foreground="gray", font=('Arial', 14))
        
        # V2: Save to unified metadata
        self._update_track_metadata(track)
        
        print(f"{'♥' if track.loved else '♡'} {track.title}")
    
    def migrate_ratings_to_favorites(self):
        """One-time, opt-in: seed favorites from existing high iTunes ratings
        for tracks that have no favorite opinion yet (Task 7b)."""
        candidates = []
        for t in getattr(self, 'library_tracks', []) or self.all_tracks:
            if getattr(t, 'loved', None) is not None:
                continue
            if getattr(t, 'loved_source', None) not in (None, 'unknown'):
                continue
            r = getattr(t, 'rating', 0) or 0
            # 4+ stars on either common iTunes scale (0-100 or POPM 0-255).
            if (0 < r <= 100 and r >= 80) or (100 < r <= 255 and r >= 204):
                candidates.append(t)

        if not candidates:
            messagebox.showinfo("Import Ratings",
                                "No un-favorited tracks rated 4+ stars were found.")
            return
        if not messagebox.askyesno(
                "Import Ratings as Favorites",
                f"Mark {len(candidates)} track(s) rated 4+ stars as favorites?\n\n"
                "This only affects tracks you haven't favorited or un-favorited "
                "yourself, and is recorded as 'migrated' so you can tell them "
                "apart later."):
            return

        try:
            import preferences as _pf
        except Exception as e:
            messagebox.showerror("Import Ratings", f"Could not load favorites store:\n{e}")
            return
        favs = getattr(self, '_favorites', {})
        for t in candidates:
            t.loved = True
            t.loved_source = 'migrated'
            self._update_track_metadata(t)
            favs = _pf.set_favorite(favs, getattr(t, 'original_filepath', t.filepath),
                                    True, source='migrated')
        self._favorites = favs
        try:
            _pf.save(str(self.preferences_file), favs)
        except Exception as e:
            print(f"⚠ Could not save migrated favorites: {e}")
        if self._validate_playlist_index():
            self._update_love_button(self.current_playlist[self.current_index])
        messagebox.showinfo("Import Ratings",
                            f"Marked {len(candidates)} track(s) as favorites.")

    def _update_love_button(self, track):
        """V2: Update love button state (always visible)"""
        self.love_button.grid()  # Make button visible for all tracks
        if track.loved:
            self.love_button.config(text="♥", foreground="red", font=('Arial', 14, 'bold'))
        else:
            self.love_button.config(text="♡", foreground="gray", font=('Arial', 14))
    
    def toggle_play_pause(self):
        """Toggle between play and pause"""
        if not self.audio_available or not self.current_playlist:
            return
        if self.is_playing:
            self.pause()
            print("Paused")
            return
        with self._engine_lock:
            has_song = self._engine.current() is not None
        if has_song:
            self._resume_engine()
            print("Unpaused")
        else:
            self.play_track_at_index(self.current_index)
            print("Started playing")

    def on_progress_click(self, event):
        """Handle click on progress bar to seek"""
        if not self.audio_available or not self.current_playlist or self.current_index < 0:
            return
        
        try:
            bar_width = self.progress_bar.winfo_width()
            click_x = event.x
            
            if click_x < 0:
                click_x = 0
            elif click_x > bar_width:
                click_x = bar_width
            
            percentage = (click_x / bar_width) * 100
            
            track = self.current_playlist[self.current_index]
            if track.duration <= 0:
                return
            
            target_pos = (percentage / 100.0) * track.duration
            
            try:
                was_playing = self.is_playing
                with self._engine_lock:
                    self._engine.seek(float(target_pos))
                # Task 8: a seek settles audible time - the jumped interval is
                # never credited as listening.
                if self._pb_tracker:
                    self._pb_tracker.seek(time.monotonic())
                    if not was_playing:
                        self._pb_tracker.pause(time.monotonic())

                # Update UI
                self.progress_var.set(percentage)
                
                current_min = int(target_pos // 60)
                current_sec = int(target_pos % 60)
                total_min = int(track.duration // 60)
                total_sec = int(track.duration % 60)
                self.time_label.config(text=f"{current_min}:{current_sec:02d} / {total_min}:{total_sec:02d}")
                
                self.root.update_idletasks()
                
                print(f"Seeked to {target_pos:.1f}s ({percentage:.1f}%)")
            except (pygame.error, OSError, IOError) as e:
                print(f"⚠ Seeking failed: {e}")
            except Exception as e:
                print(f"⚠ Unexpected seek error: {e}")
                import traceback
                traceback.print_exc()
        
        except Exception as e:
            print(f"⚠ Error in seek handler: {e}")
    
    def on_volume_change(self, value):
        """Handle volume slider change - controls system volume and pygame"""
        # Ignore if we're programmatically updating the slider
        if self._updating_volume:
            return
        
        try:
            volume_percent = float(value)
            
            # Set system volume if available
            system_set = False
            if self.volume_interface:
                system_set = self.set_system_volume(volume_percent)
            
            # The engine's master volume is the fallback only when Windows volume control failed;
            # applying both would attenuate twice.
            if self.audio_available and self._engine is not None:
                with self._engine_lock:
                    self._engine.set_master_volume(1.0 if system_set else volume_percent / 100.0)
            
            # Save preference
            try:
                with open(self.volume_pref_file, 'w') as f:
                    f.write(str(volume_percent))
            except (OSError, IOError):
                pass  # Preferences not critical
        except (ValueError, TypeError) as e:
            print(f"⚠ Invalid volume value: {e}")
        except Exception as e:
            print(f"⚠ Error setting volume: {e}")
    
    # ============================================================
    # V2: Unified Player Metadata System
    # ============================================================
    
    def _load_player_metadata(self):
        """Load unified player metadata from XML file with backup recovery"""
        if not self.player_metadata_file.exists():
            print("Player metadata file not found - will create on first save")
            return
        
        # Try to load, with backup recovery
        backup_file = self.player_metadata_file.with_suffix('.xml.backup')
        
        for attempt, filepath in enumerate([self.player_metadata_file, backup_file], 1):
            if not filepath.exists():
                continue
            
            try:
                print(f"Loading player metadata (attempt {attempt})...")
                with open(filepath, 'rb') as f:
                    data = plistlib.load(f)
                
                tracks = data.get('tracks', {})
                schema_version = int(data.get('schema_version', 1))

                if schema_version < 2 and filepath == self.player_metadata_file:
                    # One-time keep of the pre-tri-state file so the loved
                    # reclassification below is recoverable (Task 7a).
                    try:
                        import shutil
                        v1 = self.player_metadata_file.with_suffix('.xml.v1backup')
                        if not v1.exists():
                            shutil.copy2(self.player_metadata_file, v1)
                    except (OSError, IOError):
                        pass

                def _opt_int(v):
                    if v in (None, ''):
                        return None
                    v = int(v)
                    # v1 stored "unknown" as 0 for these fields; a real 0 and
                    # "never set" score identically, so fold both to None.
                    if schema_version < 2 and v == 0:
                        return None
                    return v

                def _opt_int2(v):
                    return None if v in (None, '') else int(v)

                def _load_loved(td):
                    raw = td.get('loved', None)
                    src = td.get('loved_source', None)
                    if schema_version >= 2:
                        return raw, src
                    # v1: a bare False has no provenance (mix of real "not a
                    # favourite" and playback-coerced unknown) -> treat as
                    # unknown; a True was almost certainly a deliberate heart.
                    if raw is True:
                        return True, 'user'
                    return None, None

                with self._metadata_lock:
                    for filepath_key, track_data in tracks.items():
                        key = self._norm_path(filepath_key)
                        loved, loved_source = _load_loved(track_data)
                        self.player_metadata[key] = {
                            'play_count': int(track_data.get('play_count', 0)),
                            'skip_count': _opt_int(track_data.get('skip_count')),
                            'rating': int(track_data.get('rating', 0)),
                            'last_played': _opt_int(track_data.get('last_played')),
                            'loved': loved,
                            'loved_source': loved_source,
                            'date_added': _opt_int(track_data.get('date_added')),
                            # Task 8 counters (never had a v1 sentinel)
                            'qualified_play_count': _opt_int2(track_data.get('qualified_play_count')),
                            'manual_skip_count': _opt_int2(track_data.get('manual_skip_count')),
                            'last_skipped_at': _opt_int2(track_data.get('last_skipped_at')),
                        }

                try:
                    print(f"Loaded metadata for {len(tracks)} tracks"
                          + (" (migrated from v1)" if schema_version < 2 else ""))
                except Exception:
                    pass

                # If we loaded from backup, restore it as main
                if filepath == backup_file:
                    print("  Restored from backup file")
                    try:
                        import shutil
                        shutil.copy2(backup_file, self.player_metadata_file)
                    except (OSError, IOError) as e:
                        print(f"  Could not restore backup: {e}")
                
                return  # Success
                
            except Exception as e:
                # Status prints must never derail load (a cp1252 console makes
                # the glyphs raise UnicodeEncodeError).
                try:
                    print(f"! Player metadata file corrupted (attempt {attempt}): {e}")
                    if filepath == self.player_metadata_file:
                        print("  Trying backup file...")
                except Exception:
                    pass
                continue
        
        # Both files failed
        print("⚠ Could not load metadata from primary or backup, starting fresh")
        try:
            self.player_metadata_file.unlink()
        except (OSError, IOError):
            pass
        try:
            backup_file.unlink()
        except (OSError, IOError):
            pass
    
    def _save_player_metadata(self):
        """Save unified player metadata to XML file (atomic write with backup)"""
        try:
            # Copy metadata under lock to avoid holding lock during I/O
            with self._metadata_lock:
                metadata_copy = dict(self.player_metadata)

            tracks_out = {}
            for filepath_norm, track_data in metadata_copy.items():
                entry = {
                    'play_count': int(track_data.get('play_count') or 0),
                    'rating': int(track_data.get('rating') or 0),
                }
                # Tri-state fields: only written when known, so a missing key
                # reads back as None ("never set") rather than 0/False (Task 7a/8).
                for k in ('skip_count', 'last_played', 'date_added',
                          'qualified_play_count', 'manual_skip_count', 'last_skipped_at'):
                    v = track_data.get(k)
                    if v is not None:
                        entry[k] = int(v)
                if track_data.get('loved') is not None:
                    entry['loved'] = bool(track_data['loved'])
                if track_data.get('loved_source') is not None:
                    entry['loved_source'] = str(track_data['loved_source'])
                tracks_out[filepath_norm] = entry
            
            # Create backup of existing file before overwriting
            if self.player_metadata_file.exists():
                backup_file = self.player_metadata_file.with_suffix('.xml.backup')
                try:
                    import shutil
                    shutil.copy2(self.player_metadata_file, backup_file)
                except (OSError, IOError):
                    pass  # Backup failed, but continue with save
            
            # Atomic write: write to temp file, then replace
            tmp_path = self.player_metadata_file.with_suffix(self.player_metadata_file.suffix + '.tmp')
            with open(tmp_path, 'wb') as f:
                plistlib.dump({'schema_version': 2, 'tracks': tracks_out}, f)
            
            # Verify the temp file before replacing
            if tmp_path.stat().st_size < 100:
                raise IOError("Metadata file suspiciously small, not overwriting")
            
            tmp_path.replace(self.player_metadata_file)
            
        except Exception as e:
            print(f"⚠ Error saving player metadata: {e}")
            # Try to clean up temp file
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except (OSError, IOError, NameError):
                pass

    def _update_track_metadata(self, track):
        """Update metadata for any track (unified system).

        Tri-state fields (loved, skips, last_played, date_added) keep ``None``
        for "unknown / never set" - collapsing them to 0/False was corrupting
        the shuffle taste signal on every playback (Task 7a).
        """
        filepath_to_check = getattr(track, 'original_filepath', track.filepath)
        fp = self._norm_path(filepath_to_check)

        def _int_or_none(v):
            return None if v is None else int(v)

        with self._metadata_lock:
            self.player_metadata[fp] = {
                'play_count': int(getattr(track, 'play_count', 0) or 0),
                'skip_count': _int_or_none(getattr(track, 'skips', None)),
                'rating': int(getattr(track, 'rating', 0) or 0),
                'last_played': _int_or_none(getattr(track, 'last_played', None)),
                'loved': getattr(track, 'loved', None),          # True | False | None
                'loved_source': getattr(track, 'loved_source', None),
                'date_added': _int_or_none(getattr(track, 'date_added', None)),
                'qualified_play_count': _int_or_none(getattr(track, 'qualified_play_count', None)),
                'manual_skip_count': _int_or_none(getattr(track, 'manual_skip_count', None)),
                'last_skipped_at': _int_or_none(getattr(track, 'last_skipped_at', None)),
            }

        self._queue_metadata_save()
    
    def _queue_metadata_save(self):
        """Queue metadata save in background (debounced)"""
        # Start worker thread if not running
        if self._metadata_save_thread is None or not self._metadata_save_thread.is_alive():
            self._metadata_save_stop.clear()
            self._metadata_save_thread = threading.Thread(
                target=self._metadata_save_worker, 
                daemon=True,
                name="MetadataSaveWorker"
            )
            self._metadata_save_thread.start()
        
        # Queue a save request (will be debounced by worker)
        try:
            self._metadata_save_queue.put_nowait(True)
        except queue.Full:
            pass  # Queue full = save already pending
    
    def _metadata_save_worker(self):
        """Background thread that processes metadata saves"""
        while not self._metadata_save_stop.is_set():
            try:
                # Wait for save request with timeout
                self._metadata_save_queue.get(timeout=0.5)
                
                # Debounce: wait a bit in case more saves are coming
                time.sleep(METADATA_SAVE_DEBOUNCE_SEC)
                
                # Drain queue (multiple rapid saves = one actual save)
                while not self._metadata_save_queue.empty():
                    try:
                        self._metadata_save_queue.get_nowait()
                    except queue.Empty:
                        break
                
                # Do the actual save
                self._save_player_metadata()
                
            except queue.Empty:
                continue
            except Exception as e:
                print(f"⚠ Metadata save error: {e}")
    
    def _cleanup_orphaned_metadata(self):
        """Remove metadata for files no longer in library"""
        if not self.all_tracks:
            return

        # Build set of valid normalized paths
        valid_paths = {self._norm_path(getattr(t, 'original_filepath', t.filepath))
                       for t in self.all_tracks}

        # Find and remove orphaned entries (thread-safe)
        with self._metadata_lock:
            orphaned = [fp for fp in self.player_metadata.keys() if fp not in valid_paths]
            for fp in orphaned:
                del self.player_metadata[fp]

        if orphaned:
            print(f"🗑 Cleaned up {len(orphaned)} orphaned metadata entries")
            # Save immediately after cleanup
            self._save_player_metadata()
    
    def _load_album_artwork(self, track):
        """Load and display album artwork for a track"""
        try:
            audio = open_audio(track.meta_path)
            artwork_data = None
            
            # Try embedded artwork first
            if isinstance(audio, MP3):
                if audio.tags:
                    for key in audio.tags.keys():
                        if key.startswith('APIC'):
                            artwork_data = audio.tags[key].data
                            break
            elif isinstance(audio, MP4):
                if audio.tags and 'covr' in audio.tags:
                    artwork_data = bytes(audio.tags['covr'][0])
            elif isinstance(audio, FLAC):
                # FLAC files store pictures in the 'pictures' list
                if audio.pictures:
                    artwork_data = audio.pictures[0].data
            
            # If no embedded artwork, look for external image files
            if not artwork_data:
                track_dir = os.path.dirname(track.filepath)
                
                # Priority list of common artwork filenames
                priority_filenames = [
                    'cover', 'folder', 'albumart', 'front', 'album', 'artwork'
                ]
                
                # Try priority filenames first with all image extensions
                for base_name in priority_filenames:
                    for ext in ['.jpg', '.jpeg', '.png', '.JPG', '.JPEG', '.PNG']:
                        artwork_path = os.path.join(track_dir, base_name + ext)
                        if os.path.exists(artwork_path):
                            try:
                                with open(artwork_path, 'rb') as f:
                                    artwork_data = f.read()
                                print(f"✓ Found external artwork: {base_name}{ext}")
                                break
                            except Exception as e:
                                continue
                    if artwork_data:
                        break
                
                # If still no artwork, scan directory for ANY image file
                if not artwork_data:
                    try:
                        for filename in os.listdir(track_dir):
                            lower_name = filename.lower()
                            if lower_name.endswith(('.jpg', '.jpeg', '.png')):
                                artwork_path = os.path.join(track_dir, filename)
                                try:
                                    with open(artwork_path, 'rb') as f:
                                        artwork_data = f.read()
                                    print(f"✓ Found external artwork: {filename}")
                                    break
                                except:
                                    continue
                    except:
                        pass
            
            if artwork_data:
                image = Image.open(io.BytesIO(artwork_data))
                image.thumbnail((ARTWORK_SIZE, ARTWORK_SIZE), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(image)
                self.current_artwork = photo
                self.artwork_label.config(image=photo)
                try:
                    self._track_icon_photos = [
                        ImageTk.PhotoImage(im) for im in make_icon_images(image, sizes=ICON_SIZES)
                    ]
                    self.root.iconphoto(True, *self._track_icon_photos)
                except Exception as e:
                    print(f"⚠ Could not update taskbar icon: {e}")
                return True
            else:
                self._show_placeholder_artwork()
                return False
                
        except (OSError, IOError):
            # File read errors are expected (missing files, permissions)
            self._show_placeholder_artwork()
            return False
        except Exception as e:
            print(f"⚠ Unexpected error loading artwork: {e}")
            self._show_placeholder_artwork()
            return False
    
    def _show_placeholder_artwork(self):
        """Show placeholder when no artwork available"""
        try:
            theme = self.themes['dark'] if self.dark_mode else self.themes['light']
            color = theme['placeholder']
            rgb = tuple(int(color[i:i+2], 16) for i in (1, 3, 5))
            
            placeholder = Image.new('RGB', (ARTWORK_SIZE, ARTWORK_SIZE), color=rgb)
            photo = ImageTk.PhotoImage(placeholder)
            self.current_artwork = photo
            self.artwork_label.config(image=photo)
            if self._default_icon_photos:
                try:
                    self._track_icon_photos = self._default_icon_photos
                    self.root.iconphoto(True, *self._default_icon_photos)
                except Exception as e:
                    print(f"⚠ Could not reset taskbar icon: {e}")
        except (ValueError, KeyError, AttributeError) as e:
            print(f"⚠ Could not create placeholder: {e}")
            self.artwork_label.config(image='')
    
    def locate_ffmpeg(self):
        """Try to locate FFmpeg executable"""
        try:
            result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, 
                                   encoding='utf-8', errors='ignore', timeout=2)
            if result.returncode == 0:
                print("✓ FFmpeg found in PATH")
                return "ffmpeg"
        except:
            pass
        
        winget_link = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe"
        if winget_link.exists():
            try:
                result = subprocess.run([str(winget_link), "-version"], capture_output=True, text=True,
                                       encoding='utf-8', errors='ignore', timeout=2)
                if result.returncode == 0:
                    print(f"✓ FFmpeg found at WinGet link: {winget_link}")
                    return str(winget_link)
            except:
                pass
        
        common_paths = [
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
            r"C:\ffmpeg\bin\ffmpeg.exe",
            r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
        ]
        
        try:
            winget_path = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
            if winget_path.exists():
                for item in winget_path.iterdir():
                    if "ffmpeg" in item.name.lower():
                        for root, dirs, files in os.walk(item):
                            if "ffmpeg.exe" in files:
                                ffmpeg_exe = os.path.join(root, "ffmpeg.exe")
                                common_paths.insert(0, ffmpeg_exe)
                                break
        except:
            pass
        
        for path in common_paths:
            if os.path.exists(path):
                try:
                    result = subprocess.run([path, "-version"], capture_output=True, text=True,
                                           encoding='utf-8', errors='ignore', timeout=2)
                    if result.returncode == 0:
                        print(f"✓ FFmpeg found at: {path}")
                        return path
                except:
                    pass
        
        print("✗ FFmpeg not found in PATH or common locations")
        print("  Try: Settings > Set FFmpeg Path")
        return None
    
    def check_ffmpeg(self):
        """Check if FFmpeg is available"""
        if self.ffmpeg_path:
            print(f"✓ Using FFmpeg: {self.ffmpeg_path}")
            return True
        
        try:
            result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True,
                                   encoding='utf-8', errors='ignore', timeout=2)
            if result.returncode == 0:
                print("⚠ FFmpeg command works but wasn't auto-detected")
                response = messagebox.askyesno(
                    "FFmpeg Detection Issue",
                    "FFmpeg appears to be installed but wasn't auto-detected.\n\n"
                    "Would you like to use the 'ffmpeg' command?\n\n"
                    "Click Yes to use it, or No to manually set the path."
                )
                if response:
                    self.ffmpeg_path = "ffmpeg"
                    print("✓ Using FFmpeg via command")
                    return True
                else:
                    self.set_ffmpeg_path()
                    return bool(self.ffmpeg_path)
        except:
            pass
        
        messagebox.showwarning(
            "FFmpeg Not Found",
            "FFmpeg is required for playback (it decodes every song).\n\n"
            "To fix:\n"
            "• Use Settings > Set FFmpeg Path to locate it manually\n"
            "• Or install: winget install FFmpeg\n"
            "• Then restart this app\n\n"
            "Playback is disabled until FFmpeg is configured."
        )
        return False
    
    def _norm_path(self, p):
        """Normalize path for cross-platform comparison"""
        return os.path.normcase(os.path.normpath(p or ""))
    
    def _on_close(self):
        """Graceful shutdown: stop background processes and close"""
        # Stop metadata save worker
        self._metadata_save_stop.set()
        if self._metadata_save_thread and self._metadata_save_thread.is_alive():
            # Queue one final save
            try:
                self._metadata_save_queue.put(True, timeout=0.1)
            except queue.Full:
                pass
            self._metadata_save_thread.join(timeout=1.0)

        # Final metadata save (synchronous to ensure it completes)
        try:
            self._save_player_metadata()
        except (OSError, IOError, Exception) as e:
            print(f"⚠ Error saving metadata on close: {e}")

        # Close the current playback instance (not a dislike) then flush.
        try:
            self._finalize_outgoing()
            self._finalize_playback('stop')
        except Exception:
            pass

        # Final listening-history flush
        try:
            if self._history_save_job is not None:
                self.root.after_cancel(self._history_save_job)
                self._history_save_job = None
            self._flush_history()
        except Exception as e:
            print(f"⚠ Error saving listening history on close: {e}")

        try:
            self._flush_sound_settings()
        except Exception as e:
            print(f"⚠ Error saving sound settings on close: {e}")

        try:
            self._shutdown_engine()
        except Exception as e:
            print(f"⚠ Error stopping the sound engine: {e}")
        try:
            if self._main_calls_job is not None:
                self.root.after_cancel(self._main_calls_job)
                self._main_calls_job = None
        except Exception:
            pass

        # Release COM volume interface
        try:
            if self.volume_interface:
                self.volume_interface = None
        except (AttributeError, Exception):
            pass

        try:
            import keyboard
            keyboard.unhook_all_hotkeys()
        except (ImportError, Exception):
            pass

        # Stop the media-dial poll loop before the window is destroyed.
        if self._appcmd_poll_job:
            try:
                self.root.after_cancel(self._appcmd_poll_job)
            except Exception:
                pass
            self._appcmd_poll_job = None

        # Restore original WNDPROC before the window is destroyed — restoring
        # after destroy() would target a dead HWND. Only drop the callback
        # reference once restore actually succeeds: if it fails, the window is
        # still subclassed to our proc, so a GC'd callback would crash on the
        # next message (including the ones destroy() itself generates).
        try:
            if self._appcmd_old_proc and self._appcmd_set_window_long and self._appcmd_hwnd:
                GWLP_WNDPROC = -4
                self._appcmd_set_window_long(self._appcmd_hwnd, GWLP_WNDPROC, self._appcmd_old_proc)
                self._appcmd_old_proc = None
                self._appcmd_wndproc = None
        except Exception:
            pass

        try:
            self.root.destroy()
        except (tk.TclError, RuntimeError):
            pass
    
    def scan_library(self):
        """Scan music directories for tracks with caching"""
        if hasattr(self, '_scanning') and self._scanning:
            messagebox.showinfo("Scanning", "A scan is already in progress")
            return
        
        # Stop any ongoing background conversion
        
        self._scanning = True
        self.status_label.config(text="Starting library scan...")
        
        thread = threading.Thread(target=self._scan_library_thread, daemon=True)
        thread.start()
    
    def _scan_library_thread(self):
        """Background thread for library scanning"""
        try:
            # Helper to prevent crashes if window closes during scan
            def safe_after(delay, cb):
                try:
                    self.root.after(delay, cb)
                except:
                    pass
            
            self.root.after(0, lambda: self.status_label.config(text="Loading library..."))
            
            cached_data = self.load_cache()
            
            temp_all_tracks = []
            supported_extensions = {'.mp3', '.m4a', '.flac', '.ogg', '.wav', '.aac'}
            
            current_files = {}
            skipped_files = []  # Track files that were skipped
            
            for music_dir in self.music_dirs:
                if not os.path.exists(music_dir):
                    continue
                
                for root, dirs, files in os.walk(music_dir):
                    for file in files:
                        ext = os.path.splitext(file)[1].lower()
                        if ext in supported_extensions:
                            filepath = os.path.join(root, file)
                            try:
                                mtime = os.path.getmtime(filepath)
                                current_files[filepath] = mtime
                            except Exception as e:
                                skipped_files.append((filepath, str(e)))
                        elif ext in {'.jpg', '.png', '.jpeg', '.gif', '.bmp', '.webp', '.tif', '.tiff',  # Images
                                     '.xml', '.json', '.txt', '.nfo', '.ini', '.log',  # Metadata/Config
                                     '.frag', '.initfrag', '.m3u8', '.descriptor',  # Streaming/HLS
                                     '.cue', '.m3u', '.pls', '.wpl',  # Playlists
                                     '.db', '.dat', '.cache', '.tmp'}:  # System files
                            # Skip known non-audio files silently
                            pass
                        elif ext:  # Has extension but not supported
                            skipped_files.append((os.path.join(root, file), f"Unsupported extension: {ext}"))
            
            self._last_scan_total_files = len(current_files)
            
            file_types = {}
            for filepath in current_files.keys():
                ext = os.path.splitext(filepath)[1].lower()
                file_types[ext] = file_types.get(ext, 0) + 1
            
            if self.debug_scan_logging:
                print("\n" + "="*60)
                print("SCAN DEBUG - File types found:")
                for ext, count in sorted(file_types.items(), key=lambda x: x[1], reverse=True):
                    print(f"  {ext}: {count} files")
                print(f"Total files found: {len(current_files)}")
                print("="*60 + "\n")
            
            files_to_scan = []
            
            if cached_data:
                cached_mtimes = cached_data.get('mtimes', {})
                cached_tracks = cached_data.get('tracks', {})
                
                if self.debug_scan_logging:
                    print(f"\n[CACHE] Found {len(cached_tracks)} tracks in cache")

                cached_loaded = 0
                cached_skipped = 0
                for filepath, mtime in current_files.items():
                    cached_mtime = cached_mtimes.get(filepath)
                    if cached_mtime is None or cached_mtime != mtime:
                        files_to_scan.append(filepath)
                    else:
                        if filepath in cached_tracks:
                            track = cached_tracks[filepath]

                            if self._cached_track_needs_rescan(track):
                                files_to_scan.append(filepath)
                                if self.debug_scan_logging:
                                    print(f"⚠ Cache broken for {os.path.basename(filepath)} - will re-process")
                            else:
                                # V2: Apply unified player metadata
                                norm_fp = self._norm_path(filepath)
                                player_meta = self.player_metadata.get(norm_fp)
                                if player_meta:
                                    track._apply_player_metadata(player_meta)

                                temp_all_tracks.append(track)
                                cached_loaded += 1
                        else:
                            files_to_scan.append(filepath)
                            cached_skipped += 1

                if self.debug_scan_logging:
                    print(f"[CACHE] Loaded {cached_loaded} tracks from cache")
                    print(f"[CACHE] {cached_skipped} files in current_files not found in cache")
                    print(f"[CACHE] {len(files_to_scan)} files need scanning (new/modified/broken)")
            else:
                if self.debug_scan_logging:
                    print("[CACHE] No cache found - will scan all files")
                files_to_scan = list(current_files.keys())
            
            if files_to_scan:
                self.root.after(0, lambda: self.status_label.config(
                    text=f"Scanning {len(files_to_scan)} new/modified files..."))
                
                for i, filepath in enumerate(files_to_scan):
                    try:
                        if i % 10 == 0:
                            progress = f"Scanning {i+1}/{len(files_to_scan)} files..."
                            self.root.after(0, lambda p=progress: self.status_label.config(text=p))
                        
                        # V2: Get unified player metadata for this file
                        norm_fp = self._norm_path(filepath)
                        metadata = self.player_metadata.get(norm_fp)
                        
                        track = MusicTrack(filepath, meta_path=filepath, player_metadata=metadata)
                        temp_all_tracks.append(track)
                    
                    except (OSError, IOError) as e:
                        print(f"⚠ Cannot read file {os.path.basename(filepath)}: {e}")
                    except Exception as e:
                        print(f"⚠ Error scanning {os.path.basename(filepath)}: {e}")
                        # Don't crash the whole scan for one bad file
                
                final_status = f"✓ Scanned {len(files_to_scan)} files - Total: {len(temp_all_tracks)} tracks"
                self.root.after(0, lambda: self.status_label.config(text=final_status))
            else:
                final_status = f"✓ Loaded {len(temp_all_tracks)} tracks from cache"
                self.root.after(0, lambda: self.status_label.config(text=final_status))
            
            if self.debug_scan_logging:
                print("\n" + "="*60)
                print("SCAN COMPLETE - Final track counts:")
                final_types = {}
                for track in temp_all_tracks:
                    ext = os.path.splitext(track.original_filepath if hasattr(track, 'original_filepath') else track.filepath)[1].lower()
                    final_types[ext] = final_types.get(ext, 0) + 1
                for ext, count in sorted(final_types.items(), key=lambda x: x[1], reverse=True):
                    print(f"  {ext}: {count} tracks")
                print(f"\nTotal tracks in library: {len(temp_all_tracks)}")
                print(f"Total files found on disk: {self._last_scan_total_files}")
                print(f"Difference: {self._last_scan_total_files - len(temp_all_tracks)} files NOT loaded")
                try:
                    with_loved      = sum(1 for t in temp_all_tracks if getattr(t, 'loved', None) is not None)
                    with_last_played = sum(1 for t in temp_all_tracks if getattr(t, 'last_played', None) is not None)
                    with_skips      = sum(1 for t in temp_all_tracks if getattr(t, 'skips', None) is not None)
                    with_date_added = sum(1 for t in temp_all_tracks if getattr(t, 'date_added', None) is not None)
                    if any([with_loved, with_last_played, with_skips, with_date_added]):
                        print("\nPlayer metadata found:")
                        if with_loved       > 0: print(f"  Loved: {with_loved} tracks")
                        if with_last_played > 0: print(f"  Last Played: {with_last_played} tracks")
                        if with_skips       > 0: print(f"  Skips: {with_skips} tracks")
                        if with_date_added  > 0: print(f"  Date Added: {with_date_added} tracks")
                except Exception:
                    pass
                print("="*60 + "\n")

            # Log any files that were skipped during discovery
            if skipped_files:
                print("⚠ Files skipped during scan:")
                for filepath, reason in skipped_files[:20]:  # Show first 20
                    print(f"  {os.path.basename(filepath)}: {reason}")
                if len(skipped_files) > 20:
                    print(f"  ... and {len(skipped_files) - 20} more")
                print()
            
            safe_after(0, lambda: self._finish_scan(temp_all_tracks, current_files))
            
        except Exception as e:
            print(f"Error during library scan: {e}")
            import traceback
            traceback.print_exc()
            safe_after(0, lambda: self.status_label.config(text=f"✗ Scan error: {e}"))
            safe_after(0, lambda: setattr(self, '_scanning', False))
    
    def _finish_scan(self, tracks, current_files):
        """Finish scan on main thread"""
        try:
            # Deduplicate tracks by normalized filepath
            seen = set()
            unique_tracks = []
            duplicates = 0
            
            for track in tracks:
                norm_path = self._norm_path(getattr(track, 'original_filepath', track.filepath))
                if norm_path not in seen:
                    seen.add(norm_path)
                    unique_tracks.append(track)
                else:
                    duplicates += 1
            
            if duplicates > 0:
                print(f"🗑 Removed {duplicates} duplicate entries")

            self.library_tracks = unique_tracks          # Task 1: full source-of-truth
            self.all_tracks = list(self.library_tracks)  # active working subset
            self._rebuild_library_index()
            
            self.save_cache(current_files)
            
            # Remove metadata for deleted files
            self._cleanup_orphaned_metadata()
            
            self.update_library_stats()
            
            # History-aware Smart Shuffle picks the first track too - no random
            # pick then reshuffle around it (Task 3).
            if self.all_tracks:
                self.apply_shuffle(auto_play=bool(self.audio_available))
            self._start_loudness_scan()
            
            
        finally:
            self._scanning = False
    
    def _normalize_cached_track(self, track):
        """Backwards-safe defaults for older pickles"""
        if not hasattr(track, "original_filepath"):
            track.original_filepath = track.filepath

        for name, default in (
            ("loved", None),
            ("last_played", None),
            ("skips", None),
            ("date_added", None),
        ):
            if not hasattr(track, name):
                setattr(track, name, default)

        for name, default in (
            ("rating", 0),
            ("play_count", 0),
            ("bpm", 0),
            ("genre", ""),
            ("year", ""),
            ("duration", 0),
        ):
            if not hasattr(track, name):
                setattr(track, name, default)

        return track
    
    @staticmethod
    def _cached_track_needs_rescan(track):
        """A cached track is re-read when its file is gone or it has no duration (e.g. saved while an
        MP4 named .flac couldn't be parsed)."""
        return not os.path.exists(track.filepath) or not (track.duration or 0) > 0

    def load_cache(self):
        """Load cached library data from JSON"""
        try:
            if self.cache_file.exists():
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # Convert track dicts back to MusicTrack objects
                tracks_data = (data or {}).get("tracks", {})
                tracks = {}
                for fp, track_dict in tracks_data.items():
                    try:
                        # Get player metadata for this track
                        norm_fp = self._norm_path(fp)
                        player_meta = self.player_metadata.get(norm_fp)
                        track = MusicTrack.from_dict(track_dict, player_metadata=player_meta)
                        track = self._normalize_cached_track(track)
                        tracks[fp] = track
                    except (KeyError, TypeError, ValueError) as e:
                        print(f"Error loading cached track {fp}: {e}")
                        continue

                data['tracks'] = tracks
                return data

        except json.JSONDecodeError as e:
            print(f"Error decoding cache JSON: {e}")
        except (OSError, IOError) as e:
            print(f"Error loading cache: {e}")
        except Exception as e:
            print(f"Unexpected error loading cache: {e}")
        return None

    def save_cache(self, file_mtimes):
        """Save library data to JSON cache"""
        # Check disk space before saving
        if not self._check_disk_space(MIN_DISK_SPACE_MB):
            print("⚠ Skipping cache save due to low disk space")
            return

        try:
            # Serialize tracks to dicts
            tracks_dict = {}
            for track in self.all_tracks:
                tracks_dict[track.filepath] = track.to_dict()

            cache_data = {
                'mtimes': file_mtimes,
                'tracks': tracks_dict
            }
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, indent=2)
        except (OSError, IOError, TypeError, ValueError) as e:
            print(f"Error saving cache: {e}")
    
    def force_rescan(self):
        """Force a full rescan of the library"""
        # Stop any ongoing background conversion
        
        try:
            if self.cache_file.exists():
                self.cache_file.unlink()
        except Exception as e:
            print(f"Error deleting cache: {e}")
        
        self.scan_library()
    
    def _old_mp3_cache_size(self):
        """(files, bytes) in the retired M4A->MP3 conversion cache."""
        d = Path(self.convert_cache_dir)
        if not d.is_dir():
            return 0, 0
        files = [f for f in d.iterdir() if f.is_file()]
        return len(files), sum(f.stat().st_size for f in files)

    def _delete_old_mp3_cache(self):
        """Delete only the retired conversion cache folder; returns what was there."""
        import shutil
        n, size = self._old_mp3_cache_size()
        shutil.rmtree(self.convert_cache_dir, ignore_errors=True)
        return n, size

    def delete_old_mp3_cache(self):
        """Settings > Delete Old MP3 Cache: the sound engine plays M4A directly, so these copies are unused."""
        n, size = self._old_mp3_cache_size()
        if n == 0:
            messagebox.showinfo("Old MP3 Cache", "There is no old MP3 cache to delete.")
            return
        if not messagebox.askyesno(
                "Delete Old MP3 Cache",
                f"Delete {n:,} converted MP3 files ({size / 1e9:.1f} GB)?\n\n"
                "The sound engine plays M4A files directly, so these copies are no longer used.\n"
                "Your music files, play counts and favorites are not touched."):
            return
        n, size = self._delete_old_mp3_cache()
        self.status_label.config(text=f"Deleted {n:,} old MP3 files ({size / 1e9:.1f} GB)")

    def clear_cache(self):
        """Clear all cache files"""
        result = messagebox.askyesno(
            "Clear Cache",
            "This will delete:\n"
            "• Library cache\n"
            "• M4P Downloads metadata\n"
            "• Recent listening history (shuffle continuity)\n\n"
            "M4P metadata will start fresh.\n\n"
            "Continue?"
        )
        
        if not result:
            return
        
        deleted_count = 0
        deleted_size = 0
        
        try:
            if self.cache_file.exists():
                deleted_size += self.cache_file.stat().st_size
                self.cache_file.unlink()
                deleted_count += 1
            
            # V2: Clear unified player metadata
            if self.player_metadata_file.exists():
                deleted_size += self.player_metadata_file.stat().st_size
                self.player_metadata_file.unlink()
                deleted_count += 1

            # Listening history is session-continuity cache; clear it too
            if self.shuffle_history_file.exists():
                deleted_size += self.shuffle_history_file.stat().st_size
                self.shuffle_history_file.unlink()
                deleted_count += 1
            self._history_entries = []
            self._history_dirty = False

            if self.convert_cache_dir.exists():
                for file in self.convert_cache_dir.iterdir():
                    if file.is_file():
                        deleted_size += file.stat().st_size
                        file.unlink()
                        deleted_count += 1
            
            deleted_mb = deleted_size / (1024 * 1024)
            messagebox.showinfo(
                "Cache Cleared",
                f"Deleted {deleted_count} files\n"
                f"Freed {deleted_mb:.1f} MB\n\n"
                "Library will rescan on next startup."
            )
            
        except Exception as e:
            messagebox.showerror("Error", f"Error clearing cache:\n{e}")
    
    def clear_metadata(self):
        """Clear all player metadata (play counts, loved status, etc.) with double confirmation"""
        # First confirmation
        result1 = messagebox.askyesno(
            "Clear All Metadata?",
            "⚠️ WARNING: This will permanently delete:\n\n"
            "• All play counts\n"
            "• All skip counts\n"
            "• All loved/favorited tracks\n"
            "• All last played dates\n"
            "• All ratings\n\n"
            f"This affects {len(self.player_metadata)} tracks.\n\n"
            "Are you sure you want to continue?",
            icon='warning'
        )
        
        if not result1:
            return
        
        # Second confirmation
        result2 = messagebox.askyesno(
            "Final Confirmation",
            "⚠️ FINAL WARNING ⚠️\n\n"
            "This cannot be undone!\n\n"
            "All metadata will be permanently deleted.\n"
            "You will start completely fresh.\n\n"
            "Are you absolutely sure?",
            icon='warning'
        )
        
        if not result2:
            return
        
        try:
            # Clear in-memory metadata
            track_count = len(self.player_metadata)
            self.player_metadata.clear()
            
            # Delete metadata file
            if self.player_metadata_file.exists():
                self.player_metadata_file.unlink()

            # Metadata reset also clears persisted listening history (Task 3)
            # and the durable favorites store (Task 7c).
            for f in (self.shuffle_history_file, self.preferences_file):
                try:
                    if f.exists():
                        f.unlink()
                except OSError:
                    pass
            self._history_entries = []
            self._history_dirty = False
            self._favorites = {}

            # Reset all loaded tracks
            for track in self.all_tracks:
                track.play_count = 0
                track.skips = None
                track.rating = 0
                track.last_played = None
                track.loved = None
                track.loved_source = None
                track.date_added = None
                track.qualified_play_count = None
                track.manual_skip_count = None
                track.last_skipped_at = None
            
            # Update love button for current track if playing
            if self.is_playing and 0 <= self.current_index < len(self.current_playlist):
                current_track = self.current_playlist[self.current_index]
                self._update_love_button(current_track)
            
            messagebox.showinfo(
                "Metadata Cleared",
                f"✓ Successfully cleared metadata for {track_count} tracks\n\n"
                "All tracks reset to fresh state:\n"
                "• Play counts: 0\n"
                "• Loved status: none\n"
                "• Skip counts: 0\n"
                "• Ratings: 0\n\n"
                "Metadata will rebuild as you listen."
            )
            
        except Exception as e:
            messagebox.showerror("Error", f"Error clearing metadata:\n{e}")
    
    def show_shortcuts(self):
        """Show keyboard shortcuts dialog"""
        dialog = tk.Toplevel(self.root)
        dialog.title("Keyboard Shortcuts")
        dialog.geometry("380x320")
        dialog.resizable(False, False)
        
        dialog.transient(self.root)
        dialog.grab_set()
        
        frame = ttk.Frame(dialog, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(frame, text="Keyboard Shortcuts", 
                 font=('Arial', 14, 'bold')).pack(pady=(0, 20))
        
        shortcuts = [
            ("F9-F11", "Volume (mute/down/up)"),
            ("", "Works even when window not focused"),
            ("", ""),
            ("F5-F8", "Prev/Next/Play-Pause/Stop"),
            ("+ dial", "Whatever fnlock currently maps them to"),
        ]
        
        for key, action in shortcuts:
            row = ttk.Frame(frame)
            row.pack(fill=tk.X, pady=5)
            
            ttk.Label(row, text=key, font=('Courier', 11, 'bold'), 
                     width=8).pack(side=tk.LEFT)
            ttk.Label(row, text=action, font=('Arial', 10)).pack(side=tk.LEFT)
        
        ttk.Button(dialog, text="Close", command=dialog.destroy).pack(pady=20)
    
    def test_hotkeys(self):
        """Test if keyboard hotkeys are working"""
        dialog = tk.Toplevel(self.root)
        dialog.title("Test Hotkeys")
        dialog.geometry("500x400")
        
        ttk.Label(dialog, text="Press F5-F11 to test hotkeys (via fnlock)",
                 font=('Arial', 12, 'bold')).pack(pady=10)

        text = tk.Text(dialog, height=15, width=55, wrap=tk.WORD)
        text.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)

        text.insert(tk.END, "F9  → Volume Mute\n")
        text.insert(tk.END, "F10 → Volume Down\n")
        text.insert(tk.END, "F11 → Volume Up\n")
        text.insert(tk.END, "(F9-F11 work even when this window isn't focused)\n\n")
        text.insert(tk.END, "F5-F8 and the hardware dial: this app doesn't assume a fixed\n")
        text.insert(tk.END, "mapping for these — it reacts to whatever real media command\n")
        text.insert(tk.END, "fnlock actually sends (its own GUI/config.ini controls which\n")
        text.insert(tk.END, "F-key does what). Check fnlock's config to see the current\n")
        text.insert(tk.END, "mapping. fnlock's current setup targets this app directly when\n")
        text.insert(tk.END, "it's running, so these work regardless of focus (as fnlock is\n")
        text.insert(tk.END, "currently configured).\n\n")
        text.insert(tk.END, "Press any F5-F11 key and check the console for output.\n\n")
        
        try:
            import keyboard
            text.insert(tk.END, "✓ keyboard module is installed\n\n")
            
            # Check if running as admin
            try:
                import ctypes
                is_admin = ctypes.windll.shell32.IsUserAnAdmin()
                if is_admin:
                    text.insert(tk.END, "✓ Running as administrator\n")
                else:
                    text.insert(tk.END, "⚠ NOT running as administrator\n")
                    text.insert(tk.END, "  Some hotkeys may not work\n")
                    text.insert(tk.END, "  Right-click exe → Run as administrator\n")
            except:
                text.insert(tk.END, "? Could not check admin status\n")
            
            text.insert(tk.END, "\nIf hotkeys don't work:\n")
            text.insert(tk.END, "  1. Run as administrator\n")
            text.insert(tk.END, "  2. Close other apps using F keys\n")
            text.insert(tk.END, "  3. Check console for error messages\n")
            
        except ImportError:
            text.insert(tk.END, "✗ keyboard module NOT installed\n")
            text.insert(tk.END, "  Install: pip install keyboard\n")
            text.insert(tk.END, "  Then restart the app\n")
        
        text.config(state=tk.DISABLED)
        
        ttk.Button(dialog, text="Close", command=dialog.destroy).pack(pady=10)
    
    def show_about(self):
        """Show about dialog"""
        messagebox.showinfo(
            "About Custom Shuffle Music Player",
            "Custom Shuffle Music Player\n\n"
            "A standalone music player with advanced shuffle algorithms\n\n"
            "Features:\n"
            "• Smart shuffle algorithms\n"
            "• Every format (M4A, MP3, FLAC) played through FFmpeg\n"
            "• Song transitions, equalizer and loudness leveling\n"
            "• Metadata reading and display\n"
            "• Keyboard shortcuts (media keys)\n\n"
            "Cache location:\n"
            f"{self.cache_file.parent}"
        )
    
    def show_diagnostics(self):
        """Show library diagnostics"""
        dialog = tk.Toplevel(self.root)
        dialog.title("Library Diagnostics")
        dialog.geometry("700x600")
        
        text = tk.Text(dialog, height=30, width=80, wrap=tk.WORD)
        text.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(dialog, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        text.insert(tk.END, "=== LIBRARY DIAGNOSTICS ===\n\n")
        
        text.insert(tk.END, "AUDIO STATUS:\n")
        if self.audio_available:
            text.insert(tk.END, "  ✓ Audio device initialized\n")
        else:
            text.insert(tk.END, "  ✗ Audio device NOT available\n")
            text.insert(tk.END, "     Playback will not work\n")
        text.insert(tk.END, "\n")
        
        text.insert(tk.END, "FFMPEG STATUS:\n")
        if self.ffmpeg_path:
            text.insert(tk.END, f"  ✓ FFmpeg found: {self.ffmpeg_path}\n")
            try:
                result = subprocess.run([self.ffmpeg_path, "-version"], capture_output=True, text=True,
                                       encoding='utf-8', errors='ignore', timeout=2)
                if result.returncode == 0:
                    version_line = result.stdout.split('\n')[0]
                    text.insert(tk.END, f"  ✓ Version: {version_line}\n")
                    text.insert(tk.END, f"  ✓ .m4a files will be converted on-demand when played\n")
            except Exception as e:
                text.insert(tk.END, f"  ⚠ Error checking version: {e}\n")
        else:
            text.insert(tk.END, "  ✗ FFmpeg NOT FOUND\n")
            text.insert(tk.END, "     Install with: winget install FFmpeg\n")
            text.insert(tk.END, "     Then restart app\n")
            text.insert(tk.END, "     .m4a files will not be playable\n")
        text.insert(tk.END, "\n")
        
        text.insert(tk.END, "CACHE LOCATION:\n")
        text.insert(tk.END, f"  Directory: {self.cache_file.parent}\n")
        text.insert(tk.END, f"  Library cache: {self.cache_file.name}\n")
        text.insert(tk.END, f"  Player metadata: {self.player_metadata_file.name}\n")
        text.insert(tk.END, f"  Conversion cache: {self.convert_cache_dir.name}\n")
        
        cache_size = 0
        try:
            if self.cache_file.exists():
                cache_size += self.cache_file.stat().st_size
            if self.player_metadata_file.exists():
                cache_size += self.player_metadata_file.stat().st_size
            if self.convert_cache_dir.exists():
                for file in self.convert_cache_dir.iterdir():
                    if file.is_file():
                        cache_size += file.stat().st_size
            cache_mb = cache_size / (1024 * 1024)
            text.insert(tk.END, f"  Total cache size: {cache_mb:.1f} MB\n")
        except:
            pass
        text.insert(tk.END, "\n")
        
        text.insert(tk.END, "FOLDERS BEING SCANNED:\n")
        for folder in self.music_dirs:
            exists = "✓" if os.path.exists(folder) else "✗ (NOT FOUND)"
            text.insert(tk.END, f"  {exists} {folder}\n")
        text.insert(tk.END, "\n")
        
        text.insert(tk.END, f"CURRENT LIBRARY:\n")
        text.insert(tk.END, f"  Total tracks found: {len(self.all_tracks)}\n")
        artists = set(t.artist for t in self.all_tracks)
        albums = set(f"{t.artist}||{t.album}" for t in self.all_tracks)
        text.insert(tk.END, f"  Unique artists: {len(artists)}\n")
        text.insert(tk.END, f"  Unique albums: {len(albums)}\n\n")
        
        text.insert(tk.END, "FILE TYPES FOUND:\n")
        extensions = {}
        for track in self.all_tracks:
            ext = os.path.splitext(track.filepath)[1].lower()
            extensions[ext] = extensions.get(ext, 0) + 1
        
        for ext, count in sorted(extensions.items(), key=lambda x: x[1], reverse=True):
            text.insert(tk.END, f"  {ext}: {count} files\n")
        text.insert(tk.END, "\n")
        
        text.config(state=tk.DISABLED)
        
        ttk.Button(dialog, text="Close", command=dialog.destroy).pack(pady=10)
    
    def show_metadata(self):
        """Show all player metadata in console"""
        print("\n" + "="*80)
        print("PLAYER METADATA DUMP")
        print("="*80)
        
        if not self.player_metadata:
            print("No metadata found - fresh state")
            print("="*80 + "\n")
            return
        
        print(f"Total tracks with metadata: {len(self.player_metadata)}\n")
        
        # Sort by filepath for easier reading
        sorted_metadata = sorted(self.player_metadata.items(), key=lambda x: x[0])
        
        for filepath, metadata in sorted_metadata:
            # Find track in library to get readable name
            track_name = os.path.basename(filepath)
            for track in self.all_tracks:
                if self._norm_path(track.filepath) == filepath:
                    track_name = f"{track.artist} - {track.title}"
                    break
            
            print(f"\nFile: {track_name}")
            print(f"  Path: {filepath}")
            print(f"  Play Count: {metadata.get('play_count', 0)}")
            print(f"  Skip Count: {metadata.get('skip_count', 0)}")
            print(f"  Loved: {metadata.get('loved', False)}")
            print(f"  Rating: {metadata.get('rating', 0)}")
            
            last_played = metadata.get('last_played', 0)
            if last_played:
                dt = datetime.fromtimestamp(last_played)
                print(f"  Last Played: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
            else:
                print(f"  Last Played: Never")

            date_added = metadata.get('date_added', 0)
            if date_added:
                dt = datetime.fromtimestamp(date_added)
                print(f"  Date Added: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
            else:
                print(f"  Date Added: Not set")
        
        print("\n" + "="*80)
        print(f"END OF METADATA ({len(self.player_metadata)} tracks)")
        print("="*80 + "\n")
        
        # Also show summary
        messagebox.showinfo(
            "Metadata Displayed",
            f"Metadata for {len(self.player_metadata)} tracks\n"
            "has been printed to the console.\n\n"
            "Check the console window for full details."
        )
    
    def check_file_path(self):
        """Check if a specific file path exists and is in the library"""
        dialog = tk.Toplevel(self.root)
        dialog.title("Check File Path")
        dialog.geometry("700x400")
        
        ttk.Label(dialog, text="Enter file path to check:", 
                 font=('Arial', 10)).pack(pady=10)
        
        path_var = tk.StringVar()
        path_entry = ttk.Entry(dialog, textvariable=path_var, width=80)
        path_entry.pack(pady=5, padx=10)
        
        result_text = tk.Text(dialog, height=15, width=80, wrap=tk.WORD)
        result_text.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)
        
        def check():
            result_text.delete(1.0, tk.END)
            filepath = path_var.get().strip()
            
            if not filepath:
                result_text.insert(tk.END, "Please enter a file path")
                return
            
            result_text.insert(tk.END, f"Checking: {filepath}\n\n")
            
            # Check if file exists
            if os.path.exists(filepath):
                result_text.insert(tk.END, "✓ File exists on disk\n")
                
                # Check file size
                size = os.path.getsize(filepath)
                result_text.insert(tk.END, f"  Size: {size:,} bytes ({size/1024/1024:.2f} MB)\n")
                
                # Check extension
                ext = os.path.splitext(filepath)[1].lower()
                supported = {'.mp3', '.m4a', '.flac', '.ogg', '.wav', '.aac'}
                if ext in supported:
                    result_text.insert(tk.END, f"✓ Extension {ext} is supported\n")
                else:
                    result_text.insert(tk.END, f"✗ Extension {ext} is NOT supported\n")
                    result_text.insert(tk.END, f"  Supported: {', '.join(sorted(supported))}\n")
                
                # Check if in music directories
                in_music_dir = False
                for music_dir in self.music_dirs:
                    if filepath.lower().startswith(music_dir.lower()):
                        in_music_dir = True
                        result_text.insert(tk.END, f"✓ File is in scanned directory: {music_dir}\n")
                        break
                
                if not in_music_dir:
                    result_text.insert(tk.END, f"✗ File is NOT in any scanned music directory\n")
                    result_text.insert(tk.END, f"  Scanned directories:\n")
                    for d in self.music_dirs:
                        result_text.insert(tk.END, f"    {d}\n")
                
                # Check if in library
                found_in_library = False
                for track in self.all_tracks:
                    if self._norm_path(track.filepath) == self._norm_path(filepath):
                        found_in_library = True
                        result_text.insert(tk.END, f"\n✓ FOUND IN LIBRARY\n")
                        result_text.insert(tk.END, f"  Title: {track.title}\n")
                        result_text.insert(tk.END, f"  Artist: {track.artist}\n")
                        result_text.insert(tk.END, f"  Album: {track.album}\n")
                        result_text.insert(tk.END, f"  Duration: {track.format_duration()}\n")
                        break
                
                if not found_in_library:
                    result_text.insert(tk.END, f"\n✗ NOT FOUND IN LIBRARY\n")
                    result_text.insert(tk.END, f"\nPossible reasons:\n")
                    if ext not in supported:
                        result_text.insert(tk.END, f"  • Unsupported file format\n")
                    if not in_music_dir:
                        result_text.insert(tk.END, f"  • Not in a scanned music directory\n")
                    result_text.insert(tk.END, f"  • File might be corrupted or unreadable\n")
                    result_text.insert(tk.END, f"  • Scan might need to be refreshed\n")
                    
            else:
                result_text.insert(tk.END, "✗ File does NOT exist at this path\n")
                result_text.insert(tk.END, "\nCheck for:\n")
                result_text.insert(tk.END, "  • Typos in the path\n")
                result_text.insert(tk.END, "  • File was moved or deleted\n")
                result_text.insert(tk.END, "  • Incorrect drive letter or directory\n")
        
        ttk.Button(dialog, text="Check", command=check).pack(pady=5)
        ttk.Button(dialog, text="Close", command=dialog.destroy).pack(pady=5)
    
    def update_library_stats(self):
        """Update library statistics"""
        artists = set(t.artist for t in self.all_tracks)
        albums = set(f"{t.artist}||{t.album}" for t in self.all_tracks)
        
        self.total_tracks = len(self.all_tracks)
        self.total_artists = len(artists)
        self.total_albums = len(albums)
    
    def show_library_info(self):
        """Show library information dialog"""
        messagebox.showinfo(
            "Library Information",
            f"Total Tracks: {self.total_tracks}\n"
            f"Artists: {self.total_artists}\n"
            f"Albums: {self.total_albums}"
        )
    
    def change_folders(self):
        """Change music folder locations"""
        dialog = tk.Toplevel(self.root)
        dialog.title("Music Folders")
        dialog.geometry("600x300")
        
        ttk.Label(dialog, text="Music folder paths (one per line):", 
                 font=('Arial', 10)).pack(pady=10)
        
        text = tk.Text(dialog, height=10, width=70)
        text.pack(pady=10, padx=10)
        
        for folder in self.music_dirs:
            text.insert(tk.END, folder + "\n")
        
        def save_folders():
            content = text.get("1.0", tk.END).strip()
            new_dirs = [line.strip() for line in content.split("\n") if line.strip()]
            self.music_dirs = new_dirs
            dialog.destroy()
            self.scan_library()
        
        ttk.Button(dialog, text="Save & Rescan", command=save_folders).pack(pady=10)
    
    def set_ffmpeg_path(self):
        """Manually set FFmpeg path"""
        current = self.ffmpeg_path if self.ffmpeg_path else "Not found"
        
        winget_link = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe"
        suggestion = ""
        if winget_link.exists() and not self.ffmpeg_path:
            suggestion = f"\n\nSuggested location found:\n{winget_link}"
        
        result = messagebox.askyesno(
            "Set FFmpeg Path",
            f"Current FFmpeg: {current}{suggestion}\n\n"
            "Do you want to browse for ffmpeg.exe?\n\n"
            "Or click No to use 'ffmpeg' command directly."
        )
        
        if result:
            filepath = filedialog.askopenfilename(
                title="Select ffmpeg.exe",
                initialdir=str(Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links"),
                filetypes=[("FFmpeg executable", "ffmpeg.exe"), ("All files", "*.*")]
            )
            
            if filepath:
                try:
                    result = subprocess.run([filepath, "-version"], capture_output=True, text=True,
                                           encoding='utf-8', errors='ignore', timeout=2)
                    if result.returncode == 0 and "ffmpeg" in result.stdout.lower():
                        self.ffmpeg_path = filepath
                        self._ffmpeg_path_changed()
                        messagebox.showinfo("Success", f"FFmpeg path set to:\n{filepath}\n\nSongs play through it right away.")
                    else:
                        messagebox.showerror("Error", "This doesn't appear to be a valid FFmpeg executable.")
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to verify FFmpeg:\n{e}")
        else:
            try:
                result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True,
                                       encoding='utf-8', errors='ignore', timeout=2)
                if result.returncode == 0:
                    self.ffmpeg_path = "ffmpeg"
                    self._ffmpeg_path_changed()
                    messagebox.showinfo("Success", "Using ffmpeg from PATH. Songs play through it right away.")
                else:
                    messagebox.showerror("Error", "'ffmpeg' command doesn't work. Try browsing for ffmpeg.exe instead.")
            except:
                messagebox.showerror("Error", "'ffmpeg' command not found. Try browsing for ffmpeg.exe instead.")
    
    def filter_library(self):
        """Filter library by artist, album, genre, etc."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Filter Library")
        dialog.geometry("500x400")
        
        ttk.Label(dialog, text="Filter by:", font=('Arial', 12, 'bold')).pack(pady=10)
        
        filter_frame = ttk.Frame(dialog, padding="10")
        filter_frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(filter_frame, text="Artist contains:").grid(row=0, column=0, sticky=tk.W, pady=5)
        artist_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=artist_var, width=30).grid(row=0, column=1, pady=5)
        
        ttk.Label(filter_frame, text="Album contains:").grid(row=1, column=0, sticky=tk.W, pady=5)
        album_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=album_var, width=30).grid(row=1, column=1, pady=5)
        
        ttk.Label(filter_frame, text="Genre contains:").grid(row=2, column=0, sticky=tk.W, pady=5)
        genre_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=genre_var, width=30).grid(row=2, column=1, pady=5)
        
        ttk.Label(filter_frame, text="Min rating:").grid(row=3, column=0, sticky=tk.W, pady=5)
        rating_var = tk.IntVar(value=0)
        ttk.Spinbox(filter_frame, from_=0, to=100, textvariable=rating_var, width=10).grid(row=3, column=1, sticky=tk.W, pady=5)
        
        result_label = ttk.Label(dialog, text="", font=('Arial', 10))
        result_label.pack(pady=10)
        
        def apply_filter():
            artist_filter = artist_var.get().lower()
            album_filter  = album_var.get().lower()
            genre_filter  = genre_var.get().lower()
            min_rating    = rating_var.get()

            # Task 1: filter from full library_tracks (non-destructive)
            source = self.library_tracks if self.library_tracks else self.all_tracks
            filtered = [
                t for t in source
                if (not artist_filter or artist_filter in t.artist.lower())
                and (not album_filter  or album_filter  in t.album.lower())
                and (not genre_filter  or genre_filter  in t.genre.lower())
                and t.rating >= min_rating
            ]

            if filtered:
                self.all_tracks = filtered
                self.update_library_stats()
                self.apply_shuffle()
                dialog.destroy()
                messagebox.showinfo("Filter Applied", f"Filtered to {len(filtered)} tracks")
            else:
                result_label.config(text="No tracks match these filters!")

        def reset_filter():
            # Task 1: restore from library_tracks without triggering a full rescan
            if self.library_tracks:
                self.all_tracks = list(self.library_tracks)
                self.update_library_stats()
                self.apply_shuffle()
                dialog.destroy()
            else:
                self.scan_library()
                dialog.destroy()
        
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Apply Filter", command=apply_filter).grid(row=0, column=0, padx=5)
        ttk.Button(btn_frame, text="Reset to All", command=reset_filter).grid(row=0, column=1, padx=5)
    
    def apply_shuffle(self, algo=None, auto_play=False):
        """Apply selected shuffle algorithm"""
        if not self.all_tracks:
            messagebox.showwarning("Warning", "No tracks loaded")
            return
        
        if algo is None:
            algo = "Smart Shuffle"
        self._active_algo = algo

        # Always increment token to cancel any in-flight shuffle work
        self._shuffle_token = getattr(self, "_shuffle_token", 0) + 1
        token = self._shuffle_token
        
        self.status_label.config(text=f"Applying {algo}...")
        self.root.update_idletasks()
        
        if algo == "Smart Shuffle":
            self._set_navigation_enabled(False)

            # Snapshot the fixed prefix on the main thread BEFORE the worker
            # starts (Task 4): the active track (playing or paused) plus the
            # untouched queued block.  Skipped for auto_play (startup / wrap),
            # which deliberately starts fresh from index 0.
            if auto_play:
                active_track, queued_block = None, []
            else:
                active_track = self._active_track()
                queued_block = list(self._get_queued_tracks())

            tracks_snapshot = playlist_queue.reshuffle_tail_input(
                self.all_tracks, active_track, queued_block, norm_fn=self._norm_path)

            self.status_label.config(text="Shuffling...")

            recent_snapshot, context_snapshot = self._shuffle_context()

            def compute_shuffle():
                try:
                    result = CustomShuffleAlgorithm.smart_shuffle(
                        tracks_snapshot, self.shuffle_config,
                        recent_history=recent_snapshot,
                        context=context_snapshot,
                    )

                    def apply_if_latest():
                        stale = (getattr(self, "_shuffle_token", 0) != token)
                        # Also stale if playback auto-advanced past the track we
                        # snapshotted while the worker ran (Task 4).
                        if not stale and not auto_play and active_track is not None \
                                and self._active_track() is not active_track:
                            stale = True
                        if stale:
                            self._set_navigation_enabled(True)
                            self.status_label.config(
                                text=f"✓ {len(self.current_playlist)} tracks")
                            return
                        self._apply_shuffle_results(
                            result, algo, auto_play=auto_play,
                            active_track=active_track, queued_block=queued_block)
                    
                    try:
                        self.root.after(0, apply_if_latest)
                    except:
                        pass
                except Exception as e:
                    print(f"⚠ Smart Shuffle error: {e}")
                    try:
                        self.root.after(0, lambda: self.status_label.config(text="⚠ Shuffle failed"))
                    except:
                        pass
                    try:
                        self.root.after(0, lambda: self._set_navigation_enabled(True))
                    except:
                        pass
            
            threading.Thread(target=compute_shuffle, daemon=True).start()
            return
        
        # Non-smart shuffles should never leave nav disabled
        self._set_navigation_enabled(True)

        # Only Truly Random remains (everything else is Smart Shuffle).  Same
        # fixed-prefix preservation as Smart Shuffle (Task 4).
        if auto_play:
            active_track, queued_block = None, []
        else:
            active_track = self._active_track()
            queued_block = list(self._get_queued_tracks())

        tail = CustomShuffleAlgorithm.truly_random(
            playlist_queue.reshuffle_tail_input(
                self.all_tracks, active_track, queued_block, norm_fn=self._norm_path))
        self.current_playlist, self.current_index, self.queued_count = \
            playlist_queue.reshuffle_compose(active_track, queued_block, tail)
        self.is_temp_view = False
        self._play_context = None

        # Clear search filter to show full shuffled playlist
        self.filtered_playlist = []
        self.search_var.set('')

        self.display_playlist()
        self.status_label.config(text=f"✓ {algo} applied to {len(self.current_playlist)} tracks")

        if auto_play:
            self.play_track_at_index(0)
    
    def _apply_shuffle_results(self, playlist, algo, auto_play=False,
                               active_track=None, queued_block=None):
        """Apply shuffle results on main thread.

        ``playlist`` is the freshly shuffled tail (already excludes the fixed
        prefix).  The prefix - ``active_track`` then ``queued_block`` - is
        re-attached at the front so the play position and Up Next survive the
        reshuffle (Task 4).  For auto_play both are None/empty and playback
        starts at index 0.
        """
        queued_block = queued_block or []
        self.current_playlist, self.current_index, self.queued_count = \
            playlist_queue.reshuffle_compose(active_track, queued_block, playlist)

        self.is_temp_view = False
        self._play_context = None
        self.filtered_playlist = []  # Clear search filter
        self.search_var.set('')  # Clear search box
        self.display_playlist()
        self.status_label.config(text=f"✓ {algo} applied to {len(self.current_playlist)} tracks")

        self._set_navigation_enabled(True)

        if auto_play:
            self.play_track_at_index(0)
    
    def display_playlist(self):
        """Display playlist in treeview"""
        for item in self.playlist_tree.get_children():
            self.playlist_tree.delete(item)
        
        # Use filtered playlist if search is active, otherwise show full playlist
        display_list = self.filtered_playlist if self.filtered_playlist else self.current_playlist
        
        for i, track in enumerate(display_list, 1):
            self.playlist_tree.insert('', 'end', text=str(i),
                                     values=(track.artist, track.title, track.album,
                                            track.format_duration()))
        
        if self.is_playing and 0 <= self.current_index < len(self.current_playlist):
            # Find currently playing track in the display list
            current_track = self.current_playlist[self.current_index]
            try:
                display_idx = display_list.index(current_track)
                children = self.playlist_tree.get_children()
                if 0 <= display_idx < len(children):
                    item = children[display_idx]
                    self.playlist_tree.selection_set(item)
                    self.playlist_tree.see(item)
            except ValueError:
                # Track not in display list (filtered out)
                pass
    
    def on_track_double_click(self, event):
        """Play track on double-click"""
        selection = self.playlist_tree.selection()
        if selection:
            item = selection[0]
            display_index = int(self.playlist_tree.item(item, 'text')) - 1

            # If search is active, the displayed (filtered) list takes over as
            # the main playlist, in displayed order, starting at the click.
            if self.filtered_playlist:
                if display_index < len(self.filtered_playlist):
                    self._takeover_playlist(self.filtered_playlist, display_index)
            else:
                self.queued_count = 0
                self.play_track_at_index(display_index)

    def _takeover_playlist(self, tracks, start_index, context=None):
        """Make the given track order the main playlist and play from start_index.

        A search/filtered view (context None) drops Up Next, like it always has. An album or artist
        pick (context ('album'|'artist', key)) keeps the songs in Up Next and plays them after its own
        songs, keeps the search text while the Albums tab shows, and measures the context's songs for
        album leveling. Either way the list ends in a fresh shuffle (is_temp_view).
        """
        new_list = list(tracks)
        if context is not None:
            have = {self._norm_path(t.filepath) for t in new_list}
            new_list += [t for t in self._get_queued_tracks() if self._norm_path(t.filepath) not in have]
        self.current_playlist = new_list
        self.filtered_playlist = []
        if context is None or not self._albums_tab_showing():
            self.search_var.set('')
        else:
            self._playlist_query = None   # the kept text applies to the new list when Playlist shows
        self.queued_count = 0
        self.is_temp_view = True
        self._play_context = context
        self.display_playlist()
        self.play_track_at_index(start_index)
        if context is not None:
            self._measure_context()

    # ---- Albums: in-order album/artist plays and album leveling -----------

    def _rebuild_library_index(self):
        t0 = time.perf_counter()
        try:
            self.library_index = library_index.build_index(self.library_tracks, roots=self.music_dirs)
        except Exception as e:
            print(f"⚠ Album index failed: {e}")
            self.library_index = None
            return
        print(f"✓ Album index: {len(self.library_index.artists)} artists "
              f"in {(time.perf_counter() - t0) * 1000:.0f} ms")
        refresh = getattr(self, '_refresh_albums_tree', None)
        if refresh is not None:
            refresh()

    def _albums_tab_showing(self):
        nb = getattr(self, 'playlist_notebook', None)
        try:
            return nb is not None and nb.select() == str(self.albums_tab)
        except (RuntimeError, tk.TclError):
            return False

    def play_album(self, album_key, start_track=None):
        """Play an album in track order, from its first song or from start_track."""
        idx = self.library_index
        tracks = idx.album_tracks(album_key) if idx is not None else []
        if not tracks:
            return
        start = 0
        if start_track is not None:
            want = self._norm_path(start_track.filepath)
            start = next((i for i, t in enumerate(tracks) if self._norm_path(t.filepath) == want), 0)
        self._takeover_playlist(tracks, start, context=('album', album_key))

    def play_artist(self, artist_key):
        """Play all of an artist's albums, oldest first, each in track order."""
        idx = self.library_index
        tracks = idx.artist_tracks(artist_key) if idx is not None else []
        if tracks:
            self._takeover_playlist(tracks, 0, context=('artist', artist_key))

    def queue_album(self, album_key):
        """Add an album's songs, in order, to the end of Up Next (the playing song is skipped)."""
        idx = self.library_index
        tracks = idx.album_tracks(album_key) if idx is not None else []
        if not tracks:
            return
        playing = None
        if 0 <= self.current_index < len(self.current_playlist):
            playing = self._norm_path(self.current_playlist[self.current_index].filepath)
        added = 0
        started = not self.current_playlist   # then the first song starts playing instead of queueing
        for t in tracks:
            if self._norm_path(t.filepath) == playing:
                continue
            self.queue_track(t, play_next=False, refresh=False)
            added += 1
        if started:
            added -= 1
        self.display_playlist()
        self.status_label.config(text=f"Added {added} songs to Up Next")

    def _context_albums(self, context):
        idx = self.library_index
        if idx is None or context is None:
            return []
        kind, key = context
        if kind == 'album':
            album = idx.album(key)
            return [album] if album is not None else []
        artist = idx.artist(key)
        return list(artist.albums) if artist is not None else []

    def _measure_context(self, albums=None):
        """Measure the play context's unmeasured songs, album by album in play order, ahead of the
        background scan. After each album the next song is prepared again so it can pick up album gain."""
        context = self._play_context
        svc = getattr(self, '_loudness', None)
        if context is None or svc is None or not self.audio_settings['leveling_enabled']:
            return
        if albums is None:
            albums = self._context_albums(context)
        for i, album in enumerate(albums):
            missing = [t for t in album.tracks
                       if svc.get(t.filepath) is None and not svc.known_bad(t.filepath)]
            if missing:
                rest = albums[i + 1:]

                def done():
                    if self._play_context is context:   # same pick (a re-pick starts its own chain)
                        self._prepare_next()
                        self._measure_context(rest)

                self._analyze_then(missing, done)
                return

    def _context_album_for(self, track):
        """track's Album when it belongs to the play context (Up Next strangers don't), else None."""
        context = getattr(self, '_play_context', None)
        idx = getattr(self, 'library_index', None)
        if context is None or idx is None:
            return None
        album = idx.album_of(track)
        if album is None:
            return None
        kind, key = context
        if (kind == 'album' and album.key == key) or (kind == 'artist' and album.artist_key == key):
            return album
        return None

    def _context_kind_for(self, track):
        """'album'/'artist' for the listening log when track plays as part of the context, else None."""
        if self._context_album_for(track) is None:
            return None
        return self._play_context[0]

    def _album_gain_for(self, track):
        """Album leveling gain for a song of the play context, or None (song gain applies).

        None until every song of the album is measured; songs that can't be measured are left out."""
        album = self._context_album_for(track)
        svc = getattr(self, '_loudness', None)
        if album is None or svc is None:
            return None
        louds = []
        for t in album.tracks:
            loud = svc.get(t.filepath)
            if loud is None:
                if svc.known_bad(t.filepath):
                    continue
                return None
            louds.append(loud)
        return album_gain_db(louds)
    
    def _schedule_filter(self):
        """Schedule a debounced search filter update"""
        if self._search_job:
            try:
                self.root.after_cancel(self._search_job)
            except (RuntimeError, KeyError):
                pass  # Job already cancelled
        self._search_job = self.root.after(SEARCH_DEBOUNCE_MS, self._run_filter)
    
    def _run_filter(self):
        """Execute the search filter (called after debounce delay) on whichever tab is showing"""
        self._search_job = None
        if self._albums_tab_showing():
            self._filter_albums()
        elif self.search_var.get().lower().strip() != self._playlist_query:
            # a tab switch with the same text doesn't redraw the whole playlist
            self.filter_playlist()
    
    def filter_playlist(self):
        """Filter playlist based on search query - searches active playlist/view"""
        query = self.search_var.get().lower().strip()
        self._playlist_query = query

        if not query:
            self.filtered_playlist = []
            self.display_playlist()
            if hasattr(self, 'status_label'):
                self.status_label.config(text=f"{len(self.current_playlist)} tracks")
            return

        # Task 2: search within the active playlist/view (not the full library)
        self.filtered_playlist = [
            t for t in self.current_playlist
            if query in t.title.lower()
            or query in t.artist.lower()
            or query in t.album.lower()
            or query in t.genre.lower()
        ]

        self.display_playlist()

        if hasattr(self, 'status_label'):
            self.status_label.config(
                text=f"Showing {len(self.filtered_playlist)} of {len(self.current_playlist)} tracks"
            )
    
    def clear_search(self):
        """Clear search box and show full playlist"""
        self.search_var.set('')
        self.search_entry.focus()
    
    def _play_first_result(self):
        """Play first search result when Enter is pressed - takes over the playlist"""
        if self._albums_tab_showing():
            q = self.search_var.get().strip()
            hits = self.library_index.search(q) if (q and self.library_index is not None) else []
            if hits:
                hit = hits[0]
                self.play_album(hit.album.key, start_track=None if hit.whole_album else hit.tracks[0])
            return
        if not self.filtered_playlist:
            return

        self._takeover_playlist(self.filtered_playlist, 0)
    
    # Up Next Queue Methods
    
    def _on_playlist_right_click(self, event):
        """Handle right-click on playlist row"""
        try:
            row_id = self.playlist_tree.identify_row(event.y)
            if row_id:
                self.playlist_tree.selection_set(row_id)
                track = self._get_selected_track()
                has_album = (track is not None and self.library_index is not None
                             and self.library_index.album_of(track) is not None)
                for label in ("Play album from start", "Play artist", "Add album to Up Next"):
                    self.playlist_menu.entryconfigure(label, state=tk.NORMAL if has_album else tk.DISABLED)
                self.playlist_menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self.playlist_menu.grab_release()
            except (RuntimeError, tk.TclError):
                pass  # Menu already released or window destroyed
    
    def _get_selected_track(self):
        """Get the track object for currently selected Treeview row"""
        sel = self.playlist_tree.selection()
        if not sel:
            return None
        
        item = sel[0]
        try:
            n = int(self.playlist_tree.item(item, 'text')) - 1
        except:
            return None
        
        # Use filtered playlist if search is active
        tracks_to_show = self.filtered_playlist if self.filtered_playlist else self.current_playlist
        
        if 0 <= n < len(tracks_to_show):
            return tracks_to_show[n]
        return None
    
    def _ctx_play_next(self):
        """Context menu: Play Next"""
        track = self._get_selected_track()
        if track:
            self.queue_track(track, play_next=True)
    
    def _ctx_add_up_next(self):
        """Context menu: Add to Up Next"""
        track = self._get_selected_track()
        if track:
            self.queue_track(track, play_next=False)

    def _ctx_album_action(self, action):
        """Playlist context menu: album/artist actions for the selected song."""
        track = self._get_selected_track()
        album = self.library_index.album_of(track) if (track and self.library_index is not None) else None
        if album is not None:
            self._album_action(album, action)

    def _album_action(self, album, action):
        if action == 'album':
            self.play_album(album.key)
        elif action == 'artist':
            self.play_artist(album.artist_key)
        elif action == 'queue':
            self.queue_album(album.key)

    # ---- Albums tab ------------------------------------------------------

    def _setup_albums_tab(self, frame):
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=('Info',), show='tree headings', height=5, selectmode='browse')
        tree.heading('#0', text='Artist / Album / Song')
        tree.heading('Info', text='Info')
        tree.column('#0', width=380)
        tree.column('Info', width=130, stretch=False, anchor=tk.E)
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.albums_tree = tree
        self._albums_rows = {}         # iid -> ('artist', key) | ('album', key) | ('song', album_key, track)
        self._albums_artist_iids = {}  # artist key -> iid
        self._albums_pending = {}      # album iid -> album key, songs not inserted yet
        self._albums_query = None      # search text the tree shows (None: rebuild on next filter)
        self._type_jump = ''
        self._type_jump_job = None
        self.albums_menu = tk.Menu(self.root, tearoff=0)
        tree.bind('<<TreeviewOpen>>', self._on_album_open)
        tree.bind('<Double-1>', self._on_albums_double_click)
        tree.bind('<Return>', lambda e: self._play_albums_row(self.albums_tree.focus()) or 'break')
        tree.bind('<Button-3>', self._on_albums_right_click)
        tree.bind('<Control-Button-1>', self._on_albums_right_click)
        tree.bind('<Key>', self._on_albums_key)
        self._refresh_albums_tree()

    def _refresh_albums_tree(self):
        """The library index changed: rebuild the tree, keeping the Albums search."""
        if not hasattr(self, 'albums_tree'):
            return
        self._albums_query = None
        if self._albums_tab_showing():
            self._filter_albums()
        else:
            self._fill_albums_tree(None)
            self._albums_query = ''

    def _filter_albums(self):
        q = self.search_var.get().strip()
        if q == self._albums_query:
            return
        self._albums_query = q
        idx = self.library_index
        if not q or idx is None:
            self._fill_albums_tree(None)
            if idx is not None:
                self.status_label.config(text=f"{len(idx.artists)} artists")
            return
        hits = idx.search(q)
        self._fill_albums_tree(hits)
        songs = sum(len(h.tracks) for h in hits if not h.whole_album)
        self.status_label.config(text=f"{len(hits)} albums match" + (f" ({songs} songs)" if songs else ""))

    def _fill_albums_tree(self, hits):
        """hits None: every artist, folded. Else the search hits: their artists open, albums with song-level
        matches open showing those songs (the first ALBUMS_SEARCH_OPEN_CAP), other albums folded."""
        tree = self.albums_tree
        tree.delete(*tree.get_children())
        self._albums_rows.clear()
        self._albums_artist_iids.clear()
        self._albums_pending.clear()
        idx = self.library_index
        if idx is None:
            return
        if hits is None:
            for artist in idx.artists:
                a_iid = self._insert_artist_row(artist, open_=False)
                for album in artist.albums:
                    self._insert_album_row(a_iid, album)
            return
        opened = 0
        for hit in hits:
            artist = idx.artist(hit.album.artist_key)
            if artist is None:
                continue
            a_iid = self._albums_artist_iids.get(artist.key) or self._insert_artist_row(artist, open_=True)
            if not hit.whole_album and opened < ALBUMS_SEARCH_OPEN_CAP:
                self._insert_album_row(a_iid, hit.album, songs=hit.tracks)
                opened += 1
            else:
                self._insert_album_row(a_iid, hit.album)

    def _insert_artist_row(self, artist, open_):
        n = len(artist.albums)
        iid = self.albums_tree.insert('', 'end', text=artist.name, open=open_,
                                      values=(f"{n} album{'s' if n != 1 else ''}",))
        self._albums_rows[iid] = ('artist', artist.key)
        self._albums_artist_iids[artist.key] = iid
        return iid

    def _insert_album_row(self, parent, album, songs=None):
        """songs None: fold the album and load its songs when it is first opened."""
        n = len(album.tracks)
        info = f"{n} song{'s' if n != 1 else ''}"
        if album.year:
            info = f"{album.year} · {info}"
        iid = self.albums_tree.insert(parent, 'end', text=album.name, values=(info,), open=songs is not None)
        self._albums_rows[iid] = ('album', album.key)
        if songs is None:
            self.albums_tree.insert(iid, 'end', text='…')   # placeholder so the row can fold open
            self._albums_pending[iid] = album.key
        else:
            self._insert_song_rows(iid, album, songs)
        return iid

    def _insert_song_rows(self, parent, album, songs):
        for t in songs:
            key = library_index.song_sort_key(t)
            num = '' if key[0] else (f"{key[1]}-{key[2]:02}" if key[1] > 1 else str(key[2]))
            dur = t.format_duration() if hasattr(t, 'format_duration') else ''
            iid = self.albums_tree.insert(parent, 'end', text=f"{num:>4}   {t.title}", values=(dur,))
            self._albums_rows[iid] = ('song', album.key, t)

    def _on_album_open(self, event):
        iid = self.albums_tree.focus()
        album_key = self._albums_pending.pop(iid, None)
        if album_key is None or self.library_index is None:
            return
        album = self.library_index.album(album_key)
        self.albums_tree.delete(*self.albums_tree.get_children(iid))
        if album is not None:
            self._insert_song_rows(iid, album, album.tracks)

    def _play_albums_row(self, iid):
        row = self._albums_rows.get(iid) if iid else None
        if row is None:
            return
        if row[0] == 'artist':
            self.play_artist(row[1])
        elif row[0] == 'album':
            self.play_album(row[1])
        else:
            self.play_album(row[1], start_track=row[2])

    def _on_albums_double_click(self, event):
        iid = self.albums_tree.identify_row(event.y)
        if not iid or self.albums_tree.identify_element(event.x, event.y).endswith('indicator'):
            return None   # the fold arrow folds, as usual
        self._play_albums_row(iid)
        return 'break'    # play without also folding the row

    def _on_albums_right_click(self, event):
        tree = self.albums_tree
        iid = tree.identify_row(event.y)
        row = self._albums_rows.get(iid) if iid else None
        idx = self.library_index
        if row is None or idx is None:
            return
        tree.selection_set(iid)
        tree.focus(iid)
        menu = self.albums_menu
        menu.delete(0, tk.END)
        if row[0] == 'artist':
            menu.add_command(label="Play artist", command=lambda k=row[1]: self.play_artist(k))
        else:
            album = idx.album(row[1])
            if album is None:
                return
            if row[0] == 'song':
                track = row[2]
                menu.add_command(label="Play Next", command=lambda: self.queue_track(track, play_next=True))
                menu.add_command(label="Add to Up Next", command=lambda: self.queue_track(track))
                menu.add_separator()
            menu.add_command(label="Play album from start", command=lambda: self._album_action(album, 'album'))
            menu.add_command(label="Play artist", command=lambda: self._album_action(album, 'artist'))
            menu.add_command(label="Add album to Up Next", command=lambda: self._album_action(album, 'queue'))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except (RuntimeError, tk.TclError):
                pass

    def _albums_artist_key_at(self, iid):
        tree = self.albums_tree
        while iid and tree.parent(iid):
            iid = tree.parent(iid)
        row = self._albums_rows.get(iid) if iid else None
        return row[1] if row and row[0] == 'artist' else None

    def _on_albums_key(self, event):
        """Type-to-jump: typed letters jump to the next artist whose name starts with them."""
        ctrl, alt = bool(event.state & 0x4), bool(event.state & 0x20000)
        if ctrl != alt:   # Ctrl or Alt alone: leave shortcuts alone (both = AltGr, a typed letter)
            return None
        ch = event.char
        if not ch or not ch.isprintable() or (ch == ' ' and not self._type_jump):
            return None
        idx = self.library_index
        if idx is None or not idx.artists:
            return 'break'
        if self._type_jump_job is not None:
            self.root.after_cancel(self._type_jump_job)
        self._type_jump += ch
        self._type_jump_job = self.root.after(TYPE_JUMP_RESET_MS, self._reset_type_jump)
        from_key = self._albums_artist_key_at(self.albums_tree.focus())
        if from_key is not None and len(self._type_jump) == 1:
            keys = [a.key for a in idx.artists]   # a fresh letter starts after the current artist
            pos = keys.index(from_key) if from_key in keys else -1
            from_key = keys[(pos + 1) % len(keys)]
        artist = idx.find_artist(self._type_jump, from_key=from_key)
        iid = self._albums_artist_iids.get(artist.key) if artist is not None else None
        if iid:
            self.albums_tree.selection_set(iid)
            self.albums_tree.focus(iid)
            self.albums_tree.see(iid)
        return 'break'

    def _reset_type_jump(self):
        self._type_jump = ''
        self._type_jump_job = None
    
    def queue_track(self, track, play_next=False, refresh=True):
        """Move track into the queued block right after the current song.

        refresh=False skips the status line and the playlist redraw (queue_album queues many songs, then
        redraws once)."""
        fp = getattr(track, 'filepath', None)
        if not fp:
            return

        # Task 2: normalise paths for reliable cross-case/separator comparison
        norm_fp = self._norm_path(fp)

        if not self.current_playlist:
            self.current_playlist = [track]
            self.current_index = 0
            self.is_temp_view = False
            self.play_track_at_index(0)
            return

        # Don't queue currently playing track
        if 0 <= self.current_index < len(self.current_playlist):
            current_track = self.current_playlist[self.current_index]
            if self._norm_path(getattr(current_track, 'filepath', '')) == norm_fp:
                self.status_label.config(text="Cannot queue currently playing track")
                return

        existing_index = None
        for i, t in enumerate(self.current_playlist):
            if self._norm_path(getattr(t, 'filepath', '')) == norm_fp:
                existing_index = i
                break

        remove_index, insert_index, new_current_index, new_queued_count = (
            playlist_queue.queue_target(
                self.current_index, self.queued_count, play_next, existing_index
            )
        )

        if remove_index is not None:
            self.current_playlist.pop(remove_index)
        self.current_playlist.insert(insert_index, track)
        self.current_index = new_current_index
        self.queued_count = new_queued_count

        # Invalidate any in-flight shuffle: it snapshotted the old queue, so
        # its result would drop this newly-queued track (Task 4).
        self._shuffle_token = getattr(self, "_shuffle_token", 0) + 1

        if not refresh:
            return
        if play_next:
            self.status_label.config(text=f"▶ Will play next: {track.title}")
        else:
            self.status_label.config(text=f"Added to Up Next ({self.queued_count} queued)")

        self.display_playlist()
    
    def _get_queued_tracks(self):
        """Tracks in the queued block right after the current song"""
        start = self.current_index + 1
        end = start + self.queued_count
        return self.current_playlist[start:end]

    # ---- Task 8: listening-outcome tracking ------------------------------

    def _start_playback_tracking(self, track):
        """Begin a fresh audible-time instance for `track`."""
        from playback_feedback import PlaybackTracker
        self._pb_seq += 1
        dur = getattr(track, 'duration', 0) or None
        self._pb_tracker = PlaybackTracker(
            self._pb_seq, duration=dur, now=time.monotonic())
        self._pb_tracker.ctx = self._context_kind_for(track)   # for the listening log
        self._pb_track = track

    def _finalize_playback(self, reason, transition='cut'):
        """Close the current playback instance once and fold its outcome into
        the versioned counters.  Idempotent (safe to call from every path)."""
        tracker = self._pb_tracker
        track = self._pb_track
        if tracker is None or track is None:
            return
        try:
            outcome = tracker.finalize(time.monotonic(), reason)
        except Exception as e:
            print(f"! playback finalize failed: {e}")
            outcome = None
        # Detach regardless: the instance is done, and a stale tracker must not
        # keep receiving pause/resume/seek.
        self._pb_tracker = None
        self._pb_track = None
        if outcome is None:
            return

        changed = False
        if outcome.qualified_play:
            track.qualified_play_count = int(track.qualified_play_count or 0) + 1
            changed = True
        if outcome.manual_skip:
            track.manual_skip_count = int(track.manual_skip_count or 0) + 1
            track.last_skipped_at = int(time.time())
            changed = True
        if changed:
            self._update_track_metadata(track)

        # One line per finished playback for the future stats screen; never let it break playback.
        try:
            listening_log.append(str(self.listening_log_file), listening_log.make_record(
                started_at=getattr(track, 'last_played', None) or time.time(), ended_at=time.time(),
                path=track.filepath, title=track.title, artist=getattr(track, 'artist', ''),
                album=getattr(track, 'album', ''), genre=getattr(track, 'genre', ''),
                duration_sec=getattr(track, 'duration', 0) or 0, audible_sec=outcome.audible,
                reason=outcome.reason, qualified=outcome.qualified_play, skip=outcome.manual_skip,
                transition=transition if outcome.reason == 'natural_end' else 'cut',
                ctx=getattr(tracker, 'ctx', None)))
        except Exception as e:
            print(f"⚠ Could not write listening log: {e}")

    def _active_track(self):
        """The track the player is currently on - playing OR paused (not
        stopped).  Used to keep the play position aligned across a reshuffle."""
        if self._playback_state in ('playing', 'paused') and \
                0 <= self.current_index < len(self.current_playlist):
            return self.current_playlist[self.current_index]
        return None

    def view_up_next(self):
        """Show Up Next queue in a dialog"""
        queued = self._get_queued_tracks()

        dialog = tk.Toplevel(self.root)
        dialog.title("Up Next Queue")
        dialog.geometry("500x400")

        if not queued:
            ttk.Label(dialog, text="Up Next queue is empty",
                     font=('Arial', 12)).pack(pady=50)
            ttk.Button(dialog, text="Close", command=dialog.destroy).pack()
            return

        ttk.Label(dialog, text=f"{len(queued)} tracks in queue:",
                 font=('Arial', 12, 'bold')).pack(pady=10)

        # List of queued tracks
        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        listbox = tk.Listbox(frame, font=('Arial', 10))
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        listbox.config(yscrollcommand=scrollbar.set)

        for i, track in enumerate(queued, 1):
            listbox.insert(tk.END, f"{i}. {track.artist} - {track.title}")

        # Buttons
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Clear Queue",
                  command=lambda: (self.clear_up_next(), dialog.destroy())).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Close", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

    def clear_up_next(self):
        """Clear Up Next queue"""
        count = self.queued_count
        if count > 0:
            start = self.current_index + 1
            end = start + count
            del self.current_playlist[start:end]
            self.queued_count = 0
            self._shuffle_token = getattr(self, "_shuffle_token", 0) + 1
            self.display_playlist()
            self.status_label.config(text=f"Cleared {count} tracks from Up Next")
        else:
            self.status_label.config(text="Up Next queue already empty")
    
    def play_track_at_index(self, index):
        """Play a specific track: a manual pick or an internal advance. Always a cut, never a crossfade."""
        if not self.audio_available or self._engine is None:
            messagebox.showwarning("Audio Unavailable", "Audio device not initialized. Cannot play music.")
            return

        if not self.current_playlist or index >= len(self.current_playlist):
            return

        if not self.ffmpeg_path:
            self.status_label.config(text="⚠ FFmpeg not found - Settings > Set FFmpeg Path")
            return

        # Close out the previous instance.  Callers that know a more specific
        # reason (next_track, previous_track, _handle_track_end) finalize first;
        # finalize is idempotent so this default only fires for direct picks.
        self._finalize_playback('select')

        self.current_index = index
        track = self.current_playlist[index]
        self._play_token = getattr(self, '_play_token', 0) + 1
        if self._needs_analysis(track):
            # Leveling needs this song's loudness first (~0.2 s, off the main thread)
            token = self._play_token
            self._start_pending = True
            self._loudness.pause_background()
            self._analyze_then([track], lambda: self._run_pending_start(token, track))
            return
        self._start_pending = False
        self._start_item(track)

    def _run_pending_start(self, token, track):
        """Main thread: the picked song's loudness is in. Start it unless a pick, Stop or Pause came first."""
        if token != getattr(self, '_play_token', 0):
            return
        self._start_pending = False
        self._start_item(track)

    def _cancel_pending_start(self):
        """Stop/Pause while a picked song waits for its loudness: it must not start afterwards."""
        if not getattr(self, '_start_pending', False):
            return False
        self._play_token = getattr(self, '_play_token', 0) + 1
        self._start_pending = False
        if getattr(self, '_loudness', None) is not None:
            self._loudness.resume_background()   # it was paused for the start that won't happen
        return True

    def _start_item(self, track):
        item = self._play_item_for(track)
        self._engine_item = item
        try:
            with self._engine_lock:
                self._engine.play(item)
        except OSError as e:
            # FFmpeg could not start at all: stop instead of racing through the playlist.
            print(f"⚠ Could not start FFmpeg: {e}")
            self._engine_item = None
            if getattr(self, '_loudness', None) is not None:
                self._loudness.resume_background()
            self.is_playing = False
            self._playback_state = 'stopped'
            self.status_label.config(text="⚠ FFmpeg could not start - Settings > Set FFmpeg Path")

    def _begin_track_bookkeeping(self, track):
        """A song started playing (engine 'started' event): play count, history, tracker, now-playing UI."""
        self.is_playing = True
        self._playback_state = 'playing'
        self._start_playback_tracking(track)   # Task 8
        if getattr(self, '_loudness', None) is not None:
            self._loudness.resume_background()
        # A track actually started - clear the cycle-wrap loop guard so it
        # only trips when nothing plays between two wraps (Task 5).
        self._last_wrap_mono = 0.0

        ext = os.path.splitext(track.filepath)[1]
        # V2: Increment play count for ALL tracks (defensive programming)
        track.play_count = (track.play_count or 0) + 1
        track.last_played = int(time.time())
        # NOTE (Task 7a): do NOT stamp date_added on first playback - that
        # made every old track look brand new to the shuffle's newness
        # boost.  Unknown date stays None (scored neutral).
        self._update_track_metadata(track)

        # Record this start in the persisted listening history (Task 3).
        self._record_history_start(track)
        print(f"▶ Playing: {track.title}{ext} (plays: {track.play_count})")

        self._load_album_artwork(track)
        self._update_love_button(track)

        self.now_playing_label.config(text=track.title)
        artist_album = f"{track.artist} - {track.album}" if track.album else track.artist
        self.now_playing_artist.config(text=artist_album)

        # Select the row in the CURRENT view (filtered or full), not by playlist index
        try:
            display_list = self.filtered_playlist if self.filtered_playlist else self.current_playlist
            display_idx = display_list.index(track)
            children = self.playlist_tree.get_children()
            if 0 <= display_idx < len(children):
                item = children[display_idx]
                self.playlist_tree.selection_set(item)
                self.playlist_tree.see(item)
        except Exception:
            pass

    # ============================================================
    # Sound engine glue
    # ============================================================

    def _open_decoder(self, path, start_sec, filters):
        return FfmpegDecoder(self.ffmpeg_path or "ffmpeg", path, start_sec, filters)

    def _analyze_loudness(self, path):
        """LoudnessService analyzer: always the FFmpeg currently set (it can be set after startup)."""
        return analyze_file(self.ffmpeg_path or "ffmpeg", path, low_priority=True)

    def _ffmpeg_path_changed(self):
        """Settings > Set FFmpeg Path succeeded: songs that failed to measure without it get another go."""
        svc = getattr(self, '_loudness', None)
        if svc is not None:
            svc.forget_failures()
            self._start_loudness_scan()

    def _filters_for(self, track):
        s = self.audio_settings
        gain = 0.0
        if s['leveling_enabled']:
            album_gain = self._album_gain_for(track)
            if album_gain is not None:
                gain = album_gain
            else:
                loud = self._loudness_for(track)
                if loud is not None:
                    gain = leveling_gain_db(loud.integrated_lufs, loud.true_peak_dbtp)
        return filter_chain(s['eq_bands_db'], s['eq_preamp_db'], gain, enabled=s['eq_enabled'])

    def _needs_analysis(self, track):
        svc = getattr(self, '_loudness', None)
        return (svc is not None and self.audio_settings['leveling_enabled']
                and svc.get(track.filepath) is None and not svc.known_bad(track.filepath))

    def _analyze_then(self, tracks, callback):
        """Analyse tracks on a worker thread, then always run callback on the main thread.

        A song that is already being measured (next-song prep, then Next) isn't measured twice: the
        loudness service makes the second caller wait for the first run, and both callbacks run.
        """
        paths = [t.filepath for t in tracks]

        def work():
            for p in paths:
                try:
                    self._loudness.analyze_now(p)
                except Exception as e:
                    print(f"⚠ Loudness analysis failed: {e}")
            self._post_to_main(callback)

        threading.Thread(target=work, daemon=True, name='loudness-now').start()

    def _apply_sound_settings(self, new, keys=None):
        """Sound/Equalizer window callback: validate now, save and apply after a short debounce.

        keys: the settings the calling window owns (SOUND_DIALOG_KEYS / EQ_WINDOW_KEYS); only those are
        taken from `new`. None takes all of `new` (callers that built it from the current settings).
        """
        if keys is not None:
            merged = dict(self.audio_settings)
            merged.update({k: new[k] for k in keys if k in new})
            new = merged
        if self._sound_apply_job is None:
            self._sound_before = self.audio_settings
        else:
            try:
                self.root.after_cancel(self._sound_apply_job)
            except Exception:
                pass
        self.audio_settings = audio_settings.normalize(new)
        self._sound_apply_job = self.root.after(150, self._commit_sound_settings)

    def _commit_sound_settings(self):
        self._sound_apply_job = None
        before, s = getattr(self, '_sound_before', self.audio_settings), self.audio_settings
        try:
            audio_settings.save(str(self.audio_settings_file), s)
        except OSError as e:
            print(f"⚠ Could not save sound settings: {e}")
        filters_changed = any(s[k] != before[k] for k in ('eq_enabled', 'eq_bands_db', 'eq_preamp_db',
                                                          'leveling_enabled'))
        if filters_changed and self._engine is not None:
            with self._engine_lock:
                self._engine.refilter(lambda it: self._filters_for(it.key))
        if filters_changed or any(s[k] != before[k] for k in ('transition_mode', 'transition_seconds')):
            self._prepare_next()
        if (s['leveling_enabled'] and not before['leveling_enabled']) or \
                (s['transition_mode'] == 'smart' and before['transition_mode'] != 'smart'):
            self._start_loudness_scan()

    def _flush_sound_settings(self):
        """On close: save a Sound/Equalizer change that is still inside its 150 ms debounce."""
        if self._sound_apply_job is None:
            return
        try:
            self.root.after_cancel(self._sound_apply_job)
        except Exception:
            pass
        self._sound_apply_job = None
        try:
            audio_settings.save(str(self.audio_settings_file), self.audio_settings)
        except OSError as e:
            print(f"⚠ Could not save sound settings: {e}")

    def _eq_presets(self):
        presets = dict(BUILTIN_PRESETS)
        for name, p in self.audio_settings['eq_user_presets'].items():
            presets[name] = (list(p['bands_db']), p['preamp_db'])
        return presets

    def _refresh_eq_window(self):
        try:
            if self._eq_window is not None and self._eq_window.winfo_exists():
                self._eq_window.refresh(self.audio_settings, self._eq_presets())
        except tk.TclError:
            pass

    def _import_eqf_file(self, path):
        """Add every preset in a Winamp .eqf file to the user presets. Raises OSError/ValueError."""
        with open(path, 'rb') as fh:
            presets = parse_eqf(fh.read())
        s = dict(self.audio_settings)
        user = dict(s['eq_user_presets'])
        for name, bands, preamp in presets:
            user[(name or '').strip()[:64] or 'Imported'] = {'bands_db': list(bands), 'preamp_db': preamp}
        s['eq_user_presets'] = user
        self._apply_sound_settings(s)
        return len(presets)

    def _import_eqf(self):
        path = filedialog.askopenfilename(title="Import Winamp EQ presets",
                                          filetypes=[("Winamp EQ", "*.eqf *.EQF"), ("All files", "*.*")])
        if not path:
            return
        try:
            n = self._import_eqf_file(path)
        except (OSError, ValueError):
            messagebox.showerror("Import failed", "That isn't a Winamp EQ preset file (.eqf).")
            return
        self._refresh_eq_window()
        self.status_label.config(text=f"Imported {n} EQ preset(s)")

    def _save_eq_preset(self, name):
        s = dict(self.audio_settings)
        user = dict(s['eq_user_presets'])
        user[name] = {'bands_db': list(s['eq_bands_db']), 'preamp_db': s['eq_preamp_db']}
        s['eq_user_presets'] = user
        s['eq_preset'] = name
        self._apply_sound_settings(s)
        self._refresh_eq_window()

    def _loudness_status_text(self):
        svc = getattr(self, '_loudness', None)
        if svc is None:
            return ''
        done, total = svc.status()
        if total == 0:
            return 'The library is measured once leveling or Smart is on'
        return f'Measured {done:,} of {total:,} songs' if done < total else f'All {total:,} songs measured'

    def open_sound_dialog(self):
        if self._sound_dialog is not None and self._sound_dialog.winfo_exists():
            self._sound_dialog.lift()
            return
        self._sound_dialog = SoundDialog(self.root, self.audio_settings,
                                         lambda s: self._apply_sound_settings(s, SOUND_DIALOG_KEYS),
                                         self._loudness_status_text)

    def open_equalizer(self):
        if self._eq_window is not None and self._eq_window.winfo_exists():
            self._eq_window.lift()
            return
        self._eq_window = EqualizerWindow(self.root, self.audio_settings, self._eq_presets(),
                                          lambda s: self._apply_sound_settings(s, EQ_WINDOW_KEYS),
                                          self._save_eq_preset, self._import_eqf)

    def _start_loudness_scan(self):
        """Measure the whole library in the background once leveling or smart transitions need it."""
        s = self.audio_settings
        if getattr(self, '_loudness', None) is None or not (s['leveling_enabled'] or s['transition_mode'] == 'smart'):
            return
        tracks = getattr(self, 'library_tracks', None) or getattr(self, 'all_tracks', None) or []
        self._loudness.start_background([t.filepath for t in tracks])

    def _play_item_for(self, track):
        return PlayItem(key=track, path=track.filepath,
                        duration_sec=float(getattr(track, 'duration', 0) or 0),
                        filters=self._filters_for(track))

    def _engine_emit(self, name, **data):
        """Runs on the engine thread under _engine_lock (or inside an engine call): queue it, never touch Tk.

        root.after from a non-main thread blocks until the main thread services it, and the main thread
        may be waiting for _engine_lock: that would deadlock.
        """
        self._post_to_main(lambda: self._on_engine_event(name, **data))

    def _post_to_main(self, fn):
        """Any thread: run fn (no arguments) on the main thread soon. Never calls Tk."""
        self._main_calls.put(fn)

    def _drain_main_calls(self):
        """Main thread, every MAIN_CALLS_POLL_MS: run what worker threads posted, in order."""
        for _ in range(self._main_calls.qsize()):
            try:
                fn = self._main_calls.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                import traceback
                traceback.print_exc()
        try:
            self._main_calls_job = self.root.after(MAIN_CALLS_POLL_MS, self._drain_main_calls)
        except (RuntimeError, tk.TclError):
            self._main_calls_job = None   # window closing

    def _on_engine_event(self, name, **data):
        """Main thread: react to sound-engine events."""
        item = data.get('item')
        if name == 'error':
            fname = os.path.basename(item.path) if item is not None else '?'
            print(f"⚠ Skipping unplayable file: {fname} - {str(data.get('message', '')).strip()[-200:]}")
            return
        if item is None:
            return
        if name == 'started':
            how = data.get('how')
            if how == 'cut':
                if item is self._engine_item:
                    self._begin_track_bookkeeping(item.key)
                    self._prepare_next()
            elif self._next_item is not None and (item is self._next_item or item.key is self._next_item.key):
                # identity, or the same song re-prepared after the engine had already started it
                self._on_transition_started(item, how)
            return
        if name != 'finished':
            return
        reason = data.get('reason')
        if item is self._outgoing_item:
            self._finalize_outgoing()
            return
        if item is not self._engine_item or reason == 'cut':
            return  # stale, or a manual action already finalized it
        if data.get('continued'):
            # the prepared song starts right now on the same channel (gapless, or after a decode failure)
            if reason == 'failure':
                self._finalize_playback('failure')
            else:
                self._finalize_playback('natural_end', self._next_tr)
            return
        if reason == 'failure':
            if getattr(self, '_loudness', None) is not None:
                self._loudness.resume_background()   # paused for a start that never came
            self.is_playing = False
            self._playback_state = 'stopped'
            self._finalize_playback('failure')
            self.next_track(_internal=True)
        else:
            self._handle_track_end()

    def _on_transition_started(self, item, how):
        """The prepared song started on its own (gapless or overlap): make it current without a cut."""
        if how == 'overlap' and self._pb_tracker is not None:
            # the outgoing song is still audible: its tracker runs until its audio stops
            self._pb_outgoing = (self._pb_tracker, self._pb_track)
            self._pb_tracker = self._pb_track = None
            self._outgoing_item = self._engine_item
            self._outgoing_tr = self._next_tr
        smart = getattr(self, '_active_algo', 'Smart Shuffle') == 'Smart Shuffle'
        action, new_index, new_q, new_temp = playlist_queue.advance(
            self.current_index, len(self.current_playlist), self.queued_count, self.is_temp_view,
            reshuffle_on_wrap=smart)
        if action == 'play' and 0 <= new_index < len(self.current_playlist) \
                and self.current_playlist[new_index] is item.key:
            self.current_index, self.queued_count, self.is_temp_view = new_index, new_q, new_temp
        else:
            # the playlist changed after the song was prepared: follow the song itself
            try:
                self.current_index = self.current_playlist.index(item.key)
            except ValueError:
                pass
        self._engine_item = item
        self._next_item = None
        self._begin_track_bookkeeping(item.key)
        self._prepare_next()

    def _finalize_outgoing(self):
        """The outgoing song of a crossfade stopped: close its playback instance as a natural end."""
        pending = self._pb_outgoing
        self._pb_outgoing = None
        self._outgoing_item = None
        if pending is None:
            return
        saved = (self._pb_tracker, self._pb_track)
        self._pb_tracker, self._pb_track = pending
        try:
            self._finalize_playback('natural_end', self._outgoing_tr)
        finally:
            self._pb_tracker, self._pb_track = saved

    def _loudness_for(self, track):
        """Cached loudness analysis for a track, or None (filled in by the loudness service)."""
        svc = getattr(self, '_loudness', None)
        return svc.get(track.filepath) if svc is not None else None

    def _prepare_next(self, analyse=True):
        """Tell the engine which song follows the current one, and how (Up Next and shuffle rules apply).

        Songs without loudness are measured in the background, then the next song is prepared again once
        with analyse=False, so a song that can't be measured isn't retried forever.
        """
        if self._engine is None or self._engine_item is None:
            return
        smart = getattr(self, '_active_algo', 'Smart Shuffle') == 'Smart Shuffle'
        idx = playlist_queue.peek_next_index(self.current_index, len(self.current_playlist),
                                             self.queued_count, self.is_temp_view, reshuffle_on_wrap=smart)
        if idx is None:
            self._next_item, self._next_tr = None, 'cut'
            with self._engine_lock:
                self._engine.prepare_next(None, None)
            return
        out_track, in_track = self._engine_item.key, self.current_playlist[idx]
        s = self.audio_settings
        svc = getattr(self, '_loudness', None)
        if svc is not None and (s['leveling_enabled'] or s['transition_mode'] == 'smart'):
            missing = [tr for tr in (out_track, in_track)
                       if svc.get(tr.filepath) is None and not svc.known_bad(tr.filepath)]
            if missing and analyse:
                self._analyze_then(missing, lambda: self._prepare_next(analyse=False))
        out_l, in_l = self._loudness_for(out_track), self._loudness_for(in_track)
        out_dur = out_l.duration_sec if out_l else float(getattr(out_track, 'duration', 0) or 0)
        in_dur = in_l.duration_sec if in_l else float(getattr(in_track, 'duration', 0) or 0)
        # Every natural end fades in Crossfade and Smart, consecutive album tracks included (decided
        # 2026-11-13); Off is the gapless mode.
        plan = transitions.plan_transition(s['transition_mode'], s['transition_seconds'], out_dur, in_dur,
                                           out_loudness=out_l, in_loudness=in_l)
        item = self._play_item_for(in_track)
        prev = self._next_item
        if prev is not None and prev.key is item.key and prev.path == item.path \
                and prev.filters == item.filters and prev.duration_sec == item.duration_sec:
            item = prev   # same song, same sound: the engine keeps its pre-opened stream
        self._next_item = item
        if plan.kind == 'gapless':
            self._next_tr = 'gapless'
        else:
            self._next_tr = 'smart' if s['transition_mode'] == 'smart' else 'crossfade'
        with self._engine_lock:
            self._engine.prepare_next(item, plan)

    def _check_next_prepared(self):
        """Every second: if Up Next, a search takeover or a reshuffle changed what follows, re-prepare."""
        try:
            if self._engine is not None and self._engine_item is not None and self.current_playlist:
                smart = getattr(self, '_active_algo', 'Smart Shuffle') == 'Smart Shuffle'
                idx = playlist_queue.peek_next_index(self.current_index, len(self.current_playlist),
                                                     self.queued_count, self.is_temp_view,
                                                     reshuffle_on_wrap=smart)
                want = self.current_playlist[idx] if idx is not None else None
                have = self._next_item.key if self._next_item is not None else None
                if want is not have:
                    self._prepare_next()
        except Exception as e:
            print(f"⚠ Next-song check failed: {e}")
        try:
            self.root.after(1000, self._check_next_prepared)
        except (RuntimeError, tk.TclError):
            pass

    def _engine_loop(self):
        while not self._engine_stop.is_set():
            try:
                with self._engine_lock:
                    self._engine.tick()
            except Exception as e:
                print(f"⚠ Sound engine error: {e}")
                self._engine_stop.wait(0.5)
            self._engine_stop.wait(0.02)

    def _start_engine_loop(self):
        if self._engine is None or self._engine_thread is not None:
            return
        self._engine_thread = threading.Thread(target=self._engine_loop, daemon=True, name='sound-engine')
        self._engine_thread.start()
        self.root.after(1000, self._check_next_prepared)

    def _shutdown_engine(self):
        self._engine_stop.set()
        if self._engine_thread is not None and self._engine_thread.is_alive():
            self._engine_thread.join(timeout=1.0)
        if self._engine is not None:
            with self._engine_lock:
                self._engine.stop()
        if getattr(self, '_loudness', None) is not None:
            self._loudness.stop()
        try:
            pygame.mixer.quit()
        except pygame.error:
            pass

    def _resume_engine(self):
        with self._engine_lock:
            self._engine.resume()
        self.is_playing = True
        self._playback_state = 'playing'
        if self._pb_tracker:
            self._pb_tracker.resume(time.monotonic())

    def play(self):
        """Play or resume"""
        if not self.audio_available:
            return

        if not self.current_playlist:
            messagebox.showwarning("Warning", "No playlist loaded")
            return

        with self._engine_lock:
            has_song = self._engine.current() is not None
        if not has_song:
            self.play_track_at_index(self.current_index)
        else:
            self._resume_engine()

    def pause(self):
        """Pause playback"""
        if not self.audio_available:
            return
        if self._cancel_pending_start():
            # The picked song hasn't started yet: cut the old one, so Play starts the picked song.
            self._engine_item = None
            with self._engine_lock:
                self._engine.stop()
        else:
            with self._engine_lock:
                self._engine.pause()
        self.is_playing = False
        self._playback_state = 'paused'
        if self._pb_tracker:
            self._pb_tracker.pause(time.monotonic())

    def stop(self):
        """Stop playback"""
        if not self.audio_available:
            return

        self._finalize_playback('stop')   # Task 8: stop is not a dislike
        self._play_context = None         # an album/artist play ends on Stop
        self._cancel_pending_start()
        self._engine_item = None
        with self._engine_lock:
            self._engine.stop()
        self.is_playing = False
        self._playback_state = 'stopped'
        self.now_playing_label.config(text="Not Playing")
        self.now_playing_artist.config(text="")
        self._show_placeholder_artwork()
        self.progress_var.set(0)
        self.time_label.config(text="0:00 / 0:00")
    
    def next_track(self, _internal=False):
        """Play next track"""
        if not self.current_playlist:
            return
        
        if (not _internal and hasattr(self, 'next_button') 
                and str(self.next_button['state']) == 'disabled'):
            print("⚠ Navigation disabled during shuffle")
            return
        
        # Task 8: a manual Next is a candidate skip - the audible-time tracker
        # decides (seeking near the end is not "listening"; a natural end that
        # auto-advances here with _internal=True is not a skip).  The legacy
        # `skips` heuristic counter is left frozen.
        if not _internal:
            self._finalize_playback('next')

        # Smart Shuffle regenerates a fresh order at the cycle boundary instead
        # of replaying the same sequence forever (Task 5).
        smart = getattr(self, '_active_algo', 'Smart Shuffle') == 'Smart Shuffle'
        action, new_index, new_queued_count, new_is_temp_view = playlist_queue.advance(
            self.current_index, len(self.current_playlist), self.queued_count,
            self.is_temp_view, reshuffle_on_wrap=smart,
        )

        if action == 'stop':
            return

        if action == 'reshuffle':
            # Guard against an all-unplayable library spinning forever: if we
            # wrapped again within a couple of seconds, the tracks are failing
            # instantly - stop instead of reshuffling in a tight loop.
            now_mono = time.monotonic()
            if now_mono - getattr(self, '_last_wrap_mono', 0.0) < 2.0:
                self.status_label.config(text="⚠ No playable tracks")
                return
            self._last_wrap_mono = now_mono
            self.queued_count = 0
            self.is_temp_view = False
            self.apply_shuffle(auto_play=True)  # no-arg -> Smart Shuffle; branch is Smart-only
            return

        self.current_index = new_index
        self.queued_count = new_queued_count
        self.is_temp_view = new_is_temp_view
        self.play_track_at_index(self.current_index)
    
    def previous_track(self):
        """Play previous track"""
        if not self.current_playlist:
            return
        
        if hasattr(self, 'prev_button') and str(self.prev_button['state']) == 'disabled':
            print("⚠ Navigation disabled during shuffle")
            return
        
        self._finalize_playback('previous')   # Task 8: not a dislike
        self.current_index = playlist_queue.previous_position(
            self.current_index, len(self.current_playlist)
        )
        # Going backward abandons the forward-looking Up Next queue: keeping the
        # block "adjacent to current" is impossible once you navigate past it,
        # and re-consuming it on the way forward again would be surprising.
        self.queued_count = 0
        self.play_track_at_index(self.current_index)
    
    def start_update_thread(self):
        """Start thread to update progress bar and sync volume"""
        def safe_ui_call(callback, *args):
            """Safely call UI callback, checking if window still exists"""
            try:
                if self.root.winfo_exists():
                    self.root.after(0, callback, *args)
            except (RuntimeError, tk.TclError):
                pass  # Window destroyed or Tcl interpreter deleted
        
        def update_progress():
            _tick = 0.1  # seconds per loop iteration
            _volume_sync_ticks = round(VOLUME_SYNC_INTERVAL_SEC / _tick)

            volume_sync_counter = 0

            while True:
                # Sync volume slider with system volume every VOLUME_SYNC_INTERVAL_SEC
                volume_sync_counter += 1
                if volume_sync_counter >= _volume_sync_ticks:
                    volume_sync_counter = 0
                    safe_ui_call(self._sync_volume_slider)
                
                if self.audio_available and self.is_playing and self._engine is not None:
                    try:
                        with self._engine_lock:
                            item = self._engine.current()
                            pos = self._engine.position()
                        if item is not None:
                            duration = self._get_safe_duration(item.key)
                            if duration > 0.01:
                                progress = min((pos / duration) * 100, 100)
                                display_pos = min(pos, duration)
                                time_text = (f"{int(display_pos // 60)}:{int(display_pos % 60):02d} / "
                                             f"{int(duration // 60)}:{int(duration % 60):02d}")
                                safe_ui_call(self._update_progress_ui, progress, time_text)
                    except Exception as e:
                        # Log unexpected errors but don't crash the thread
                        print(f"⚠ Progress update error: {e}")
                        time.sleep(1)  # Back off briefly before retrying

                time.sleep(_tick)
        
        thread = threading.Thread(target=update_progress, daemon=True)
        thread.start()
    
    def _update_progress_ui(self, progress, time_text):
        """Update progress bar and time label"""
        try:
            self.progress_var.set(min(progress, 100))
            self.time_label.config(text=time_text)
        except:
            pass
    
    def _handle_track_end(self):
        """A song ended on its own with nothing prepared after it: count it, then advance."""
        if 0 <= self.current_index < len(self.current_playlist):
            print(f"✓ Completed: {self.current_playlist[self.current_index].title}")
        self.is_playing = False
        self._playback_state = 'stopped'  # until the next track starts
        self._finalize_playback('natural_end')  # Task 8
        self.next_track(_internal=True)  # Allow auto-advance even during shuffle


def main():
    # Console codepage (e.g. cp1252) can't encode the ✓/⚠ glyphs used in status
    # prints below; this raises UnicodeEncodeError whenever stdout isn't a real
    # console (piped, redirected to a file). Force UTF-8 so those prints never crash.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass

    # Single-instance guard: a second launch would install its own competing
    # keyboard hook and its own WM_APPCOMMAND-subclassed window fighting the
    # first instance over the same input. Named mutex survival across the
    # process's life is how Windows apps normally detect "already running".
    if sys.platform == 'win32':
        try:
            import ctypes
            from ctypes import wintypes

            ERROR_ALREADY_EXISTS = 183
            SW_RESTORE = 9

            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            kernel32.CreateMutexW.restype = wintypes.HANDLE

            ctypes.set_last_error(0)
            # Intentionally unused beyond this point — kept alive only by staying in
            # scope for main()'s lifetime (it blocks on root.mainloop() below), which
            # is what keeps the mutex held for the life of the process. Windows
            # releases it automatically on exit; no explicit CloseHandle needed.
            _instance_mutex = kernel32.CreateMutexW(None, False, "MusicPlayerV2_iTunesShuffler_SingleInstance")

            if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
                # A second instance MUST NOT start past this point — the mutex has
                # already proven one is running. Any failure while trying to focus
                # the existing window is cosmetic and must not fall through to
                # starting a second instance, so it gets its own try/except that
                # still returns either way (unlike the outer one, which is allowed
                # to fall through to a normal start if the guard itself failed).
                print("Music Player V2 is already running — switching to it.")
                try:
                    user32 = ctypes.WinDLL('user32', use_last_error=True)
                    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
                    user32.FindWindowW.restype = wintypes.HWND
                    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
                    user32.ShowWindow.restype = wintypes.BOOL
                    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
                    user32.SetForegroundWindow.restype = wintypes.BOOL

                    hwnd = user32.FindWindowW(None, "Music Player V2")
                    if hwnd:
                        user32.ShowWindow(hwnd, SW_RESTORE)
                        user32.SetForegroundWindow(hwnd)
                except Exception as e:
                    print(f"⚠ Couldn't focus the existing window: {e}")
                return
        except Exception as e:
            print(f"⚠ Single-instance check failed, continuing anyway: {e}")

    print("="*70)
    print("Custom Shuffle Music Player V2 - Starting...")
    print("Unified metadata system - all tracks tracked equally")
    print("Console visible for debugging - errors will be shown here")
    print("="*70)
    print()
    
    # Minimize console window on Windows
    if sys.platform == 'win32':
        try:
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 2)  # SW_SHOWMINIMIZED = 2
        except:
            pass
    
    # Must be set before any window exists so Windows correlates this running
    # process with its own taskbar identity/grouping (standard practice for
    # non-manifest Win32 apps). NOTE: this does NOT make a pinned taskbar
    # tile's icon track live iconphoto() changes - confirmed that a pinned
    # tile's primary icon is sourced from System.AppUserModel.RelaunchIconResource
    # (or the exe's static PE icon as fallback), by Windows Shell design,
    # regardless of AUMID. Only the titlebar/Alt-Tab/unpinned-taskbar icon
    # updates live; that's a hard OS limitation, not a bug here.
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("iTunesShuffler.MusicPlayerV2")
        except Exception as e:
            print(f"⚠ Could not set AppUserModelID: {e}")

    try:
        root = tk.Tk()
        app = MusicPlayerGUI(root)
        root.mainloop()
        print("\n" + "="*70)
        print("App closed normally")
        print("="*70)
    except Exception as e:
        print("\n" + "="*70)
        print("CRITICAL ERROR - APP CRASHED")
        print("="*70)
        import traceback
        traceback.print_exc()
        print("="*70)
        input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()