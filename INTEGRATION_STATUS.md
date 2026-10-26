# Complete repository merge — integration status

This distribution contains the repaired `tg-ytdlp-bot`-based application at the project root and the full source trees of the other three uploaded repositories under `INTEGRATIONS/SOURCE_REPOSITORIES/`.

## What is active in the main bot
- Main Telegram bot, commands, format/quality selection, thumbnails, progress, force-join and dashboard/statistics from the primary `tg-ytdlp-bot` project.
- URL detection and the isolated optional TeraBox adapter under `ADAPTERS/terabox.py`. TeraBox support is disabled by default because it depends on a third-party extraction service.
- yt-dlp's existing site extractors cover many public supported sites, including Instagram, Facebook, Pinterest and YouTube where the current extractor supports the URL.

## What is included but not auto-started
- `yt-dlp-bot`: complete source snapshot of the separate FastAPI + worker + PostgreSQL + RabbitMQ architecture. It is kept isolated because it has a different runtime architecture and its original API endpoints and exposed services require security hardening before use.
- `instagram-bot`: complete source snapshot of the separate Pyrogram/Instaloader bot. It is not started automatically because it is a separate bot implementation with overlapping functionality and login/session features. Public Instagram URL downloading should use the main bot's yt-dlp path first.
- `terabox-downloader-bot`: complete source snapshot of the original TeraBox bot. It is retained as a separate reference implementation; its ZIP had no LICENSE file, so its code should not be copied into production without permission. The main bot has a separate adapter.

## Why these projects are not all imported into one Python process
They use different Telegram clients, task models, configuration systems, dependencies and storage approaches. Copying all files into one namespace or running multiple bots with the same token would create import conflicts, duplicate handlers and runtime failures. This package merges the source distribution while keeping incompatible subprojects isolated. A single production runtime still requires staged adapter integration and live tests.

## Security hygiene
- Actual `.env` files are not included.
- Cookie/session files are replaced with empty local placeholders/instructions. Add only cookies you are authorized to use on your server; never commit them.
- The reference yt-dlp service env files are stored as `.example` templates with insecure sample passwords replaced.
- Do not expose dashboard/API/database/queue ports publicly without authentication and firewall restrictions.

## Verification status
Archive integrity, Python syntax compilation, tests, and configuration checks should be rerun after extraction. This package has not been proven end-to-end with real Telegram credentials or live downloads from every platform. Platform support depends on upstream extractors and each site's current restrictions.
