"""
uploader.py — Task 1: Video Processor & Uploader (Optimized with Subtitle Merging)
=================================================================================
"""

import asyncio
import sys
import re
from pathlib import Path
from typing import Optional

import pysrt                                   # pip install pysrt
from pyrogram import Client                    # pip install pyrogram
from pyrogram.errors import FloodWait

from config import API_ID, API_HASH, BOT_TOKEN, STORAGE_CHANNEL, MEDIA_DIR
from database import init_db, clip_exists, insert_clip, set_file_id
from processor import get_clip


# ── Helpers ───────────────────────────────────────────────────────────────────

def pretty_title(filename: str) -> str:
    """Fayl nomini chiroyli kino nomiga aylantiradi."""
    name = Path(filename).stem
    name = re.sub(r"[._\-]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name.title()


def srt_time_to_seconds(t: pysrt.SubRipTime) -> float:
    """pysrt vaqtini umumiy soniyalarga o'tkazadi."""
    return (
        t.hours * 3600
        + t.minutes * 60
        + t.seconds
        + t.milliseconds / 1000.0
    )


def clean_srt_text(text: str) -> str:
    """HTML teglar va ortiqcha bo'shliqlarni olib tashlaydi."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\{[^}]*\}", "", text)
    return re.sub(r"\s+", " ", text).strip()


def merge_subtitles(subs, max_duration=7.5, gap_threshold=0.8):
    """
    Subtitrlarni mantiqiy gaplar bo'yicha birlashtiradi.
    - gap_threshold: Ikki subtitr orasidagi maksimal bo'shliq (sekund).
    - max_duration: Birlashgan klipning maksimal uzunligi (sekund).
    """
    merged = []
    if not subs:
        return merged

    current_sub = None

    for sub in subs:
        sub.text = clean_srt_text(sub.text)
        if not sub.text:
            continue

        if current_sub is None:
            current_sub = sub
            continue

        current_start = srt_time_to_seconds(current_sub.start)
        current_end = srt_time_to_seconds(current_sub.end)
        next_start = srt_time_to_seconds(sub.start)
        next_end = srt_time_to_seconds(sub.end)
        
        duration_if_merged = next_end - current_start
        gap = next_start - current_end
        
        # Gap tugallanganini tekshirish (. ! ? " belgilar bilan)
        is_sentence_end = current_sub.text.strip().endswith(('.', '!', '?', '"', '”'))

        # Birlashtirish shartlari
        if (not is_sentence_end or gap < gap_threshold) and (duration_if_merged <= max_duration):
            current_sub.end = sub.end
            current_sub.text += " " + sub.text
        else:
            merged.append(current_sub)
            current_sub = sub

    if current_sub:
        merged.append(current_sub)
    
    return merged


def validate_files(mp4: Path, srt: Path) -> None:
    if not mp4.exists():
        sys.exit(f"[ERROR] Video file topilmadi: {mp4}")
    if not srt.exists():
        sys.exit(f"[ERROR] Subtitr file topilmadi: {srt}")


# ── Core upload logic ─────────────────────────────────────────────────────────

async def upload_movie(mp4_path: Path, srt_path: Path) -> None:
    validate_files(mp4_path, srt_path)
    init_db()

    movie_filename = mp4_path.name
    movie_title    = pretty_title(mp4_path.name)

    print(f"\n{'='*60}")
    print(f"  Movie   : {movie_title}")
    print(f"  Channel : {STORAGE_CHANNEL}")
    print(f"{'='*60}\n")

    # Subtitrlarni ochish
    try:
        raw_subs = pysrt.open(str(srt_path), encoding="utf-8")
    except UnicodeDecodeError:
        raw_subs = pysrt.open(str(srt_path), encoding="latin-1")

    # Mantiqiy birlashtirish
    subs = merge_subtitles(raw_subs, max_duration=7.0)

    total   = len(subs)
    skipped = 0
    uploaded = 0
    errors  = 0

    async with Client(
        "bot_session", # bot.py bilan bir xil sessiya
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN # BOT REJIMIDA ISHLASH SHART!
    ) as app:

        for i, sub in enumerate(subs, start=1):
            start_s = srt_time_to_seconds(sub.start)
            end_s   = srt_time_to_seconds(sub.end)
            text    = sub.text # merge_subtitles'da tozalangan

            # Juda qisqa kliplarni tashlab ketish
            if (end_s - start_s) < 0.3:
                skipped += 1
                continue

            if clip_exists(movie_filename, start_s, end_s):
                skipped += 1
                print(f"  [{i:>5}/{total}] SKIP  {start_s:.2f}s  {text[:40]}...")
                continue

            print(f"  [{i:>5}/{total}] CUT   {start_s:.2f}s–{end_s:.2f}s")

            # 1. FFmpeg orqali kesish
            try:
                clip_file = await get_clip(mp4_path, start_s, end_s)
            except Exception as exc:
                print(f"           ⚠ FFmpeg xatosi: {exc}")
                errors += 1
                continue

            # 2. Bazaga yozish
            clip_id = insert_clip(
                movie=movie_filename,
                start=start_s,
                end=end_s,
                raw_text=text,
            )

            # 3. Telegramga yuklash
            caption = (
                f"🎬 {movie_title}\n"
                f"⏱ {sub.start} → {sub.end}\n"
                f"💬 {text}"
            )
            
            file_id: Optional[str] = None
            for attempt in range(3):
                try:
                    msg = await app.send_video(
                        chat_id=STORAGE_CHANNEL,
                        video=str(clip_file),
                        caption=caption,
                        supports_streaming=True,
                    )
                    if msg and msg.video:
                        file_id = msg.video.file_id
                    break
                except FloodWait as fw:
                    await asyncio.sleep(fw.value + 1)
                except Exception as exc:
                    print(f"           ⚠ Yuklashda xato: {exc}")
                    await asyncio.sleep(2)

            # 4. file_id ni bazaga yangilash
            if file_id:
                set_file_id(clip_id, file_id)
                uploaded += 1
            else:
                errors += 1

    print(f"\n{'='*60}")
    print(f"  Tugadi!  Yuklandi: {uploaded}  O'tkazildi: {skipped}  Xato: {errors}")
    print(f"{'='*60}\n")

def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python uploader.py <movie.mp4> <movie.srt>")
        sys.exit(1)

    mp4 = Path(sys.argv[1])
    srt = Path(sys.argv[2])

    if not mp4.exists() and (MEDIA_DIR / mp4).exists():
        mp4 = MEDIA_DIR / mp4
    if not srt.exists() and (MEDIA_DIR / srt).exists():
        srt = MEDIA_DIR / srt

    asyncio.run(upload_movie(mp4, srt))

if __name__ == "__main__":
    main()