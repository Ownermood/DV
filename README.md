# All Downloader Bot

Telegram bot for **YouTube, TikTok, Snapchat, Pinterest and TeraBox**. Other sites are rejected.

## Setup
1. `cp .env.example .env` and fill the 4 required values (`BOT_TOKEN`, `API_ID`, `API_HASH`, `ADMIN_IDS`).
2. Run: `docker compose up -d --build` or `pip install -r requirements.txt && python magic.py`.

## Users
Send a link and the bot shows a menu:
- YouTube: pick a quality (sizes shown), or Best Quality
- Other platforms: Best Quality
- Every link: MP3 and Thumbnail

Best quality stays under `MAX_FILE_SIZE_GB` (default 4). If it's too big, the bot picks the next lower quality (1080p -> 720p).
Telegram bots upload max 2 GB per file, so bigger files arrive in parts.

Users only see `/start` (Language + Help).

## Admins
`/admin` (or the button on `/start`): stats, broadcast (pin, live progress, stop), delete broadcast,
users list, block/unblock, uncache URL, premium emoji on/off, start image.

Premium emoji show only if the bot owner has Telegram Premium. If Telegram rejects them, the bot switches to normal emoji by itself.
