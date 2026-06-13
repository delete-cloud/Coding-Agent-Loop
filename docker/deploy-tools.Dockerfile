ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

ARG KUBECTL_VERSION=1.36.0
ARG HELM_VERSION=3.17.3
ARG TARGETARCH

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl tar gzip; \
    arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
    case "$arch" in \
      amd64|x86_64) arch="amd64" ;; \
      arm64|aarch64) arch="arm64" ;; \
      *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    kubectl_url="https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/${arch}/kubectl"; \
    curl -fsSL -o /tmp/kubectl "${kubectl_url}"; \
    curl -fsSL -o /tmp/kubectl.sha256 "${kubectl_url}.sha256"; \
    echo "$(cat /tmp/kubectl.sha256)  /tmp/kubectl" | sha256sum -c -; \
    install -m 0755 /tmp/kubectl /usr/local/bin/kubectl; \
    helm_archive="helm-v${HELM_VERSION}-linux-${arch}.tar.gz"; \
    helm_url="https://get.helm.sh/${helm_archive}"; \
    curl -fsSL -o /tmp/helm.tgz "${helm_url}"; \
    curl -fsSL -o /tmp/helm.tgz.sha256sum "${helm_url}.sha256sum"; \
    echo "$(cut -d ' ' -f 1 /tmp/helm.tgz.sha256sum)  /tmp/helm.tgz" | sha256sum -c -; \
    tar -xzf /tmp/helm.tgz -C /tmp; \
    mv "/tmp/linux-${arch}/helm" /usr/local/bin/helm; \
    chmod 0755 /usr/local/bin/helm; \
    rm -rf /tmp/kubectl /tmp/kubectl.sha256 /tmp/helm.tgz /tmp/helm.tgz.sha256sum "/tmp/linux-${arch}"; \
    rm -rf /var/lib/apt/lists/*
