# syntax=docker/dockerfile:1
FROM mcr.microsoft.com/devcontainers/python:3.12-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && pip install --no-cache-dir ".[azure]"

COPY config ./config
COPY evals ./evals

# Run as a non-root user: Container Apps and AKS both expect it.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
    CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8000)}/healthz')"

CMD ["sh", "-c", "uvicorn starter.api.main:app --host 0.0.0.0 --port ${PORT}"]
