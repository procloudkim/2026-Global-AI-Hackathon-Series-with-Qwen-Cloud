FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src ./src
COPY bench ./bench
COPY deploy ./deploy
COPY scripts ./scripts
COPY submission ./submission
COPY aidlc-docs ./aidlc-docs
COPY README.md LICENSE ./

RUN mkdir -p /app/memory

EXPOSE 8080

CMD ["uv", "run", "uvicorn", "librarian.main:app", "--host", "0.0.0.0", "--port", "8080"]

