"""In-memory codex OAuth device-flow registry for the HTTP server.

Manages concurrent device-code login flows: each flow gets a server-side
``flow_id``, polls in a background task, and on success writes the account
record to :class:`OAuthStore` under ``codex:<label>``. Flows live only in
process memory (restarts clear them); finished flows stay queryable for the
flow TTL so clients can poll their outcome.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx

from coding_agent.oauth.codex import CodexOAuthClient, DeviceCode
from coding_agent.oauth.store import OAuthStore
from coding_agent.oauth.types import OAuthAccount

logger = logging.getLogger(__name__)

FLOW_TTL_SECONDS = 10 * 60

CODEX_PROVIDER_KEY_PATTERN = re.compile(r"^codex(:[a-z0-9][a-z0-9-]{0,30})?$")

FlowState = Literal["pending", "authorized", "error", "expired", "cancelled"]

_TERMINAL_STATES: frozenset[str] = frozenset(
    {"authorized", "error", "expired", "cancelled"}
)


def exception_error_message(exc: BaseException) -> str:
    """Best-effort human-readable message for an exception (never empty).

    Local copy of the ``exception_error_message()`` helper from #685; replicate
    here instead of depending on that unmerged change.
    """
    message = str(exc).strip()
    if message:
        return message
    return type(exc).__name__ or "unknown error"


def _sanitize_label(raw: str) -> str:
    label = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    return label[:31].strip("-")


def _derive_label(account: OAuthAccount) -> str:
    """Derive an account label from id_token claims (email first)."""
    if account.email:
        label = _sanitize_label(account.email)
        if label:
            return label
    if account.chatgpt_account_id:
        label = _sanitize_label(account.chatgpt_account_id[:12])
        if label:
            return label
    return "account"


@dataclass
class CodexOAuthFlow:
    """One in-flight or recently finished codex device login flow."""

    flow_id: str
    device_code: DeviceCode
    requested_label: str | None
    created_at: datetime
    expires_at: datetime
    state: FlowState = "pending"
    account_label: str | None = None
    error: str | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "flow_id": self.flow_id,
            "state": self.state,
            "verification_url": self.device_code.verification_url,
            "user_code": self.device_code.user_code,
            "account_label": self.account_label,
            "error": self.error,
        }


class CodexOAuthFlowManager:
    """Registry of codex OAuth flows plus account list/delete helpers."""

    def __init__(
        self,
        *,
        store: OAuthStore | None = None,
        client_factory: Callable[[], CodexOAuthClient] | None = None,
        ttl_seconds: int = FLOW_TTL_SECONDS,
    ) -> None:
        self._store = store or OAuthStore()
        self._client_factory = client_factory or (
            lambda: CodexOAuthClient(store=self._store)
        )
        self._ttl_seconds = ttl_seconds
        self._flows: dict[str, CodexOAuthFlow] = {}

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    async def start(self, label: str | None = None) -> CodexOAuthFlow:
        """Request a device code and launch the background poll task.

        Raises whatever ``request_device_code`` raises (caller maps to 502);
        no flow is registered in that case.
        """
        client = self._client_factory()
        try:
            device_code = await asyncio.to_thread(client.request_device_code)
        except BaseException:
            client.close()
            raise
        now = datetime.now(UTC)
        flow = CodexOAuthFlow(
            flow_id=str(uuid.uuid4()),
            device_code=device_code,
            requested_label=label,
            created_at=now,
            expires_at=now + timedelta(seconds=self._ttl_seconds),
        )
        self._flows[flow.flow_id] = flow
        flow.task = asyncio.create_task(self._run(flow, client))
        return flow

    def get_flow(self, flow_id: str) -> CodexOAuthFlow | None:
        self._prune()
        return self._flows.get(flow_id)

    def list_flows(self) -> list[CodexOAuthFlow]:
        self._prune()
        return sorted(self._flows.values(), key=lambda flow: flow.created_at)

    def cancel(self, flow_id: str) -> CodexOAuthFlow | None:
        flow = self._flows.get(flow_id)
        if flow is None:
            return None
        if flow.state == "pending":
            flow.state = "cancelled"
            if flow.task is not None:
                flow.task.cancel()
        return flow

    def list_accounts(self) -> list[dict[str, Any]]:
        """Connected codex accounts: the default key plus every codex:<label>."""
        auth = self._store.load()
        accounts: list[dict[str, Any]] = []
        for key in sorted(auth.providers):
            if key != "codex" and not key.startswith("codex:"):
                continue
            record = auth.providers[key]
            accounts.append(
                {
                    "provider": key,
                    "label": key.split(":", 1)[1] if ":" in key else "default",
                    "email": record.account.email,
                    "plan": record.account.chatgpt_plan_type,
                    "connected_at": record.last_refresh_at,
                }
            )
        return accounts

    def delete_account(self, provider_key: str) -> bool:
        """Delete the local record for a codex key (no remote revoke)."""
        return self._store.delete_provider(provider_key) is not None

    def has_account(self, provider_key: str) -> bool:
        """Whether a local record exists for the given provider key."""
        return self._store.get_provider(provider_key) is not None

    async def _run(self, flow: CodexOAuthFlow, client: CodexOAuthClient) -> None:
        try:
            token_code = await asyncio.to_thread(
                client.poll_device_token,
                flow.device_code,
                timeout_seconds=self._ttl_seconds,
            )
            record = await asyncio.to_thread(client.exchange_device_code, token_code)
        except asyncio.CancelledError:
            if flow.state == "pending":
                flow.state = "cancelled"
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (403, 404):
                flow.state = "expired"
            else:
                flow.state = "error"
                flow.error = exception_error_message(exc)
            return
        except Exception as exc:
            flow.state = "error"
            flow.error = exception_error_message(exc)
            logger.exception("Codex OAuth flow %s failed", flow.flow_id)
            return
        finally:
            client.close()

        if flow.state != "pending":
            # Cancelled while the blocking poll/exchange was in flight.
            return
        label = flow.requested_label or self._allocate_label(record.account)
        self._store.set_provider(f"codex:{label}", record)
        flow.account_label = label
        flow.state = "authorized"

    def _allocate_label(self, account: OAuthAccount) -> str:
        """Pick a derived label, appending -2/-3/... on store conflicts."""
        base = _derive_label(account)
        auth = self._store.load()
        candidate = base
        suffix = 2
        while f"codex:{candidate}" in auth.providers:
            candidate = f"{base[:28].rstrip('-')}-{suffix}"
            suffix += 1
        return candidate

    def _prune(self) -> None:
        now = datetime.now(UTC)
        stale = [
            flow_id
            for flow_id, flow in self._flows.items()
            if flow.state in _TERMINAL_STATES and now > flow.expires_at
        ]
        for flow_id in stale:
            del self._flows[flow_id]
