# 🎬 Movie Clip Bot

A Telegram bot system that splits movies into subtitle-synced clips and uses a Telegram Channel as a "cloud database" — no paid file hosting needed.

---

## Architecture

```
movies/
  Inception.mp4
  Inception.srt
  ├── uploader.py      ← Task 1: cuts clips, uploads to channel, populates DB
  └── bot.py           ← Task 2: search bot with pagination
config.py              ← all secrets & settings
database.py            ← SQLite + FTS5 schema & queries
processor.py           ← async FFmpeg wrapper
data/
  clips.db             ← SQLite database (auto-created)
cache/                 ← temporary .mp4 clips (auto-created)
```

---

## 1. Prerequisites

### macOS (M3 / Apple Silicon)

```bash
# Install Homebrew if you haven't already
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install FFmpeg (with all common codecs)
brew install ffmpeg

# Verify
ffmpeg -version
```

### Python environment

```bash
# Python 3.11+ recommended
python3 --version

# Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## 2. Telegram Setup

### Step A — Get your API credentials
1. Go to https://my.telegram.org and log in.
2. Click **API development tools**.
3. Create a new app — note the `api_id` (integer) and `api_hash` (string).

### Step B — Create a Bot
1. Message **@BotFather** on Telegram.
2. Send `/newbot` and follow the steps.
3. Copy the **bot token**.

### Step C — Create a Storage Channel
1. Create a **public** Telegram channel (or private, but the bot must be admin).
2. Add your bot as an **Admin** with "Post Messages" permission.
3. Note the channel username (e.g. `@my_clips_store`) or its numeric ID.

---

## 3. Configuration

Edit **`config.py`**:

```python
API_ID           = 12345678         # from my.telegram.org
API_HASH         = "abc123..."      # from my.telegram.org
BOT_TOKEN        = "7123...:AAF..." # from @BotFather
STORAGE_CHANNEL  = "@my_clips_store"  # or -1001234567890
```

---

## 4. Running the Uploader (Task 1)

Place your movie and subtitle files in `movies/`:

```
movies/
  Inception.mp4
  Inception.srt
```

Then run:

```bash
python uploader.py movies/Inception.mp4 movies/Inception.srt
```

**First run:** Pyrogram will ask you to log in with your phone number (one-time only — saved to `uploader_session.session`).

The uploader will:
- Parse every subtitle cue from the `.srt`
- Cut the clip with FFmpeg (7 seconds max, ±0.35 s padding)
- Upload the clip to your Storage Channel
- Save the `file_id`, title, text, and timestamps to `data/clips.db`

Re-running the same movie is **safe** — already-uploaded clips are skipped.

---

## 5. Running the Search Bot (Task 2)

```bash
python bot.py
```

The bot is ready immediately — no phone login needed (bot token auth).

### User experience

| Action | Result |
|--------|--------|
| Send `fire` | First matching clip + Prev \| 1/12 \| Next buttons |
| Tap **Next ➡️** | Message edits in-place to show clip 2/12 |
| Tap **⬅️ Prev** | Goes back to clip 1/12 |
| Send `/q I have a better idea` | Explicit search command |

---

## 6. File & Database Layout

### `data/clips.db` tables

| Table | Purpose |
|-------|---------|
| `clips` | One row per subtitle cue: `movie`, `start`, `end`, `raw_text`, `norm_text`, `file_id` |
| `clips_fts` | FTS5 virtual table — phrase + AND searches via `bm25` ranking |

### `cache/` directory

Temporary `.mp4` clips (named by SHA-1 hash of `movie|start|end`).  
You can safely delete this folder at any time — clips will be re-cut on demand, but since the `file_id` is stored in the DB, re-uploads to Telegram are avoided.

---

## 7. Tips & Tuning

| Setting (config.py) | Default | Notes |
|---------------------|---------|-------|
| `CLIP_PAD_SEC` | 0.35 | seconds added before/after each cue |
| `MAX_CLIP_SEC` | 7.0 | hard cap per clip |
| `VIDEO_HEIGHT` | 720 | output resolution |
| `CRF` | 24 | quality (18=lossless, 28=small) |
| `PRESET` | veryfast | FFmpeg speed/quality tradeoff |
| `FFMPEG_SEMAPHORE` | 2 | parallel cut jobs (raise on M3) |
| `DIVERSITY_CAP` | 3 | max clips from one movie per search |
| `DIVERSITY_TAKE` | 20 | total results per session |

---

## 8. Troubleshooting

**`FloodWait` during upload** — The uploader auto-waits. For large movies (>500 cues) consider running during off-peak hours.

**`FFmpeg not found`** — Make sure `ffmpeg` is on your PATH: `which ffmpeg`

**Bot doesn't respond** — Check that `BOT_TOKEN` and `API_ID`/`API_HASH` are set correctly in `config.py`.

**"Source file missing"** — The bot tried to cut a clip but the `.mp4` isn't in `movies/`. The uploader uploads to Telegram, so once `file_id` is stored the movie file is only needed to cut *new* cues.
