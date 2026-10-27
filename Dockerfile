FROM python:3.12-slim

ARG TZ=Asia/Kolkata
ENV TZ="$TZ"

# System dependencies:
# - git, ffmpeg, mediainfo, rsync (README: base deps + FFmpeg)
# - build-essential: compiles TgCrypto (no prebuilt wheel for Python 3.12)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ffmpeg \
    aria2 \
    mediainfo \
    rsync \
    fonts-noto-core \
    fonts-noto-color-emoji \
    fontconfig \
    libass9 \
    ca-certificates \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Deno: JS runtime yt-dlp needs for YouTube signature/n-challenges (2025.11+)
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Faster uploads: 8 parallel part uploads per connection (pyrogram's default is 4) x 3 connections
RUN python -c "import pyrogram, pathlib; p = pathlib.Path(pyrogram.__file__).parent / 'methods/advanced/save_file.py'; s = p.read_text(); n = s.replace('workers_count = 4 if is_big else 1', 'workers_count = 8 if is_big else 1'); p.write_text(n); print('pyrogram upload workers patched' if n != s else 'pyrogram upload patch NOT applied')"

COPY . .

# Copy and make the entrypoint executable
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

CMD ["/usr/local/bin/docker-entrypoint.sh"]
