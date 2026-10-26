# Integration and QA report

## Included in this build
- Primary `tg-ytdlp-bot` application and its existing user-facing features.
- Environment-backed `CONFIG/config.py` wrapper; the source template is retained in `CONFIG/_config.py`.
- Optional TeraBox adapter with strict hostname allowlisting, network timeouts, file-size cap and isolated temporary files.
- Router hook that handles supported TeraBox hostnames before generic yt-dlp/gallery-dl routing.
- Tests for TeraBox hostname validation and extractor-response parsing.
- Python syntax fixes for the supplied Japanese and Chinese language modules.

## Deliberately not copied
- The second project's FastAPI/PostgreSQL/RabbitMQ stack: it duplicates the primary app's download workflow and its supplied API routes did not have adequate authentication.
- Instagram account/session-management commands: public URL downloads are already covered by the primary extractor when supported; private account/session features are outside the requested downloader scope.
- Original TeraBox source code: no license file was present in that archive. The adapter was independently written but uses a third-party extraction endpoint; it is disabled by default.

## Static checks performed
- `python -m compileall -q .`
- `pytest -q tests/test_terabox_adapter.py` (requires pytest installed)
- Manual review of dependency lists, configuration placeholders, Docker exposure and source license files.

## Not yet verified
- Telegram login with the user's bot token/API credentials.
- Force-join behaviour against the user's actual channel and permissions.
- Live downloads for each platform, because this requires live network access and representative URLs.
- Production deployment and load behaviour on the target VPS.

A successful compile is not a runtime or end-to-end test. Treat this build as a cleaned integration candidate, not as a guarantee that every platform always works.
