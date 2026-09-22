FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.14

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY intraday ./intraday
ENTRYPOINT ["/app/.venv/bin/python", "-m", "intraday"]
