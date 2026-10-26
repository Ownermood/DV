# All Downloader — merged VPS build

This package uses `tg-ytdlp-bot` as the primary Telegram application because it already contains the broadest downloader workflow, interactive quality selection, progress handling, force-join checks, usage tracking and dashboard features. It does **not** blindly combine all four projects' dependency trees: doing so would create conflicting Telegram clients, duplicate download engines, multiple databases and a larger attack surface.

## Integration decisions

- **Primary application:** `tg-ytdlp-bot-main` source, retaining its upstream MIT license and notices.
- **Generic platform support:** the primary yt-dlp/gallery-dl engines are used for sites they currently support, including public Instagram posts/Reels, YouTube, Pinterest and Facebook. Site support changes over time; no universal guarantee is possible.
- **TeraBox:** an independently written, isolated adapter lives in `ADAPTERS/terabox.py`. It uses an optional third-party extraction endpoint and is **disabled by default**. The endpoint may stop working or change its terms/response format. Enable only after reviewing the endpoint and applicable terms. The original TeraBox ZIP had no LICENSE file, so its code was not copied.
- **Instagram manager:** account/session, follower-list, saved-post and story-management functions were not imported. The main engine already handles supported public URLs; importing an old Pyrogram/Instaloader account manager would add dependency conflicts and sensitive session handling.
- **Second yt-dlp project:** its FastAPI/PostgreSQL/RabbitMQ stack was not copied wholesale. Its API lacked adequate authentication in the supplied archive and exposed internal service ports/default credentials. The primary app's downloader flow is retained instead.

## Quick start (Ubuntu VPS)

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg git
cd All-Downloader
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip wheel
pip install -r requirements.txt
cp .env.example .env
nano .env
```

Set `BOT_TOKEN`, `API_ID`, `API_HASH`, `ADMIN_IDS`, `SUBSCRIBE_CHANNEL`, `SUBSCRIBE_CHANNEL_URL`, and a strong `DASHBOARD_PASSWORD` of at least 16 characters. Use comma-separated Telegram numeric IDs for `ADMIN_IDS`. Set `SUBSCRIBE_CHANNEL` to the numeric channel ID and `SUBSCRIBE_CHANNEL_URL` to its public/invite link. The startup wrapper refuses to launch with placeholder credentials or a missing force-join channel. Add the bot as an administrator in the required channel. If force join is not required, review the existing channel-guard configuration before disabling it.

Then start with Docker Compose (recommended; it starts the bot and dashboard together):

```bash
docker compose up -d --build
docker compose logs -f app
```

The dashboard port is bound to `127.0.0.1:5555` on the VPS by default, not directly to the public interface. Use an authenticated HTTPS reverse proxy if you need remote dashboard access. For a bot-only manual run, use `python magic.py`; the dashboard then needs a separate Uvicorn process.

Do not put real tokens, cookies, session strings, Firebase credentials or passwords in source control. `.env` is gitignored. Never upload a real `.env` file when sharing bug reports.

## TeraBox adapter

By default `TERABOX_ENABLED=false`. If you have reviewed the external extraction endpoint and choose to enable it, set `TERABOX_ENABLED=true`. Set conservative `TERABOX_MAX_BYTES`, `TERABOX_CONNECT_TIMEOUT` and `TERABOX_READ_TIMEOUT` values. This is not an official TeraBox integration and can fail whenever the external endpoint or TeraBox changes. It supports public share links only; it does not bypass account permissions, access controls or DRM.

## Important deployment hardening

1. Run as a non-root Linux user where possible.
2. Keep SSH, database, cache and dashboard ports private. Do not expose internal services to the public internet.
3. Use a firewall and reverse proxy with HTTPS if exposing the dashboard; change/remove default credentials before enabling it.
4. Restrict disk usage and concurrent downloads. Monitor `df -h`, `free -h` and logs.
5. Do not use cookies/session files unless you own the account and are authorized to access the content. Protect them as credentials.
6. Use media only when you have permission and comply with each platform's terms and applicable law.

## Verification status

The build has been statically checked for Python syntax and includes adapter URL-validation tests. This is **not** a claim of successful live Telegram login or end-to-end downloads: those require your own credentials, channel permissions, VPS environment and real public test links. Platform extractors are inherently changeable.

## Source licenses

The primary project is distributed under its included MIT `LICENSE`. The supplied second yt-dlp project has a BSD 3-Clause license, but its source code was not copied into this build. The supplied Instagram project has an MIT license, but its account-management source was not copied. The supplied TeraBox project archive had no license file and its source was not copied; the adapter in this build was independently written. Keep the primary project's license and copyright notices intact.
