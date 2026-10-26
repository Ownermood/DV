# Integration sources

The three uploaded repositories are preserved under `SOURCE_REPOSITORIES/`, separated to avoid Python module and dependency collisions. The main application is at the project root.

- `SOURCE_REPOSITORIES/yt-dlp-bot/`: separate API/worker architecture; reference only until API auth and network exposure are hardened.
- `SOURCE_REPOSITORIES/instagram-bot/`: separate Instagram-focused Telegram bot; reference only, not auto-started.
- `SOURCE_REPOSITORIES/terabox-downloader-bot/`: original TeraBox source; no LICENSE was present in the supplied archive.

Do not run multiple bot implementations with the same `BOT_TOKEN` simultaneously.
