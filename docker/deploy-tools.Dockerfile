ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

ARG KUBECTL_VERSION=1.36.0
ARG HELM_VERSION=3.17.3
ARG TARGETARCH
ARG APT_DEBIAN_MIRROR=http://deb.debian.org/debian
ARG APT_DEBIAN_SECURITY_MIRROR=http://deb.debian.org/debian-security

RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i \
        -e "s|^URIs: http://deb.debian.org/debian-security$|URIs: ${APT_DEBIAN_SECURITY_MIRROR}|" \
        -e "s|^URIs: http://deb.debian.org/debian$|URIs: ${APT_DEBIAN_MIRROR}|" \
        /etc/apt/sources.list.d/debian.sources; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl tar gzip; \
    fetch() { curl --fail --show-error --silent --location --retry 3 --retry-all-errors --retry-delay 2 --connect-timeout 15 --speed-time 30 --speed-limit 1024 --max-time 180 -o "$1" "$2"; }; \
    arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
    case "$arch" in \
      amd64|x86_64) arch="amd64" ;; \
      arm64|aarch64) arch="arm64" ;; \
      *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    kubectl_url="https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/${arch}/kubectl"; \
    fetch /tmp/kubectl "${kubectl_url}"; \
    fetch /tmp/kubectl.sha256 "${kubectl_url}.sha256"; \
    echo "$(cat /tmp/kubectl.sha256)  /tmp/kubectl" | sha256sum -c -; \
    install -m 0755 /tmp/kubectl /usr/local/bin/kubectl; \
    helm_archive="helm-v${HELM_VERSION}-linux-${arch}.tar.gz"; \
    helm_url="https://get.helm.sh/${helm_archive}"; \
    fetch /tmp/helm.tgz "${helm_url}"; \
    fetch /tmp/helm.tgz.sha256sum "${helm_url}.sha256sum"; \
    echo "$(cut -d ' ' -f 1 /tmp/helm.tgz.sha256sum)  /tmp/helm.tgz" | sha256sum -c -; \
    tar -xzf /tmp/helm.tgz -C /tmp; \
    mv "/tmp/linux-${arch}/helm" /usr/local/bin/helm; \
    chmod 0755 /usr/local/bin/helm; \
    rm -rf /tmp/kubectl /tmp/kubectl.sha256 /tmp/helm.tgz /tmp/helm.tgz.sha256sum "/tmp/linux-${arch}"; \
    groupadd --gid 10001 deploy; \
    useradd --uid 10001 --gid 10001 --create-home --home-dir /home/deploy --shell /usr/sbin/nologin deploy; \
    rm -rf /var/lib/apt/lists/*

ENV HOME=/home/deploy
USER 10001:10001
