ARG PYTHON_BASE_IMAGE=python:3.12-slim

FROM node:20-alpine AS webui

ARG PNPM_VERSION=10.23.0

ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0

WORKDIR /webui/app

RUN corepack enable \
    && corepack prepare "pnpm@${PNPM_VERSION}" --activate

COPY webui/app/package.json webui/app/pnpm-lock.yaml webui/app/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY webui/app ./
RUN pnpm build


FROM ${PYTHON_BASE_IMAGE} AS builder

ARG PIP_DEFAULT_TIMEOUT=120
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG UV_DEFAULT_INDEX=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=${PIP_DEFAULT_TIMEOUT} \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    UV_DEFAULT_INDEX=${UV_DEFAULT_INDEX} \
    UV_LINK_MODE=copy

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev
RUN uv pip install --python /app/.venv/bin/python redis


FROM ${PYTHON_BASE_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    AGENT_DATA_DIR="/var/lib/coding-agent/data" \
    WEBUI_DIST_DIR="/app/webui-dist"

RUN apt-get update \
    && apt-get install -y --no-install-recommends bubblewrap git \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin coding-agent \
    && mkdir -p /app /workspace /var/lib/coding-agent/data \
    && chown -R coding-agent:coding-agent /app /workspace /var/lib/coding-agent

COPY --chown=coding-agent:coding-agent --from=builder /app/.venv /app/.venv
COPY --chown=coding-agent:coding-agent --from=builder /app/src /app/src
COPY --chown=coding-agent:coding-agent --from=webui /webui/app/dist /app/webui-dist

WORKDIR /workspace
USER coding-agent

EXPOSE 8080

ENTRYPOINT ["python", "-m", "coding_agent", "serve", "--host", "0.0.0.0", "--port", "8080"]
