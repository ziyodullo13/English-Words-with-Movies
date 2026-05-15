"""
processor.py — Video clip cutting via FFmpeg.

Uses libx264 re-encode (NOT stream-copy) so every clip is a valid,
self-contained MP4 that Telegram can stream immediately.
Stream-copy (-c copy) is fast but produces clips that often can't be
seeked / streamed because the keyframe grid of the source is unknown.
"""

import asyncio
import hashlib
import subprocess
from pathlib import Path

from config import (
    CACHE_DIR,
    CLIP_PAD_SEC,
    MAX_CLIP_SEC,
    VIDEO_HEIGHT,
    CRF,
    PRESET,
    FFMPEG_SEMAPHORE,
)

# Global semaphore (initialised lazily inside async context)
_sem: asyncio.Semaphore | None = None


def _get_sem() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(FFMPEG_SEMAPHORE)
    return _sem


# ── Path helpers ──────────────────────────────────────────────────────────────

def clip_cache_path(movie: str, start: float, end: float) -> Path:
    """Deterministic cache path for a clip — uses SHA-1 of its key."""
    key = f"{movie}|{start:.3f}|{end:.3f}".encode()
    name = hashlib.sha1(key).hexdigest() + ".mp4"
    return CACHE_DIR / name


# ── Synchronous FFmpeg call (runs in a thread pool) ───────────────────────────

def _cut_sync(video_path: Path, start: float, end: float, out_path: Path) -> None:
    """
    Cut [start, end] from *video_path* and write to *out_path*.

    Approach:
    • Seek to (start - pad) before opening the input  → fast input seek
    • Then encode exactly `duration` seconds
    • -vf scale=-2:{height}  keeps aspect ratio, forces even width
    • +faststart moves MOOV atom to the front for instant streaming
    """
    ss       = max(0.0, start - CLIP_PAD_SEC)
    t_end    = end + CLIP_PAD_SEC
    duration = min(max(0.1, t_end - ss), MAX_CLIP_SEC)

    cmd = [
        "ffmpeg",
        "-hide_banner", "-loglevel", "error",
        "-ss", f"{ss:.3f}",
        "-i", str(video_path),
        "-t", f"{duration:.3f}",
        # video
        "-vf", f"scale=-2:{VIDEO_HEIGHT}",
        "-c:v", "libx264",
        "-preset", PRESET,
        "-crf", str(CRF),
        # audio
        "-c:a", "aac",
        "-b:a", "128k",
        # container
        "-movflags", "+faststart",
        "-y",          # overwrite if exists
        str(out_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg failed for {video_path.name} "
            f"[{start:.2f}s–{end:.2f}s]:\n{result.stderr}"
        )


# ── Public async API ──────────────────────────────────────────────────────────

async def get_clip(
    video_path: Path,
    start: float,
    end: float,
    *,
    force: bool = False,
) -> Path:
    """
    Return the path to a cached clip, cutting it if necessary.

    Parameters
    ----------
    video_path : full path to the source .mp4 / .mkv
    start / end : subtitle cue timestamps in seconds
    force       : re-cut even if the file already exists in cache
    """
    out = clip_cache_path(str(video_path.name), start, end)

    if not force and out.exists() and out.stat().st_size > 0:
        return out

    async with _get_sem():
        # double-check after acquiring semaphore (another coroutine may have cut it)
        if not force and out.exists() and out.stat().st_size > 0:
            return out
        await asyncio.to_thread(_cut_sync, video_path, start, end, out)

    return out
