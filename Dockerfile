# The API and the worker are the same image, started with different commands.
FROM python:3.11-slim

# ffmpeg/ffprobe do every bit of the audio and video work. The Noto fonts are
# what keep burned-in Urdu, Hindi and CJK subtitles from rendering as blank
# boxes — a missing font doesn't error, it just draws nothing.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fontconfig \
        fonts-dejavu-core \
        fonts-noto-core \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first: application edits then don't invalidate this layer.
COPY requirements.txt requirements-postgres.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-postgres.txt

COPY . .

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
