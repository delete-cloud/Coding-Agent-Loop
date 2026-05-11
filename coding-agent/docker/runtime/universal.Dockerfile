FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        build-essential \
        ca-certificates \
        coreutils \
        curl \
        findutils \
        git \
        jq \
        ripgrep \
        wget \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir --upgrade pip uv \
    && useradd --create-home --uid 1000 --shell /bin/bash agent

WORKDIR /workspace
USER agent

CMD ["sleep", "infinity"]
