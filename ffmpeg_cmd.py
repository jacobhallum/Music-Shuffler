"""Pure helpers that build FFmpeg argument lists (nothing is ever run)."""

SAMPLE_RATE = 48000
CHANNELS = 2
BYTES_PER_FRAME = 8          # float32 x 2 channels
BYTES_PER_SEC = 384000       # SAMPLE_RATE * BYTES_PER_FRAME


def decode_args(ffmpeg: str, path: str, *, start_sec: float = 0.0,
                filters: str = '') -> list[str]:
    """Build the argument list for decoding a song to raw PCM on stdout.

    Output is 32-bit float PCM, CHANNELS channels, SAMPLE_RATE Hz. The path
    is always a single list element, passed through unchanged.
    """
    args = [ffmpeg, '-hide_banner', '-nostdin', '-v', 'error']
    if start_sec > 0:
        args += ['-ss', f'{start_sec:.3f}']
    args += ['-i', path, '-map', '0:a:0', '-vn']
    if filters:
        args += ['-af', filters]
    args += ['-f', 'f32le', '-ac', '2', '-ar', '48000', 'pipe:1']
    return args


def analyze_args(ffmpeg: str, path: str) -> list[str]:
    """Build the argument list for loudness analysis with the ebur128 filter."""
    return [
        ffmpeg, '-hide_banner', '-nostdin', '-nostats', '-v', 'verbose',
        '-i', path, '-map', '0:a:0', '-vn',
        '-af', 'ebur128=framelog=verbose:peak=true',
        '-f', 'null', '-',
    ]


def seconds_to_bytes(sec: float) -> int:
    """Convert seconds of PCM playback to the corresponding byte count."""
    return int(round(sec * SAMPLE_RATE)) * BYTES_PER_FRAME


def bytes_to_seconds(n: int) -> float:
    """Convert a PCM byte count to seconds; a partial frame is ignored."""
    return (n // BYTES_PER_FRAME) / SAMPLE_RATE
