# Downloder — @Downloader_Villanbot

All bot text is **bold** (quotes too). The bot detects the platform from the link by itself.

Telegram bot for **YouTube, Instagram, Facebook, Snapchat, Pinterest and TeraBox**. Other sites are rejected.

## Setup
1. `cp .env.example .env` and fill the 4 required values (`BOT_TOKEN`, `API_ID`, `API_HASH`, `ADMIN_IDS`).
2. Run: `docker compose up -d --build` (recommended: includes ffmpeg, Deno and the YouTube PO-token provider,
   and updates yt-dlp / gallery-dl on every start).
   Without Docker: install `ffmpeg` and [Deno](https://deno.land), then `pip install -r requirements.txt && python magic.py`.

## YouTube "Sign in to confirm you're not a bot"
YouTube often blocks server IPs. Fix: admin panel → **YouTube Cookie** → send a `cookies.txt`
exported from a spare YouTube account. Every user without their own cookie then uses it.

## Instagram / Facebook / TeraBox
- Instagram blocks most server IPs without a login: Admin Panel → **Cookie Manager** → Instagram (spare accounts, up to 10).
- Facebook public videos work without anything; private ones need a Facebook cookie in the Cookie Manager.
- TeraBox: Admin Panel → **Cookie Manager** → TeraBox (cookies.txt with `ndus`, up to 10). The bot then reads
  the share itself; without it, it tries the external extraction service. TeraBox files up to MAX_FILE_SIZE_GB (4 GB); videos over 2 GB arrive in parts.

## Private links
Private / age-restricted / login-only posts: the user sends `/cookie` once and uploads a `cookies.txt`
exported from their own account (YouTube, Instagram, Facebook). After that the same link downloads.
TeraBox password-protected shares are not supported.

## Users
Send a link and the bot shows a menu:
- YouTube: pick a quality (sizes shown), or Best Quality
- Other platforms: Best Quality
- Every link: MP3 and Thumbnail

Best quality stays under `MAX_FILE_SIZE_GB` (default 4). If it's too big, the bot picks the next lower quality (1080p -> 720p).
Telegram bots upload max 2 GB per file, so bigger files arrive in parts.

Users only see `/start` and Help. The bot is English only.

## Admins
`/admin` (or the button on `/start`). Owners = `ADMIN_IDS` in `.env`.
- **Home:** today's new users, active users, downloads; members, downloads running, uptime
- **Bot Stats:** users (joined / active today·7d·30d), downloads (today·7d·30d·all, running), system (CPU, RAM, disk, yt-dlp, Python)
- **Users Stats:** totals, admins, blocked, new users, active / inactive
- **Broadcast:** send any message or album (or reply `/broadcast`), copied without the forward tag, premium emojis kept; Send or Send + Pin; live card with Stop
- **Delete Broadcast:** last broadcasts, confirm (48h note), live card; or reply `/delbc` to a broadcast
- **Admins:** list; owners add (`Name user_id`, premium emojis in the name) and remove admins; the new admin gets a welcome message
- **Users:** block / unblock, users list, uncache URL
- **Cookie Manager:** up to 10 cookies each for YouTube, Instagram, Facebook, TeraBox; add many files at once; ON/OFF, replace, delete, test; automatic rotation, DM when one dies or is about to expire
- **Settings:** start image, premium emoji on/off

Activity and download counts start from the update that added them.
