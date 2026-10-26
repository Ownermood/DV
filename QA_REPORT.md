# All Downloader - QA report

This report records local static checks for the packaged source. It is not a claim
that live Telegram login or every external platform was tested.

## Checks

- ZIP archive integrity: checked with Python `zipfile.testzip()`.
- Python syntax: `python -m compileall -q .`.
- Unit tests: `python -m pytest -q`.
- TeraBox adapter unit tests cover supported host recognition, lookalike URL rejection,
  and extraction response parsing.
- Stats collector tests cover fixture parsing, live event updates, active-user tracking,
  and top-user statistics.

## Required before production

1. Copy `.env.example` to `.env` and fill in real Telegram credentials, admin ID,
   a strong dashboard password, and force-join channel settings.
2. Keep `.env`, Telegram session files, cookies, and backups private; do not commit them.
3. Keep the dashboard bound to localhost or behind authenticated HTTPS access.
4. Review the TeraBox third-party endpoint terms and availability before enabling it.
5. Run live tests with your own bot, channels, VPS, and representative public URLs.

## Known limitations

- External platform behaviour can change; no downloader can promise every URL works.
- TeraBox adapter is disabled by default and depends on a third-party extraction endpoint.
- Syntax and unit tests do not verify Telegram authentication, platform availability,
  upload limits, load handling, or production deployment.
