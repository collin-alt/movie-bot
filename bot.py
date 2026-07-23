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
    r"\[.*?\]",
    r"\(.*?\)",
]

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm"}


def clean_title(filename: str) -> tuple[str, str | None]:
    """Extract a probable (title, year) pair from a release filename."""
    name, _ext = os.path.splitext(filename)

    # Replace separators with spaces
    name = re.sub(r"[._]", " ", name)

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
    params = {"api_key": TMDB_API_KEY, "query": title, "include_adult": "false"}
    if year:
        params["year"] = year
    try:
        resp = requests.get(TMDB_SEARCH_URL, params=params, timeout=10)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except requests.RequestException as e:
        logger.warning("TMDB lookup failed: %s", e)
        return None

    # Prefer movie/tv results, ignore people
    results = [r for r in results if r.get("media_type") in ("movie", "tv")]
    return results[0] if results else None


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

    filename = getattr(file_obj, "file_name", None) or (message.caption or "")
    if not filename:
        return

    # Skip non-video documents (e.g. subtitles, nfo files)
    ext = os.path.splitext(filename)[1].lower()
    if message.document and ext not in VIDEO_EXTENSIONS:
        return

    title, year = clean_title(filename)
    if not title:
        return

    logger.info("Detected upload: filename=%r -> title=%r year=%r", filename, title, year)

    meta = search_tmdb(title, year)
    if not meta:
        logger.info("No TMDB match found for %r", title)
        return

    caption, poster_url = format_announcement(meta)

    try:
        if poster_url:
            await context.bot.send_photo(
                chat_id=message.chat_id,
                photo=poster_url,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
                reply_to_message_id=message.message_id,
            )
        else:
            await context.bot.send_message(
                chat_id=message.chat_id,
                text=caption,
                parse_mode=ParseMode.MARKDOWN,
                reply_to_message_id=message.message_id,
            )
    except Exception as e:
        logger.error("Failed to send announcement: %s", e)


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
