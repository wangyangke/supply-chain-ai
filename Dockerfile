# ── stage 1: builder ──────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# install build deps for any C-extension wheels
RUN apt-get update -qq && apt-get install -y --no-install-recommends -qq \
    gcc && \
    rm -rf /var/lib/apt/lists/*

# copy only dependency manifest first for layer caching
COPY pyproject.toml ./

# install runtime deps into a clean prefix we can copy later
RUN pip install --no-cache-dir --prefix=/install \
    "fastapi>=0.110" "uvicorn[standard]>=0.29" "typer>=0.12" "pydantic>=2.6" "httpx>=0.27"

# ── stage 2: runtime ──────────────────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="supply-chain-ai"
LABEL org.opencontainers.image.description="Reproducible supply chain & partnership research service (NVIDIA dataset)"
LABEL org.opencontainers.image.source="https://github.com/wangyangke/supply-chain-ai"
LABEL org.opencontainers.image.licenses="MIT"

# create non-root user
RUN groupadd -r scr && useradd -r -g scr -d /app -s /sbin/nologin scr

WORKDIR /app

# copy installed packages from builder
COPY --from=builder /install /usr/local

# copy application code, data, and dashboard
COPY --chown=scr:scr src/ ./src/
COPY --chown=scr:scr data/ ./data/
COPY --chown=scr:scr dashboard.html ./
COPY --chown=scr:scr pyproject.toml ./
COPY --chown=scr:scr scripts/ ./scripts/

# install the package (editable so scripts/CLI are available)
RUN pip install --no-cache-dir --no-deps -e . && \
    pip install --no-cache-dir rich

# environment defaults
ENV SCR_DATA_DIR=/app/data \
    SCR_HOST=0.0.0.0 \
    SCR_PORT=8000 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# healthcheck: hit /health every 30s
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3); sys.exit(0)" || exit 1

USER scr

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
