"""
bot.py — Task 2: Search & Delivery Bot (Pyrogram, async)
=========================================================

FIXES (v2):
  • MediaEmpty xatosi hal qilindi
  • file_id yo'q bo'lsa local fayldan kesib yuboradi
  • Xato bo'lsa keyingi natijaga o'tadi (skip)
  • edit_message_media xatosi yaxshilandi
"""

import asyncio
import hashlib
import re
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaVideo,
    Message,
)
from pyrogram.errors import FloodWait, MessageNotModified, BadRequest, MediaEmpty

from config import (
    API_ID, API_HASH, BOT_TOKEN, MEDIA_DIR,
    DIVERSITY_CAP, DIVERSITY_TAKE, SEARCH_LIMIT,
)
from database import init_db, search_clips, set_file_id
from processor import get_clip


# ── Session store ─────────────────────────────────────────────────────────────
SESSIONS: dict[str, dict] = {}
SESSION_TTL = 3600
CHAT_LOCKS: dict[int, asyncio.Lock] = {}


def chat_lock(chat_id: int) -> asyncio.Lock:
    if chat_id not in CHAT_LOCKS:
        CHAT_LOCKS[chat_id] = asyncio.Lock()
    return CHAT_LOCKS[chat_id]


def new_session(chat_id: int, hits: list[dict]) -> str:
    sid = hashlib.sha1(f"{chat_id}|{time.time()}".encode()).hexdigest()[:12]
    SESSIONS[sid] = {"hits": hits, "idx": 0, "expires": time.time() + SESSION_TTL}
    return sid


def _purge_old_sessions() -> None:
    now = time.time()
    for k in [k for k, v in SESSIONS.items() if v["expires"] < now]:
        del SESSIONS[k]


# ── Diversify results ─────────────────────────────────────────────────────────
def diversify(hits: list[dict]) -> list[dict]:
    buckets: dict[str, deque] = defaultdict(deque)
    for h in hits:
        buckets[h["movie"]].append(h)
    movies  = list(buckets.keys())
    per_cap = defaultdict(int)
    out: list[dict] = []
    while movies and len(out) < DIVERSITY_TAKE:
        m = movies.pop(0)
        if not buckets[m]:
            continue
        if per_cap[m] < DIVERSITY_CAP:
            out.append(buckets[m].popleft())
            per_cap[m] += 1
        if buckets[m] and per_cap[m] < DIVERSITY_CAP:
            movies.append(m)
    if len(out) < DIVERSITY_TAKE:
        rest = [item for dq in buckets.values() for item in dq]
        out.extend(rest[: DIVERSITY_TAKE - len(out)])
    return out


# ── UI helpers ────────────────────────────────────────────────────────────────
def nav_keyboard(sid: str, idx: int, total: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Prev",            callback_data=f"nav:{sid}:{max(0,idx-1)}"),
        InlineKeyboardButton(f"{idx+1}/{total}",   callback_data="noop"),
        InlineKeyboardButton("Next ➡️",             callback_data=f"nav:{sid}:{min(total-1,idx+1)}"),
    ]])


def make_caption(hit: dict) -> str:
    name = re.sub(r"[._\-]+", " ", Path(hit["movie"]).stem).strip().title()
    return f"🎬 {name}\n💬 {hit['raw_text']}"


def _save_file_id(hit: dict, file_id: str) -> None:
    if hit.get("id") and file_id:
        set_file_id(hit["id"], file_id)
        hit["file_id"] = file_id


# ── Video resolver ────────────────────────────────────────────────────────────
async def _resolve_video(hit: dict) -> tuple[Optional[str], Optional[Path]]:
    """
    Returns (file_id, None) or (None, local_path) or (None, None).
    MediaEmpty xatosidan himoya: bo'sh string ni None sifatida qabul qiladi.
    """
    file_id = hit.get("file_id")
    if file_id and str(file_id).strip():
        return str(file_id).strip(), None

    # Local fayldan kesib olishga urinish
    video_path = MEDIA_DIR / hit["movie"]
    if not video_path.exists():
        print(f"[WARN] Fayl topilmadi: {video_path}")
        return None, None

    try:
        clip_file = await get_clip(video_path, float(hit["start"]), float(hit["end"]))
        if clip_file.exists() and clip_file.stat().st_size > 1024:  # min 1KB
            return None, clip_file
        print(f"[WARN] Klip juda kichik yoki bo'sh: {clip_file}")
    except Exception as e:
        print(f"[FFmpeg ERROR] {hit['movie']} @ {hit['start']:.2f}s : {e}")
    return None, None


# ── Core send / edit ──────────────────────────────────────────────────────────
async def send_clip(client: Client, target, sid: str, idx: int, *, new_message: bool) -> None:
    sess = SESSIONS.get(sid)
    if not sess:
        msg = "⏰ Sessiya tugadi. Iltimos qayta qidiring."
        if new_message:
            await target.reply(msg)
        else:
            try:
                await target.edit_message_caption(msg)
            except Exception:
                pass
        return

    hits  = sess["hits"]
    total = len(hits)
    idx   = max(0, min(idx, total - 1))
    sess["idx"] = idx

    # Try this index and skip forward if video is unavailable
    for attempt_idx in range(idx, total):
        hit     = hits[attempt_idx]
        caption = make_caption(hit)
        kb      = nav_keyboard(sid, attempt_idx, total)

        file_id, local_path = await _resolve_video(hit)

        # No video source at all → try next result
        if file_id is None and local_path is None:
            print(f"[SKIP] {hit['movie']} {hit['start']:.2f}s — video yoq")
            continue

        video_src = file_id if file_id else str(local_path)
        success   = False

        # ── NEW MESSAGE ───────────────────────────────────────────────────────
        if new_message:
            chat_id = target.chat.id
            for retry in range(3):
                try:
                    msg = await client.send_video(
                        chat_id, video=video_src,
                        caption=caption, reply_markup=kb,
                        supports_streaming=True,
                    )
                    if msg and msg.video:
                        _save_file_id(hit, msg.video.file_id)
                    sess["idx"] = attempt_idx
                    success = True
                    break
                except FloodWait as fw:
                    await asyncio.sleep(fw.value + 1)
                except (MediaEmpty, BadRequest) as e:
                    print(f"[MEDIA ERROR new_msg] {e}")
                    hit["file_id"] = None   # bu file_id artiq ishlamaydi
                    # Agar local fayl ham yo'q bo'lsa keyingisiga o't
                    if not local_path:
                        break
                    video_src = str(local_path)  # file_id o'rniga local fayldan qayta urin
                except Exception as e:
                    print(f"[SEND ERROR] {e}")
                    break

        # ── EDIT EXISTING MESSAGE ─────────────────────────────────────────────
        else:
            cq_msg  = target.message
            chat_id = cq_msg.chat.id
            msg_id  = cq_msg.id

            for retry in range(3):
                try:
                    media = InputMediaVideo(video_src, caption=caption, supports_streaming=True)
                    edited = await client.edit_message_media(chat_id, msg_id, media, reply_markup=kb)
                    if edited and edited.video:
                        _save_file_id(hit, edited.video.file_id)
                    sess["idx"] = attempt_idx
                    success = True
                    break
                except MessageNotModified:
                    success = True
                    break
                except FloodWait as fw:
                    await asyncio.sleep(fw.value + 1)
                except (MediaEmpty, BadRequest) as e:
                    print(f"[MEDIA ERROR edit] {e}")
                    hit["file_id"] = None
                    if not local_path:
                        break
                    video_src = str(local_path)
                except Exception as e:
                    print(f"[EDIT ERROR] {e} — yangi xabar sifatida yuborilmoqda")
                    # Fallback: send as new message
                    try:
                        msg = await client.send_video(
                            chat_id, video=video_src,
                            caption=caption, reply_markup=kb,
                            supports_streaming=True,
                        )
                        if msg and msg.video:
                            _save_file_id(hit, msg.video.file_id)
                        sess["idx"] = attempt_idx
                        success = True
                    except Exception:
                        pass
                    break

        if success:
            return

    # Loop tamom, hech qanday klip muvaffaqiyatli yuborilmadi
    if new_message:
        await target.reply(
            "⚠️ Topilgan kliplarning video fayllari mavjud emas.\n"
            "Avval `uploader.py` ni ishlatib filmni yuklang:\n"
            "`python uploader.py movies/Film.mp4 movies/Film.srt`"
        )


# ── Pyrogram bot ──────────────────────────────────────────────────────────────
app = Client("bot_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


@app.on_message(filters.command("start") & filters.private)
async def cmd_start(client: Client, message: Message):
    await message.reply(
        "👋 Xush kelibsiz!\n\n"
        "Istalgan so'z yoki ibora yuboring — men kinolardan mos klip topib beraman.\n\n"
        "Masalan:\n"
        "  • `fire`\n"
        "  • `I have a better idea`\n\n"
        "Natijalar orasida ⬅️ / ➡️ tugmalar orqali ko'chishingiz mumkin.\n"
        "/q <ibora> — qidiruv buyrug'i"
    )


@app.on_message(filters.command("help") & filters.private)
async def cmd_help(client: Client, message: Message):
    await message.reply(
        "📖 *Foydalanish*\n\n"
        "Istalgan so'zni yuboring yoki:\n"
        "  `/q fire` — maxsus qidiruv\n\n"
        "Bot eng mos klipni yuboradi, ⬅️➡️ bilan qolganlarini ko'rasiz."
    )


@app.on_message(filters.command("q") & filters.private)
async def cmd_q(client: Client, message: Message):
    query = " ".join(message.command[1:]).strip()
    if not query:
        await message.reply("Ishlatish: `/q <ibora>`\nMasalan: `/q I love you`")
        return
    await handle_search(client, message, query)


@app.on_message(filters.text & filters.private & ~filters.command(""))
async def on_text(client: Client, message: Message):
    text = (message.text or "").strip()
    if text:
        await handle_search(client, message, text)


async def handle_search(client: Client, message: Message, query: str) -> None:
    _purge_old_sessions()
    hits = search_clips(query, limit=SEARCH_LIMIT)
    if not hits:
        await message.reply(
            "😕 Bu ibora topilmadi.\n"
            "So'zning to'g'ri yozilganini tekshiring yoki boshqa ibora yuboring."
        )
        return
    hits = diversify(hits)
    sid  = new_session(message.chat.id, hits)
    await send_clip(client, message, sid, 0, new_message=True)


@app.on_callback_query(filters.regex(r"^noop$"))
async def cb_noop(client: Client, query: CallbackQuery):
    await query.answer()


@app.on_callback_query(filters.regex(r"^nav:"))
async def cb_nav(client: Client, query: CallbackQuery):
    await query.answer()
    parts = query.data.split(":")
    if len(parts) != 3:
        return
    _, sid, idx_str = parts
    idx = int(idx_str)

    lock = chat_lock(query.message.chat.id)
    if lock.locked():
        await query.answer("⏳ Biroz kuting…", show_alert=False)
        return
    async with lock:
        await send_clip(client, query, sid, idx, new_message=False)


def main() -> None:
    init_db()
    print("🤖 Bot ishga tushdi… To'xtatish uchun Ctrl+C bosing.")
    app.run()


if __name__ == "__main__":
    main()