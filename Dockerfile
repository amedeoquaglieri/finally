# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1: build the Next.js static export (frontend/out)
# ---------------------------------------------------------------------------
FROM node:20-slim AS frontend

WORKDIR /frontend

# Install dependencies first so they are cached until package*.json changes.
COPY frontend/package.json frontend/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY frontend/ ./
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build && test -f out/index.html


# ---------------------------------------------------------------------------
# Stage 2: Python runtime (FastAPI served by uvicorn)
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

RUN pip install --no-cache-dir uv==0.9.26

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies only (cached until pyproject.toml / uv.lock change). The uv download cache
# lives in a BuildKit cache mount so it speeds up rebuilds without bloating the image.
# The `dev` optional extra is not requested, so test tooling stays out of the image.
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Backend source, then install the project itself into the venv.
COPY backend/ ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Frontend static export.
COPY --from=frontend /frontend/out /app/static

ENV PATH="/app/.venv/bin:$PATH" \
    DB_PATH=/app/db/finally.db \
    STATIC_DIR=/app/static

RUN mkdir -p /app/db

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
