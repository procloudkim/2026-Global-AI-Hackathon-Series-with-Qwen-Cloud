FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS runtime

ARG UV_VERSION=0.11.28
ARG LIBRARIAN_BUILD_SHA=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LIBRARIAN_MEMORY_ROOT=/app/memory \
    LIBRARIAN_DEPLOYED_SHA=${LIBRARIAN_BUILD_SHA}

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && python -m pip install --no-cache-dir "uv==${UV_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src ./src
COPY bench ./bench
COPY deploy ./deploy
COPY scripts ./scripts
COPY submission ./submission
COPY aidlc-docs ./aidlc-docs
COPY README.md LICENSE ./

RUN groupadd --gid 10001 librarian \
    && useradd --uid 10001 --gid librarian --no-create-home --shell /usr/sbin/nologin librarian \
    && mkdir -p /app/memory \
    && chown -R librarian:librarian /app/memory \
    && chmod -R a-w /app/src /app/.venv

LABEL org.opencontainers.image.title="Librarian Track 1 MemoryAgent" \
      org.opencontainers.image.revision="${LIBRARIAN_BUILD_SHA}"

USER 10001:10001

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl --fail --silent --show-error http://127.0.0.1:8080/health >/dev/null || exit 1

CMD ["/app/.venv/bin/uvicorn", "librarian.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
