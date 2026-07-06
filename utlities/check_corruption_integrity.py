#!/usr/bin/env python
"""Scan M4P Downloads for truncated FLAC downloads and write a CSV report."""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from beathunter.config import load_config
from beathunter.integrity import scan_directory

_CONFIG_PATH = Path(__file__).parent.parent / "config.toml"
_DEFAULT_ROOT = Path.home() / "Music" / "M4P Downloads"


def _resolve_root(path_arg: str | None) -> Path:
    if path_arg:
        return Path(path_arg)
    config = load_config(_CONFIG_PATH if _CONFIG_PATH.exists() else None)
    root = config.paths.m4p_downloads_root
    if not root.exists():
        print(f"Notice: {root} not found, using default {_DEFAULT_ROOT}", file=sys.stderr)
        return _DEFAULT_ROOT
    return root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan FLAC files for truncated downloads"
    )
    parser.add_argument("path", nargs="?", help="Root folder to scan")
    parser.add_argument("--output", help="CSV output path")
    args = parser.parse_args(argv)

    root = _resolve_root(args.path)
    output = (
        Path(args.output)
        if args.output
        else Path(__file__).parent / f"integrity_{date.today().isoformat()}.csv"
    )

    flac_cli = shutil.which("flac")
    if not flac_cli:
        print(
            "Notice: flac CLI not on PATH — decode test (step 3) skipped",
            file=sys.stderr,
        )

    print(f"Scanning: {root}", file=sys.stderr)

    total_scanned = [0]

    def progress(current: int, total: int, filename: str) -> None:
        total_scanned[0] = total
        print(f"\r[{current:4d} / {total}] {filename}", end="", flush=True, file=sys.stderr)

    results = scan_directory(root, flac_cli=flac_cli, progress=progress)
    print(file=sys.stderr)
    print(
        f"Done. {total_scanned[0]} files scanned, {len(results)} flagged.",
        file=sys.stderr,
    )

    try:
        with open(output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "path", "artist", "album", "title",
                    "size_mb", "duration", "effective_kbps",
                    "issue", "action",
                ],
            )
            writer.writeheader()
            for r in results:
                writer.writerow({
                    "path": str(r.path),
                    "artist": r.artist,
                    "album": r.album,
                    "title": r.title,
                    "size_mb": r.size_mb,
                    "duration": r.duration,
                    "effective_kbps": r.effective_kbps if r.effective_kbps is not None else "",
                    "issue": r.issue,
                    "action": r.action,
                })
    except OSError as e:
        print(f"Error writing CSV: {e}", file=sys.stderr)
        return 1

    print(f"Report saved to: {output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
