#!/bin/bash
set -e
# Sites change often: pull the newest yt-dlp / gallery-dl on every start (failure is not fatal)
pip install --no-cache-dir -q -U --pre "yt-dlp[default,curl-cffi]" gallery-dl bgutil-ytdlp-pot-provider \
    || echo "engine update skipped"
exec python -u magic.py   # -u: print() lines reach `docker compose logs` at once (not 8 KB later)
