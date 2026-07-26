"""
Telegram Movie Announcer Bot
-----------------------------
Watches a Telegram group for newly uploaded video/document files,
guesses the movie/show title from the filename, looks it up on TMDB,
and posts a nicely formatted announcement (with poster) back to the group.

Setup:
1. pip install -r requirements.txt
2. Copy .env.example to .env and fill in your tokens
3. Add the bot to your group as an ADMIN, and turn OFF "Group Privacy"
   in @BotFather (Bot Settings > Group Privacy > Turn off) so it can see
   file uploads from other members, not just commands.
4. Run: python bot.py
"""

import os
import re
import threading
import logging
import requests
from dotenv import load_dotenv
from flask import Flask
from telegram import Update, InputMediaPhoto
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

load_dotenv()

# ---------------------------------------------------------------------------
# Keep-alive web server (Render's free tier requires the service to listen
# on a port and respond to HTTP requests; this tiny Flask app does just
# that. An external pinger like UptimeRobot should hit this URL every ~5
# minutes to stop Render from putting the service to sleep.)
# ---------------------------------------------------------------------------

keep_alive_app = Flask(__name__)


@keep_alive_app.route("/")
def health_check():
    return "Movie Announcer Bot is running."


def run_keep_alive_server():
    port = int(os.environ.get("PORT", 10000))
    keep_alive_app.run(host="0.0.0.0", port=port)

BOT_TOKEN = os.getenv("BOT_TOKEN")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")  # optional: restrict to one group

TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/multi"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

# Posted instead of the poster/synopsis whenever a movie can't be matched
# on TMDB, so the audience still sees something clean instead of the
# original promo/spam caption from the source channel.
FALLBACK_CAPTION = (
    "🎬✨ TRANSLATED MOVIES™️ ✨🎬\n\n"
    "🍿 We deliver all your favorite TRANSLATED movies & shows, straight to "
    "this group — fresh uploads added regularly! 🔥📽️\n\n"
    "✈️ Telegram: @collyni | @wilber256\n"
    "🟢 WhatsApp: 0744546518 | 0775716867\n\n"
    "🌟 Stay tuned, more coming soon! 🚀"
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Title cleanup
# ---------------------------------------------------------------------------

# Common junk found in scene-release filenames that we want to strip out
# before searching TMDB.
JUNK_PATTERNS = [
    r"\b(1080p|720p|2160p|480p|4k|uhd|hdr|hdrip)\b",
    r"\b(webrip|web-dl|webdl|bluray|blu-ray|bdrip|dvdrip|hdtv|hdcam|camrip)\b",
    r"\b(x264|x265|h264|h265|hevc|avc)\b",
    r"\b(aac|ac3|dts|5\.1|7\.1)\b",
    r"\b(yify|yts|rarbg|galaxyrg|evo|ntb|ethel)\b",
    r"\bvj\s+\w+\b",  # strip translator/VJ credit tags, e.g. "Vj Junior"
    r"#\w+",  # strip hashtags, e.g. "#SCI_FI"
    r"\[.*?\]",
    r"\(.*?\)",
]

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm"}


def clean_title(filename: str) -> tuple[str, str | None]:
    """Extract a probable (title, year) pair from a release filename or caption."""
    # Only look at the first non-empty line. Multi-line captions from
    # source channels are almost always: [title line] + [promo/spam block].
    first_line = next((ln for ln in filename.splitlines() if ln.strip()), filename)
    name, _ext = os.path.splitext(first_line)

    # Replace separators with spaces
    name = re.sub(r"[._]", " ", name)

    # Cut off genre/rating tags that follow a bullet-style separator,
    # e.g. "Mortal Engines 2018 ‧ Action/Sci-fi" -> stop before "‧"
    sep_match = re.search(r"[‧•·|]", name)
    if sep_match:
        name = name[: sep_match.start()]

    # Pull out a year if present (helps TMDB disambiguate)
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", name)
    year = year_match.group(1) if year_match else None

    # Cut everything from the year onward, since junk tags usually follow it
    if year_match:
        name = name[: year_match.start()]

    # Strip known junk tags
    for pattern in JUNK_PATTERNS:
        name = re.sub(pattern, "", name, flags=re.IGNORECASE)

    name = re.sub(r"\s+", " ", name).strip(" -_")
    return name, year


# ---------------------------------------------------------------------------
# TMDB lookup
# ---------------------------------------------------------------------------

def search_tmdb(title: str, year: str | None = None) -> dict | None:
    if not TMDB_API_KEY:
        return None

    def _query(with_year: bool) -> dict | None:
        params = {"api_key": TMDB_API_KEY, "query": title, "include_adult": "false"}
        if with_year and year:
            params["year"] = year
        try:
            resp = requests.get(TMDB_SEARCH_URL, params=params, timeout=10)
            resp.raise_for_status()
            results = resp.json().get("results", [])
        except requests.RequestException as e:
            logger.warning("TMDB lookup failed: %s", e)
            return None
        results = [r for r in results if r.get("media_type") in ("movie", "tv")]
        return results[0] if results else None

    # Try with the year first (more precise), then fall back without it in
    # case the number we extracted wasn't actually a release year.
    result = _query(with_year=True)
    if not result and year:
        logger.info("No match with year=%s, retrying without year filter", year)
        result = _query(with_year=False)
    return result


def format_announcement(meta: dict) -> tuple[str, str | None]:
    """Return (caption, poster_url) for a TMDB result."""
    media_type = meta.get("media_type")
    title = meta.get("title") or meta.get("name") or "Unknown Title"
    date = meta.get("release_date") or meta.get("first_air_date") or ""
    year = date.split("-")[0] if date else "N/A"
    rating = meta.get("vote_average")
    overview = meta.get("overview") or "No description available."
    if len(overview) > 400:
        overview = overview[:400].rsplit(" ", 1)[0] + "…"

    kind = "🎬 Movie" if media_type == "movie" else "📺 TV Show"
    rating_line = f"⭐ {rating:.1f}/10\n" if rating else ""

    caption = (
        f"{kind} • New Upload!\n\n"
        f"*{title}* ({year})\n"
        f"{rating_line}\n"
        f"{overview}"
    )

    poster_path = meta.get("poster_path")
    poster_url = f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else None
    return caption, poster_url


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def handle_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return

    if GROUP_CHAT_ID and str(message.chat_id) != str(GROUP_CHAT_ID):
        return

    file_obj = message.video or message.document
    if not file_obj:
        return

    filename = getattr(file_obj, "file_name", None) or ""
    caption_text = message.caption or ""

    # Skip non-video documents (e.g. subtitles, nfo files) -- only check
    # this when we actually have a filename with an extension to judge.
    if filename:
        ext = os.path.splitext(filename)[1].lower()
        if message.document and ext not in VIDEO_EXTENSIONS:
            return

    if not filename and not caption_text:
        return

    meta = None
    title = year = None

    # Try the actual filename first...
    if filename:
        title, year = clean_title(filename)
        if title:
            logger.info("Trying filename-derived title: %r year=%r", title, year)
            meta = search_tmdb(title, year)

    # ...then fall back to the caption if that didn't find anything. This
    # matters a lot for groups where the real file has a generic/random
    # name (e.g. "VID2024.mp4") but the actual title is written in the
    # caption instead.
    if not meta and caption_text:
        cap_title, cap_year = clean_title(caption_text)
        if cap_title and cap_title != title:
            logger.info("Trying caption-derived title: %r year=%r", cap_title, cap_year)
            meta = search_tmdb(cap_title, cap_year)
            if meta:
                title, year = cap_title, cap_year

    if not title:
        return

    if not meta:
        logger.info("No TMDB match found for %r (filename=%r, caption=%r)", title, filename, caption_text)

    thread_id = message.message_thread_id  # keeps it in the same topic, if any

    # Drop the promo/spam block that source channels often tack onto
    # captions (join-channel ads, prices, contact info, links). Keep
    # only the first line, which is normally the actual title text.
    original_caption = None
    if message.caption:
        first_line = next(
            (ln.strip() for ln in message.caption.splitlines() if ln.strip()),
            None,
        )
        original_caption = first_line or None

    try:
        # 1. Post the cover + info FIRST, if we found a match. If not, we
        #    still clean up the post below so the audience never sees the
        #    promo spam, just without the poster/synopsis on top.
        if meta:
            info_caption, poster_url = format_announcement(meta)
            if poster_url:
                await context.bot.send_photo(
                    chat_id=message.chat_id,
                    photo=poster_url,
                    caption=info_caption,
                    parse_mode=ParseMode.MARKDOWN,
                    message_thread_id=thread_id,
                )
            else:
                await context.bot.send_message(
                    chat_id=message.chat_id,
                    text=info_caption,
                    parse_mode=ParseMode.MARKDOWN,
                    message_thread_id=thread_id,
                )
        else:
            await context.bot.send_message(
                chat_id=message.chat_id,
                text=FALLBACK_CAPTION,
                message_thread_id=thread_id,
            )

        # 2. Re-post the actual video/file (with the cleaned caption) right
        #    below the announcement. Reusing the file_id means Telegram
        #    just re-links the existing file — no re-uploading of bytes.
        if message.video:
            await context.bot.send_video(
                chat_id=message.chat_id,
                video=message.video.file_id,
                caption=original_caption,
                message_thread_id=thread_id,
            )
        else:
            await context.bot.send_document(
                chat_id=message.chat_id,
                document=message.document.file_id,
                caption=original_caption,
                message_thread_id=thread_id,
            )

        # 3. Remove the original upload so it isn't duplicated.
        #    Requires the bot to have "Delete Messages" admin permission.
        try:
            await context.bot.delete_message(
                chat_id=message.chat_id, message_id=message.message_id
            )
        except Exception as e:
            logger.warning(
                "Could not delete original upload (check bot has Delete "
                "Messages permission): %s", e
            )
    except Exception as e:
        logger.error("Failed to post announcement/repost file: %s", e)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Movie Announcer Bot is running.\n"
        "I'll automatically post a cover + summary whenever a video is uploaded to this group."
    )


async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("pong ✅")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is not set. Check your .env file.")
    if not TMDB_API_KEY:
        logger.warning("TMDB_API_KEY not set — cover/lookup features will be disabled.")

    # Start the keep-alive web server in the background so Render sees this
    # as a live web service and keeps it on the free tier.
    threading.Thread(target=run_keep_alive_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("ping", ping_cmd))
    app.add_handler(
        MessageHandler((filters.VIDEO | filters.Document.ALL) & ~filters.COMMAND, handle_upload)
    )

    logger.info("Bot started. Listening for uploads...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Bot crashed with an unhandled exception:")
        raise
