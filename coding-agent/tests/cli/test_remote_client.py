from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import asyncio
from pathlib import Path

import httpx
import click
import pytest
from click.testing import CliRunner

from agentkit.observability import ObservationEvent, SpanRecord
from coding_agent.__main__ import main


class RecordingObservationSink:
    def __init__(self) -> None:
        self.spans: list[SpanRecord] = []
        self.events: list[ObservationEvent] = []

    def record_span(self, span: SpanRecord) -> None:
        self.spans.append(span)

    def record_event(self, event: ObservationEvent) -> None:
        self.events.append(event)


class FailingObservationSink:
    def record_span(self, span: SpanRecord) -> None:
        del span
        raise RuntimeError("span sink failed")

    def record_event(self, event: ObservationEvent) -> None:
        del event
        raise RuntimeError("event sink failed")


def test_create_remote_session_sends_git_workspace_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coding_agent.remote.client import RemoteEndpoint, create_remote_session

    calls: list[tuple[str, dict[str, object] | None, dict[str, str]]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"session_id": "sess-git"}

    class FakeClient:
        def __init__(
            self,
            *,
            base_url: str,
            headers: dict[str, str] | None = None,
            timeout: float,
        ) -> None:
            assert base_url == "http://agent.example"
            assert timeout == 60.0
            self.headers = headers or {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            calls.append((path, json, self.headers))
            return FakeResponse()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    session_id = create_remote_session(
        RemoteEndpoint("dev", "http://agent.example", "secret-token"),
        workspace_source={
            "kind": "git",
            "remote_url": "https://github.com/org/repo.git",
            "base_ref": "main",
            "base_sha": "abc123",
        },
        approval_policy="auto",
        runtime_profile="universal",
    )

    assert session_id == "sess-git"
    assert calls == [
        (
            "/sessions",
            {
                "workspace_source": {
                    "kind": "git",
                    "remote_url": "https://github.com/org/repo.git",
                    "base_ref": "main",
                    "base_sha": "abc123",
                    "runtime_profile": "universal",
                },
                "approval_policy": "auto",
            },
            {"Authorization": "Bearer secret-token"},
        )
    ]


def test_create_remote_session_records_remote_provision_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coding_agent.remote.client import RemoteEndpoint, create_remote_session

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"session_id": "sess-git"}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            del path, json
            return FakeResponse()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    sink = RecordingObservationSink()

    session_id = create_remote_session(
        RemoteEndpoint("dev", "http://agent.example", None),
        workspace_source={"kind": "git", "remote_url": "https://example.invalid/repo"},
        observation_sink=sink,
    )

    assert session_id == "sess-git"
    assert [span.name for span in sink.spans] == ["remote.workspace.provision"]
    assert sink.spans[0].status == "ok"
    assert sink.spans[0].attributes == {
        "workspace_ref_kind": "git",
        "remote_status": "completed",
        "session_id": "sess-git",
    }


def test_create_remote_session_observation_sink_failure_fails_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coding_agent.remote.client import RemoteEndpoint, create_remote_session

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"session_id": "sess-docker"}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            del path, json
            return FakeResponse()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    session_id = create_remote_session(
        RemoteEndpoint("dev", "http://agent.example", None),
        observation_sink=FailingObservationSink(),
    )

    assert session_id == "sess-docker"


def test_create_local_daemon_session_sends_local_run_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from coding_agent.remote.client import (
        RemoteEndpoint,
        create_local_daemon_session,
    )

    calls: list[tuple[str, dict[str, object] | None, dict[str, str]]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"session_id": "sess-local"}

    class FakeClient:
        def __init__(
            self,
            *,
            base_url: str,
            headers: dict[str, str] | None = None,
            timeout: float,
        ) -> None:
            assert base_url == "http://127.0.0.1:8080"
            assert timeout == 60.0
            self.headers = headers or {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            calls.append((path, json, self.headers))
            return FakeResponse()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    session_id = create_local_daemon_session(
        RemoteEndpoint("local-daemon", "http://127.0.0.1:8080", None),
        repo_path=tmp_path,
        approval_policy="auto",
    )

    assert session_id == "sess-local"
    assert calls == [
        (
            "/sessions",
            {
                "run_target": {
                    "workspace": {
                        "kind": "local_path",
                        "path": str(tmp_path.resolve()),
                    },
                    "executor": {"kind": "local_daemon"},
                    "isolation": {"kind": "default_local_sandbox"},
                    "constraints": {},
                    "annotations": {},
                },
                "approval_policy": "auto",
            },
            {},
        )
    ]


def test_create_local_daemon_session_sends_runtime_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from coding_agent.remote.client import (
        RemoteEndpoint,
        create_local_daemon_session,
    )

    calls: list[dict[str, object] | None] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"session_id": "sess-local"}

    class FakeClient:
        def __init__(
            self,
            *,
            base_url: str,
            headers: dict[str, str] | None = None,
            timeout: float,
        ) -> None:
            del base_url, headers, timeout

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            assert path == "/sessions"
            calls.append(json)
            return FakeResponse()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    session_id = create_local_daemon_session(
        RemoteEndpoint("local-daemon", "http://127.0.0.1:8080", None),
        repo_path=tmp_path,
        approval_policy="auto",
        provider_name="codex",
        model_name="gpt-5.5",
        base_url="https://chatgpt.com/backend-api/codex",
    )

    assert session_id == "sess-local"
    assert calls[0] is not None
    assert calls[0]["provider"] == "codex"
    assert calls[0]["model"] == "gpt-5.5"
    assert calls[0]["base_url"] == "https://chatgpt.com/backend-api/codex"


def test_create_local_daemon_session_reports_non_json_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from coding_agent.remote.client import (
        RemoteEndpoint,
        create_local_daemon_session,
    )

    class FakeResponse:
        headers = {"content-type": "text/plain"}
        text = "Hello Kubernetes bootcamp!"

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            raise json.JSONDecodeError("Expecting value", self.text, 0)

    class FakeClient:
        def __init__(
            self,
            *,
            base_url: str,
            headers: dict[str, str] | None = None,
            timeout: float,
        ) -> None:
            del base_url, headers, timeout

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            del path, json
            return FakeResponse()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    with pytest.raises(click.ClickException) as exc_info:
        create_local_daemon_session(
            RemoteEndpoint("local-daemon", "http://127.0.0.1:8080", None),
            repo_path=tmp_path,
            approval_policy="auto",
        )

    message = str(exc_info.value)
    assert "create local daemon session returned non-JSON response" in message
    assert "content-type=text/plain" in message
    assert "Hello Kubernetes bootcamp!" in message


def _run_git(repo_path: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        shell=False,
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    )
    return result.stdout.strip()


def _init_clean_git_repo_with_origin(tmp_path: Path) -> tuple[Path, str]:
    repo_path = tmp_path / "repo"
    remote_path = tmp_path / "remote.git"
    remote_path.mkdir()
    _ = subprocess.run(
        ["git", "init", "--bare", str(remote_path)],
        shell=False,
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    )
    repo_path.mkdir()
    _run_git(repo_path, ["init", "-b", "main"])
    _run_git(repo_path, ["config", "user.name", "Test User"])
    _run_git(repo_path, ["config", "user.email", "test@example.com"])
    (repo_path / "README.md").write_text("# repo\n", encoding="utf-8")
    _run_git(repo_path, ["add", "README.md"])
    _run_git(repo_path, ["commit", "-m", "initial"])
    _run_git(repo_path, ["remote", "add", "origin", remote_path.as_uri()])
    _run_git(repo_path, ["push", "-u", "origin", "main"])
    _run_git(
        repo_path, ["remote", "set-url", "origin", "https://github.com/org/repo.git"]
    )
    head_sha = _run_git(repo_path, ["rev-parse", "HEAD"])
    return repo_path, head_sha


class _RemoteFakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


def test_remote_semantic_memory_client_helpers_call_exact_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coding_agent.remote.client import (
        RemoteEndpoint,
        get_remote_semantic_memory_status,
        rebuild_remote_semantic_memory,
    )

    calls: list[tuple[str, str, dict[str, object] | None, dict[str, str]]] = []

    class FakeClient:
        def __init__(
            self,
            *,
            base_url: str,
            headers: dict[str, str] | None = None,
            timeout: float,
        ) -> None:
            assert base_url == "http://agent.example"
            assert timeout == 30.0
            self.headers = headers or {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def get(self, path: str) -> _RemoteFakeResponse:
            calls.append(("get", path, None, self.headers))
            return _RemoteFakeResponse(
                {
                    "document_count": 3,
                    "reviewed_memory_count": 4,
                    "accepted_reviewed_memory_count": 2,
                    "topic_store_available": True,
                }
            )

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> _RemoteFakeResponse:
            calls.append(("post", path, json, self.headers))
            return _RemoteFakeResponse(
                {
                    "topic_count": 5,
                    "reviewed_memory_count": 2,
                    "indexed_count": 6,
                    "skipped_count": 1,
                    "deleted_count": 1,
                    "indexed_ids": ["topic:1", "memory:1"],
                    "deleted_ids": ["stale:1"],
                }
            )

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    endpoint = RemoteEndpoint("dev", "http://agent.example", "secret-token")

    status = get_remote_semantic_memory_status(endpoint, "sess-1")
    rebuild = rebuild_remote_semantic_memory(
        endpoint,
        "sess-1",
        25,
        True,
    )

    assert status == {
        "document_count": 3,
        "reviewed_memory_count": 4,
        "accepted_reviewed_memory_count": 2,
        "topic_store_available": True,
    }
    assert rebuild["indexed_ids"] == ["topic:1", "memory:1"]
    assert calls == [
        (
            "get",
            "/sessions/sess-1/memory/semantic/status",
            None,
            {"Authorization": "Bearer secret-token"},
        ),
        (
            "post",
            "/sessions/sess-1/memory/semantic/rebuild",
            {"batch_size": 25, "allow_rebuild": True},
            {"Authorization": "Bearer secret-token"},
        ),
    ]


def test_remote_semantic_memory_client_helpers_use_admin_token_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coding_agent.remote.client import (
        RemoteEndpoint,
        get_remote_semantic_memory_status,
        rebuild_remote_semantic_memory,
    )

    calls: list[tuple[str, str, dict[str, object] | None, dict[str, str]]] = []

    class FakeClient:
        def __init__(
            self,
            *,
            base_url: str,
            headers: dict[str, str] | None = None,
            timeout: float,
        ) -> None:
            assert base_url == "http://agent.example"
            assert timeout == 30.0
            self.headers = headers or {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def get(self, path: str) -> _RemoteFakeResponse:
            calls.append(("get", path, None, self.headers))
            return _RemoteFakeResponse({"document_count": 0})

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> _RemoteFakeResponse:
            calls.append(("post", path, json, self.headers))
            return _RemoteFakeResponse({"indexed_count": 0})

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    endpoint = RemoteEndpoint(
        name="dev",
        url="http://agent.example",
        token="user-token",
        admin_token="admin-token",
    )

    _ = get_remote_semantic_memory_status(endpoint, "sess-1")
    _ = rebuild_remote_semantic_memory(endpoint, "sess-1", 25, True)

    assert calls == [
        (
            "get",
            "/sessions/sess-1/memory/semantic/status",
            None,
            {"Authorization": "Bearer admin-token"},
        ),
        (
            "post",
            "/sessions/sess-1/memory/semantic/rebuild",
            {"batch_size": 25, "allow_rebuild": True},
            {"Authorization": "Bearer admin-token"},
        ),
    ]


def test_remote_semantic_memory_admin_token_env_resolves_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coding_agent.remote.client import (
        RemoteEndpoint,
        get_remote_semantic_memory_status,
    )

    calls: list[dict[str, str]] = []

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            calls.append(headers if isinstance(headers, dict) else {})

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def get(self, path: str) -> _RemoteFakeResponse:
            assert path == "/sessions/sess-1/memory/semantic/status"
            return _RemoteFakeResponse({"document_count": 0})

    monkeypatch.setenv("REMOTE_ADMIN_TOKEN", "env-admin-token")
    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    _ = get_remote_semantic_memory_status(
        RemoteEndpoint(
            name="dev",
            url="http://agent.example",
            token="user-token",
            admin_token_env="REMOTE_ADMIN_TOKEN",
        ),
        "sess-1",
    )

    assert calls == [{"Authorization": "Bearer env-admin-token"}]


@pytest.mark.parametrize("env_value", [None, "", "   "])
def test_remote_semantic_memory_admin_token_env_missing_or_blank_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str | None,
) -> None:
    from coding_agent.remote.client import (
        RemoteEndpoint,
        get_remote_semantic_memory_status,
    )

    if env_value is None:
        monkeypatch.delenv("REMOTE_ADMIN_TOKEN", raising=False)
    else:
        monkeypatch.setenv("REMOTE_ADMIN_TOKEN", env_value)

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            del kwargs
            raise AssertionError("admin token env failure must happen before HTTP")

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    with pytest.raises(click.ClickException) as exc_info:
        get_remote_semantic_memory_status(
            RemoteEndpoint(
                name="dev",
                url="http://agent.example",
                token="user-token",
                admin_token_env="REMOTE_ADMIN_TOKEN",
            ),
            "sess-1",
        )

    assert "Admin token environment variable is not set or is blank" in str(
        exc_info.value
    )
    assert "REMOTE_ADMIN_TOKEN" in str(exc_info.value)


def test_remote_client_normal_endpoints_keep_using_user_token_with_admin_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coding_agent.remote.client import RemoteEndpoint, list_remote_sessions

    calls: list[tuple[str, dict[str, str]]] = []

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def get(self, path: str) -> _RemoteFakeResponse:
            calls.append((path, self.headers))
            return _RemoteFakeResponse({"sessions": []})

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    sessions = list_remote_sessions(
        RemoteEndpoint(
            name="dev",
            url="http://agent.example",
            token="user-token",
            admin_token="admin-token",
        )
    )

    assert sessions == []
    assert calls == [("/sessions", {"Authorization": "Bearer user-token"})]


def test_serve_config_sets_explicit_server_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "server.toml"
    config_path.write_text(
        "\n".join(
            [
                "[agent]",
                'name = "test-agent"',
                'model = "test-model"',
                'provider = "openai"',
                "",
                "[server]",
                'bearer_token = "test-token"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_uvicorn_run(app: object, *, host: str, port: int) -> None:
        del app
        captured["host"] = host
        captured["port"] = port
        captured["config"] = os.environ.get("CODING_AGENT_SERVER_CONFIG")

    monkeypatch.setattr("uvicorn.run", fake_uvicorn_run)
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "serve",
            "--config",
            str(config_path),
            "--host",
            "0.0.0.0",
            "--port",
            "9000",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert captured == {
        "host": "0.0.0.0",
        "port": 9000,
        "config": str(config_path.resolve()),
    }


def test_serve_rejects_non_localhost_without_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "server.toml"
    config_path.write_text(
        "\n".join(
            [
                "[agent]",
                'name = "test-agent"',
                'model = "test-model"',
                'provider = "openai"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    def fail_uvicorn_run(app: object, *, host: str, port: int) -> None:
        raise AssertionError("uvicorn.run must not be called")

    monkeypatch.setattr("uvicorn.run", fail_uvicorn_run)
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "serve",
            "--config",
            str(config_path),
            "--host",
            "0.0.0.0",
            "--port",
            "9000",
        ],
    )

    assert result.exit_code != 0
    assert "refuses to listen on non-localhost" in result.output


def test_serve_allows_non_localhost_with_configured_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "server.toml"
    config_path.write_text(
        "\n".join(
            [
                "[agent]",
                'name = "test-agent"',
                'model = "test-model"',
                'provider = "openai"',
                "",
                "[server]",
                'bearer_token = "test-token"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_uvicorn_run(app: object, *, host: str, port: int) -> None:
        del app
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("uvicorn.run", fake_uvicorn_run)
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "serve",
            "--config",
            str(config_path),
            "--host",
            "0.0.0.0",
            "--port",
            "9000",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert captured == {"host": "0.0.0.0", "port": 9000}


def test_serve_allow_unauthenticated_bypasses_non_localhost_auth_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "server.toml"
    config_path.write_text(
        "\n".join(
            [
                "[agent]",
                'name = "test-agent"',
                'model = "test-model"',
                'provider = "openai"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_uvicorn_run(app: object, *, host: str, port: int) -> None:
        del app
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("uvicorn.run", fake_uvicorn_run)
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "serve",
            "--config",
            str(config_path),
            "--host",
            "0.0.0.0",
            "--port",
            "9000",
            "--allow-unauthenticated",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert captured == {"host": "0.0.0.0", "port": 9000}


def test_daemon_command_starts_foreground_local_control_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_uvicorn_run(app: object, *, host: str, port: int) -> None:
        del app
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("uvicorn.run", fake_uvicorn_run)
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["daemon", "--host", "127.0.0.1", "--port", "8765"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert captured == {"host": "127.0.0.1", "port": 8765}
    assert "Starting Coding Agent local daemon control plane" in result.output


def test_daemon_run_uses_local_http_control_plane_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    def fake_create_local_daemon_session(endpoint, *, repo_path, approval_policy):
        calls.append(
            (
                "create",
                {
                    "endpoint": endpoint,
                    "repo_path": repo_path,
                    "approval_policy": approval_policy,
                },
            )
        )
        return "sess-local"

    def fake_stream_prompt_or_run_request(
        *,
        base_url: str,
        session_id: str,
        prompt: str,
        headers: dict[str, str],
    ) -> int:
        calls.append(
            (
                "stream",
                {
                    "base_url": base_url,
                    "session_id": session_id,
                    "prompt": prompt,
                    "headers": headers,
                },
            )
        )
        return 0

    monkeypatch.setattr(
        "coding_agent.remote.client.create_local_daemon_session",
        fake_create_local_daemon_session,
    )
    monkeypatch.setattr(
        "coding_agent.remote.client.stream_prompt_or_run_request",
        fake_stream_prompt_or_run_request,
    )
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "daemon",
            "run",
            "--url",
            "http://127.0.0.1:8765",
            "--repo",
            str(tmp_path),
            "--goal",
            "hello daemon",
            "--approval",
            "auto",
            "--keep-session",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "Created daemon-backed local session sess-local" in result.output
    assert calls[0][0] == "create"
    create_payload = calls[0][1]
    assert isinstance(create_payload, dict)
    endpoint = create_payload["endpoint"]
    assert endpoint.name == "local-daemon"
    assert endpoint.url == "http://127.0.0.1:8765"
    assert endpoint.token is None
    assert create_payload["repo_path"] == tmp_path.resolve()
    assert create_payload["approval_policy"] == "auto"
    assert calls[1] == (
        "stream",
        {
            "base_url": "http://127.0.0.1:8765",
            "session_id": "sess-local",
            "prompt": "hello daemon",
            "headers": {},
        },
    )


def test_daemon_run_sends_env_provider_model_to_local_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object | None] = {}

    def fake_create_local_daemon_session(
        endpoint,
        *,
        repo_path,
        approval_policy,
        provider_name=None,
        model_name=None,
        base_url=None,
    ):
        del endpoint, repo_path, approval_policy
        captured["provider_name"] = provider_name
        captured["model_name"] = model_name
        captured["base_url"] = base_url
        return "sess-local"

    def fake_stream_prompt_or_run_request(
        *,
        base_url: str,
        session_id: str,
        prompt: str,
        headers: dict[str, str],
    ) -> int:
        del base_url, session_id, prompt, headers
        return 0

    monkeypatch.setenv("AGENT_PROVIDER", "codex")
    monkeypatch.setenv("AGENT_MODEL", "gpt-5.5")
    monkeypatch.setattr(
        "coding_agent.remote.client.create_local_daemon_session",
        fake_create_local_daemon_session,
    )
    monkeypatch.setattr(
        "coding_agent.remote.client.stream_prompt_or_run_request",
        fake_stream_prompt_or_run_request,
    )
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "daemon",
            "run",
            "--url",
            "http://127.0.0.1:8765",
            "--repo",
            str(tmp_path),
            "--goal",
            "hello daemon",
            "--keep-session",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert captured == {
        "provider_name": "codex",
        "model_name": "gpt-5.5",
        "base_url": None,
    }


def test_daemon_run_cleanup_session_deletes_after_stream_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    def fake_create_local_daemon_session(endpoint, *, repo_path, approval_policy):
        del repo_path, approval_policy
        calls.append(("create", endpoint.url))
        return "sess-local"

    def fake_stream_prompt_or_run_request(
        *,
        base_url: str,
        session_id: str,
        prompt: str,
        headers: dict[str, str],
    ) -> int:
        del base_url, session_id, prompt, headers
        calls.append(("stream", None))
        raise click.ClickException("stream failed")

    def fake_delete_remote_session(
        *,
        base_url: str,
        session_id: str,
        headers: dict[str, str],
    ) -> None:
        calls.append(
            (
                "delete",
                {
                    "base_url": base_url,
                    "session_id": session_id,
                    "headers": headers,
                },
            )
        )

    monkeypatch.setattr(
        "coding_agent.remote.client.create_local_daemon_session",
        fake_create_local_daemon_session,
    )
    monkeypatch.setattr(
        "coding_agent.remote.client.stream_prompt_or_run_request",
        fake_stream_prompt_or_run_request,
    )
    monkeypatch.setattr(
        "coding_agent.remote.client.delete_remote_session",
        fake_delete_remote_session,
    )
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "daemon",
            "run",
            "--url",
            "http://127.0.0.1:8765",
            "--repo",
            str(tmp_path),
            "--goal",
            "hello daemon",
            "--cleanup-session",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code != 0
    assert "stream failed" in result.output
    assert calls == [
        ("create", "http://127.0.0.1:8765"),
        ("stream", None),
        (
            "delete",
            {
                "base_url": "http://127.0.0.1:8765",
                "session_id": "sess-local",
                "headers": {},
            },
        ),
    ]


def test_daemon_repl_reuses_one_daemon_session_for_multiple_prompts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    def fake_create_local_daemon_session(endpoint, *, repo_path, approval_policy):
        calls.append(
            (
                "create",
                {
                    "endpoint": endpoint,
                    "repo_path": repo_path,
                    "approval_policy": approval_policy,
                },
            )
        )
        return "sess-repl"

    def fake_stream_prompt_or_run_request(
        *,
        base_url: str,
        session_id: str,
        prompt: str,
        headers: dict[str, str],
    ) -> int:
        calls.append(
            (
                "stream",
                {
                    "base_url": base_url,
                    "session_id": session_id,
                    "prompt": prompt,
                    "headers": headers,
                },
            )
        )
        return 0

    monkeypatch.setattr(
        "coding_agent.remote.client.create_local_daemon_session",
        fake_create_local_daemon_session,
    )
    monkeypatch.setattr(
        "coding_agent.remote.client.stream_prompt_or_run_request",
        fake_stream_prompt_or_run_request,
    )
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "daemon",
            "repl",
            "--url",
            "http://127.0.0.1:8765",
            "--repo",
            str(tmp_path),
            "--approval",
            "auto",
            "--keep-session",
        ],
        input="first prompt\nsecond prompt\n/exit\n",
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "Created daemon-backed local REPL session sess-repl" in result.output
    assert calls[0][0] == "create"
    create_payload = calls[0][1]
    assert isinstance(create_payload, dict)
    endpoint = create_payload["endpoint"]
    assert endpoint.name == "local-daemon"
    assert endpoint.url == "http://127.0.0.1:8765"
    assert create_payload["repo_path"] == tmp_path.resolve()
    assert create_payload["approval_policy"] == "auto"
    assert calls[1:] == [
        (
            "stream",
            {
                "base_url": "http://127.0.0.1:8765",
                "session_id": "sess-repl",
                "prompt": "first prompt",
                "headers": {},
            },
        ),
        (
            "stream",
            {
                "base_url": "http://127.0.0.1:8765",
                "session_id": "sess-repl",
                "prompt": "second prompt",
                "headers": {},
            },
        ),
    ]


def test_daemon_repl_cleanup_session_after_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    def fake_create_local_daemon_session(endpoint, *, repo_path, approval_policy):
        del endpoint, repo_path, approval_policy
        calls.append(("create", None))
        return "sess-repl"

    def fake_stream_prompt_or_run_request(
        *,
        base_url: str,
        session_id: str,
        prompt: str,
        headers: dict[str, str],
    ) -> int:
        del base_url, session_id, prompt, headers
        calls.append(("stream", None))
        return 0

    def fake_delete_remote_session(
        *,
        base_url: str,
        session_id: str,
        headers: dict[str, str],
    ) -> None:
        calls.append(
            (
                "delete",
                {
                    "base_url": base_url,
                    "session_id": session_id,
                    "headers": headers,
                },
            )
        )

    monkeypatch.setattr(
        "coding_agent.remote.client.create_local_daemon_session",
        fake_create_local_daemon_session,
    )
    monkeypatch.setattr(
        "coding_agent.remote.client.stream_prompt_or_run_request",
        fake_stream_prompt_or_run_request,
    )
    monkeypatch.setattr(
        "coding_agent.remote.client.delete_remote_session",
        fake_delete_remote_session,
    )
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "daemon",
            "repl",
            "--url",
            "http://127.0.0.1:8765",
            "--repo",
            str(tmp_path),
            "--cleanup-session",
        ],
        input="hello\n/quit\n",
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert calls == [
        ("create", None),
        ("stream", None),
        (
            "delete",
            {
                "base_url": "http://127.0.0.1:8765",
                "session_id": "sess-repl",
                "headers": {},
            },
        ),
    ]


def test_daemon_tui_uses_local_http_control_plane_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []
    fake_consumer = object()

    class FakeTUI:
        def __init__(self, *, model_name: str, max_steps: int) -> None:
            calls.append(
                (
                    "tui-init",
                    {
                        "model_name": model_name,
                        "max_steps": max_steps,
                    },
                )
            )
            self.consumer = fake_consumer

        def __enter__(self) -> FakeTUI:
            calls.append(("tui-enter", None))
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            calls.append(("tui-exit", None))

        def add_user_message(self, content: str) -> None:
            calls.append(("tui-user", content))

    def fake_create_local_daemon_session(endpoint, *, repo_path, approval_policy):
        calls.append(
            (
                "create",
                {
                    "endpoint": endpoint,
                    "repo_path": repo_path,
                    "approval_policy": approval_policy,
                },
            )
        )
        return "sess-tui"

    def fake_stream_prompt_or_run_request(
        *,
        base_url: str,
        session_id: str,
        prompt: str,
        headers: dict[str, str],
        display_consumer: object | None = None,
    ) -> int:
        calls.append(
            (
                "stream",
                {
                    "base_url": base_url,
                    "session_id": session_id,
                    "prompt": prompt,
                    "headers": headers,
                    "display_consumer": display_consumer,
                },
            )
        )
        return 0

    monkeypatch.setattr(
        "coding_agent.remote.client.create_local_daemon_session",
        fake_create_local_daemon_session,
    )
    monkeypatch.setattr(
        "coding_agent.remote.client.stream_prompt_or_run_request",
        fake_stream_prompt_or_run_request,
    )
    monkeypatch.setattr("coding_agent.ui.rich_tui.CodingAgentTUI", FakeTUI)
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "daemon",
            "tui",
            "--url",
            "http://127.0.0.1:8765",
            "--repo",
            str(tmp_path),
            "--goal",
            "hello tui",
            "--approval",
            "auto",
            "--max-steps",
            "12",
            "--model",
            "test-model",
            "--keep-session",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "Created daemon-backed local TUI session sess-tui" in result.output
    assert calls[0] == (
        "tui-init",
        {
            "model_name": "test-model",
            "max_steps": 12,
        },
    )
    assert calls[1] == ("tui-enter", None)
    assert calls[2] == ("tui-user", "hello tui")
    assert calls[3][0] == "create"
    create_payload = calls[3][1]
    assert isinstance(create_payload, dict)
    endpoint = create_payload["endpoint"]
    assert endpoint.name == "local-daemon"
    assert endpoint.url == "http://127.0.0.1:8765"
    assert create_payload["repo_path"] == tmp_path.resolve()
    assert create_payload["approval_policy"] == "auto"
    assert calls[4][0] == "stream"
    stream_payload = calls[4][1]
    assert isinstance(stream_payload, dict)
    assert stream_payload["base_url"] == "http://127.0.0.1:8765"
    assert stream_payload["session_id"] == "sess-tui"
    assert stream_payload["prompt"] == "hello tui"
    assert stream_payload["headers"] == {}
    assert stream_payload["display_consumer"] is fake_consumer
    assert calls[5] == ("tui-exit", None)


def test_serve_config_uses_server_host_and_port_when_cli_omits_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "server.toml"
    config_path.write_text(
        "\n".join(
            [
                "[agent]",
                'name = "test-agent"',
                'model = "test-model"',
                'provider = "openai"',
                "",
                "[server]",
                'host = "0.0.0.0"',
                "port = 9000",
                'bearer_token = "test-token"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_uvicorn_run(app: object, *, host: str, port: int) -> None:
        del app
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("uvicorn.run", fake_uvicorn_run)
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["serve", "--config", str(config_path)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert captured == {"host": "0.0.0.0", "port": 9000}


def test_serve_config_rejects_boolean_port(tmp_path: Path) -> None:
    config_path = tmp_path / "server.toml"
    config_path.write_text(
        "\n".join(
            [
                "[agent]",
                'name = "test-agent"',
                'model = "test-model"',
                'provider = "openai"',
                "",
                "[server]",
                "port = true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["serve", "--config", str(config_path)],
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "server.port must be a positive integer" in result.output


def test_remote_repl_help_describes_one_shot_remote_run() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["remote", "repl", "--help"], catch_exceptions=False)
    normalized_output = " ".join(result.output.split())

    assert result.exit_code == 0
    assert "one-shot remote run" in result.output
    assert "download the final remote workspace into it" in normalized_output
    assert "--download" not in result.output


def test_attach_help_describes_single_prompt_attach() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["attach", "--help"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "Send one prompt to an existing remote session" in result.output


def test_remote_add_list_remove_manage_named_endpoint(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()

    add = runner.invoke(
        main,
        [
            "remote",
            "add",
            "dev",
            "http://127.0.0.1:8080/",
            "--token",
            "secret-token",
        ],
        catch_exceptions=False,
    )

    assert add.exit_code == 0
    assert "Added remote dev" in add.output
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved == {
        "remotes": {
            "dev": {
                "url": "http://127.0.0.1:8080",
                "token": "secret-token",
            }
        }
    }

    listed = runner.invoke(main, ["remote", "list"], catch_exceptions=False)

    assert listed.exit_code == 0
    assert "dev" in listed.output
    assert "http://127.0.0.1:8080" in listed.output
    assert "secret-token" not in listed.output

    removed = runner.invoke(main, ["remote", "remove", "dev"], catch_exceptions=False)

    assert removed.exit_code == 0
    assert "Removed remote dev" in removed.output
    assert json.loads(config_path.read_text(encoding="utf-8")) == {"remotes": {}}


def test_remote_add_persists_admin_token_options_and_list_redacts_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()

    direct = runner.invoke(
        main,
        [
            "remote",
            "add",
            "direct",
            "http://agent.example",
            "--token",
            "user-token",
            "--admin-token",
            " admin-secret ",
        ],
        catch_exceptions=False,
    )
    env = runner.invoke(
        main,
        [
            "remote",
            "add",
            "env",
            "http://agent-env.example",
            "--admin-token-env",
            " REMOTE_ADMIN_TOKEN ",
        ],
        catch_exceptions=False,
    )

    assert direct.exit_code == 0
    assert env.exit_code == 0
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved == {
        "remotes": {
            "direct": {
                "url": "http://agent.example",
                "token": "user-token",
                "admin_token": "admin-secret",
            },
            "env": {
                "url": "http://agent-env.example",
                "admin_token_env": "REMOTE_ADMIN_TOKEN",
            },
        }
    }

    listed = runner.invoke(main, ["remote", "list"], catch_exceptions=False)

    assert listed.exit_code == 0
    assert "direct" in listed.output
    assert "env" in listed.output
    assert "user-auth=token" in listed.output
    assert "user-auth=no-token" in listed.output
    assert "admin-auth=admin-token" in listed.output
    assert "admin-auth=admin-token-env" in listed.output
    assert "user-token" not in listed.output
    assert "admin-secret" not in listed.output
    assert "REMOTE_ADMIN_TOKEN" not in listed.output


def test_remote_add_rejects_conflicting_admin_token_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "remote",
            "add",
            "dev",
            "http://agent.example",
            "--admin-token",
            "admin-secret",
            "--admin-token-env",
            "REMOTE_ADMIN_TOKEN",
        ],
    )

    assert result.exit_code != 0
    assert "Pass either --admin-token or --admin-token-env, not both." in result.output
    assert not config_path.exists()


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("--admin-token", "", "Admin token must not be blank."),
        ("--admin-token", "   ", "Admin token must not be blank."),
        (
            "--admin-token-env",
            "",
            "Admin token environment variable must not be blank.",
        ),
        (
            "--admin-token-env",
            "   ",
            "Admin token environment variable must not be blank.",
        ),
    ],
)
def test_remote_add_rejects_blank_admin_token_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    option: str,
    value: str,
    message: str,
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "remote",
            "add",
            "dev",
            "http://agent.example",
            option,
            value,
        ],
    )

    assert result.exit_code != 0
    assert message in result.output
    assert not config_path.exists()


def test_remote_local_run_uses_external_worker_binding(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    calls: list[dict[str, object]] = []

    async def fake_run_local_attached_executor_once(**kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(
        "coding_agent.remote.worker.run_local_attached_executor_once",
        fake_run_local_attached_executor_once,
    )
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )

    result = runner.invoke(
        main,
        [
            "remote",
            "local-run",
            "dev",
            "--repo",
            str(repo_path),
            "--goal",
            "do local work",
            "--approval",
            "yolo",
            "--max-steps",
            "7",
            "--worker-id",
            "worker-test",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert calls == [
        {
            "base_url": "http://agent.example",
            "headers": {"Authorization": "Bearer secret-token"},
            "repo_path": repo_path.resolve(),
            "goal": "do local work",
            "approval_policy": "yolo",
            "provider_name": None,
            "model_name": None,
            "base_url_override": None,
            "max_steps": 7,
            "worker_id": "worker-test",
        }
    ]


@pytest.mark.asyncio
async def test_attached_executor_client_creates_local_attached_session(
    tmp_path: Path,
) -> None:
    from coding_agent.remote import worker as remote_worker

    requests: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "json": json.loads(request.content.decode("utf-8")),
            }
        )
        return httpx.Response(200, json={"session_id": "sess-local-attached"})

    async with httpx.AsyncClient(
        base_url="http://agent.example",
        transport=httpx.MockTransport(handler),
    ) as client:
        session_id = await remote_worker._create_attached_executor_session(
            client=client,
            repo_path=tmp_path,
            approval_policy="yolo",
            provider_name=None,
            model_name=None,
            base_url_override=None,
            max_steps=7,
            worker_id="executor-1",
        )

    assert session_id == "sess-local-attached"
    assert requests == [
        {
            "method": "POST",
            "path": "/sessions",
            "json": {
                "approval_policy": "yolo",
                "max_steps": 7,
                "run_target": {
                    "workspace": {
                        "kind": "external_worker_ref",
                        "ref": {
                            "kind": "local_path",
                            "display_path": str(tmp_path),
                        },
                        "provider_instance_id": "executor-1",
                    },
                    "executor": {
                        "kind": "local_attached",
                        "executor_kind": "local_cli",
                        "worker_pool": "default",
                    },
                    "isolation": {"kind": "external_worker_policy"},
                    "constraints": {},
                    "annotations": {},
                },
            },
        }
    ]


@pytest.mark.asyncio
async def test_attached_executor_client_claims_via_executor_endpoint(
    tmp_path: Path,
) -> None:
    from coding_agent.remote import worker as remote_worker

    requests: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "json": json.loads(request.content.decode("utf-8")),
            }
        )
        return httpx.Response(
            200,
            json={
                "run_id": "run-1",
                "session_id": "sess-1",
                "claim_token": "claim-token",
                "prompt": "hello",
                "approval_policy": "yolo",
                "max_steps": 7,
            },
        )

    async with httpx.AsyncClient(
        base_url="http://agent.example",
        transport=httpx.MockTransport(handler),
    ) as client:
        claim = await remote_worker._claim_run(
            client=client,
            session_id="sess-1",
            worker_id="executor-1",
            worker_instance_id="executor-1:instance",
            repo_path=tmp_path,
        )

    assert claim is not None
    assert claim["run_id"] == "run-1"
    assert requests[0]["method"] == "POST"
    assert requests[0]["path"] == "/executor/runs/claim"
    payload = requests[0]["json"]
    assert isinstance(payload, dict)
    assert payload["executor_id"] == "executor-1"
    assert payload["executor_kind"] == "local_cli"
    assert payload["session_id"] == "sess-1"


@pytest.mark.asyncio
async def test_attached_executor_records_agent_phase_span(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coding_agent.adapter.types import StopReason, TurnOutcome
    from coding_agent.remote import worker as remote_worker

    sink = RecordingObservationSink()
    completed_requests: list[dict[str, object]] = []

    class FakeTape:
        tape_id = "tape-1"

        def to_list(self) -> list[dict[str, object]]:
            return [{"kind": "message"}]

    class FakeContext:
        tape = FakeTape()
        config = {"observation_sink": sink}

    class FakePipelineAdapter:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        async def run_turn(self, prompt: str) -> TurnOutcome:
            assert prompt == "private prompt should not be traced"
            return TurnOutcome(
                stop_reason=StopReason.NO_TOOL_CALLS,
                final_message="done",
                steps_taken=2,
            )

    def fake_create_agent(**kwargs: object) -> tuple[object, FakeContext]:
        assert kwargs["session_id_override"] == "sess-1"
        assert kwargs["run_id_override"] == "run-1"
        return object(), FakeContext()

    async def fake_heartbeat_until_complete(**kwargs: object) -> None:
        del kwargs
        await asyncio.sleep(0)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/executor/runs/run-1/complete"
        completed_requests.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={})

    monkeypatch.setattr("coding_agent.remote.worker.create_agent", fake_create_agent)
    monkeypatch.setattr(
        "coding_agent.remote.worker.PipelineAdapter",
        FakePipelineAdapter,
    )
    monkeypatch.setattr(
        "coding_agent.remote.worker._heartbeat_until_complete",
        fake_heartbeat_until_complete,
    )

    async with httpx.AsyncClient(
        base_url="http://agent.example",
        transport=httpx.MockTransport(handler),
    ) as client:
        status = await remote_worker._execute_claimed_run(
            client=client,
            claim={
                "run_id": "run-1",
                "session_id": "sess-1",
                "claim_token": "claim-token",
                "prompt": "private prompt should not be traced",
                "approval_policy": "yolo",
                "max_steps": 7,
            },
            repo_path=tmp_path,
            worker_id="executor-1",
            worker_instance_id="executor-1:instance",
        )

    assert status == 0
    assert completed_requests[0]["status"] == "completed"
    assert [span.name for span in sink.spans] == ["remote.workspace.agent_phase"]
    assert sink.spans[0].status == "ok"
    assert sink.spans[0].attributes == {
        "session_id": "sess-1",
        "run_id": "run-1",
        "executor_kind": "local",
        "workspace_ref_kind": "local_path",
        "remote_status": "completed",
    }
    assert "private prompt should not be traced" not in str(sink.spans[0].attributes)


def test_remote_worker_runs_external_worker_loop(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "remotes.json"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    calls: list[dict[str, object]] = []

    async def fake_run_attached_executor_loop(**kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(
        "coding_agent.remote.worker.run_attached_executor_loop",
        fake_run_attached_executor_loop,
    )
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )

    result = runner.invoke(
        main,
        [
            "remote",
            "worker",
            "dev",
            "--repo",
            str(repo_path),
            "--worker-id",
            "worker-test",
            "--once",
            "--poll-interval",
            "0.5",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert calls == [
        {
            "base_url": "http://agent.example",
            "headers": {"Authorization": "Bearer secret-token"},
            "repo_path": repo_path.resolve(),
            "worker_id": "worker-test",
            "once": True,
            "poll_interval_seconds": 0.5,
        }
    ]


def test_remote_executor_alias_runs_existing_attached_executor_loop(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    calls: list[dict[str, object]] = []

    async def fake_run_attached_executor_loop(**kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(
        "coding_agent.remote.worker.run_attached_executor_loop",
        fake_run_attached_executor_loop,
    )
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )

    result = runner.invoke(
        main,
        [
            "remote",
            "executor",
            "dev",
            "--repo",
            str(repo_path),
            "--executor-id",
            "executor-test",
            "--once",
            "--poll-interval",
            "0.5",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert calls == [
        {
            "base_url": "http://agent.example",
            "headers": {"Authorization": "Bearer secret-token"},
            "repo_path": repo_path.resolve(),
            "worker_id": "executor-test",
            "once": True,
            "poll_interval_seconds": 0.5,
        }
    ]


def test_remote_prompt_streams_existing_external_worker_session(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    calls: list[dict[str, object]] = []

    def fake_stream_prompt_or_run_request(**kwargs: object) -> int:
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(
        "coding_agent.remote.client.stream_prompt_or_run_request",
        fake_stream_prompt_or_run_request,
    )
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )

    result = runner.invoke(
        main,
        ["remote", "prompt", "dev", "sess-1", "--goal", "continue work"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert calls == [
        {
            "base_url": "http://agent.example",
            "session_id": "sess-1",
            "prompt": "continue work",
            "headers": {"Authorization": "Bearer secret-token"},
        }
    ]


def test_remote_resume_streams_existing_session(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    calls: list[dict[str, object]] = []

    def fake_stream_resume_or_run_request(**kwargs: object) -> int:
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(
        "coding_agent.remote.client.stream_resume_or_run_request",
        fake_stream_resume_or_run_request,
    )
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )

    result = runner.invoke(
        main,
        [
            "remote",
            "resume",
            "dev",
            "--session",
            "sess-1",
            "--prompt",
            "continue work",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert calls == [
        {
            "base_url": "http://agent.example",
            "session_id": "sess-1",
            "prompt": "continue work",
            "headers": {"Authorization": "Bearer secret-token"},
        }
    ]


def test_remote_attach_consumes_existing_session_events(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    calls: list[dict[str, object]] = []

    def fake_attach_remote_session(**kwargs: object) -> int:
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(
        "coding_agent.remote.client.attach_remote_session",
        fake_attach_remote_session,
    )
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )

    result = runner.invoke(
        main,
        ["remote", "attach", "dev", "sess-1"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert calls == [
        {
            "base_url": "http://agent.example",
            "session_id": "sess-1",
            "headers": {"Authorization": "Bearer secret-token"},
        }
    ]


def test_remote_attach_uses_display_event_stream(monkeypatch, capsys) -> None:
    calls: list[tuple[str, str, str]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class FakeEventSource:
        response = FakeResponse()

        def __enter__(self) -> FakeEventSource:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def iter_sse(self):
            yield type(
                "SSE",
                (),
                {
                    "event": "assistant_text_delta",
                    "data": json.dumps(
                        {
                            "source_event_id": "live:sess-1:event-1",
                            "run_id": "run-1",
                            "sequence": None,
                            "display_kind": "assistant_text_delta",
                            "payload": {"content": "hello"},
                            "created_at": "2026-06-03T00:00:00+00:00",
                        }
                    ),
                },
            )()
            yield type(
                "SSE",
                (),
                {
                    "event": "final_result",
                    "data": json.dumps(
                        {
                            "source_event_id": "live:sess-1:event-2",
                            "run_id": "run-1",
                            "sequence": None,
                            "display_kind": "final_result",
                            "payload": {"completion_status": "completed"},
                            "created_at": "2026-06-03T00:00:01+00:00",
                        }
                    ),
                },
            )()

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            self.base_url = str(kwargs["base_url"])

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    def fake_connect_sse(client, method: str, path: str, **kwargs: object):
        del kwargs
        calls.append((client.base_url, method, path))
        return FakeEventSource()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr("coding_agent.remote.client.connect_sse", fake_connect_sse)

    from coding_agent.remote.client import attach_remote_session

    status = attach_remote_session(
        base_url="http://agent.example",
        session_id="sess-1",
        headers={},
    )

    assert status == 0
    assert calls == [("http://agent.example", "GET", "/sessions/sess-1/display-events")]
    assert capsys.readouterr().out == "hello\n"


def test_remote_workers_lists_external_worker_status(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))

    def fake_list_remote_executors(endpoint):
        assert endpoint.name == "dev"
        return [
            {
                "executor_id": "worker-1",
                "status": "running",
                "executor_kind": "local_cli",
                "current_run_id": "run-1",
                "last_seen_at": "2026-05-31T12:00:00+00:00",
            }
        ]

    monkeypatch.setattr(
        "coding_agent.remote.client.list_remote_executors",
        fake_list_remote_executors,
    )
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )

    result = runner.invoke(
        main,
        ["remote", "workers", "dev"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "worker-1\trunning\tlocal_cli\trun-1\t2026-05-31T12:00:00+00:00" in (
        result.output
    )


def test_remote_executors_alias_lists_existing_executor_status(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))

    def fake_list_remote_executors(endpoint):
        assert endpoint.name == "dev"
        return [
            {
                "executor_id": "executor-1",
                "status": "running",
                "executor_kind": "local_cli",
                "current_run_id": "run-1",
                "last_seen_at": "2026-05-31T12:00:00+00:00",
            }
        ]

    monkeypatch.setattr(
        "coding_agent.remote.client.list_remote_executors",
        fake_list_remote_executors,
    )
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )

    result = runner.invoke(
        main,
        ["remote", "executors", "dev"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "executor-1\trunning\tlocal_cli\trun-1\t2026-05-31T12:00:00+00:00" in (
        result.output
    )


def test_remote_interactions_list_and_resolve(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))

    def fake_list_remote_interactions(endpoint, *, session_id, run_id, status):
        assert endpoint.name == "dev"
        assert session_id == "sess-1"
        assert run_id is None
        assert status == "pending"
        return [
            {
                "interaction_id": "run-1:approval-1",
                "status": "pending",
                "interaction_kind": "approval",
                "run_id": "run-1",
                "created_at": "2026-05-31T12:00:00+00:00",
                "metadata": {"request_id": "approval-1"},
            }
        ]

    def fake_resolve_remote_interaction(
        endpoint,
        interaction_id,
        *,
        approved,
        feedback,
        scope,
    ):
        assert endpoint.name == "dev"
        assert interaction_id == "run-1:approval-1"
        assert approved is True
        assert feedback == "ok"
        assert scope == "once"
        return {
            "interaction_id": interaction_id,
            "status": "approved",
        }

    monkeypatch.setattr(
        "coding_agent.remote.client.list_remote_interactions",
        fake_list_remote_interactions,
    )
    monkeypatch.setattr(
        "coding_agent.remote.client.resolve_remote_interaction",
        fake_resolve_remote_interaction,
    )
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )

    list_result = runner.invoke(
        main,
        [
            "remote",
            "interactions",
            "dev",
            "--session",
            "sess-1",
            "--status",
            "pending",
        ],
        catch_exceptions=False,
    )
    resolve_result = runner.invoke(
        main,
        [
            "remote",
            "resolve-interaction",
            "dev",
            "run-1:approval-1",
            "--approve",
            "--feedback",
            "ok",
        ],
        catch_exceptions=False,
    )

    assert list_result.exit_code == 0
    assert (
        "run-1:approval-1\tpending\tapproval\tapproval-1\trun-1\t"
        "2026-05-31T12:00:00+00:00"
    ) in list_result.output
    assert resolve_result.exit_code == 0
    assert "status: approved" in resolve_result.output


def test_remote_runs_lists_session_runs(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))

    def fake_list_remote_session_runs(endpoint, session_id: str):
        assert endpoint.name == "dev"
        assert session_id == "sess-1"
        return [
            {
                "run_id": "run-1",
                "status": "running",
                "tape_id": "tape-1",
                "metadata": {"executor_id": "executor-1", "worker_id": "worker-1"},
            }
        ]

    monkeypatch.setattr(
        "coding_agent.remote.client.list_remote_session_runs",
        fake_list_remote_session_runs,
    )
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )

    result = runner.invoke(
        main,
        ["remote", "runs", "dev", "--session", "sess-1"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "run-1\trunning\texecutor-1\ttape-1" in result.output


def test_list_remote_run_display_events_uses_display_replay_endpoint(
    monkeypatch,
) -> None:
    from coding_agent.remote.client import (
        RemoteEndpoint,
        list_remote_run_display_events,
    )

    calls: list[tuple[str, dict[str, str]]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "run_id": "run-1",
                "events": [
                    {
                        "source_event_id": "event-1",
                        "run_id": "run-1",
                        "sequence": 1,
                        "display_kind": "assistant_text_delta",
                        "payload": {"content": "hello"},
                        "created_at": "2026-06-03T00:00:00+00:00",
                    }
                ],
            }

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def get(self, path: str) -> FakeResponse:
            calls.append((path, self.headers))
            return FakeResponse()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    events = list_remote_run_display_events(
        RemoteEndpoint("dev", "http://agent.example", "secret-token"),
        "run-1",
    )

    assert calls == [
        (
            "/runs/run-1/display-events",
            {"Authorization": "Bearer secret-token"},
        )
    ]
    assert events == [
        {
            "source_event_id": "event-1",
            "run_id": "run-1",
            "sequence": 1,
            "display_kind": "assistant_text_delta",
            "payload": {"content": "hello"},
            "created_at": "2026-06-03T00:00:00+00:00",
        }
    ]


def test_remote_events_lists_display_events(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))

    def fake_list_remote_run_display_events(endpoint, run_id: str):
        assert endpoint.name == "dev"
        assert run_id == "run-1"
        return [
            {
                "sequence": 7,
                "display_kind": "assistant_text_delta",
                "source_event_id": "event-7",
                "created_at": "2026-06-03T00:00:00+00:00",
            }
        ]

    monkeypatch.setattr(
        "coding_agent.remote.client.list_remote_run_display_events",
        fake_list_remote_run_display_events,
    )
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )

    result = runner.invoke(
        main,
        ["remote", "events", "dev", "--run", "run-1"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "7\tassistant_text_delta\tevent-7\t2026-06-03T00:00:00+00:00" in (
        result.output
    )


def test_remote_repl_creates_cloud_session_and_streams_prompt_events(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )
    calls: list[tuple[str, str, dict[str, object] | None, dict[str, str]]] = []

    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

    class FakeClient:
        def __init__(
            self,
            *,
            base_url: str,
            headers: dict[str, str] | None = None,
            timeout: float,
        ) -> None:
            self.base_url = base_url
            self.headers = headers or {}
            self.timeout = timeout

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            calls.append(("post", path, json, self.headers))
            if path == "/sessions":
                return FakeResponse({"session_id": "sess-123"})
            raise AssertionError(f"unexpected post {path}")

        def delete(self, path: str) -> FakeResponse:
            calls.append(("delete", path, None, self.headers))
            return FakeResponse({"status": "closed"})

    def fake_stream_prompt(
        *, base_url: str, session_id: str, prompt: str, headers: dict[str, str]
    ) -> int:
        calls.append(
            (
                "stream",
                f"/sessions/{session_id}/prompt",
                {"prompt": prompt, "base_url": base_url},
                headers,
            )
        )
        return 0

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr("coding_agent.remote.client.stream_prompt", fake_stream_prompt)

    result = runner.invoke(
        main,
        ["remote", "repl", "dev", "--empty-workspace", "--goal", "hello"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "Created one-shot remote session sess-123 on remote dev" in result.output
    assert "Cleaned up remote session sess-123" in result.output
    assert calls == [
        (
            "post",
            "/sessions",
            {"workspace_source": {"kind": "docker"}, "approval_policy": "auto"},
            {"Authorization": "Bearer secret-token"},
        ),
        (
            "stream",
            "/sessions/sess-123/prompt",
            {"prompt": "hello", "base_url": "http://agent.example"},
            {"Authorization": "Bearer secret-token"},
        ),
        (
            "delete",
            "/sessions/sess-123",
            None,
            {"Authorization": "Bearer secret-token"},
        ),
    ]


def test_remote_run_creates_one_shot_remote_session(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )
    calls: list[tuple[str, str, dict[str, object] | None, dict[str, str]]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"session_id": "sess-run"}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            calls.append(("post", path, json, self.headers))
            return FakeResponse()

        def delete(self, path: str) -> FakeResponse:
            calls.append(("delete", path, None, self.headers))
            return FakeResponse()

    def fake_stream_prompt(
        *, base_url: str, session_id: str, prompt: str, headers: dict[str, str]
    ) -> int:
        calls.append(
            (
                "stream",
                f"/sessions/{session_id}/prompt",
                {"prompt": prompt, "base_url": base_url},
                headers,
            )
        )
        return 0

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr("coding_agent.remote.client.stream_prompt", fake_stream_prompt)

    result = runner.invoke(
        main,
        ["remote", "run", "dev", "--empty-workspace", "--goal", "hello"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "Created one-shot remote session sess-run on remote dev" in result.output
    assert "Remote session sess-run left open for result inspection." in result.output
    assert "coding_agent remote result dev --session sess-run" in result.output
    assert "coding_agent remote diff dev --session sess-run" not in result.output
    assert "coding_agent remote patch dev --session sess-run" not in result.output
    assert "coding_agent remote publish dev" not in result.output
    assert "coding_agent remote sessions close dev sess-run" in result.output
    assert calls == [
        (
            "post",
            "/sessions",
            {"workspace_source": {"kind": "docker"}, "approval_policy": "auto"},
            {"Authorization": "Bearer secret-token"},
        ),
        (
            "stream",
            "/sessions/sess-run/prompt",
            {"prompt": "hello", "base_url": "http://agent.example"},
            {"Authorization": "Bearer secret-token"},
        ),
    ]


def test_remote_run_sends_runtime_profile_in_workspace_source(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )
    calls: list[tuple[str, str, dict[str, object] | None, dict[str, str]]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"session_id": "sess-run"}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            calls.append(("post", path, json, self.headers))
            return FakeResponse()

        def delete(self, path: str) -> FakeResponse:
            calls.append(("delete", path, None, self.headers))
            return FakeResponse()

    def fake_stream_prompt(
        *, base_url: str, session_id: str, prompt: str, headers: dict[str, str]
    ) -> int:
        calls.append(
            (
                "stream",
                f"/sessions/{session_id}/prompt",
                {"prompt": prompt, "base_url": base_url},
                headers,
            )
        )
        return 0

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr("coding_agent.remote.client.stream_prompt", fake_stream_prompt)

    result = runner.invoke(
        main,
        [
            "remote",
            "run",
            "dev",
            "--empty-workspace",
            "--runtime",
            "universal",
            "--goal",
            "hello",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert calls[0] == (
        "post",
        "/sessions",
        {
            "workspace_source": {
                "kind": "docker",
                "runtime_profile": "universal",
            },
            "approval_policy": "auto",
        },
        {"Authorization": "Bearer secret-token"},
    )


def test_remote_run_sends_configured_approval_policy(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )
    calls: list[tuple[str, str, dict[str, object] | None, dict[str, str]]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"session_id": "sess-run"}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            calls.append(("post", path, json, self.headers))
            return FakeResponse()

        def delete(self, path: str) -> FakeResponse:
            calls.append(("delete", path, None, self.headers))
            return FakeResponse()

    def fake_stream_prompt(
        *, base_url: str, session_id: str, prompt: str, headers: dict[str, str]
    ) -> int:
        calls.append(
            (
                "stream",
                f"/sessions/{session_id}/prompt",
                {"prompt": prompt, "base_url": base_url},
                headers,
            )
        )
        return 0

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr("coding_agent.remote.client.stream_prompt", fake_stream_prompt)

    result = runner.invoke(
        main,
        [
            "remote",
            "run",
            "dev",
            "--empty-workspace",
            "--goal",
            "hello",
            "--approval",
            "yolo",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert calls[0] == (
        "post",
        "/sessions",
        {"workspace_source": {"kind": "docker"}, "approval_policy": "yolo"},
        {"Authorization": "Bearer secret-token"},
    )


def test_attach_streams_prompt_to_existing_session(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example"],
        catch_exceptions=False,
    )
    calls: list[dict[str, object]] = []

    def fake_attach_stream_prompt_or_run_request(
        *, base_url: str, session_id: str, prompt: str, headers: dict[str, str]
    ) -> int:
        calls.append(
            {
                "base_url": base_url,
                "session_id": session_id,
                "prompt": prompt,
                "headers": headers,
            }
        )
        return 0

    monkeypatch.setattr(
        "coding_agent.remote.client.stream_prompt_or_run_request",
        fake_attach_stream_prompt_or_run_request,
    )

    result = runner.invoke(
        main,
        ["attach", "dev", "--session", "sess-456", "--goal", "continue"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert calls == [
        {
            "base_url": "http://agent.example",
            "session_id": "sess-456",
            "prompt": "continue",
            "headers": {},
        }
    ]


def test_remote_sessions_commands_call_operations_api(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )
    calls: list[tuple[str, str, dict[str, str]]] = []

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def get(self, path: str) -> _RemoteFakeResponse:
            calls.append(("get", path, self.headers))
            if path == "/sessions":
                return _RemoteFakeResponse(
                    {
                        "sessions": [
                            {
                                "session_id": "sess-1",
                                "status": "running",
                                "turn_status": "running",
                                "workspace_id": "ws-1",
                                "last_run_status": "interrupted",
                                "resumable": True,
                                "latest_checkpoint_id": "cp-1",
                            }
                        ]
                    }
                )
            if path == "/sessions/sess-1":
                return _RemoteFakeResponse(
                    {
                        "session_id": "sess-1",
                        "status": "running",
                        "turn_status": "idle",
                        "origin": "remote",
                        "workspace_id": "ws-1",
                    }
                )
            raise AssertionError(f"unexpected get {path}")

        def post(self, path: str) -> _RemoteFakeResponse:
            calls.append(("post", path, self.headers))
            assert path == "/sessions/sess-1/cancel"
            return _RemoteFakeResponse(
                {"session_id": "sess-1", "turn_id": "turn-1", "status": "cancelling"}
            )

        def delete(self, path: str) -> _RemoteFakeResponse:
            calls.append(("delete", path, self.headers))
            assert path == "/sessions/sess-1"
            return _RemoteFakeResponse({"status": "closed"})

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    listed = runner.invoke(
        main, ["remote", "sessions", "list", "dev"], catch_exceptions=False
    )
    status = runner.invoke(
        main,
        ["remote", "sessions", "status", "dev", "sess-1"],
        catch_exceptions=False,
    )
    cancelled = runner.invoke(
        main,
        ["remote", "sessions", "cancel", "dev", "sess-1"],
        catch_exceptions=False,
    )
    closed = runner.invoke(
        main,
        ["remote", "sessions", "close", "dev", "sess-1"],
        catch_exceptions=False,
    )

    assert listed.exit_code == 0
    assert "sess-1\trunning\trunning\tws-1\tinterrupted\tresumable\tcp-1" in (
        listed.output
    )
    assert status.exit_code == 0
    assert "session_id: sess-1" in status.output
    assert "workspace_id: ws-1" in status.output
    assert cancelled.exit_code == 0
    assert "Cancelling remote session sess-1 turn turn-1" in cancelled.output
    assert closed.exit_code == 0
    assert "Closed remote session sess-1" in closed.output
    assert calls == [
        ("get", "/sessions", {"Authorization": "Bearer secret-token"}),
        ("get", "/sessions/sess-1", {"Authorization": "Bearer secret-token"}),
        ("post", "/sessions/sess-1/cancel", {"Authorization": "Bearer secret-token"}),
        ("delete", "/sessions/sess-1", {"Authorization": "Bearer secret-token"}),
    ]


def test_remote_memory_status_prints_semantic_memory_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        [
            "remote",
            "add",
            "dev",
            "http://agent.example",
            "--token",
            "user-token",
        ],
        catch_exceptions=False,
    )
    calls: list[tuple[str, str, dict[str, str]]] = []

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def get(self, path: str) -> _RemoteFakeResponse:
            calls.append(("get", path, self.headers))
            assert path == "/sessions/sess-1/memory/semantic/status"
            return _RemoteFakeResponse(
                {
                    "document_count": 3,
                    "reviewed_memory_count": 4,
                    "accepted_reviewed_memory_count": 2,
                    "topic_store_available": True,
                }
            )

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    result = runner.invoke(
        main,
        ["remote", "memory", "status", "dev", "--session", "sess-1"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "document_count: 3" in result.output
    assert "reviewed_memory_count: 4" in result.output
    assert "accepted_reviewed_memory_count: 2" in result.output
    assert "topic_store_available: True" in result.output
    assert calls == [
        (
            "get",
            "/sessions/sess-1/memory/semantic/status",
            {"Authorization": "Bearer user-token"},
        )
    ]


def test_remote_memory_rebuild_requires_confirm_before_http_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )
    calls: list[str] = []

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            del kwargs
            calls.append("http-client-created")

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    result = runner.invoke(
        main,
        ["remote", "memory", "rebuild", "dev", "--session", "sess-1"],
    )
    allow_rebuild_result = runner.invoke(
        main,
        [
            "remote",
            "memory",
            "rebuild",
            "dev",
            "--session",
            "sess-1",
            "--allow-rebuild",
        ],
    )

    assert result.exit_code != 0
    assert "Pass --confirm to rebuild semantic memory." in result.output
    assert allow_rebuild_result.exit_code != 0
    assert "Pass --confirm to rebuild semantic memory." in allow_rebuild_result.output
    assert calls == []


def test_remote_memory_rebuild_posts_confirmed_payload_and_prints_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    monkeypatch.setenv("REMOTE_ADMIN_TOKEN", "env-admin-token")
    runner = CliRunner()
    runner.invoke(
        main,
        [
            "remote",
            "add",
            "dev",
            "http://agent.example",
            "--token",
            "user-token",
            "--admin-token-env",
            "REMOTE_ADMIN_TOKEN",
        ],
        catch_exceptions=False,
    )
    calls: list[tuple[str, dict[str, object] | None, dict[str, str]]] = []

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> _RemoteFakeResponse:
            calls.append((path, json, self.headers))
            assert path == "/sessions/sess-1/memory/semantic/rebuild"
            return _RemoteFakeResponse(
                {
                    "topic_count": 5,
                    "reviewed_memory_count": 2,
                    "indexed_count": 6,
                    "skipped_count": 1,
                    "deleted_count": 1,
                    "indexed_ids": ["topic:1", "memory:1"],
                    "deleted_ids": ["stale:1"],
                }
            )

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    default_result = runner.invoke(
        main,
        [
            "remote",
            "memory",
            "rebuild",
            "dev",
            "--session",
            "sess-1",
            "--confirm",
        ],
        catch_exceptions=False,
    )
    custom_result = runner.invoke(
        main,
        [
            "remote",
            "memory",
            "rebuild",
            "dev",
            "--session",
            "sess-1",
            "--confirm",
            "--batch-size",
            "25",
            "--allow-rebuild",
        ],
        catch_exceptions=False,
    )

    assert default_result.exit_code == 0
    assert custom_result.exit_code == 0
    assert "indexed_count: 6" in custom_result.output
    assert "deleted_count: 1" in custom_result.output
    assert '"topic:1"' in custom_result.output
    assert '"stale:1"' in custom_result.output
    assert calls == [
        (
            "/sessions/sess-1/memory/semantic/rebuild",
            {"batch_size": 10, "allow_rebuild": False},
            {"Authorization": "Bearer env-admin-token"},
        ),
        (
            "/sessions/sess-1/memory/semantic/rebuild",
            {"batch_size": 25, "allow_rebuild": True},
            {"Authorization": "Bearer env-admin-token"},
        ),
    ]


def test_remote_workspaces_commands_call_admin_operations_api(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "ops", "http://agent.example", "--token", "admin-token"],
        catch_exceptions=False,
    )
    calls: list[tuple[str, str, dict[str, str], dict[str, object] | None]] = []

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def get(self, path: str) -> _RemoteFakeResponse:
            calls.append(("get", path, self.headers, None))
            if path == "/workspaces":
                return _RemoteFakeResponse(
                    {
                        "workspaces": [
                            {
                                "workspace_id": "ws-1",
                                "status": "stale",
                                "updated_at": "2026-05-10T11:00:00Z",
                            }
                        ]
                    }
                )
            assert path == "/workspaces/ws-1"
            return _RemoteFakeResponse(
                {
                    "workspace_id": "ws-1",
                    "status": "retained",
                    "session_id": "sess-1",
                    "provider": "docker",
                    "provider_instance_id": "docker-a",
                    "workspace_host_label": "docker-a.local",
                    "retention_policy": "pinned",
                    "is_local": True,
                    "updated_at": "2026-05-10T11:00:00Z",
                }
            )

        def post(
            self,
            path: str,
            json: dict[str, object] | None = None,
        ) -> _RemoteFakeResponse:
            calls.append(("post", path, self.headers, json))
            if path == "/workspaces/gc":
                return _RemoteFakeResponse({"cleaned_count": 2})
            if path == "/workspaces/ws-1/retain":
                assert json == {"retention_policy": "ttl", "ttl_seconds": 3600}
                return _RemoteFakeResponse(
                    {
                        "workspace_id": "ws-1",
                        "retention_policy": "ttl",
                        "ttl_seconds": 3600,
                        "status": "retained",
                    }
                )
            if path == "/workspaces/ws-1/pin":
                assert json is None
                return _RemoteFakeResponse(
                    {
                        "workspace_id": "ws-1",
                        "retention_policy": "pinned",
                        "ttl_seconds": None,
                        "status": "retained",
                    }
                )
            assert path == "/workspaces/ws-1/unpin"
            assert json == {"retention_policy": "delete_on_close"}
            return _RemoteFakeResponse(
                {
                    "workspace_id": "ws-1",
                    "retention_policy": "delete_on_close",
                    "ttl_seconds": None,
                    "status": "retained",
                }
            )

        def delete(self, path: str) -> _RemoteFakeResponse:
            calls.append(("delete", path, self.headers, None))
            assert path == "/workspaces/ws-1"
            return _RemoteFakeResponse({"workspace_id": "ws-1", "status": "cleaned"})

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    listed = runner.invoke(
        main, ["remote", "workspaces", "list", "ops"], catch_exceptions=False
    )
    status = runner.invoke(
        main, ["remote", "workspaces", "status", "ops", "ws-1"], catch_exceptions=False
    )
    retained = runner.invoke(
        main,
        ["remote", "workspaces", "retain", "ops", "ws-1", "--ttl", "3600"],
        catch_exceptions=False,
    )
    pinned = runner.invoke(
        main, ["remote", "workspaces", "pin", "ops", "ws-1"], catch_exceptions=False
    )
    unpinned = runner.invoke(
        main,
        ["remote", "workspaces", "unpin", "ops", "ws-1", "--policy", "delete_on_close"],
        catch_exceptions=False,
    )
    cleaned_stale = runner.invoke(
        main,
        ["remote", "workspaces", "cleanup", "ops", "--stale"],
        catch_exceptions=False,
    )
    removed = runner.invoke(
        main,
        ["remote", "workspaces", "rm", "ops", "ws-1"],
        catch_exceptions=False,
    )

    assert listed.exit_code == 0
    assert "ws-1\tstale\t2026-05-10T11:00:00Z" in listed.output
    assert status.exit_code == 0
    assert "workspace_id: ws-1" in status.output
    assert "retention_policy: pinned" in status.output
    assert retained.exit_code == 0
    assert "Workspace ws-1 retained as ttl" in retained.output
    assert pinned.exit_code == 0
    assert "Workspace ws-1 pinned" in pinned.output
    assert unpinned.exit_code == 0
    assert "Workspace ws-1 unpinned to delete_on_close" in unpinned.output
    assert cleaned_stale.exit_code == 0
    assert "Cleaned 2 stale workspaces" in cleaned_stale.output
    assert removed.exit_code == 0
    assert "Cleaned workspace ws-1" in removed.output
    assert calls == [
        ("get", "/workspaces", {"Authorization": "Bearer admin-token"}, None),
        ("get", "/workspaces/ws-1", {"Authorization": "Bearer admin-token"}, None),
        (
            "post",
            "/workspaces/ws-1/retain",
            {"Authorization": "Bearer admin-token"},
            {"retention_policy": "ttl", "ttl_seconds": 3600},
        ),
        (
            "post",
            "/workspaces/ws-1/pin",
            {"Authorization": "Bearer admin-token"},
            None,
        ),
        (
            "post",
            "/workspaces/ws-1/unpin",
            {"Authorization": "Bearer admin-token"},
            {"retention_policy": "delete_on_close"},
        ),
        ("post", "/workspaces/gc", {"Authorization": "Bearer admin-token"}, None),
        ("delete", "/workspaces/ws-1", {"Authorization": "Bearer admin-token"}, None),
    ]


def test_remote_download_fetches_manifest_and_confirms_before_overwrite(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )
    calls: list[tuple[str, str, dict[str, str]]] = []
    applied: list[tuple[Path, str]] = []

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def get(self, path: str) -> _RemoteFakeResponse:
            calls.append(("get", path, self.headers))
            if path == "/sessions/sess-1/workspace/archive/manifest":
                return _RemoteFakeResponse(
                    {
                        "workspace_id": "ws-1",
                        "session_id": "sess-1",
                        "format": "tar.gz",
                        "file_count": 2,
                        "total_bytes": 19,
                        "changed_files": ["a.txt", "src/app.py"],
                        "deleted_files": [],
                        "excluded_files": [".git"],
                        "archive_sha256": "a" * 64,
                    }
                )
            if path == "/sessions/sess-1/workspace/archive":
                return _RemoteFakeResponse({"archive_base64": "result-archive"})
            raise AssertionError(f"unexpected get {path}")

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr(
        "coding_agent.environment.archive.extract_workspace_archive_base64",
        lambda repo_path_arg, archive_base64: applied.append(
            (repo_path_arg, archive_base64)
        ),
    )

    result = runner.invoke(
        main,
        ["remote", "download", "dev", "--session", "sess-1", "--repo", str(repo_path)],
        input="y\n",
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert (
        "Remote snapshot contains 2 archived files, 0 deleted entries, 19 bytes"
        in result.output
    )
    assert "This will overwrite" in result.output
    assert "Downloaded remote workspace snapshot and overwrote" in result.output
    assert calls == [
        (
            "get",
            "/sessions/sess-1/workspace/archive/manifest",
            {"Authorization": "Bearer secret-token"},
        ),
        (
            "get",
            "/sessions/sess-1/workspace/archive",
            {"Authorization": "Bearer secret-token"},
        ),
    ]
    assert applied == [(repo_path, "result-archive")]


def test_remote_repl_reports_http_session_create_error(
    tmp_path: Path, monkeypatch
) -> None:
    import httpx

    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example"],
        catch_exceptions=False,
    )

    class FailingClient:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def __enter__(self) -> FailingClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> httpx.Response:
            del path, json
            request = httpx.Request("POST", "http://agent.example/sessions")
            return httpx.Response(
                400, json={"detail": "cloud workspace disabled"}, request=request
            )

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FailingClient)

    result = runner.invoke(
        main,
        ["remote", "repl", "dev", "--empty-workspace", "--goal", "hello"],
    )

    assert result.exit_code != 0
    assert "Failed to create remote session" in result.output
    assert "cloud workspace disabled" in result.output


def test_remote_repl_requires_repo_or_empty_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example"],
        catch_exceptions=False,
    )

    result = runner.invoke(
        main,
        ["remote", "repl", "dev", "--goal", "hello"],
    )

    assert result.exit_code != 0
    assert "Pass --repo to use a local repo or --empty-workspace" in result.output


def test_remote_diff_prints_changed_files(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )
    calls: list[tuple[str, dict[str, str]]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "workspace_id": "ws-123",
                "additions": 7,
                "deletions": 2,
                "files": [
                    {
                        "path": "src/app.py",
                        "status": "modified",
                        "old_path": None,
                        "additions": 2,
                        "deletions": 1,
                        "binary": False,
                    },
                    {
                        "path": "new.bin",
                        "status": "added",
                        "old_path": None,
                        "additions": None,
                        "deletions": None,
                        "binary": True,
                    },
                ],
            }

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def get(self, path: str) -> FakeResponse:
            calls.append((path, self.headers))
            return FakeResponse()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    result = runner.invoke(
        main,
        ["remote", "diff", "dev", "--session", "sess-123"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "Remote workspace ws-123: 2 files changed, +7/-2" in result.output
    assert "modified  src/app.py  +2/-1" in result.output
    assert "added     new.bin  binary" in result.output
    assert calls == [
        (
            "/sessions/sess-123/workspace/diff",
            {"Authorization": "Bearer secret-token"},
        )
    ]


def test_remote_diff_guides_snapshot_workspace_users(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )

    class FakeResponse:
        status_code = 400
        text = ""

        def raise_for_status(self) -> None:
            request = httpx.Request(
                "GET", "http://agent.example/sessions/sess-123/workspace/diff"
            )
            raise httpx.HTTPStatusError(
                "bad status",
                request=request,
                response=httpx.Response(
                    400,
                    request=request,
                    json={"detail": "workspace diff requires a Git workspace"},
                ),
            )

        def json(self) -> dict[str, object]:
            return {"detail": "workspace diff requires a Git workspace"}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def get(self, path: str) -> FakeResponse:
            assert path == "/sessions/sess-123/workspace/diff"
            return FakeResponse()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    result = runner.invoke(main, ["remote", "diff", "dev", "--session", "sess-123"])

    assert result.exit_code != 0
    assert "workspace diff requires a Git workspace" in result.output
    assert "snapshot fallback sessions do not support remote diff" in result.output
    assert "Use 'coding_agent remote result dev --session sess-123'" in result.output
    assert "Use 'coding_agent remote download dev --session sess-123'" in result.output


def test_remote_patch_prints_unified_diff(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )
    calls: list[tuple[str, dict[str, str]]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "workspace_id": "ws-123",
                "format": "unified_diff",
                "patch": "diff --git a/README.md b/README.md\n+hello\n",
            }

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def get(self, path: str) -> FakeResponse:
            calls.append((path, self.headers))
            return FakeResponse()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    result = runner.invoke(
        main,
        ["remote", "patch", "dev", "--session", "sess-123"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert result.output == "diff --git a/README.md b/README.md\n+hello\n"
    assert calls == [
        (
            "/sessions/sess-123/workspace/patch",
            {"Authorization": "Bearer secret-token"},
        )
    ]


def test_remote_patch_guides_snapshot_workspace_users(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )

    class FakeResponse:
        status_code = 400
        text = ""

        def raise_for_status(self) -> None:
            request = httpx.Request(
                "GET", "http://agent.example/sessions/sess-123/workspace/patch"
            )
            raise httpx.HTTPStatusError(
                "bad status",
                request=request,
                response=httpx.Response(
                    400,
                    request=request,
                    json={"detail": "workspace patch requires a Git workspace"},
                ),
            )

        def json(self) -> dict[str, object]:
            return {"detail": "workspace patch requires a Git workspace"}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def get(self, path: str) -> FakeResponse:
            assert path == "/sessions/sess-123/workspace/patch"
            return FakeResponse()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    result = runner.invoke(main, ["remote", "patch", "dev", "--session", "sess-123"])

    assert result.exit_code != 0
    assert "workspace patch requires a Git workspace" in result.output
    assert "snapshot fallback sessions do not support remote patch" in result.output
    assert "Use 'coding_agent remote result dev --session sess-123'" in result.output
    assert "Use 'coding_agent remote download dev --session sess-123'" in result.output


def test_remote_result_prints_session_result_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )
    calls: list[tuple[str, dict[str, str]]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "session_id": "sess-123",
                "status": "completed",
                "turn_status": "idle",
                "turn_id": "turn-123",
                "workspace_id": "ws-123",
                "origin": {"channel": "http"},
                "provider_name": "openai",
                "model_name": "result-model",
                "final_answer": "Fixed and verified.",
                "verification_summary": "Tool activity: shell_command: uv run pytest",
                "failure_details": None,
            }

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def get(self, path: str) -> FakeResponse:
            calls.append((path, self.headers))
            return FakeResponse()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    result = runner.invoke(
        main,
        ["remote", "result", "dev", "--session", "sess-123"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "Session: sess-123" in result.output
    assert "Status: completed" in result.output
    assert "Turn: idle" in result.output
    assert "Workspace: ws-123" in result.output
    assert "Provider: openai" in result.output
    assert "Model: result-model" in result.output
    assert "Final answer:\nFixed and verified." in result.output
    assert "Verification:\nTool activity: shell_command: uv run pytest" in result.output
    assert calls == [
        (
            "/sessions/sess-123/result",
            {"Authorization": "Bearer secret-token"},
        )
    ]


def test_remote_publish_branch_prints_publication_result(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )
    calls: list[tuple[str, dict[str, object] | None, dict[str, str]]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "session_id": "sess-123",
                "mode": "branch",
                "status": "published",
                "branch_name": "coding-agent/result",
                "pushed_ref": "refs/heads/coding-agent/result",
                "commit_sha": "abc123",
                "remote_url": "https://github.com/org/repo.git",
                "pr_url": None,
                "error": None,
            }

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            calls.append((path, json, self.headers))
            return FakeResponse()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    result = runner.invoke(
        main,
        [
            "remote",
            "publish",
            "dev",
            "--session",
            "sess-123",
            "--branch",
            "coding-agent/result",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "Published branch coding-agent/result" in result.output
    assert "Ref: refs/heads/coding-agent/result" in result.output
    assert "Commit: abc123" in result.output
    assert "Remote: https://github.com/org/repo.git" in result.output
    assert calls == [
        (
            "/sessions/sess-123/publish",
            {"mode": "branch", "branch_name": "coding-agent/result"},
            {"Authorization": "Bearer secret-token"},
        )
    ]


def test_publish_remote_result_records_safe_publication_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coding_agent.remote.client import RemoteEndpoint, publish_remote_result

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "session_id": "sess-123",
                "mode": "pr",
                "status": "published",
                "branch_name": "coding-agent/result",
                "pushed_ref": "refs/heads/coding-agent/result",
                "commit_sha": "abc123",
                "remote_url": "https://github.com/org/repo.git",
                "pr_url": "https://github.com/org/repo/pull/12",
                "error": None,
            }

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            del path, json
            return FakeResponse()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    sink = RecordingObservationSink()

    publication = publish_remote_result(
        RemoteEndpoint("dev", "http://agent.example", None),
        "sess-123",
        mode="pr",
        branch_name="coding-agent/result",
        observation_sink=sink,
    )

    assert publication["status"] == "published"
    assert [span.name for span in sink.spans] == ["result.publish_pr"]
    attributes = sink.spans[0].attributes
    assert attributes == {
        "session_id": "sess-123",
        "publication_mode": "pr",
        "remote_status": "completed",
        "publication_status": "published",
    }
    assert "branch_name" not in attributes
    assert "remote_url" not in attributes
    assert "pr_url" not in attributes


def test_remote_publish_branch_prints_partial_publication_result(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "session_id": "sess-123",
                "mode": "branch",
                "status": "partial",
                "branch_name": "coding-agent/result",
                "pushed_ref": "refs/heads/coding-agent/result",
                "commit_sha": "abc123",
                "remote_url": "https://github.com/org/repo.git",
                "pr_url": None,
                "error": "git push failed",
            }

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            del path, json
            return FakeResponse()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    result = runner.invoke(
        main,
        [
            "remote",
            "publish",
            "dev",
            "--session",
            "sess-123",
            "--branch",
            "coding-agent/result",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "git push failed" in result.output
    assert "Partial branch publication coding-agent/result" in result.output
    assert "Ref: refs/heads/coding-agent/result" in result.output
    assert "Commit: abc123" in result.output
    assert "Remote: https://github.com/org/repo.git" in result.output


def test_remote_publish_pr_prints_pr_result(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )
    calls: list[tuple[str, dict[str, object] | None, dict[str, str]]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "session_id": "sess-123",
                "mode": "pr",
                "status": "published",
                "branch_name": "coding-agent/result",
                "pushed_ref": "refs/heads/coding-agent/result",
                "commit_sha": "abc123",
                "remote_url": "https://github.com/org/repo.git",
                "pr_url": "https://github.com/org/repo/pull/12",
                "error": None,
            }

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            calls.append((path, json, self.headers))
            return FakeResponse()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    result = runner.invoke(
        main,
        [
            "remote",
            "publish",
            "dev",
            "--session",
            "sess-123",
            "--branch",
            "coding-agent/result",
            "--pr",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "Published PR https://github.com/org/repo/pull/12" in result.output
    assert "Branch: coding-agent/result" in result.output
    assert "Commit: abc123" in result.output
    assert calls == [
        (
            "/sessions/sess-123/publish",
            {"mode": "pr", "branch_name": "coding-agent/result"},
            {"Authorization": "Bearer secret-token"},
        )
    ]


def test_remote_publish_pr_reports_branch_when_pr_creation_fails(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "session_id": "sess-123",
                "mode": "pr",
                "status": "failed",
                "branch_name": "coding-agent/result",
                "pushed_ref": "refs/heads/coding-agent/result",
                "commit_sha": "abc123",
                "remote_url": "https://github.com/org/repo.git",
                "pr_url": None,
                "error": "GitHub PR publication failed: github unavailable",
            }

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            del path, json
            return FakeResponse()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    result = runner.invoke(
        main,
        [
            "remote",
            "publish",
            "dev",
            "--session",
            "sess-123",
            "--branch",
            "coding-agent/result",
            "--pr",
        ],
    )

    assert result.exit_code != 0
    assert "GitHub PR publication failed: github unavailable" in result.output
    assert "Branch: coding-agent/result" in result.output
    assert "Ref: refs/heads/coding-agent/result" in result.output
    assert "Commit: abc123" in result.output
    assert "Remote: https://github.com/org/repo.git" in result.output


def test_remote_publish_pr_reports_branch_when_branch_push_is_partial(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "session_id": "sess-123",
                "mode": "pr",
                "status": "partial",
                "branch_name": "coding-agent/result",
                "pushed_ref": "refs/heads/coding-agent/result",
                "commit_sha": "abc123",
                "remote_url": "https://github.com/org/repo.git",
                "pr_url": None,
                "error": "git push failed",
            }

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            del path, json
            return FakeResponse()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    result = runner.invoke(
        main,
        [
            "remote",
            "publish",
            "dev",
            "--session",
            "sess-123",
            "--branch",
            "coding-agent/result",
            "--pr",
        ],
    )

    assert result.exit_code == 0
    assert "git push failed" in result.output
    assert "Branch: coding-agent/result" in result.output
    assert "Ref: refs/heads/coding-agent/result" in result.output
    assert "Commit: abc123" in result.output
    assert "Remote: https://github.com/org/repo.git" in result.output


def test_remote_repl_with_repo_uploads_snapshot_and_downloads_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )
    calls: list[tuple[str, str, dict[str, object] | None, dict[str, str]]] = []
    applied: list[tuple[Path, str]] = []

    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            calls.append(("post", path, json, self.headers))
            if path == "/sessions":
                return FakeResponse({"session_id": "sess-upload"})
            raise AssertionError(f"unexpected post {path}")

        def delete(self, path: str) -> FakeResponse:
            calls.append(("delete", path, None, self.headers))
            return FakeResponse({"status": "closed"})

    def fake_stream_prompt(
        *, base_url: str, session_id: str, prompt: str, headers: dict[str, str]
    ) -> int:
        calls.append(
            (
                "stream",
                f"/sessions/{session_id}/prompt",
                {"prompt": prompt, "base_url": base_url},
                headers,
            )
        )
        return 0

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr("coding_agent.remote.client.stream_prompt", fake_stream_prompt)
    monkeypatch.setattr(
        "coding_agent.environment.archive.create_workspace_archive_base64",
        lambda path: "archive-encoded",
    )

    def fake_download_workspace_archive(
        *, base_url: str, session_id: str, headers: dict[str, str]
    ) -> str:
        calls.append(("download", session_id, {"base_url": base_url}, headers))
        return "result-archive"

    monkeypatch.setattr(
        "coding_agent.remote.client.download_workspace_archive",
        fake_download_workspace_archive,
    )

    def fake_download_workspace_manifest(
        *, base_url: str, session_id: str, headers: dict[str, str]
    ) -> dict[str, object]:
        calls.append(("manifest", session_id, {"base_url": base_url}, headers))
        return {"changed_files": ["result.txt"], "deleted_files": [], "total_bytes": 12}

    monkeypatch.setattr(
        "coding_agent.remote.client.download_workspace_manifest",
        fake_download_workspace_manifest,
    )
    monkeypatch.setattr(
        "coding_agent.environment.archive.extract_workspace_archive_base64",
        lambda repo_path_arg, archive_base64: applied.append(
            (repo_path_arg, archive_base64)
        ),
    )

    result = runner.invoke(
        main,
        [
            "remote",
            "repl",
            "dev",
            "--repo",
            str(repo_path),
            "--goal",
            "hello",
            "--yes",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "Created one-shot remote session sess-upload on remote dev" in result.output
    assert "Downloaded remote workspace snapshot and overwrote" in result.output
    assert "while preserving .git" in result.output
    assert "Cleaned up remote session sess-upload" in result.output
    assert calls == [
        (
            "post",
            "/sessions",
            {
                "workspace_source": {
                    "kind": "docker",
                    "snapshot_archive_base64": "archive-encoded",
                },
                "approval_policy": "auto",
            },
            {"Authorization": "Bearer secret-token"},
        ),
        (
            "stream",
            "/sessions/sess-upload/prompt",
            {"prompt": "hello", "base_url": "http://agent.example"},
            {"Authorization": "Bearer secret-token"},
        ),
        (
            "manifest",
            "sess-upload",
            {"base_url": "http://agent.example"},
            {"Authorization": "Bearer secret-token"},
        ),
        (
            "download",
            "sess-upload",
            {"base_url": "http://agent.example"},
            {"Authorization": "Bearer secret-token"},
        ),
        (
            "delete",
            "/sessions/sess-upload",
            None,
            {"Authorization": "Bearer secret-token"},
        ),
    ]
    assert applied == [(repo_path, "result-archive")]


def test_remote_run_with_repo_does_not_download_or_cleanup_by_default(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )
    calls: list[tuple[str, str, dict[str, object] | None, dict[str, str]]] = []
    applied: list[tuple[Path, str]] = []

    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            calls.append(("post", path, json, self.headers))
            if path == "/sessions":
                return FakeResponse({"session_id": "sess-run"})
            raise AssertionError(f"unexpected post {path}")

        def delete(self, path: str) -> FakeResponse:
            calls.append(("delete", path, None, self.headers))
            return FakeResponse({"status": "closed"})

    def fake_stream_prompt(
        *, base_url: str, session_id: str, prompt: str, headers: dict[str, str]
    ) -> int:
        calls.append(
            (
                "stream",
                f"/sessions/{session_id}/prompt",
                {"prompt": prompt, "base_url": base_url},
                headers,
            )
        )
        return 0

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr("coding_agent.remote.client.stream_prompt", fake_stream_prompt)
    monkeypatch.setattr(
        "coding_agent.environment.archive.create_workspace_archive_base64",
        lambda path: "archive-encoded",
    )
    monkeypatch.setattr(
        "coding_agent.environment.archive.extract_workspace_archive_base64",
        lambda repo_path_arg, archive_base64: applied.append(
            (repo_path_arg, archive_base64)
        ),
    )

    result = runner.invoke(
        main,
        [
            "remote",
            "run",
            "dev",
            "--repo",
            str(repo_path),
            "--snapshot-fallback",
            "--goal",
            "hello",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "Remote session sess-run left open for result inspection." in result.output
    assert "coding_agent remote result dev --session sess-run" in result.output
    assert "coding_agent remote diff dev --session sess-run" not in result.output
    assert "coding_agent remote patch dev --session sess-run" not in result.output
    assert "coding_agent remote publish dev" not in result.output
    assert "coding_agent remote download dev --session sess-run --repo" in result.output
    assert "Downloaded remote workspace snapshot" not in result.output
    assert calls == [
        (
            "post",
            "/sessions",
            {
                "workspace_source": {
                    "kind": "docker",
                    "snapshot_archive_base64": "archive-encoded",
                },
                "approval_policy": "auto",
            },
            {"Authorization": "Bearer secret-token"},
        ),
        (
            "stream",
            "/sessions/sess-run/prompt",
            {"prompt": "hello", "base_url": "http://agent.example"},
            {"Authorization": "Bearer secret-token"},
        ),
    ]
    assert applied == []


def test_remote_run_clean_git_repo_uses_git_workspace_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "remotes.json"
    repo_path, head_sha = _init_clean_git_repo_with_origin(tmp_path)
    remote_url = _run_git(repo_path, ["config", "--get", "remote.origin.url"])
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )
    calls: list[tuple[str, str, dict[str, object] | None, dict[str, str]]] = []

    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            calls.append(("post", path, json, self.headers))
            if path == "/sessions":
                return FakeResponse({"session_id": "sess-git"})
            raise AssertionError(f"unexpected post {path}")

    def fake_stream_prompt(
        *, base_url: str, session_id: str, prompt: str, headers: dict[str, str]
    ) -> int:
        calls.append(
            (
                "stream",
                f"/sessions/{session_id}/prompt",
                {"prompt": prompt, "base_url": base_url},
                headers,
            )
        )
        return 0

    def fail_archive(path: Path) -> str:
        raise AssertionError(f"unexpected snapshot archive for {path}")

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr("coding_agent.remote.client.stream_prompt", fake_stream_prompt)
    monkeypatch.setattr(
        "coding_agent.environment.archive.create_workspace_archive_base64",
        fail_archive,
    )

    result = runner.invoke(
        main,
        [
            "remote",
            "run",
            "dev",
            "--repo",
            str(repo_path),
            "--runtime",
            "universal",
            "--goal",
            "hello",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert (
        "coding_agent remote publish dev --session sess-git --branch coding-agent/session-sess-git"
        in result.output
    )
    assert calls == [
        (
            "post",
            "/sessions",
            {
                "workspace_source": {
                    "kind": "git",
                    "remote_url": remote_url,
                    "base_ref": "main",
                    "base_sha": head_sha,
                    "runtime_profile": "universal",
                },
                "approval_policy": "auto",
            },
            {"Authorization": "Bearer secret-token"},
        ),
        (
            "stream",
            "/sessions/sess-git/prompt",
            {"prompt": "hello", "base_url": "http://agent.example"},
            {"Authorization": "Bearer secret-token"},
        ),
    ]


def test_remote_run_dirty_git_repo_requires_explicit_snapshot_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "remotes.json"
    repo_path, _ = _init_clean_git_repo_with_origin(tmp_path)
    (repo_path / "README.md").write_text("# changed\n", encoding="utf-8")
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )

    class FailingClient:
        def __init__(self, **kwargs: object) -> None:
            del kwargs
            raise AssertionError("remote run should fail before creating a session")

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FailingClient)

    result = runner.invoke(
        main,
        [
            "remote",
            "run",
            "dev",
            "--repo",
            str(repo_path),
            "--goal",
            "hello",
        ],
    )

    assert result.exit_code != 0
    assert "remote run --repo requires a clean Git working tree" in result.output
    assert "--snapshot-fallback" in result.output


def test_remote_run_reports_missing_git_for_git_backed_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "remotes.json"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )

    class FailingClient:
        def __init__(self, **kwargs: object) -> None:
            del kwargs
            raise AssertionError("remote run should fail before creating a session")

    def raise_missing_git(
        *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        raise FileNotFoundError("git")

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FailingClient)
    monkeypatch.setattr(subprocess, "run", raise_missing_git)

    result = runner.invoke(
        main,
        [
            "remote",
            "run",
            "dev",
            "--repo",
            str(repo_path),
            "--goal",
            "hello",
        ],
    )

    assert result.exit_code != 0
    assert "git not found" in result.output


def test_remote_run_snapshot_fallback_uploads_archive_for_local_only_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "remotes.json"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )
    calls: list[tuple[str, str, dict[str, object] | None, dict[str, str]]] = []

    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            calls.append(("post", path, json, self.headers))
            if path == "/sessions":
                return FakeResponse({"session_id": "sess-snapshot"})
            raise AssertionError(f"unexpected post {path}")

    def fake_stream_prompt(
        *, base_url: str, session_id: str, prompt: str, headers: dict[str, str]
    ) -> int:
        calls.append(
            (
                "stream",
                f"/sessions/{session_id}/prompt",
                {"prompt": prompt, "base_url": base_url},
                headers,
            )
        )
        return 0

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr("coding_agent.remote.client.stream_prompt", fake_stream_prompt)
    monkeypatch.setattr(
        "coding_agent.environment.archive.create_workspace_archive_base64",
        lambda path: "archive-encoded",
    )

    result = runner.invoke(
        main,
        [
            "remote",
            "run",
            "dev",
            "--repo",
            str(repo_path),
            "--snapshot-fallback",
            "--goal",
            "hello",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert calls[0] == (
        "post",
        "/sessions",
        {
            "workspace_source": {
                "kind": "docker",
                "snapshot_archive_base64": "archive-encoded",
            },
            "approval_policy": "auto",
        },
        {"Authorization": "Bearer secret-token"},
    )


def test_remote_run_with_download_keeps_explicit_archive_overwrite(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )
    calls: list[tuple[str, str, dict[str, object] | None, dict[str, str]]] = []
    applied: list[tuple[Path, str]] = []

    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            calls.append(("post", path, json, self.headers))
            if path == "/sessions":
                return FakeResponse({"session_id": "sess-run"})
            raise AssertionError(f"unexpected post {path}")

        def delete(self, path: str) -> FakeResponse:
            calls.append(("delete", path, None, self.headers))
            return FakeResponse({"status": "closed"})

    def fake_stream_prompt(
        *, base_url: str, session_id: str, prompt: str, headers: dict[str, str]
    ) -> int:
        calls.append(
            (
                "stream",
                f"/sessions/{session_id}/prompt",
                {"prompt": prompt, "base_url": base_url},
                headers,
            )
        )
        return 0

    def fake_download_workspace_archive(
        *, base_url: str, session_id: str, headers: dict[str, str]
    ) -> str:
        calls.append(("download", session_id, {"base_url": base_url}, headers))
        return "result-archive"

    def fake_download_workspace_manifest(
        *, base_url: str, session_id: str, headers: dict[str, str]
    ) -> dict[str, object]:
        calls.append(("manifest", session_id, {"base_url": base_url}, headers))
        return {"changed_files": ["result.txt"], "deleted_files": [], "total_bytes": 12}

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr("coding_agent.remote.client.stream_prompt", fake_stream_prompt)
    monkeypatch.setattr(
        "coding_agent.environment.archive.create_workspace_archive_base64",
        lambda path: "archive-encoded",
    )
    monkeypatch.setattr(
        "coding_agent.remote.client.download_workspace_archive",
        fake_download_workspace_archive,
    )
    monkeypatch.setattr(
        "coding_agent.remote.client.download_workspace_manifest",
        fake_download_workspace_manifest,
    )
    monkeypatch.setattr(
        "coding_agent.environment.archive.extract_workspace_archive_base64",
        lambda repo_path_arg, archive_base64: applied.append(
            (repo_path_arg, archive_base64)
        ),
    )

    result = runner.invoke(
        main,
        [
            "remote",
            "run",
            "dev",
            "--repo",
            str(repo_path),
            "--snapshot-fallback",
            "--goal",
            "hello",
            "--download",
            "--yes",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "Downloaded remote workspace snapshot and overwrote" in result.output
    assert "Cleaned up remote session sess-run" in result.output
    assert (
        "manifest",
        "sess-run",
        {"base_url": "http://agent.example"},
        {"Authorization": "Bearer secret-token"},
    ) in calls
    assert (
        "download",
        "sess-run",
        {"base_url": "http://agent.example"},
        {"Authorization": "Bearer secret-token"},
    ) in calls
    assert (
        "delete",
        "/sessions/sess-run",
        None,
        {"Authorization": "Bearer secret-token"},
    ) in calls
    assert applied == [(repo_path, "result-archive")]


def test_remote_repl_with_repo_downloads_results_when_stream_fails(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )
    calls: list[tuple[str, str, dict[str, object] | None, dict[str, str]]] = []
    applied: list[tuple[Path, str]] = []

    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            calls.append(("post", path, json, self.headers))
            if path == "/sessions":
                return FakeResponse({"session_id": "sess-upload"})
            raise AssertionError(f"unexpected post {path}")

        def delete(self, path: str) -> FakeResponse:
            calls.append(("delete", path, None, self.headers))
            return FakeResponse({"status": "closed"})

    def failing_stream_prompt(
        *, base_url: str, session_id: str, prompt: str, headers: dict[str, str]
    ) -> int:
        calls.append(
            (
                "stream",
                f"/sessions/{session_id}/prompt",
                {"prompt": prompt, "base_url": base_url},
                headers,
            )
        )
        raise click.ClickException("stream failed")

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr(
        "coding_agent.remote.client.stream_prompt", failing_stream_prompt
    )
    monkeypatch.setattr(
        "coding_agent.environment.archive.create_workspace_archive_base64",
        lambda path: "archive-encoded",
    )
    monkeypatch.setattr(
        "coding_agent.remote.client.download_workspace_archive",
        lambda *, base_url, session_id, headers: "result-archive",
    )

    def fake_download_workspace_manifest(
        *, base_url: str, session_id: str, headers: dict[str, str]
    ) -> dict[str, object]:
        calls.append(("manifest", session_id, {"base_url": base_url}, headers))
        return {"changed_files": ["result.txt"], "deleted_files": [], "total_bytes": 12}

    monkeypatch.setattr(
        "coding_agent.remote.client.download_workspace_manifest",
        fake_download_workspace_manifest,
    )
    monkeypatch.setattr(
        "coding_agent.environment.archive.extract_workspace_archive_base64",
        lambda repo_path_arg, archive_base64: applied.append(
            (repo_path_arg, archive_base64)
        ),
    )

    result = runner.invoke(
        main,
        [
            "remote",
            "repl",
            "dev",
            "--repo",
            str(repo_path),
            "--goal",
            "hello",
            "--yes",
        ],
    )

    assert result.exit_code != 0
    assert "stream failed" in result.output
    assert applied == [(repo_path, "result-archive")]
    assert calls == [
        (
            "post",
            "/sessions",
            {
                "workspace_source": {
                    "kind": "docker",
                    "snapshot_archive_base64": "archive-encoded",
                },
                "approval_policy": "auto",
            },
            {"Authorization": "Bearer secret-token"},
        ),
        (
            "stream",
            "/sessions/sess-upload/prompt",
            {"prompt": "hello", "base_url": "http://agent.example"},
            {"Authorization": "Bearer secret-token"},
        ),
        (
            "manifest",
            "sess-upload",
            {"base_url": "http://agent.example"},
            {"Authorization": "Bearer secret-token"},
        ),
        (
            "delete",
            "/sessions/sess-upload",
            None,
            {"Authorization": "Bearer secret-token"},
        ),
    ]


def test_remote_repl_with_repo_retains_session_when_extract_fails(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    repo_path = tmp_path / "repo with spaces"
    repo_path.mkdir()
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )
    calls: list[tuple[str, str, dict[str, object] | None, dict[str, str]]] = []

    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            calls.append(("post", path, json, self.headers))
            if path == "/sessions":
                return FakeResponse({"session_id": "sess-upload"})
            raise AssertionError(f"unexpected post {path}")

        def delete(self, path: str) -> FakeResponse:
            calls.append(("delete", path, None, self.headers))
            return FakeResponse({"status": "closed"})

    def fake_stream_prompt(
        *, base_url: str, session_id: str, prompt: str, headers: dict[str, str]
    ) -> int:
        calls.append(
            (
                "stream",
                f"/sessions/{session_id}/prompt",
                {"prompt": prompt, "base_url": base_url},
                headers,
            )
        )
        return 0

    def failing_extract(repo_path_arg: Path, archive_base64: str) -> None:
        del repo_path_arg, archive_base64
        raise OSError("extract failed")

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr("coding_agent.remote.client.stream_prompt", fake_stream_prompt)
    monkeypatch.setattr(
        "coding_agent.environment.archive.create_workspace_archive_base64",
        lambda path: "archive-encoded",
    )
    monkeypatch.setattr(
        "coding_agent.remote.client.download_workspace_archive",
        lambda *, base_url, session_id, headers: "result-archive",
    )

    def fake_download_workspace_manifest(
        *, base_url: str, session_id: str, headers: dict[str, str]
    ) -> dict[str, object]:
        calls.append(("manifest", session_id, {"base_url": base_url}, headers))
        return {"changed_files": ["result.txt"], "deleted_files": [], "total_bytes": 12}

    monkeypatch.setattr(
        "coding_agent.remote.client.download_workspace_manifest",
        fake_download_workspace_manifest,
    )
    monkeypatch.setattr(
        "coding_agent.environment.archive.extract_workspace_archive_base64",
        failing_extract,
    )

    result = runner.invoke(
        main,
        [
            "remote",
            "repl",
            "dev",
            "--repo",
            str(repo_path),
            "--goal",
            "hello",
            "--yes",
        ],
    )

    assert result.exit_code != 0
    assert "extract failed" in result.output
    assert "Remote session sess-upload left open" in result.output
    assert (
        "python -m coding_agent remote download dev --session sess-upload --repo "
        + shlex.quote(str(repo_path))
        in result.output
    )
    assert "python -m coding_agent remote attach dev sess-upload" in result.output
    assert calls == [
        (
            "post",
            "/sessions",
            {
                "workspace_source": {
                    "kind": "docker",
                    "snapshot_archive_base64": "archive-encoded",
                },
                "approval_policy": "auto",
            },
            {"Authorization": "Bearer secret-token"},
        ),
        (
            "stream",
            "/sessions/sess-upload/prompt",
            {"prompt": "hello", "base_url": "http://agent.example"},
            {"Authorization": "Bearer secret-token"},
        ),
        (
            "manifest",
            "sess-upload",
            {"base_url": "http://agent.example"},
            {"Authorization": "Bearer secret-token"},
        ),
    ]


def _workspace_manifest_payload() -> dict[str, object]:
    return {"changed_files": ["result.txt"], "deleted_files": [], "total_bytes": 12}


def test_remote_repl_with_repo_retains_session_when_stream_and_extract_fail(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )

    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

    calls: list[tuple[str, str, dict[str, object] | None, dict[str, str]]] = []

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            calls.append(("post", path, json, self.headers))
            if path == "/sessions":
                return FakeResponse({"session_id": "sess-upload"})
            raise AssertionError(f"unexpected post {path}")

        def delete(self, path: str) -> FakeResponse:
            calls.append(("delete", path, None, self.headers))
            return FakeResponse({"status": "closed"})

    def failing_stream_prompt(
        *, base_url: str, session_id: str, prompt: str, headers: dict[str, str]
    ) -> int:
        calls.append(
            (
                "stream",
                f"/sessions/{session_id}/prompt",
                {"prompt": prompt, "base_url": base_url},
                headers,
            )
        )
        raise click.ClickException("stream failed")

    def fake_download_workspace_manifest(
        *, base_url: str, session_id: str, headers: dict[str, str]
    ) -> dict[str, object]:
        calls.append(("manifest", session_id, {"base_url": base_url}, headers))
        return _workspace_manifest_payload()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr(
        "coding_agent.remote.client.stream_prompt", failing_stream_prompt
    )
    monkeypatch.setattr(
        "coding_agent.environment.archive.create_workspace_archive_base64",
        lambda path: "archive-encoded",
    )
    monkeypatch.setattr(
        "coding_agent.remote.client.download_workspace_archive",
        lambda *, base_url, session_id, headers: "result-archive",
    )
    monkeypatch.setattr(
        "coding_agent.remote.client.download_workspace_manifest",
        fake_download_workspace_manifest,
    )
    monkeypatch.setattr(
        "coding_agent.environment.archive.extract_workspace_archive_base64",
        lambda repo_path_arg, archive_base64: (_ for _ in ()).throw(
            OSError("extract failed")
        ),
    )

    result = runner.invoke(
        main,
        [
            "remote",
            "repl",
            "dev",
            "--repo",
            str(repo_path),
            "--goal",
            "hello",
            "--yes",
        ],
    )

    assert result.exit_code != 0
    assert "stream failed" in result.output
    assert "Remote session sess-upload left open" in result.output
    assert (
        "python -m coding_agent remote download dev --session sess-upload --repo "
        + shlex.quote(str(repo_path))
        in result.output
    )
    assert "python -m coding_agent remote attach dev sess-upload" in result.output
    assert calls == [
        (
            "post",
            "/sessions",
            {
                "workspace_source": {
                    "kind": "docker",
                    "snapshot_archive_base64": "archive-encoded",
                },
                "approval_policy": "auto",
            },
            {"Authorization": "Bearer secret-token"},
        ),
        (
            "stream",
            "/sessions/sess-upload/prompt",
            {"prompt": "hello", "base_url": "http://agent.example"},
            {"Authorization": "Bearer secret-token"},
        ),
        (
            "manifest",
            "sess-upload",
            {"base_url": "http://agent.example"},
            {"Authorization": "Bearer secret-token"},
        ),
    ]


def test_remote_repl_can_explicitly_create_empty_docker_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example"],
        catch_exceptions=False,
    )
    calls: list[tuple[str, str, dict[str, object] | None, dict[str, str]]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"session_id": "sess-empty"}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            headers = kwargs.get("headers")
            self.headers = headers if isinstance(headers, dict) else {}

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            calls.append(("post", path, json, self.headers))
            return FakeResponse()

        def delete(self, path: str) -> FakeResponse:
            calls.append(("delete", path, None, self.headers))
            return FakeResponse()

    def fake_stream_prompt(
        *, base_url: str, session_id: str, prompt: str, headers: dict[str, str]
    ) -> int:
        calls.append(
            (
                "stream",
                f"/sessions/{session_id}/prompt",
                {"prompt": prompt, "base_url": base_url},
                headers,
            )
        )
        return 0

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr("coding_agent.remote.client.stream_prompt", fake_stream_prompt)

    result = runner.invoke(
        main,
        ["remote", "repl", "dev", "--empty-workspace", "--goal", "hello"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert calls == [
        (
            "post",
            "/sessions",
            {"workspace_source": {"kind": "docker"}, "approval_policy": "auto"},
            {},
        ),
        (
            "stream",
            "/sessions/sess-empty/prompt",
            {"prompt": "hello", "base_url": "http://agent.example"},
            {},
        ),
        (
            "delete",
            "/sessions/sess-empty",
            None,
            {},
        ),
    ]


def test_remote_approval_request_prompts_before_submitting_decision(
    monkeypatch,
) -> None:
    approvals: list[dict[str, object] | None] = []
    prompts: list[str] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"status": "ok"}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            del path
            approvals.append(json)
            return FakeResponse()

    def fake_prompt(
        text: str, default: str | None = None, show_default: bool = True
    ) -> str:
        del default, show_default
        prompts.append(text)
        return "a"

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr("coding_agent.remote.client.click.prompt", fake_prompt)

    from coding_agent.remote.client import handle_sse_event

    status, line_open = handle_sse_event(
        base_url="http://agent.example",
        session_id="sess-approval",
        headers={},
        event="ApprovalRequest",
        data=json.dumps(
            {
                "request_id": "req-1",
                "tool_call": {
                    "tool_name": "bash_run",
                    "arguments": {"command": "rm -rf scratch"},
                    "call_id": "call-1",
                },
            }
        ),
    )

    assert status is None
    assert line_open is False
    assert prompts == ["→"]
    assert approvals == [{"request_id": "req-1", "approved": True, "scope": "session"}]


def test_remote_approval_request_can_reject_with_reason(monkeypatch) -> None:
    approvals: list[dict[str, object] | None] = []
    prompts: list[str] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"status": "ok"}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            del path
            approvals.append(json)
            return FakeResponse()

    answers = iter(["r", "Need a safer command"])

    def fake_prompt(
        text: str, default: str | None = None, show_default: bool = True
    ) -> str:
        del default, show_default
        prompts.append(text)
        return next(answers)

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr("coding_agent.remote.client.click.prompt", fake_prompt)

    from coding_agent.remote.client import handle_sse_event

    status, line_open = handle_sse_event(
        base_url="http://agent.example",
        session_id="sess-approval",
        headers={},
        event="ApprovalRequest",
        data=json.dumps(
            {
                "request_id": "req-1",
                "tool_call": {
                    "tool_name": "bash_run",
                    "arguments": {"command": "rm -rf scratch"},
                    "call_id": "call-1",
                },
            }
        ),
    )

    assert status is None
    assert line_open is False
    assert prompts == ["→", "Reason"]
    assert approvals == [
        {
            "request_id": "req-1",
            "approved": False,
            "scope": "once",
            "feedback": "Need a safer command",
        }
    ]


def test_handle_sse_event_formats_approval_after_inline_stream_output(
    monkeypatch, capsys
) -> None:
    approvals: list[dict[str, object] | None] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"status": "ok"}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> FakeResponse:
            del path
            approvals.append(json)
            return FakeResponse()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr(
        "coding_agent.remote.client.click.prompt",
        lambda text, default=None, show_default=True: "n",
    )

    from coding_agent.remote.client import handle_sse_event

    status, line_open = handle_sse_event(
        base_url="http://agent.example",
        session_id="sess-approval",
        headers={},
        event="StreamDelta",
        data=json.dumps({"content": "partial"}),
    )

    assert status is None
    assert line_open is True

    status, line_open = handle_sse_event(
        base_url="http://agent.example",
        session_id="sess-approval",
        headers={},
        event="ApprovalRequest",
        data=json.dumps(
            {
                "request_id": "req-1",
                "tool_call": {
                    "tool_name": "bash_run",
                    "arguments": {"command": "rm -rf scratch"},
                    "call_id": "call-1",
                },
            }
        ),
        line_open=line_open,
    )

    assert status is None
    assert line_open is False
    assert approvals == [
        {
            "request_id": "req-1",
            "approved": False,
            "scope": "once",
            "feedback": "Rejected by user",
        }
    ]
    assert capsys.readouterr().out == (
        "partial\n"
        "[approval] Remote tool request bash_run\n"
        '{\n  "command": "rm -rf scratch"\n}\n'
        "[y]=approve  [a]=approve all (session)  [n]=reject  [r]=reject with reason\n"
    )


def test_handle_display_sse_event_renders_sanitized_tool_result(capsys) -> None:
    from coding_agent.remote.client import handle_display_sse_event

    status, line_open = handle_display_sse_event(
        base_url="http://agent.example",
        session_id="sess-1",
        headers={},
        event="tool_result",
        data=json.dumps(
            {
                "source_event_id": "event-1",
                "run_id": "run-1",
                "sequence": 1,
                "display_kind": "tool_result",
                "payload": {
                    "tool_name": "bash_run",
                    "display_result": "[tool output redacted]",
                },
                "created_at": "2026-06-03T00:00:00+00:00",
            }
        ),
    )

    assert status is None
    assert line_open is False
    assert capsys.readouterr().out == "[tool output redacted]\n"


def test_remote_approval_abort_reports_actionable_noninteractive_error(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "coding_agent.remote.client.click.prompt",
        lambda *args, **kwargs: (_ for _ in ()).throw(click.Abort()),
    )

    from coding_agent.remote.client import handle_sse_event

    with pytest.raises(
        click.ClickException,
        match=r"Remote approval requires input.*--approval yolo.*stdin",
    ):
        handle_sse_event(
            base_url="http://agent.example",
            session_id="sess-approval",
            headers={},
            event="ApprovalRequest",
            data=json.dumps(
                {
                    "request_id": "req-1",
                    "tool_call": {
                        "tool_name": "bash_run",
                        "arguments": {"command": "echo hi"},
                        "call_id": "call-1",
                    },
                }
            ),
        )


def test_remote_config_file_is_written_private(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_remote_add_reports_invalid_remotes_json(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "remotes.json"
    _ = config_path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()

    result = runner.invoke(main, ["remote", "list"])

    assert result.exit_code != 0
    assert f"Invalid remotes file: {config_path}" in result.output


@pytest.mark.parametrize("field", ["admin_token", "admin_token_env"])
def test_remote_list_rejects_invalid_admin_token_field_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    config_path = tmp_path / "remotes.json"
    config_path.write_text(
        json.dumps(
            {
                "remotes": {
                    "dev": {
                        "url": "http://agent.example",
                        field: True,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()

    result = runner.invoke(main, ["remote", "list"])

    assert result.exit_code != 0
    assert f"Remote dev has invalid {field}" in result.output


def test_save_remotes_does_not_chmod_existing_override_parent(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "nested" / "remotes.json"
    config_path.parent.mkdir(parents=True)
    original_mode = stat.S_IMODE(config_path.parent.stat().st_mode)
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert stat.S_IMODE(config_path.parent.stat().st_mode) == original_mode


def test_remote_repl_reports_request_error_on_session_create(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example"],
        catch_exceptions=False,
    )

    class FailingClient:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def __enter__(self) -> FailingClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> httpx.Response:
            del path, json
            request = httpx.Request("POST", "http://agent.example/sessions")
            raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FailingClient)

    result = runner.invoke(
        main,
        ["remote", "repl", "dev", "--empty-workspace", "--goal", "hello"],
    )

    assert result.exit_code != 0
    assert "Failed to create remote session" in result.output
    assert "connection refused" in result.output


def test_remote_repl_reports_setup_failure_without_docker_command(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "remotes.json"
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example"],
        catch_exceptions=False,
    )

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(
            self, path: str, json: dict[str, object] | None = None
        ) -> httpx.Response:
            del path, json
            request = httpx.Request("POST", "http://agent.example/sessions")
            return httpx.Response(
                500,
                request=request,
                json={
                    "detail": (
                        "setup phase failed with exit code 42\n"
                        "setup phase stderr:\nsetup failed intentionally\n"
                    )
                },
            )

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)

    result = runner.invoke(
        main,
        ["remote", "repl", "dev", "--empty-workspace", "--goal", "hello"],
    )

    assert result.exit_code != 0
    assert "setup phase failed with exit code 42" in result.output
    assert "setup failed intentionally" in result.output
    assert "docker run" not in result.output


def test_stream_prompt_reports_non_200_sse_response(monkeypatch) -> None:
    class FakeResponse:
        status_code = 503

        def raise_for_status(self) -> None:
            request = httpx.Request(
                "POST", "http://agent.example/sessions/sess-1/prompt"
            )
            raise httpx.HTTPStatusError(
                "bad status",
                request=request,
                response=httpx.Response(
                    503, request=request, json={"detail": "server busy"}
                ),
            )

        def json(self) -> dict[str, object]:
            return {"detail": "server busy"}

    class FakeEventSource:
        response = FakeResponse()

        def __enter__(self) -> FakeEventSource:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def iter_sse(self):
            if False:
                yield None

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            self.timeout = kwargs.get("timeout")

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr(
        "coding_agent.remote.client.connect_sse",
        lambda *args, **kwargs: FakeEventSource(),
    )

    from coding_agent.remote.client import stream_prompt

    with pytest.raises(
        click.ClickException, match="Failed to stream remote prompt: server busy"
    ):
        stream_prompt(
            base_url="http://agent.example",
            session_id="sess-1",
            prompt="hello",
            headers={},
        )


def test_stream_prompt_requests_display_event_stream(monkeypatch, capsys) -> None:
    calls: list[tuple[str, str, str]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class FakeEventSource:
        response = FakeResponse()

        def __enter__(self) -> FakeEventSource:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def iter_sse(self):
            yield type(
                "SSE",
                (),
                {
                    "event": "assistant_text_delta",
                    "data": json.dumps(
                        {
                            "source_event_id": "live:sess-1:event-1",
                            "run_id": "run-1",
                            "sequence": None,
                            "display_kind": "assistant_text_delta",
                            "payload": {"content": "hello"},
                            "created_at": "2026-06-03T00:00:00+00:00",
                        }
                    ),
                },
            )()
            yield type(
                "SSE",
                (),
                {
                    "event": "final_result",
                    "data": json.dumps(
                        {
                            "source_event_id": "live:sess-1:event-2",
                            "run_id": "run-1",
                            "sequence": None,
                            "display_kind": "final_result",
                            "payload": {"completion_status": "completed"},
                            "created_at": "2026-06-03T00:00:01+00:00",
                        }
                    ),
                },
            )()

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            self.base_url = str(kwargs["base_url"])

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    def fake_connect_sse(client, method: str, path: str, **kwargs: object):
        del kwargs
        calls.append((client.base_url, method, path))
        return FakeEventSource()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr("coding_agent.remote.client.connect_sse", fake_connect_sse)

    from coding_agent.remote.client import stream_prompt

    status = stream_prompt(
        base_url="http://agent.example",
        session_id="sess-1",
        prompt="hello",
        headers={},
    )

    assert status == 0
    assert calls == [
        (
            "http://agent.example",
            "POST",
            "/sessions/sess-1/prompt?event_format=display",
        )
    ]
    assert capsys.readouterr().out == "hello\n"


def test_stream_prompt_can_emit_display_events_to_consumer(monkeypatch, capsys) -> None:
    from coding_agent.wire.protocol import CompletionStatus, StreamDelta, TurnEnd

    emitted: list[object] = []

    class FakeConsumer:
        async def emit(self, msg: object) -> None:
            emitted.append(msg)

        async def request_approval(self, req: object) -> object:
            raise AssertionError(f"unexpected approval request: {req!r}")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class FakeEventSource:
        response = FakeResponse()

        def __enter__(self) -> FakeEventSource:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def iter_sse(self):
            yield type(
                "SSE",
                (),
                {
                    "event": "assistant_text_delta",
                    "data": json.dumps(
                        {
                            "source_event_id": "live:sess-1:event-1",
                            "run_id": "run-1",
                            "sequence": None,
                            "display_kind": "assistant_text_delta",
                            "payload": {"content": "hello"},
                            "created_at": "2026-06-03T00:00:00+00:00",
                        }
                    ),
                },
            )()
            yield type(
                "SSE",
                (),
                {
                    "event": "final_result",
                    "data": json.dumps(
                        {
                            "source_event_id": "live:sess-1:event-2",
                            "run_id": "run-1",
                            "sequence": None,
                            "display_kind": "final_result",
                            "payload": {
                                "turn_id": "turn-1",
                                "completion_status": "completed",
                            },
                            "created_at": "2026-06-03T00:00:01+00:00",
                        }
                    ),
                },
            )()

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            self.base_url = str(kwargs["base_url"])

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr(
        "coding_agent.remote.client.connect_sse",
        lambda *args, **kwargs: FakeEventSource(),
    )

    from coding_agent.remote.client import stream_prompt_or_run_request

    status = stream_prompt_or_run_request(
        base_url="http://agent.example",
        session_id="sess-1",
        prompt="hello",
        headers={},
        display_consumer=FakeConsumer(),
    )

    assert status == 0
    assert capsys.readouterr().out == ""
    assert len(emitted) == 2
    assert isinstance(emitted[0], StreamDelta)
    assert emitted[0].session_id == "sess-1"
    assert emitted[0].content == "hello"
    assert isinstance(emitted[1], TurnEnd)
    assert emitted[1].session_id == "sess-1"
    assert emitted[1].turn_id == "turn-1"
    assert emitted[1].completion_status is CompletionStatus.COMPLETED


def test_stream_resume_requests_display_event_stream(monkeypatch, capsys) -> None:
    calls: list[tuple[str, str, str, dict[str, str]]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class FakeEventSource:
        response = FakeResponse()

        def __enter__(self) -> FakeEventSource:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def iter_sse(self):
            yield type(
                "SSE",
                (),
                {
                    "event": "final_result",
                    "data": json.dumps(
                        {
                            "source_event_id": "live:sess-1:event-1",
                            "run_id": "run-1",
                            "sequence": None,
                            "display_kind": "final_result",
                            "payload": {"completion_status": "completed"},
                            "created_at": "2026-06-03T00:00:01+00:00",
                        }
                    ),
                },
            )()

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            self.base_url = str(kwargs["base_url"])

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    def fake_connect_sse(
        client,
        method: str,
        path: str,
        *,
        json: dict[str, str],
    ):
        calls.append((client.base_url, method, path, json))
        return FakeEventSource()

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr("coding_agent.remote.client.connect_sse", fake_connect_sse)

    from coding_agent.remote.client import stream_resume_or_run_request

    status = stream_resume_or_run_request(
        base_url="http://agent.example",
        session_id="sess-1",
        prompt="continue",
        headers={},
    )

    assert status == 0
    assert calls == [
        (
            "http://agent.example",
            "POST",
            "/sessions/sess-1/resume?event_format=display",
            {"resume_reason": "remote_cli_resume", "prompt": "continue"},
        )
    ]
    assert capsys.readouterr().out == ""


def test_stream_prompt_rejects_truncated_stream_without_turn_end(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class FakeEventSource:
        response = FakeResponse()

        def __enter__(self) -> FakeEventSource:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def iter_sse(self):
            yield type(
                "SSE",
                (),
                {"event": "StreamDelta", "data": json.dumps({"content": "partial"})},
            )()

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            self.timeout = kwargs.get("timeout")

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr(
        "coding_agent.remote.client.connect_sse",
        lambda *args, **kwargs: FakeEventSource(),
    )

    from coding_agent.remote.client import stream_prompt

    with pytest.raises(
        click.ClickException, match="Remote prompt stream ended without TurnEnd"
    ):
        stream_prompt(
            base_url="http://agent.example",
            session_id="sess-1",
            prompt="hello",
            headers={},
        )


def test_handle_sse_event_reports_invalid_json(monkeypatch) -> None:
    from coding_agent.remote.client import handle_sse_event

    with pytest.raises(
        click.ClickException, match="Remote SSE event payload must be valid JSON"
    ):
        handle_sse_event(
            base_url="http://agent.example",
            session_id="sess-1",
            headers={},
            event="StreamDelta",
            data="{not-json",
        )
