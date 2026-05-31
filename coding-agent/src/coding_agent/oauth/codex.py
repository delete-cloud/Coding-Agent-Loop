"""Codex OAuth device-code login and refresh client."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import AliasChoices, BaseModel, Field

from coding_agent.oauth.store import OAuthStore, StoreBackedTokenSource
from coding_agent.oauth.types import (
    OAuthAccount,
    OAuthProviderRecord,
    OAuthTokens,
)

CODEX_ISSUER = "https://auth.openai.com"
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_TOKEN_ENDPOINT = "https://auth.openai.com/oauth/token"  # noqa: S105
CODEX_REVOKE_ENDPOINT = "https://auth.openai.com/oauth/revoke"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CODEX_DEVICE_REDIRECT_URI = "https://auth.openai.com/deviceauth/callback"
CODEX_SCOPES = [
    "openid",
    "profile",
    "email",
    "offline_access",
    "api.connectors.read",
    "api.connectors.invoke",
]


@dataclass(frozen=True)
class CodexOAuthProfile:
    """Codex OAuth endpoint configuration."""

    issuer: str = CODEX_ISSUER
    client_id: str = CODEX_CLIENT_ID
    token_endpoint: str = CODEX_TOKEN_ENDPOINT
    revoke_endpoint: str = CODEX_REVOKE_ENDPOINT
    base_url: str = CODEX_BASE_URL
    scopes: tuple[str, ...] = tuple(CODEX_SCOPES)

    @property
    def device_user_code_endpoint(self) -> str:
        return f"{self.issuer.rstrip('/')}/api/accounts/deviceauth/usercode"

    @property
    def device_token_endpoint(self) -> str:
        return f"{self.issuer.rstrip('/')}/api/accounts/deviceauth/token"

    @property
    def verification_url(self) -> str:
        return f"{self.issuer.rstrip('/')}/codex/device"

    @property
    def device_redirect_uri(self) -> str:
        return f"{self.issuer.rstrip('/')}/deviceauth/callback"


CODEX_PROFILE = CodexOAuthProfile()


class DeviceCode(BaseModel):
    """Device authorization code returned to the user."""

    verification_url: str
    user_code: str
    device_auth_id: str
    interval: int = 5


class _UserCodeResponse(BaseModel):
    device_auth_id: str
    user_code: str = Field(validation_alias=AliasChoices("user_code", "usercode"))
    interval: int | str = 5

    def to_device_code(self, profile: CodexOAuthProfile) -> DeviceCode:
        return DeviceCode(
            verification_url=profile.verification_url,
            user_code=self.user_code,
            device_auth_id=self.device_auth_id,
            interval=int(self.interval),
        )


class _DeviceTokenResponse(BaseModel):
    authorization_code: str
    code_challenge: str
    code_verifier: str


class _TokenResponse(BaseModel):
    id_token: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None


def _decode_jwt_payload(jwt: str) -> dict[str, Any]:
    """Decode a JWT payload without signature validation for local metadata extraction."""
    parts = jwt.split(".")
    if len(parts) != 3 or not parts[1]:
        raise ValueError("invalid JWT format")
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
    data = json.loads(decoded.decode("utf-8"))
    if not isinstance(data, dict):
        raise TypeError("invalid JWT payload")
    return data


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _plan_type(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        raw = value.get("raw_value") or value.get("value") or value.get("name")
        return _string_or_none(raw)
    return None


def _account_from_id_token(id_token: str) -> OAuthAccount:
    """Extract ChatGPT account metadata from an ID token."""
    claims = _decode_jwt_payload(id_token)
    profile = claims.get("https://api.openai.com/profile")
    auth = claims.get("https://api.openai.com/auth")
    profile_data = profile if isinstance(profile, dict) else {}
    auth_data = auth if isinstance(auth, dict) else {}
    return OAuthAccount(
        email=_string_or_none(claims.get("email"))
        or _string_or_none(profile_data.get("email")),
        chatgpt_user_id=_string_or_none(auth_data.get("chatgpt_user_id"))
        or _string_or_none(auth_data.get("user_id")),
        chatgpt_account_id=_string_or_none(auth_data.get("chatgpt_account_id")),
        chatgpt_plan_type=_plan_type(auth_data.get("chatgpt_plan_type")),
        chatgpt_account_is_fedramp=bool(
            auth_data.get("chatgpt_account_is_fedramp", False)
        ),
    )


class CodexOAuthClient:
    """Codex OAuth device-code login and refresh client."""

    def __init__(
        self,
        *,
        profile: CodexOAuthProfile = CODEX_PROFILE,
        store: OAuthStore | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.profile = profile
        self.store = store or OAuthStore()
        self.http_client = http_client or httpx.Client(timeout=30)
        self._owns_http_client = http_client is None

    def close(self) -> None:
        if self._owns_http_client:
            self.http_client.close()

    def request_device_code(self) -> DeviceCode:
        """Request a device authorization code from OpenAI."""
        response = self.http_client.post(
            self.profile.device_user_code_endpoint,
            json={"client_id": self.profile.client_id},
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return _UserCodeResponse.model_validate(response.json()).to_device_code(
            self.profile
        )

    def poll_device_token(
        self, device_code: DeviceCode, *, timeout_seconds: int = 15 * 60
    ) -> _DeviceTokenResponse:
        """Poll for device authorization until the user completes the flow."""
        monotonic = __import__("time").monotonic
        end_at = monotonic() + timeout_seconds
        while True:
            response = self.http_client.post(
                self.profile.device_token_endpoint,
                json={
                    "device_auth_id": device_code.device_auth_id,
                    "user_code": device_code.user_code,
                },
                headers={"Content-Type": "application/json"},
            )
            if response.is_success:
                return _DeviceTokenResponse.model_validate(response.json())
            if response.status_code in (403, 404) and monotonic() < end_at:
                sleep_for = min(device_code.interval, max(0.0, end_at - monotonic()))
                __import__("time").sleep(sleep_for)
                continue
            response.raise_for_status()
            raise RuntimeError("Codex device authorization failed")

    def exchange_device_code(
        self, code_response: _DeviceTokenResponse
    ) -> OAuthProviderRecord:
        """Exchange device authorization code for tokens."""
        response = self.http_client.post(
            self.profile.token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code_response.authorization_code,
                "redirect_uri": self.profile.device_redirect_uri,
                "client_id": self.profile.client_id,
                "code_verifier": code_response.code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        token_response = _TokenResponse.model_validate(response.json())
        return self._record_from_token_response(token_response)

    def refresh_record(self, record: OAuthProviderRecord) -> OAuthProviderRecord:
        """Refresh an existing OAuth provider record."""
        refresh_token = record.tokens.refresh_token
        if not refresh_token:
            raise RuntimeError(
                "Codex refresh token is missing; run `coding-agent oauth login codex` again."
            )
        response = self.http_client.post(
            self.profile.token_endpoint,
            json={
                "client_id": self.profile.client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        token_response = _TokenResponse.model_validate(response.json())
        account = (
            _account_from_id_token(token_response.id_token)
            if token_response.id_token
            else record.account
        )
        _validate_same_account(record.account, account)
        return record.with_refreshed_tokens(
            id_token=token_response.id_token,
            access_token=token_response.access_token,
            refresh_token=token_response.refresh_token,
            account=account,
        )

    def revoke_record(self, record: OAuthProviderRecord) -> None:
        """Revoke tokens for a provider record."""
        token = record.tokens.refresh_token or record.tokens.access_token
        if not token or not self.profile.revoke_endpoint:
            return
        response = self.http_client.post(
            self.profile.revoke_endpoint,
            data={"client_id": self.profile.client_id, "token": token},
        )
        if response.status_code < 400:
            return
        response.raise_for_status()

    def make_token_source(self) -> StoreBackedTokenSource:
        """Create an async token source backed by the store."""
        return StoreBackedTokenSource(
            "codex", store=self.store, refresh_provider=self.refresh_record
        )

    def _record_from_token_response(
        self, token_response: _TokenResponse
    ) -> OAuthProviderRecord:
        if not token_response.access_token:
            raise RuntimeError("Codex token response did not include access_token")
        account = OAuthAccount()
        if token_response.id_token:
            account = _account_from_id_token(token_response.id_token)
        return OAuthProviderRecord(
            issuer=self.profile.issuer,
            client_id=self.profile.client_id,
            token_endpoint=self.profile.token_endpoint,
            revoke_endpoint=self.profile.revoke_endpoint,
            base_url=self.profile.base_url,
            scopes=list(self.profile.scopes),
            tokens=OAuthTokens(
                id_token=token_response.id_token,
                access_token=token_response.access_token,
                refresh_token=token_response.refresh_token,
            ),
            account=account,
            last_refresh_at=datetime.now(UTC),
        )


def _validate_same_account(old: OAuthAccount, new: OAuthAccount) -> None:
    if (
        old.chatgpt_account_id
        and new.chatgpt_account_id
        and old.chatgpt_account_id != new.chatgpt_account_id
    ):
        raise RuntimeError(
            "Codex refresh returned a different ChatGPT account; "
            "run `coding-agent oauth login codex` again."
        )
    if (
        old.chatgpt_user_id
        and new.chatgpt_user_id
        and old.chatgpt_user_id != new.chatgpt_user_id
    ):
        raise RuntimeError(
            "Codex refresh returned a different ChatGPT user; "
            "run `coding-agent oauth login codex` again."
        )


def create_codex_token_source(
    *, store: OAuthStore | None = None
) -> StoreBackedTokenSource:
    """Create a token source for Codex OAuth."""
    return CodexOAuthClient(store=store).make_token_source()


def redact_record(record: OAuthProviderRecord) -> dict[str, Any]:
    """Return a redacted copy of a provider record (no token values)."""
    data = record.model_dump(mode="json")
    tokens = data.get("tokens")
    if isinstance(tokens, dict):
        for key in list(tokens):
            if tokens[key]:
                tokens[key] = "<redacted>"
    return data
