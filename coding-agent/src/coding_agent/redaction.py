from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


def redact_url_credentials(url: str) -> str:
    parts = urlsplit(url)
    if parts.username is None and parts.password is None:
        return url

    hostname = parts.hostname or ""
    if ":" in hostname:
        netloc = f"[{hostname}]"
    else:
        netloc = hostname
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"

    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


_CREDENTIAL_URL_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s]+")


def redact_sensitive_text(value: str) -> str:
    return _CREDENTIAL_URL_RE.sub(
        lambda match: redact_url_credentials(match.group(0)),
        value,
    )
