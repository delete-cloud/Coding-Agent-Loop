FROM node:20.19.5-bookworm-slim@sha256:9e70124bd00f47dd023e349cd587132ae61892acc0e47ed641416c3e18f401c3 AS node

FROM ghcr.io/astral-sh/uv:0.12.1@sha256:cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded AS uv

FROM ghcr.io/astral-sh/ruff:0.15.12@sha256:e42f3866bb9f701dbc459cdec3f5aca06511e6a38e6e04bfd4d04a2be26d4fd4 AS ruff

FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=1000

ENV COREPACK_HOME=/usr/local/share/corepack \
    PATH=/workspaces/coding-agent/.venv/bin:${PATH}

COPY --from=node /usr/local/ /usr/local/
COPY --from=uv /uv /uvx /usr/local/bin/
COPY --from=ruff /ruff /usr/local/bin/ruff

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        build-essential \
        ca-certificates \
        git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${USER_GID}" "${USERNAME}" \
    && useradd --uid "${USER_UID}" --gid "${USER_GID}" --create-home --shell /bin/bash "${USERNAME}" \
    && mkdir -p "${COREPACK_HOME}" /workspaces/coding-agent \
    && corepack enable pnpm \
    && corepack install --global pnpm@10.23.0 \
    && chown -R "${USERNAME}:${USERNAME}" "${COREPACK_HOME}" /workspaces/coding-agent

USER vscode
WORKDIR /workspaces/coding-agent

CMD ["sleep", "infinity"]
