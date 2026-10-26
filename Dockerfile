FROM python:3.14.6-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.12 /uv /uvx /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH" \
    MVP_HOSTED=true \
    MVP_MODE=fixture \
    MVP_AZURE_CREDENTIAL_MODE=managed_identity \
    MVP_RUNTIME_DIR=/home/mvp-labs/2026-10-15-content-understanding-video-checklist/app \
    MVP_AUTHORIZATION_PATH=/home/mvp-labs/2026-10-15-content-understanding-video-checklist/authorization.json \
    MVP_LEDGER_PATH=/home/mvp-labs/2026-10-15-content-understanding-video-checklist/ledger.sqlite3 \
    MVP_DEPLOYMENT_PATH=/home/mvp-labs/2026-10-15-content-understanding-video-checklist/deployment.json \
    MVP_SAMPLE_PATH=/home/mvp-labs/2026-10-15-content-understanding-video-checklist/media/base.mp4

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

ARG MVP_SOURCE_COMMIT
RUN python -c 'import json, os, re; from pathlib import Path; commit = os.environ.get("MVP_SOURCE_COMMIT", ""); assert re.fullmatch(r"[0-9a-f]{40}", commit), "MVP_SOURCE_COMMIT must be the full source Git SHA"; Path("/app/source-manifest.json").write_text(json.dumps({"commit": commit}) + "\n")'
LABEL org.opencontainers.image.title="MVP video checklist lab" \
    org.opencontainers.image.revision="${MVP_SOURCE_COMMIT}"

# hosted initializes private files on mounted /home, then starts one ASGI worker.
# Media, credentials and runtime state are never copied into the image.
EXPOSE 8000
CMD ["python", "-m", "video_checklist.hosted"]
