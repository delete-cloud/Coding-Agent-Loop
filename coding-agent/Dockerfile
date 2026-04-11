FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev
RUN uv pip install --python /app/.venv/bin/python redis


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    AGENT_DATA_DIR="/var/lib/coding-agent/data"

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin coding-agent \
    && mkdir -p /app /workspace /var/lib/coding-agent/data \
    && chown -R coding-agent:coding-agent /app /workspace /var/lib/coding-agent

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

WORKDIR /workspace
USER coding-agent

EXPOSE 8080

ENTRYPOINT ["python", "-m", "coding_agent", "serve", "--host", "0.0.0.0", "--port", "8080"]
