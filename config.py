"""
config.py — Central configuration for the Movie Clip Bot system.
All secrets and tuneable parameters live here.
"""

from pathlib import Path

# ── Telegram credentials ──────────────────────────────────────────────────────
# Get these from https://my.telegram.org  →  API Development Tools
API_ID   = 27946667             # ← replace with your api_id  (integer)
API_HASH = "cc436e00956568467672774c33ccc63f"            # ← replace with your api_hash (string)

# Bot token from @BotFather  (used by the Search Bot)
BOT_TOKEN = "8347961988:AAHXbbbWji-loLMiLa18G0toZRT0IoymN3M"      # ← e.g. "7123456789:AAFGrs-..."

# The channel that stores the clips  (must be public OR the bot must be admin)
# Use the numeric id (e.g. -1001234567890) or public username "@my_clips_channel"
STORAGE_CHANNEL = "@kimnimalatopomayqolsin"   # ← e.g. "@myclipstore" or -1001234567890

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
MEDIA_DIR  = BASE_DIR / "movies"      # place your .mp4 + .srt pairs here
DB_PATH    = BASE_DIR / "data" / "clips.db"
CACHE_DIR  = BASE_DIR / "cache"       # temp ffmpeg output

# ── FFmpeg clip settings ──────────────────────────────────────────────────────
CLIP_PAD_SEC   = 0.35    # seconds to pad before/after subtitle cue
MAX_CLIP_SEC   = 7.0     # hard cap on clip duration
VIDEO_HEIGHT   = 720     # output video height (width auto-scaled)
CRF            = 24      # libx264 quality  (lower = better, bigger file)
PRESET         = "veryfast"

# ── Search / session settings ─────────────────────────────────────────────────
SEARCH_LIMIT      = 100   # max rows fetched from FTS
RESULTS_PER_PAGE  = 1     # clips sent per page (one at a time, like the screenshots)
DIVERSITY_CAP     = 3     # max clips from the same movie per result set
DIVERSITY_TAKE    = 30    # total clips returned after diversity filter

# ── Concurrency ───────────────────────────────────────────────────────────────
FFMPEG_SEMAPHORE  = 2     # simultaneous ffmpeg jobs

# create required dirs
for _d in (MEDIA_DIR, DB_PATH.parent, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)
