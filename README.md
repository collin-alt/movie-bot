# Movie Announcer Bot

Watches your Telegram group for new video uploads, looks the title up on TMDB,
and posts a cover image + summary announcement automatically.

## Setup

### 1. Create the bot
1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. `/newbot` → follow the prompts → copy the **bot token**
3. `/mybots` → select your bot → **Bot Settings** → **Group Privacy** → **Turn off**
   (this is required so the bot can see files uploaded by *other* members, not just commands sent to it)

### 2. Get a TMDB API key
1. Create a free account at https://www.themoviedb.org
2. Go to Settings → API → request an API key (choose "Developer", it's free)
3. Copy the **API Key (v3 auth)**

### 3. Add the bot to your group
- Add your bot to the Telegram group like any member
- Make it an **admin** (needed to reliably read/send messages in some group types)

### 4. Configure
```bash
cp .env.example .env
# then edit .env and paste in BOT_TOKEN and TMDB_API_KEY
```

### 5. Install & run
```bash
pip install -r requirements.txt
python bot.py
```

You should see `Bot started. Listening for uploads...` in the console.
Upload a video file to the group (e.g. `Inception.2010.1080p.BluRay.x264.mkv`) —
the bot will reply with the poster, title, year, rating, and synopsis.

## How title detection works
The bot strips common release-tag junk (resolution, codec, source, release
group names) and pulls out a year if present, then searches TMDB with what's
left. This works well for typically-formatted scene-release filenames. If a
file's name is too generic (e.g. `video1.mp4`) it won't be able to find a match
and will silently skip it — check the console logs to see what title it
extracted.

## Running it 24/7
This script uses long-polling and needs to stay running. Options:
- Run it on a small VPS or Raspberry Pi with `systemd` or `pm2`
- Use `screen`/`tmux` on any always-on machine
- Deploy to a free-tier host like Railway, Render, or Fly.io

## Customizing
- **Only react in one group**: set `GROUP_CHAT_ID` in `.env`
- **Change the message format**: edit `format_announcement()` in `bot.py`
- **Add more junk patterns**: extend the `JUNK_PATTERNS` list in `bot.py`
- **TV shows vs movies**: TMDB's `/search/multi` already covers both; the bot
  labels them automatically (🎬 Movie / 📺 TV Show)

## Notes on content
This bot only reads filenames/captions and fetches public poster art and
metadata from TMDB — it never touches or re-hosts the video files themselves.
Make sure whatever you're sharing in the group complies with copyright law in
your jurisdiction.
