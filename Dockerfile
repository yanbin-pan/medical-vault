# The frontend is built here and served by the API process, so one image and one
# pod carry the whole application. On a four-node Raspberry Pi cluster that is
# worth more than the tidiness of a separate nginx deployment.

FROM node:22-alpine AS web
WORKDIR /build
COPY packages/web/package.json packages/web/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY packages/web/ ./
RUN npm run build


FROM python:3.11-slim AS runtime

# IMPORTANT: this must be an arm64 image. The cluster is Raspberry Pi 4s, and an
# amd64-only image fails with `exec format error`, which reads like an
# application crash rather than the architecture mismatch it is. The release
# workflow builds linux/arm64 explicitly.

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY packages/api/pyproject.toml ./packages/api/
# The JSON Schemas, the MANIFEST template and the analyte catalogue all live
# inside the package, so that an installed wheel carries everything a vault
# needs to describe itself.
COPY packages/api/medvault ./packages/api/medvault
COPY packages/api/scripts ./packages/api/scripts

RUN pip install --no-cache-dir ./packages/api

COPY --from=web /build/dist ./web

# Run as a non-root user. The vault holds medical records; a container escape
# should not also be a root shell.
RUN useradd --create-home --uid 10001 medvault \
    && mkdir -p /data/vault \
    && chown -R medvault:medvault /data /app
USER medvault

ENV MEDVAULT_VAULT_PATH=/data/vault \
    MEDVAULT_WEB_ROOT=/app/web

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["uvicorn", "medvault.main:app", "--host", "0.0.0.0", "--port", "8000"]
