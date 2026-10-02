# The API and the worker are the same image, started with different commands.

# ---- the interface: a static export, built once, served by FastAPI ----------
FROM node:22-slim AS web
WORKDIR /web
# Dependencies first, so editing a component doesn't reinstall them.
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- the service --------------------------------------------------------------
FROM python:3.11-slim

# ffmpeg/ffprobe do every bit of the audio and video work. The Noto fonts are
# what keep burned-in Urdu, Hindi and CJK subtitles from rendering as blank
# boxes — a missing font doesn't error, it just draws nothing.
# libsndfile is what Kokoro writes its audio with.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fontconfig \
        fonts-dejavu-core \
        fonts-noto-core \
        fonts-noto-cjk \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Kokoro's model files (~340 MB) are not baked in: they are fetched once,
# checksummed, into the data volume (see the `models` service in compose), so
# the image stays small and a rebuild never downloads them again.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/app/data \
    KOKORO_MODEL=/app/data/models/kokoro/kokoro-v1.0.onnx \
    KOKORO_VOICES=/app/data/models/kokoro/voices-v1.0.bin

WORKDIR /app

# Dependencies first: application edits then don't invalidate this layer.
# Voices (Kokoro) and S3 are optional on a laptop but belong in the image: the
# open-source voice is the default, and a bucket is a setting away.
COPY requirements.txt requirements-postgres.txt requirements-voices.txt requirements-s3.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-postgres.txt \
        -r requirements-voices.txt -r requirements-s3.txt

COPY . .
# Without this the container would serve the old page: web/out is gitignored,
# so it only exists if it is built.
COPY --from=web /web/out /app/web/out

# Never run the pipeline as root — it executes ffmpeg on model-generated input.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/data \
    && chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# Compose overrides this for the worker service.
CMD ["python", "main.py", "serve", "--host", "0.0.0.0", "--port", "8000"]
