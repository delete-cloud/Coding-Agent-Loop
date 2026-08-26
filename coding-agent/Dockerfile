ARG PYTHON_BASE_IMAGE=python:3.12-slim
ARG NODE_BASE_IMAGE=node:20-alpine

FROM ${NODE_BASE_IMAGE} AS webui

ARG PNPM_VERSION=10.23.0

ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0

WORKDIR /webui/app-next

RUN corepack enable \
    && corepack prepare "pnpm@${PNPM_VERSION}" --activate

COPY webui/app-next/package.json webui/app-next/pnpm-lock.yaml webui/app-next/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY webui/app-next ./
RUN pnpm build


FROM ${PYTHON_BASE_IMAGE} AS builder

ARG PIP_DEFAULT_TIMEOUT=120
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG UV_DEFAULT_INDEX=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=${PIP_DEFAULT_TIMEOUT}

WORKDIR /app

RUN PIP_INDEX_URL="${PIP_INDEX_URL}" pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./

RUN python -m venv /app/.venv \
    && UV_DEFAULT_INDEX="${UV_DEFAULT_INDEX}" uv export --frozen --no-dev --extra redis --no-emit-project --no-hashes --output-file /tmp/requirements.txt > /dev/null \
    && PIP_INDEX_URL="${PIP_INDEX_URL}" /app/.venv/bin/python -m pip install --upgrade pip \
    && PIP_INDEX_URL="${PIP_INDEX_URL}" /app/.venv/bin/python -m pip install --no-cache-dir -r /tmp/requirements.txt

COPY src ./src

RUN PIP_INDEX_URL="${PIP_INDEX_URL}" /app/.venv/bin/python -m pip install --no-cache-dir --no-deps .


FROM ${PYTHON_BASE_IMAGE} AS runtime

ARG APT_DEBIAN_MIRROR=http://deb.debian.org/debian
ARG APT_DEBIAN_SECURITY_MIRROR=http://deb.debian.org/debian-security

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    AGENT_DATA_DIR="/var/lib/coding-agent/data" \
    WEBUI_DIST_DIR="/app/webui-dist"

RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i \
            -e "s|^URIs: http://deb.debian.org/debian-security$|URIs: ${APT_DEBIAN_SECURITY_MIRROR}|" \
            -e "s|^URIs: http://deb.debian.org/debian$|URIs: ${APT_DEBIAN_MIRROR}|" \
            /etc/apt/sources.list.d/debian.sources; \
    elif [ -f /etc/apt/sources.list ]; then \
        sed -i \
            -e "s|http://deb.debian.org/debian-security|${APT_DEBIAN_SECURITY_MIRROR}|g" \
            -e "s|http://deb.debian.org/debian|${APT_DEBIAN_MIRROR}|g" \
            /etc/apt/sources.list; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends bubblewrap git \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin coding-agent \
    && mkdir -p /app /workspace /var/lib/coding-agent/data \
    && chown -R coding-agent:coding-agent /app /workspace /var/lib/coding-agent

COPY --chown=coding-agent:coding-agent --from=builder /app/.venv /app/.venv
COPY --chown=coding-agent:coding-agent --from=builder /app/src /app/src
COPY --chown=coding-agent:coding-agent --from=webui /webui/app-next/out /app/webui-dist

WORKDIR /workspace
USER coding-agent

EXPOSE 8080

ENTRYPOINT ["python", "-m", "coding_agent", "serve", "--host", "0.0.0.0", "--port", "8080"]
