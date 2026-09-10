# iTunes Shuffler

A custom music player for large iTunes/M4A libraries with a smart shuffle algorithm, album artwork, global media keys, and a pre-conversion cache for DRM-free tracks.

## Features

- **Smart Shuffle** — a bucketed stride-interleave that spaces artists evenly at
  any library skew, runs in well under a second on 15K+ tracks, varies between
  sessions, and keeps recently-played tracks out of the next shuffle's opening.
  Regenerates a fresh order when the playlist wraps.
- **Background M4A pre-conversion** — converts tracks via FFmpeg so playback is instant
- **Unified metadata** — play count, rating, favorite, and honest listening
  outcomes (`qualified_play_count` / `manual_skip_count`) for all 8K+ tracks
- **Favorites drive taste** — the ♥ button is the preference signal (legacy
  numeric rating still works); favorites are stored outside the cache so
  "Clear Cache" can't wipe them. "Import iTunes Ratings as Favorites" seeds
  them one-off from existing 4★+ ratings.
- **Album artwork** display
- **Up Next queue** — survives a reshuffle
- **Global media keys** (F5–F8) work system-wide
- **Search & filter** across the full library

### Shuffle scoring modes

`legacy` (default) keeps the historical additive weighting including numeric
rating. `weighted` (opt-in, set `scoring_mode` in `shuffle_config.json`) uses
scale-free favorite-forward weighting with a single `beta` strength knob and
drops numeric rating. Both modes share the interleave; neither can effectively
exclude a track. Listening feedback (`min(240s, 80%)` = a qualified play; a
manual Next before that = a skip; seeking is never "listening") gently demotes
tracks you consistently skip and lets that decay over ~a week.

## Requirements

- Python 3.10+
- [pygame](https://pypi.org/project/pygame/) — audio playback
- [mutagen](https://pypi.org/project/mutagen/) — audio metadata
- [Pillow](https://pypi.org/project/Pillow/) — album artwork
- [FFmpeg](https://ffmpeg.org/download.html) — M4A/FLAC conversion (must be on PATH or configured)
- [PyQt6](https://pypi.org/project/PyQt6/) — GUI framework for Library Manager
- [pywin32](https://pypi.org/project/pywin32/) — iTunes COM automation for Library Manager (Windows only)

```
pip install pygame mutagen Pillow PyQt6 pywin32
```

## Setup

1. Copy the config template and fill in your paths:
   ```
   cp config.example.json config.local.json
   ```
   Edit `config.local.json`:
   ```json
   {
       "music_dirs": [
           "C:\\Users\\YourName\\Music\\iTunes\\iTunes Media\\Music",
           "C:\\Users\\YourName\\Music\\M4P Downloads"
       ]
   }
   ```
   If no `config.local.json` is present, the app falls back to `~/Music/iTunes/iTunes Media/Music`.

2. Run the player:
   ```
   python music_player.py
   ```

## Privacy note

The `music_shuffler_cache/` directory contains your personal library index and
listening history and is intentionally excluded from version control. Never
commit it. `shuffler_preferences.json` (favorites, kept beside the cache dir so
a cache wipe doesn't lose it) is likewise personal — don't commit it.

## Utilities

| Script | Purpose |
|--------|---------|
| `utlities/list_albums.py` | List albums ranked by play count, cross-referenced against a local download wishlist |
| `utlities/check_track_stats.py <path-or-fragment>` | Smart Shuffle score breakdown for a track — reads the app's own metadata + config and the real scorer (never a copy) |
| `scripts/benchmark_shuffle.py [--skew F] [--sizes ...] [--iters N]` | Runtime + spacing quality (adjacency, longest run, session coverage); `--skew 0.7` exercises a dominant-artist library |
| `scripts/shuffle_probes.py [probe ...]` | Reproduces every headline measurement in `docs/plans/2026-09-09-smart-shuffle-enhancements.md` |
| `utlities/detect_media_keys.py` | Debug global media key detection |
| `scripts/preflight_privacy_check.sh` | Verify no personal paths/cache files would be committed before a push |
| `utlities/library_manager.py` | GUI tool to sort album track order by date-added and edit all song metadata. Run: `python utlities/library_manager.py` |
