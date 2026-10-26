FROM python:3.14.6-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.12 /uv /uvx /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH" \
    MVP_HOSTED=true \
    MVP_MODE=azure \
    MVP_RUNTIME_PROFILE=azd \
    MVP_AZURE_CREDENTIAL_MODE=managed_identity \
    MVP_RUNTIME_DIR=/home/video-checklist

RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --python /usr/local/bin/python \
    && uv cache clean

COPY video_checklist/ ./video_checklist/
COPY data/manifest.json ./data/manifest.json
COPY LICENSE ./LICENSE

COPY .build/source-manifest.json ./source-manifest.json
RUN python -c 'import json, re; from pathlib import Path; assert re.fullmatch(r"[0-9a-f]{40}", json.loads(Path("/app/source-manifest.json").read_text())["commit"]), "Invalid build source manifest"'
LABEL org.opencontainers.image.title="Video checklist Azure lab"

# A single ASGI worker stores private state on the persistent /home mount.
# Media, credentials and runtime state are never copied into the image.
EXPOSE 8000
CMD ["python", "-m", "video_checklist.hosted"]
