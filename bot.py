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
import json
import random
import asyncio
import threading
import logging
import difflib
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

try:
    from telegram.ext import MessageReactionHandler
except ImportError:
    MessageReactionHandler = None

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
# original promo/spam caption from the source channel. One is picked at
# random each time so it doesn't feel repetitive.
FALLBACK_CAPTIONS = [
    (
        "🎬✨ TRANSLATED MOVIES™️ ✨🎬\n\n"
        "🍿 We deliver all your favorite TRANSLATED movies & shows, straight to "
        "this group — fresh uploads added regularly! 🔥📽️\n\n"
        "✈️ Telegram: @collyni | @wilber256\n"
        "🟢 WhatsApp: 0744546518 | 0775716867\n\n"
        "🌟 Stay tuned, more coming soon! 🚀"
    ),
    (
        "🔥🎬 TRANSLATED MOVIES™️ 🎬🔥\n\n"
        "The hottest TRANSLATED movies & shows land here first! 🍿⚡ New drops "
        "all the time — don't sleep on it.\n\n"
        "✈️ Telegram: @collyni | @wilber256\n"
        "🟢 WhatsApp: 0744546518 | 0775716867\n\n"
        "👀 Keep watching, more loading... 🚀"
    ),
    (
        "🎥 Welcome to TRANSLATED MOVIES™️ 🎥\n\n"
        "A home for movie lovers — every upload here is TRANSLATED just for "
        "you. 🍿💬 New titles added often, so stick around!\n\n"
        "✈️ Telegram: @collyni | @wilber256\n"
        "🟢 WhatsApp: 0744546518 | 0775716867\n\n"
        "🌍 Thanks for being part of the family! 💫"
    ),
    (
        "🍿✨ TRANSLATED MOVIES™️ ✨🍿\n\n"
        "Popcorn ready? 🎬 New TRANSLATED movies & shows keep sliding into "
        "this group — grab a seat! 🛋️\n\n"
        "✈️ Telegram: @collyni | @wilber256\n"
        "🟢 WhatsApp: 0744546518 | 0775716867\n\n"
        "🎞️ Stay glued, more coming! 🔥"
    ),
    (
        "📢 TRANSLATED MOVIES™️ 📢\n\n"
        "Your go-to spot for TRANSLATED movies & shows — updated regularly, "
        "no cap. 🎬🔥\n\n"
        "✈️ Telegram: @collyni | @wilber256\n"
        "🟢 WhatsApp: 0744546518 | 0775716867\n\n"
        "🚨 More titles dropping soon 🚨"
    ),
    (
        "🔥🎬 TRANSLATED MOVIES™️ 🎬🔥\n\n"
        "The hottest TRANSLATED movies & shows land here first! 🍿⚡ From "
        "blockbuster action to gripping drama, thrillers, comedy, and "
        "everything in between — if it's worth watching, it's worth "
        "TRANSLATING, and you'll find it right here.\n\n"
        "New drops all the time, so keep your notifications on — you don't "
        "want to miss what's coming next. 🎥💥\n\n"
        "#TranslatedMovies #NewUploads #MovieNight #VJTranslated\n\n"
        "✈️ Telegram: @collyni | @wilber256\n"
        "🟢 WhatsApp: 0744546518 | 0775716867\n\n"
        "👀 Keep watching, more loading... 🚀"
    ),
    (
        "🎥 Welcome to TRANSLATED MOVIES™️ 🎥\n\n"
        "A home for movie lovers — every upload here is TRANSLATED just for "
        "you, picked with care so you never run out of something great to "
        "watch. 🍿💬\n\n"
        "Whether you're into action, romance, thrillers, or something "
        "totally unexpected, this group has a little bit of everything. New "
        "titles added often, so stick around and be part of the movement! "
        "🌍✨\n\n"
        "#TranslatedMovies #MovieCommunity #VJContent #StayTuned\n\n"
        "✈️ Telegram: @collyni | @wilber256\n"
        "🟢 WhatsApp: 0744546518 | 0775716867\n\n"
        "🌍 Thanks for being part of the family! 💫"
    ),
    (
        "📢 TRANSLATED MOVIES™️ 📢\n\n"
        "Your go-to spot for TRANSLATED movies & shows — updated regularly, "
        "no cap. 🎬🔥 We bring you the latest and greatest, all translated "
        "and ready to watch, so you never have to go searching elsewhere.\n\n"
        "Big releases, hidden gems, and everything worth your time — this "
        "is the place. 🎞️\n\n"
        "#TranslatedMovies #NewReleases #MovieHub #VJUpload\n\n"
        "✈️ Telegram: @collyni | @wilber256\n"
        "🟢 WhatsApp: 0744546518 | 0775716867\n\n"
        "🚨 More titles dropping soon 🚨"
    ),
]

# Static .webp stickers sent alongside the fallback bio for a bit of fun.
STICKERS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stickers")
STICKER_FILES = ["hi.webp", "ok.webp", "laughing_mouse.webp", "cool.webp"]


def _random_sticker_path() -> str | None:
    available = [f for f in STICKER_FILES if os.path.isfile(os.path.join(STICKERS_DIR, f))]
    if not available:
        return None
    return os.path.join(STICKERS_DIR, random.choice(available))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Tracks which movies/shows already got a cover posted per chat/topic, so
# multi-part or multi-episode uploads of the same title only get the
# poster/info once. Keyed by TMDB ID when we have a match (reliable even
# if the extracted title text varies slightly between episodes), falling
# back to normalized title text only when there's no TMDB match at all.
# Persisted to disk so it survives the bot briefly sleeping/waking up
# (it resets only on an actual redeploy).
_DEDUP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "posted_titles.json")
_RECENT_LIMIT = 1000  # cap memory/file size; oldest entries drop off first


def _load_recently_posted() -> dict:
    try:
        with open(_DEDUP_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


_recently_posted: dict = _load_recently_posted()


def _save_recently_posted() -> None:
    try:
        with open(_DEDUP_FILE, "w") as f:
            json.dump(_recently_posted, f)
    except Exception as e:
        logger.warning("Could not persist dedup file: %s", e)


# ---------------------------------------------------------------------------
# Stats store — powers /find, /request, /leaderboard, weekly recap, and
# milestone celebrations. Persisted to disk the same way as the dedup file.
# ---------------------------------------------------------------------------

_STATS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_stats.json")


def _load_stats() -> dict:
    try:
        with open(_STATS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"upload_log": [], "reaction_counts": {}, "last_milestone": 0}


_stats: dict = _load_stats()


def _save_stats() -> None:
    try:
        with open(_STATS_FILE, "w") as f:
            json.dump(_stats, f)
    except Exception as e:
        logger.warning("Could not persist stats file: %s", e)


def _log_upload(title: str, vj: str | None, chat_id: int, message_id: int | None) -> None:
    import datetime

    _stats.setdefault("upload_log", []).append({
        "ts": datetime.datetime.now().isoformat(),
        "title": title,
        "vj": vj,
        "chat_id": chat_id,
        "message_id": message_id,
    })
    if len(_stats["upload_log"]) > 5000:
        _stats["upload_log"] = _stats["upload_log"][-5000:]
    _save_stats()


def _dedup_keys(chat_id: int, thread_id: int | None, meta: dict | None, title: str) -> list[str]:
    keys = [f"{chat_id}:{thread_id}:title:{title.strip().lower()}"]
    if meta and meta.get("id") is not None:
        keys.append(f"{chat_id}:{thread_id}:tmdb:{meta.get('media_type')}:{meta['id']}")
    return keys


def _mark_posted(chat_id: int, thread_id: int | None, meta: dict | None, title: str) -> None:
    for key in _dedup_keys(chat_id, thread_id, meta, title):
        _recently_posted[key] = True
    if len(_recently_posted) > _RECENT_LIMIT:
        _recently_posted.pop(next(iter(_recently_posted)))
    _save_recently_posted()


def _was_recently_posted(chat_id: int, thread_id: int | None, meta: dict | None, title: str) -> bool:
    return any(key in _recently_posted for key in _dedup_keys(chat_id, thread_id, meta, title))


def _should_post_fallback_bio(chat_id: int, thread_id: int | None) -> bool:
    """Only show the fallback bio every 2-4 no-match uploads (randomly),
    not on every single one, so it doesn't feel repetitive/spammy."""
    count_key = f"{chat_id}:{thread_id}:nomatch_count"
    threshold_key = f"{chat_id}:{thread_id}:nomatch_threshold"

    if threshold_key not in _recently_posted:
        _recently_posted[threshold_key] = random.randint(2, 4)

    count = _recently_posted.get(count_key, 0) + 1
    threshold = _recently_posted[threshold_key]

    if count >= threshold:
        _recently_posted[count_key] = 0
        _recently_posted[threshold_key] = random.randint(2, 4)
        _save_recently_posted()
        return True

    _recently_posted[count_key] = count
    _save_recently_posted()
    return False


def _next_episode_number(chat_id: int, thread_id: int | None, meta: dict | None, title: str) -> int:
    """Returns 1 for the first upload of this title in this chat/topic, 2
    for the next, and so on — used to number episodes/parts in order.
    Matches on title text OR TMDB ID (whichever is already tracked) so the
    count keeps going even if the extracted title text varies slightly
    between episodes."""
    keys = [f"{chat_id}:{thread_id}:count:title:{title.strip().lower()}"]
    if meta and meta.get("id") is not None:
        keys.append(f"{chat_id}:{thread_id}:count:tmdb:{meta.get('media_type')}:{meta['id']}")
    current = max((_recently_posted.get(k, 0) for k in keys), default=0)
    next_num = current + 1
    for k in keys:
        _recently_posted[k] = next_num
    _save_recently_posted()
    return next_num

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


def extract_vj_credit(text: str) -> str | None:
    """Pull out a 'Vj Name' translator credit before it gets stripped out
    for title matching, so it can be preserved in the posted caption."""
    if not text:
        return None
    first_line = next((ln for ln in text.splitlines() if ln.strip()), text)
    match = re.search(r"\bvj\s+([a-zA-Z][\w'-]*(?:\s+[a-zA-Z][\w'-]*)?)", first_line, re.IGNORECASE)
    if not match:
        return None
    return "Vj " + match.group(1).title()


def clean_title(filename: str) -> tuple[str, str | None, bool]:
    """Extract a probable (title, year, is_series) triple from a release
    filename or caption. is_series is True only if the raw text actually
    contains an episode/season marker (S01E02, Episode 3, Season 2...) —
    used so a plain title with no such marker searches movies only,
    instead of risking a match against an unrelated TV show of the same
    name."""
    # Only look at the first non-empty line. Multi-line captions from
    # source channels are almost always: [title line] + [promo/spam block].
    first_line = next((ln for ln in filename.splitlines() if ln.strip()), filename)

    is_series = bool(
        re.search(r"\bs\d{1,2}[.\s]*e\d{1,3}\b", first_line, re.IGNORECASE)
        or re.search(r"\b(episode|ep)\.?\s*\d+\b", first_line, re.IGNORECASE)
        or re.search(r"\bseason\s*\d+\b", first_line, re.IGNORECASE)
    )

    # Strip a real video file extension only (avoid os.path.splitext here —
    # it would wrongly treat something like "...VJ JR.2026" as if ".2026"
    # were the file extension and silently eat the year).
    name = re.sub(r"\.(mp4|mkv|avi|mov|m4v|webm)$", "", first_line, flags=re.IGNORECASE)

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

    # Strip multi-part indicators (Part 1, Pt2, CD1, Disc 2, etc.) so that
    # split-up uploads of the same movie all resolve to the same title.
    name = re.sub(r"\b(part|pt|cd|disc)\.?\s*\d+\b", "", name, flags=re.IGNORECASE)

    # Strip episode/season numbering (Episode 3, Ep03, S01E02, S01.E02,
    # Season 2) so multiple episodes of the same show collapse to one
    # title. Allow an optional space between the season and episode
    # numbers since "S01.E01" becomes "S01 E01" after dots are converted
    # to spaces above.
    name = re.sub(r"\bs\d{1,2}\s*e\d{1,3}\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\b(episode|ep)\.?\s*\d+\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\bseason\s*\d+\b", "", name, flags=re.IGNORECASE)

    # Strip known junk tags
    for pattern in JUNK_PATTERNS:
        name = re.sub(pattern, "", name, flags=re.IGNORECASE)

    name = re.sub(r"\s+", " ", name).strip(" -_")
    return name, year, is_series


# ---------------------------------------------------------------------------
# TMDB lookup
# ---------------------------------------------------------------------------

def search_tmdb(title: str, year: str | None = None, is_series: bool = False) -> dict | None:
    if not TMDB_API_KEY:
        return None

    allowed_types = ("tv",) if is_series else ("movie",)

    def _query(with_year: bool, restrict_type: bool = True) -> list[dict]:
        params = {"api_key": TMDB_API_KEY, "query": title, "include_adult": "false"}
        if with_year and year:
            params["year"] = year
        try:
            resp = requests.get(TMDB_SEARCH_URL, params=params, timeout=10)
            resp.raise_for_status()
            results = resp.json().get("results", [])
        except requests.RequestException as e:
            logger.warning("TMDB lookup failed: %s", e)
            return []
        types = allowed_types if restrict_type else ("movie", "tv")
        return [r for r in results if r.get("media_type") in types]

    def _best_match(results: list[dict]) -> dict | None:
        if not results:
            return None
        query_norm = title.strip().lower()

        def score(r: dict) -> float:
            name = (r.get("title") or r.get("name") or "").strip().lower()
            similarity = difflib.SequenceMatcher(None, name, query_norm).ratio()
            r_year = (r.get("release_date") or r.get("first_air_date") or "")[:4]
            year_bonus = 0.3 if (year and r_year == year) else 0.0
            popularity_bonus = min(r.get("popularity", 0) or 0, 50) / 500
            return similarity + year_bonus + popularity_bonus

        ranked = sorted(results, key=score, reverse=True)
        best = ranked[0]
        # Reject weak matches entirely rather than posting a probably-wrong
        # poster — e.g. a short/generic title colliding with an unrelated
        # show that happens to share the same words.
        best_name = (best.get("title") or best.get("name") or "").strip().lower()
        similarity = difflib.SequenceMatcher(None, best_name, query_norm).ratio()
        if similarity < 0.6:
            return None
        return best

    # Try with the year first (more precise), then fall back without it in
    # case the number we extracted wasn't actually a release year, and
    # finally allow both media types as a last resort in case our
    # movie-vs-series guess was wrong for this particular title.
    result = _best_match(_query(with_year=True))
    if not result and year:
        logger.info("No confident match with year=%s, retrying without year filter", year)
        result = _best_match(_query(with_year=False))
    if not result:
        logger.info("No confident match restricted to %s, retrying with both types", allowed_types)
        result = _best_match(_query(with_year=bool(year), restrict_type=False))
    return result


def format_announcement(meta: dict, vj_credit: str | None = None) -> tuple[str, str | None]:
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
    title_line = f"{title} {vj_credit}" if vj_credit else title

    caption = (
        f"{kind} • New Upload!\n\n"
        f"*{title_line}* ({year})\n"
        f"{rating_line}\n"
        f"{overview}"
    )

    poster_path = meta.get("poster_path")
    poster_url = f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else None
    return caption, poster_url


FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "Poppins-Bold.ttf")


def add_vj_watermark(poster_url: str, vj_credit: str) -> bytes | None:
    """Download the poster and stamp a 'Vj Name' badge onto its top-right
    corner, similar to how VJs brand their own posters manually. Returns
    the modified image bytes, or None if anything goes wrong (caller
    should fall back to the plain poster_url in that case)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        from io import BytesIO

        resp = requests.get(poster_url, timeout=10)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGBA")
        W, H = img.size

        label = vj_credit.upper()
        font_size = max(18, W // 14)
        font = ImageFont.truetype(FONT_PATH, font_size)

        draw = ImageDraw.Draw(img)
        pad_x, pad_y = font_size // 2, font_size // 3
        bbox = draw.textbbox((0, 0), label, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        badge_w, badge_h = text_w + pad_x * 2, text_h + pad_y * 2

        margin_x = W * 0.03
        margin_y = H * 0.06  # further from the top than the sides, so
                              # Telegram's own overlay UI never covers it
        x0, y0 = W - badge_w - margin_x, margin_y
        x1, y1 = x0 + badge_w, y0 + badge_h

        # Yellow badge with a dark blue border, like the reference example
        draw.rounded_rectangle([x0, y0, x1, y1], radius=badge_h * 0.25,
                                fill=(255, 221, 0, 255), outline=(20, 30, 90, 255),
                                width=max(2, font_size // 12))
        draw.text((x0 + pad_x - bbox[0], y0 + pad_y - bbox[1]), label, font=font, fill=(15, 15, 60, 255))

        out = BytesIO()
        img.convert("RGB").save(out, format="JPEG", quality=90)
        return out.getvalue()
    except Exception as e:
        logger.warning("Could not watermark poster: %s", e)
        return None


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

# One lock per (chat_id, thread_id) so uploads to the same group/topic are
# always handled one at a time, in order. This matters when several files
# of the same movie/show (e.g. Part 1 + Part 2) get sent together — without
# this, two uploads could both check "already posted?" at the same instant
# before either finishes marking it, resulting in the cover being posted
# twice. It also keeps reposts coming out in the same order they arrived.
_chat_locks: dict[tuple, asyncio.Lock] = {}


def _get_chat_lock(chat_id: int, thread_id: int | None) -> asyncio.Lock:
    key = (chat_id, thread_id)
    if key not in _chat_locks:
        _chat_locks[key] = asyncio.Lock()
    return _chat_locks[key]


async def handle_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return

    if GROUP_CHAT_ID and str(message.chat_id) != str(GROUP_CHAT_ID):
        return

    file_obj = message.video or message.document
    if not file_obj:
        return

    async with _get_chat_lock(message.chat_id, message.message_thread_id):
        await _process_upload(message, context, file_obj)


async def _process_upload(message, context: ContextTypes.DEFAULT_TYPE, file_obj):
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

    vj_credit = extract_vj_credit(caption_text) or extract_vj_credit(filename)

    meta = None
    title = year = None

    # Try the actual filename first...
    if filename:
        title, year, is_series = clean_title(filename)
        if title:
            logger.info("Trying filename-derived title: %r year=%r is_series=%r", title, year, is_series)
            meta = search_tmdb(title, year, is_series)

    # ...then fall back to the caption if that didn't find anything. This
    # matters a lot for groups where the real file has a generic/random
    # name (e.g. "VID2024.mp4") but the actual title is written in the
    # caption instead.
    if not meta and caption_text:
        cap_title, cap_year, cap_is_series = clean_title(caption_text)
        if cap_title and cap_title != title:
            logger.info("Trying caption-derived title: %r year=%r is_series=%r", cap_title, cap_year, cap_is_series)
            meta = search_tmdb(cap_title, cap_year, cap_is_series)
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
        # 1. Post the cover + info FIRST (or the fallback bio if no match),
        #    but only if we haven't already posted for this exact movie in
        #    this chat/topic — avoids repeating it for every episode/part.
        already_posted = _was_recently_posted(message.chat_id, thread_id, meta, title)
        announcement_message_id = None
        if meta and not already_posted:
            info_caption, poster_url = format_announcement(meta, vj_credit)
            if poster_url:
                photo_to_send = poster_url
                if vj_credit:
                    watermarked = add_vj_watermark(poster_url, vj_credit)
                    if watermarked:
                        photo_to_send = watermarked
                sent = await context.bot.send_photo(
                    chat_id=message.chat_id,
                    photo=photo_to_send,
                    caption=info_caption,
                    parse_mode=ParseMode.MARKDOWN,
                    message_thread_id=thread_id,
                )
            else:
                sent = await context.bot.send_message(
                    chat_id=message.chat_id,
                    text=info_caption,
                    parse_mode=ParseMode.MARKDOWN,
                    message_thread_id=thread_id,
                )
            _mark_posted(message.chat_id, thread_id, meta, title)
            announcement_message_id = sent.message_id
        elif not meta and not already_posted:
            if _should_post_fallback_bio(message.chat_id, thread_id):
                sent = await context.bot.send_message(
                    chat_id=message.chat_id,
                    text=random.choice(FALLBACK_CAPTIONS),
                    message_thread_id=thread_id,
                )
                announcement_message_id = sent.message_id
                sticker_path = _random_sticker_path()
                if sticker_path:
                    try:
                        with open(sticker_path, "rb") as sticker_file:
                            await context.bot.send_sticker(
                                chat_id=message.chat_id,
                                sticker=sticker_file,
                                message_thread_id=thread_id,
                            )
                    except Exception as e:
                        logger.warning("Could not send sticker: %s", e)
            _mark_posted(message.chat_id, thread_id, meta, title)

        # 2. Re-post the actual video/file, labeled "Title N" for TV shows
        #    (in upload order) or just "Title" for a one-off upload, with
        #    the VJ credit attached here, right under the video itself,
        #    below the announcement. Reusing the file_id means Telegram
        #    just re-links the existing file — no re-uploading of bytes.
        #
        #    Numbering is based on whether this exact title has actually
        #    been uploaded before in this chat/topic — NOT on TMDB's
        #    movie/TV classification, which isn't reliable enough (e.g.
        #    TMDB lists some one-off films as "TV" in their database). A
        #    true single upload never gets a second one, so it never gets
        #    numbered; only a real second/third part or episode does.
        display_title = (meta.get("title") or meta.get("name")) if meta else title
        episode_number = _next_episode_number(message.chat_id, thread_id, meta, title)
        video_caption = f"{display_title} {episode_number}" if episode_number > 1 else display_title
        if vj_credit:
            video_caption += f"\n\n🎙️ {vj_credit}"

        if message.video:
            await context.bot.send_video(
                chat_id=message.chat_id,
                video=message.video.file_id,
                caption=video_caption,
                message_thread_id=thread_id,
            )
        else:
            await context.bot.send_document(
                chat_id=message.chat_id,
                document=message.document.file_id,
                caption=video_caption,
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

        # Log this upload for /find, /leaderboard, and the weekly recap.
        _log_upload(display_title, vj_credit, message.chat_id, announcement_message_id)
    except Exception as e:
        logger.error("Failed to post announcement/repost file: %s", e)


# ---------------------------------------------------------------------------
# Reaction tracking — powers the "most reacted movie" stat in the weekly
# recap. Best-effort: if this specific Telegram feature isn't available in
# the installed library version, it's skipped safely (see MessageReactionHandler
# import above).
# ---------------------------------------------------------------------------

async def track_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    mr = update.message_reaction
    if not mr:
        return
    key = str(mr.message_id)
    delta = len(mr.new_reaction) - len(mr.old_reaction)
    counts = _stats.setdefault("reaction_counts", {})
    counts[key] = max(0, counts.get(key, 0) + delta)
    _save_stats()


# ---------------------------------------------------------------------------
# Welcome message for new members
# ---------------------------------------------------------------------------

async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not msg.new_chat_members:
        return
    for member in msg.new_chat_members:
        if member.is_bot:
            continue
        name = member.full_name or "there"
        text = (
            f"👋 Welcome {name} to TRANSLATED MOVIES™️!\n\n"
            "🍿 New TRANSLATED movies & shows get uploaded regularly across our topics.\n\n"
            "📌 A few tips:\n"
            "• /find <title> — check if we've already uploaded something\n"
            "• /request <title> — ask us to add a movie you want\n\n"
            "Enjoy! 🎬"
        )
        try:
            await msg.reply_text(text)
        except Exception as e:
            logger.warning("Could not send welcome message: %s", e)


# ---------------------------------------------------------------------------
# /find <title> — search the upload log
# ---------------------------------------------------------------------------

async def find_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip().lower() if context.args else ""
    if not query:
        await update.message.reply_text("Usage: /find <movie title>")
        return

    matches = [
        entry for entry in _stats.get("upload_log", [])
        if query in (entry.get("title") or "").lower()
    ]
    # Most recent first, deduped by title
    seen_titles = set()
    unique_matches = []
    for entry in reversed(matches):
        t = entry.get("title")
        if t and t not in seen_titles:
            seen_titles.add(t)
            unique_matches.append(entry)

    if not unique_matches:
        await update.message.reply_text(
            f"🔍 No match found for \"{query}\".\n\nWant it added? Try /request {query}"
        )
        return

    lines = [f"🔍 Found {len(unique_matches)} match(es) for \"{query}\":\n"]
    for entry in unique_matches[:10]:
        date_str = entry.get("ts", "")[:10]
        lines.append(f"🎬 {entry.get('title')} — uploaded {date_str}")
    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# /request <title> — forward a request to the admin's DMs
# ---------------------------------------------------------------------------

ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")


async def _forward_request(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str) -> None:
    requester = update.effective_user
    requester_name = requester.full_name if requester else "Someone"

    await update.effective_message.reply_text(f"📥 Got it! Your request for \"{query}\" has been sent to the admin.")

    if ADMIN_USER_ID:
        try:
            await context.bot.send_message(
                chat_id=int(ADMIN_USER_ID),
                text=f"🎬 New movie request from {requester_name}:\n\n\"{query}\"",
            )
        except Exception as e:
            logger.warning("Could not DM admin with request (have they started a chat with the bot?): %s", e)
    else:
        logger.warning("ADMIN_USER_ID not set — request from %s for %r was not forwarded.", requester_name, query)


async def request_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        await update.message.reply_text("Usage: /request <movie title>")
        return
    await _forward_request(update, context, query)


async def auto_request_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Any plain (non-command) text message sent in the General Updates
    topic is treated as a movie request automatically — no /request
    needed. Note: this means casual chat in that topic also gets
    forwarded as a "request"."""
    msg = update.effective_message
    if not msg or not msg.text:
        return
    if not UPDATES_CHAT_ID or str(msg.chat_id) != str(UPDATES_CHAT_ID):
        return
    if msg.message_thread_id != UPDATES_TOPIC_ID:
        return
    query = msg.text.strip()
    if not query:
        return
    await _forward_request(update, context, query)


# ---------------------------------------------------------------------------
# /leaderboard — top VJs by upload count
# ---------------------------------------------------------------------------

def _vj_counts(days: int | None = None) -> list[tuple[str, int]]:
    import datetime

    cutoff = datetime.datetime.now() - datetime.timedelta(days=days) if days else None
    counts: dict[str, int] = {}
    for entry in _stats.get("upload_log", []):
        vj = entry.get("vj")
        if not vj:
            continue
        if cutoff:
            try:
                if datetime.datetime.fromisoformat(entry["ts"]) < cutoff:
                    continue
            except Exception:
                pass
        counts[vj] = counts.get(vj, 0) + 1
    return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)


async def leaderboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ranked = _vj_counts()
    if not ranked:
        await update.message.reply_text("No VJ-credited uploads logged yet.")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 VJ Leaderboard (all-time)\n"]
    for i, (vj, count) in enumerate(ranked[:10]):
        prefix = medals[i] if i < len(medals) else f"{i + 1}."
        lines.append(f"{prefix} {vj} — {count} upload{'s' if count != 1 else ''}")
    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Weekly recap — total uploads, top VJ, most-reacted movie
# ---------------------------------------------------------------------------

async def post_weekly_recap(context: ContextTypes.DEFAULT_TYPE, reschedule: bool = True) -> None:
    import datetime

    if not UPDATES_CHAT_ID:
        logger.warning("UPDATES_CHAT_ID not set — skipping weekly recap.")
        return

    cutoff = datetime.datetime.now() - datetime.timedelta(days=7)
    week_entries = []
    for entry in _stats.get("upload_log", []):
        try:
            if datetime.datetime.fromisoformat(entry["ts"]) >= cutoff:
                week_entries.append(entry)
        except Exception:
            continue

    total = len(week_entries)
    top_vj_list = _vj_counts(days=7)
    top_vj = top_vj_list[0] if top_vj_list else None

    most_reacted_title, most_reacted_count = None, 0
    reaction_counts = _stats.get("reaction_counts", {})
    for entry in week_entries:
        mid = entry.get("message_id")
        if mid is None:
            continue
        count = reaction_counts.get(str(mid), 0)
        if count > most_reacted_count:
            most_reacted_count = count
            most_reacted_title = entry.get("title")

    lines = ["📊 Weekly Recap — TRANSLATED MOVIES™️\n"]
    lines.append(f"🎬 {total} movie{'s' if total != 1 else ''} uploaded this week")
    if top_vj:
        lines.append(f"🏆 Top VJ: {top_vj[0]} ({top_vj[1]} uploads)")
    if most_reacted_title and most_reacted_count > 0:
        lines.append(f"🔥 Most reacted: {most_reacted_title} ({most_reacted_count} reactions)")
    lines.append("\nThanks for being part of the group! More coming next week 🍿")

    try:
        await context.bot.send_message(
            chat_id=UPDATES_CHAT_ID,
            text="\n".join(lines),
            message_thread_id=UPDATES_TOPIC_ID,
        )
    except Exception as e:
        logger.error("Failed to post weekly recap: %s", e)

    if reschedule:
        app = context.application
        app.job_queue.run_once(post_weekly_recap, when=7 * 24 * 3600)


async def testrecap_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Posting the weekly recap now...")
    await post_weekly_recap(context, reschedule=False)
    await update.message.reply_text("Done — check the Updates topic.")


# ---------------------------------------------------------------------------
# Milestone celebrations — checks member count periodically
# ---------------------------------------------------------------------------

MILESTONE_STEP = 100
MILESTONE_CHECK_INTERVAL = 6 * 3600  # every 6 hours


async def check_milestone(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = UPDATES_CHAT_ID
    if not chat_id:
        return
    try:
        count = await context.bot.get_chat_member_count(chat_id)
    except Exception as e:
        logger.warning("Could not fetch member count for milestone check: %s", e)
        return

    last = _stats.get("last_milestone", 0)
    next_milestone = ((last // MILESTONE_STEP) + 1) * MILESTONE_STEP

    if count >= next_milestone:
        _stats["last_milestone"] = next_milestone
        _save_stats()
        try:
            gif_path = os.path.join(GIF_DIR, "intro.gif")
            if os.path.isfile(gif_path):
                with open(gif_path, "rb") as f:
                    await context.bot.send_animation(
                        chat_id=chat_id, animation=f, message_thread_id=UPDATES_TOPIC_ID,
                    )
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🎉🎊 We just hit {next_milestone} members! 🎊🎉\n\nThanks for being part of TRANSLATED MOVIES™️ — here's to many more! 🍿🔥",
                message_thread_id=UPDATES_TOPIC_ID,
            )
        except Exception as e:
            logger.error("Failed to post milestone celebration: %s", e)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Movie Announcer Bot is running.\n"
        "I'll automatically post a cover + summary whenever a video is uploaded to this group."
    )


async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("pong ✅")


async def topicid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send /topicid inside the topic you want announcements to post to —
    replies with the exact chat_id/thread_id to put in the env vars."""
    msg = update.effective_message
    await msg.reply_text(
        f"chat_id: {msg.chat_id}\n"
        f"message_thread_id: {msg.message_thread_id}\n\n"
        f"Put these in Render as UPDATES_CHAT_ID and UPDATES_TOPIC_ID."
    )


async def testannounce_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually fire the daily announcements right now, for testing —
    doesn't wait for or affect the random daily schedule."""
    await update.message.reply_text("Posting today's picks now, one moment...")
    await post_daily_announcements(context, reschedule=False)
    await update.message.reply_text("Done — check the Updates topic.")


async def testpoll_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually fire an engagement poll right now, for testing."""
    await update.message.reply_text("Posting a poll now, one moment...")
    await post_engagement_poll(context, reschedule=False)
    await update.message.reply_text("Done — check the Updates topic.")


# ---------------------------------------------------------------------------
# Daily movie/show announcements (theaters, streaming, trending + trailers)
# ---------------------------------------------------------------------------

UPDATES_CHAT_ID = os.getenv("UPDATES_CHAT_ID") or GROUP_CHAT_ID
UPDATES_TOPIC_ID = os.getenv("UPDATES_TOPIC_ID")
UPDATES_TOPIC_ID = int(UPDATES_TOPIC_ID) if UPDATES_TOPIC_ID else None
ANNOUNCEMENTS_PER_DAY = 5
# Random posting window each day (24h clock, local to the server) — avoids
# posting in the middle of the night. Adjust if you'd rather it be fully
# random across all 24 hours.
RANDOM_WINDOW_START_HOUR = 8
RANDOM_WINDOW_END_HOUR = 23

TMDB_TRENDING_URL = "https://api.themoviedb.org/3/trending/movie/day"
TMDB_NOW_PLAYING_URL = "https://api.themoviedb.org/3/movie/now_playing"
TMDB_DISCOVER_URL = "https://api.themoviedb.org/3/discover/movie"
TMDB_VIDEOS_URL = "https://api.themoviedb.org/3/movie/{id}/videos"


def _tmdb_get(url: str, **params) -> list[dict]:
    if not TMDB_API_KEY:
        return []
    params["api_key"] = TMDB_API_KEY
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json().get("results", [])
    except requests.RequestException as e:
        logger.warning("TMDB request failed (%s): %s", url, e)
        return []


def fetch_daily_picks() -> list[dict]:
    """Pull a diverse mix: trending, in theaters, and new-to-streaming,
    deduped by movie ID, capped at ANNOUNCEMENTS_PER_DAY."""
    trending = _tmdb_get(TMDB_TRENDING_URL)
    theaters = _tmdb_get(TMDB_NOW_PLAYING_URL, region="US")
    streaming = _tmdb_get(
        TMDB_DISCOVER_URL, region="US", with_release_type=4,
        sort_by="release_date.desc",
        **{"release_date.lte": __import__("datetime").date.today().isoformat()},
    )

    picks, seen_ids = [], set()
    for pool in (trending, theaters, streaming):
        for movie in pool:
            if movie["id"] in seen_ids:
                continue
            seen_ids.add(movie["id"])
            picks.append(movie)
            if len(picks) >= ANNOUNCEMENTS_PER_DAY:
                return picks
    return picks


def fetch_trailer_url(movie_id: int) -> str | None:
    videos = _tmdb_get(TMDB_VIDEOS_URL.format(id=movie_id))
    for v in videos:
        if v.get("site") == "YouTube" and v.get("type") == "Trailer":
            return f"https://www.youtube.com/watch?v={v['key']}"
    for v in videos:  # fall back to a teaser if no full trailer exists
        if v.get("site") == "YouTube" and v.get("type") == "Teaser":
            return f"https://www.youtube.com/watch?v={v['key']}"
    return None


async def post_daily_announcements(context: ContextTypes.DEFAULT_TYPE, reschedule: bool = True) -> None:
    if not UPDATES_CHAT_ID:
        logger.warning("UPDATES_CHAT_ID not set — skipping daily announcements.")
        return

    picks = fetch_daily_picks()
    logger.info("Daily announcements: posting %d picks", len(picks))

    if not picks:
        # TMDB is likely down or returned nothing — keep the topic active
        # with a poll instead of posting nothing at all.
        logger.warning("No picks found (TMDB may be down) — posting a fallback poll instead.")
        await post_engagement_poll(context, reschedule=False, force_static=True)
        if reschedule:
            _schedule_next_daily_run(context.application)
        return

    for movie in picks:
        meta = dict(movie)
        meta["media_type"] = "movie"
        caption, poster_url = format_announcement(meta)
        trailer_url = fetch_trailer_url(movie["id"])

        try:
            if poster_url:
                await context.bot.send_photo(
                    chat_id=UPDATES_CHAT_ID,
                    photo=poster_url,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN,
                    message_thread_id=UPDATES_TOPIC_ID,
                )
            else:
                await context.bot.send_message(
                    chat_id=UPDATES_CHAT_ID,
                    text=caption,
                    parse_mode=ParseMode.MARKDOWN,
                    message_thread_id=UPDATES_TOPIC_ID,
                )
            if trailer_url:
                await context.bot.send_message(
                    chat_id=UPDATES_CHAT_ID,
                    text=f"🎞️ Trailer: {trailer_url}",
                    message_thread_id=UPDATES_TOPIC_ID,
                )
        except Exception as e:
            logger.error("Failed to post daily announcement for %s: %s", movie.get("title"), e)

    if reschedule:
        _schedule_next_daily_run(context.application)


# ---------------------------------------------------------------------------
# Engagement polls — keep the topic active on quiet days, and as a fallback
# whenever TMDB doesn't return any daily picks (e.g. the API is down).
# ---------------------------------------------------------------------------

STATIC_POLL_QUESTIONS = [
    ("🎬 What are you in the mood for?", ["🔫 Action", "😂 Comedy", "👻 Horror", "❤️ Romance", "🔪 Thriller", "🚀 Sci-Fi"]),
    ("🍿 Which genre should we upload more of?", ["🔫 Action", "🎭 Drama", "👻 Horror", "🎨 Animation", "🕵️ Crime"]),
    ("⭐ How would you rate this week's uploads?", ["🔥 Excellent", "👍 Good", "😐 Okay", "👎 Not great"]),
    ("🎥 Movie night pick — what's the vibe?", ["😱 Something scary", "😂 Something funny", "😢 Something emotional", "💥 Something action-packed"]),
    ("🌍 Which region's movies do you want more of?", ["🎬 Hollywood", "🎞️ Nollywood", "🎪 Bollywood", "🇰🇷 Korean", "🌍 Local/African"]),
]

GIF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gifs")
POLL_INTRO_GIFS = ["intro.gif"]


def _fetch_trending_poll_options() -> list[str] | None:
    """Try to build a 'vote for what we upload next' poll using real
    trending titles. Returns None if TMDB isn't reachable right now."""
    trending = _tmdb_get(TMDB_TRENDING_URL)
    titles = [m.get("title") for m in trending if m.get("title")][:5]
    return titles if len(titles) >= 3 else None


def _fetch_trivia_question() -> tuple[str, list[str], int] | None:
    """Build a quiz-mode trivia question from a real trending movie:
    'What year was X released?' with 3 wrong-year distractors. Returns
    (question, options, correct_option_id), or None if TMDB is unreachable."""
    trending = _tmdb_get(TMDB_TRENDING_URL)
    candidates = [m for m in trending if m.get("title") and m.get("release_date")]
    if not candidates:
        return None

    movie = random.choice(candidates)
    correct_year = int(movie["release_date"][:4])

    distractors = set()
    while len(distractors) < 3:
        offset = random.choice([-3, -2, -1, 1, 2, 3])
        year = correct_year + offset
        if year != correct_year:
            distractors.add(year)

    years = list(distractors) + [correct_year]
    random.shuffle(years)
    correct_option_id = years.index(correct_year)
    options = [str(y) for y in years]
    question = f"🧠 What year was \"{movie['title']}\" released?"
    return question, options, correct_option_id


async def _send_poll_lead_in(context: ContextTypes.DEFAULT_TYPE) -> None:
    """A little 'drumroll' before the poll — either a themed GIF or a fun
    sticker, picked at random, to make the poll feel more alive."""
    try:
        if random.random() < 0.5 and POLL_INTRO_GIFS:
            gif_path = os.path.join(GIF_DIR, random.choice(POLL_INTRO_GIFS))
            if os.path.isfile(gif_path):
                with open(gif_path, "rb") as f:
                    await context.bot.send_animation(
                        chat_id=UPDATES_CHAT_ID, animation=f, message_thread_id=UPDATES_TOPIC_ID,
                    )
                return
        sticker_path = _random_sticker_path()
        if sticker_path:
            with open(sticker_path, "rb") as f:
                await context.bot.send_sticker(
                    chat_id=UPDATES_CHAT_ID, sticker=f, message_thread_id=UPDATES_TOPIC_ID,
                )
    except Exception as e:
        logger.warning("Could not send poll lead-in: %s", e)


async def post_engagement_poll(context: ContextTypes.DEFAULT_TYPE, reschedule: bool = True, force_static: bool = False) -> None:
    if not UPDATES_CHAT_ID:
        logger.warning("UPDATES_CHAT_ID not set — skipping engagement poll.")
        return

    await _send_poll_lead_in(context)

    # Pick a poll style: trivia quiz, "vote what's next" (dynamic), or a
    # static preference poll — trivia and dynamic both need TMDB up.
    roll = random.random() if not force_static else 1.0
    is_quiz = False
    correct_option_id = None
    question, options = None, None

    if roll < 0.3:
        trivia = _fetch_trivia_question()
        if trivia:
            question, options, correct_option_id = trivia
            is_quiz = True
    elif roll < 0.55:
        trending_titles = _fetch_trending_poll_options()
        if trending_titles:
            question, options = "🗳️ Which of these should we upload next?", trending_titles

    if not question:
        question, options = random.choice(STATIC_POLL_QUESTIONS)

    # Countdown timer on some polls (Telegram shows a live ticking clock)
    open_period = random.choice([None, None, 600, 1800])  # ~50% chance of a timer

    try:
        poll_kwargs = dict(
            chat_id=UPDATES_CHAT_ID,
            question=question,
            options=options,
            is_anonymous=True,
            message_thread_id=UPDATES_TOPIC_ID,
        )
        if open_period:
            poll_kwargs["open_period"] = open_period
        if is_quiz:
            poll_kwargs["type"] = "quiz"
            poll_kwargs["correct_option_id"] = correct_option_id

        sent = await context.bot.send_poll(**poll_kwargs)
        logger.info("Posted engagement poll (%s): %r", "quiz" if is_quiz else "regular", question)

        # A little auto-reaction to kick things off
        try:
            from telegram import ReactionTypeEmoji
            await context.bot.set_message_reaction(
                chat_id=UPDATES_CHAT_ID,
                message_id=sent.message_id,
                reaction=[ReactionTypeEmoji(emoji=random.choice(["🔥", "🎬", "👀", "🍿"]))],
            )
        except Exception as e:
            logger.warning("Could not auto-react to poll: %s", e)
    except Exception as e:
        logger.error("Failed to post engagement poll: %s", e)

    if reschedule:
        _schedule_next_poll_run(context.application)


def _schedule_next_poll_run(app: Application) -> None:
    """Polls run independently of the daily announcements — roughly every
    1-2 days, at a random time, so the topic stays active between digests."""
    import datetime

    now = datetime.datetime.now()
    days_ahead = random.randint(1, 2)
    target_date = now.date() + datetime.timedelta(days=days_ahead)
    rand_hour = random.randint(RANDOM_WINDOW_START_HOUR, RANDOM_WINDOW_END_HOUR - 1)
    rand_minute = random.randint(0, 59)
    run_at = datetime.datetime.combine(target_date, datetime.time(rand_hour, rand_minute))
    delay_seconds = (run_at - now).total_seconds()

    app.job_queue.run_once(post_engagement_poll, when=delay_seconds)
    logger.info("Next engagement poll scheduled for %s (in %.0f minutes)", run_at, delay_seconds / 60)


def _schedule_next_daily_run(app: Application) -> None:
    """Pick a random time (within the configured window) for tomorrow and
    schedule the job to fire once then — re-scheduling itself again each
    time it runs, so the posting time varies day to day."""
    import datetime

    now = datetime.datetime.now()
    tomorrow = now.date() + datetime.timedelta(days=1)
    rand_hour = random.randint(RANDOM_WINDOW_START_HOUR, RANDOM_WINDOW_END_HOUR - 1)
    rand_minute = random.randint(0, 59)
    run_at = datetime.datetime.combine(tomorrow, datetime.time(rand_hour, rand_minute))
    delay_seconds = (run_at - now).total_seconds()

    app.job_queue.run_once(post_daily_announcements, when=delay_seconds)
    logger.info("Next daily announcement scheduled for %s (in %.0f minutes)", run_at, delay_seconds / 60)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def _seconds_until_first_run() -> float:
    """On startup, pick a random time within today's window if we haven't
    passed it yet, otherwise tomorrow's window."""
    import datetime

    now = datetime.datetime.now()
    rand_hour = random.randint(RANDOM_WINDOW_START_HOUR, RANDOM_WINDOW_END_HOUR - 1)
    rand_minute = random.randint(0, 59)
    run_at = now.replace(hour=rand_hour, minute=rand_minute, second=0, microsecond=0)
    if run_at <= now:
        run_at += datetime.timedelta(days=1)
    return (run_at - now).total_seconds()


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
    app.add_handler(CommandHandler("topicid", topicid_cmd))
    app.add_handler(CommandHandler("testannounce", testannounce_cmd))
    app.add_handler(CommandHandler("testpoll", testpoll_cmd))
    app.add_handler(CommandHandler("testrecap", testrecap_cmd))
    app.add_handler(CommandHandler("find", find_cmd))
    app.add_handler(CommandHandler("request", request_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, auto_request_handler))
    app.add_handler(CommandHandler("leaderboard", leaderboard_cmd))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    if MessageReactionHandler:
        app.add_handler(MessageReactionHandler(track_reaction))
    else:
        logger.warning("MessageReactionHandler not available in this PTB version — reaction tracking disabled.")
    app.add_handler(
        MessageHandler((filters.VIDEO | filters.Document.ALL) & ~filters.COMMAND, handle_upload)
    )

    # Kick off the first daily announcement at a random time today (if
    # we're still before the window's end) or tomorrow otherwise.
    app.job_queue.run_once(post_daily_announcements, when=_seconds_until_first_run())
    # Engagement polls run on their own independent schedule.
    app.job_queue.run_once(post_engagement_poll, when=_seconds_until_first_run() + 3600)
    # Weekly recap, 7 days from now.
    app.job_queue.run_once(post_weekly_recap, when=7 * 24 * 3600)
    # Milestone check, every 6 hours.
    app.job_queue.run_repeating(check_milestone, interval=MILESTONE_CHECK_INTERVAL, first=60)

    logger.info("Bot started. Listening for uploads...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Bot crashed with an unhandled exception:")
        raise
