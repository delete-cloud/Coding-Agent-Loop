from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from coding_agent.oauth.auth import OAuthBearerAuth
from coding_agent.oauth.codex import CODEX_BASE_URL, redact_record
from coding_agent.oauth.store import OAuthStore, StoreBackedTokenSource
from coding_agent.oauth.types import OAuthProviderRecord, OAuthTokens


def _record(
    access_token: str = "access-token",
    refresh_token: str | None = "refresh-token",
) -> OAuthProviderRecord:
    return OAuthProviderRecord(
        issuer="https://auth.openai.com",
        client_id="codex-client",
        token_endpoint="https://auth.openai.com/oauth/token",
        revoke_endpoint="https://auth.openai.com/oauth/revoke",
        base_url=CODEX_BASE_URL,
        scopes=["openid", "offline_access"],
        tokens=OAuthTokens(
            access_token=access_token,
            refresh_token=refresh_token,
        ),
    )


def test_oauth_store_writes_private_file_and_round_trips_record(
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "nested" / "auth.json"
    store = OAuthStore(auth_path)

    store.set_provider("codex", _record())

    if os.name != "nt":
        assert stat.S_IMODE(auth_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(auth_path.stat().st_mode) == 0o600
    loaded = store.get_provider("codex")
    assert loaded is not None
    assert loaded.tokens.access_token == "access-token"
    assert loaded.tokens.refresh_token == "refresh-token"


@pytest.mark.asyncio
async def test_store_backed_token_source_refreshes_and_persists_token(
    tmp_path: Path,
) -> None:
    store = OAuthStore(tmp_path / "auth.json")
    store.set_provider("codex", _record(access_token="old-token"))

    def refresh_provider(record: OAuthProviderRecord) -> OAuthProviderRecord:
        assert record.tokens.access_token == "old-token"
        return record.with_refreshed_tokens(access_token="new-token")

    source = StoreBackedTokenSource(
        "codex",
        store=store,
        refresh_provider=refresh_provider,
    )

    snapshot = await source.refresh_token()

    assert snapshot.access_token == "new-token"
    assert store.get_provider("codex").tokens.access_token == "new-token"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_store_backed_token_source_refreshes_outside_store_update(
    tmp_path: Path,
) -> None:
    store = OAuthStore(tmp_path / "auth.json")
    store.set_provider("codex", _record(access_token="old-token"))
    update_depth = 0
    original_update = store.update

    def tracking_update(updater):
        nonlocal update_depth
        update_depth += 1
        try:
            return original_update(updater)
        finally:
            update_depth -= 1

    store.update = tracking_update  # type: ignore[method-assign]

    def refresh_provider(record: OAuthProviderRecord) -> OAuthProviderRecord:
        assert update_depth == 0
        return record.with_refreshed_tokens(access_token="new-token")

    source = StoreBackedTokenSource(
        "codex",
        store=store,
        refresh_provider=refresh_provider,
    )

    snapshot = await source.refresh_token()

    assert snapshot.access_token == "new-token"


@pytest.mark.asyncio
async def test_store_backed_token_source_missing_login_fails_fast(
    tmp_path: Path,
) -> None:
    source = StoreBackedTokenSource(
        "codex",
        store=OAuthStore(tmp_path / "auth.json"),
        refresh_provider=lambda record: record,
    )

    with pytest.raises(RuntimeError, match="not logged in"):
        await source.get_token()


@dataclass
class _MemoryTokenSource:
    access_token: str = "old-token"
    refresh_count: int = 0

    async def get_token(self):
        from coding_agent.oauth.types import TokenSnapshot

        return TokenSnapshot(provider_name="codex", access_token=self.access_token)

    async def refresh_token(self):
        from coding_agent.oauth.types import TokenSnapshot

        self.refresh_count += 1
        self.access_token = "new-token"
        return TokenSnapshot(provider_name="codex", access_token=self.access_token)


@pytest.mark.asyncio
async def test_oauth_bearer_auth_refreshes_on_unauthorized_response() -> None:
    seen_authorization: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_authorization.append(request.headers["Authorization"])
        if len(seen_authorization) == 1:
            return httpx.Response(401, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    token_source = _MemoryTokenSource()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        auth=OAuthBearerAuth(token_source),
    ) as client:
        response = await client.get("https://example.invalid/test")

    assert response.status_code == 200
    assert seen_authorization == ["Bearer old-token", "Bearer new-token"]
    assert token_source.refresh_count == 1


def test_redact_record_removes_token_values() -> None:
    safe = redact_record(_record(access_token="secret-access"))

    assert safe["tokens"]["access_token"] == "<redacted>"
    assert safe["tokens"]["refresh_token"] == "<redacted>"
    assert "secret-access" not in str(safe)
