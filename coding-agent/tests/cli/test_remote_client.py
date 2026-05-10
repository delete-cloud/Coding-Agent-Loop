from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import httpx
import click
import pytest
from click.testing import CliRunner

from coding_agent.__main__ import main


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
    assert "Created remote session sess-123" in result.output
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


def test_attach_streams_prompt_to_existing_session(
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
    calls: list[dict[str, object]] = []

    def fake_stream_prompt(
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

    monkeypatch.setattr("coding_agent.remote.client.stream_prompt", fake_stream_prompt)

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
            return httpx.Response(400, json={"detail": "cloud workspace disabled"}, request=request)

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
    assert "Pass --repo to upload a workspace snapshot or --empty-workspace" in result.output


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
        "coding_agent.workspace_archive.create_workspace_archive_base64",
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
    monkeypatch.setattr(
        "coding_agent.workspace_archive.extract_workspace_archive_base64",
        lambda repo_path_arg, archive_base64: applied.append(
            (repo_path_arg, archive_base64)
        ),
    )

    result = runner.invoke(
        main,
        ["remote", "repl", "dev", "--repo", str(repo_path), "--goal", "hello"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "Created remote session sess-upload" in result.output
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
    monkeypatch.setattr("coding_agent.remote.client.stream_prompt", failing_stream_prompt)
    monkeypatch.setattr(
        "coding_agent.workspace_archive.create_workspace_archive_base64",
        lambda path: "archive-encoded",
    )
    monkeypatch.setattr(
        "coding_agent.remote.client.download_workspace_archive",
        lambda *, base_url, session_id, headers: "result-archive",
    )
    monkeypatch.setattr(
        "coding_agent.workspace_archive.extract_workspace_archive_base64",
        lambda repo_path_arg, archive_base64: applied.append(
            (repo_path_arg, archive_base64)
        ),
    )

    result = runner.invoke(
        main,
        ["remote", "repl", "dev", "--repo", str(repo_path), "--goal", "hello"],
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
        "coding_agent.workspace_archive.create_workspace_archive_base64",
        lambda path: "archive-encoded",
    )
    monkeypatch.setattr(
        "coding_agent.remote.client.download_workspace_archive",
        lambda *, base_url, session_id, headers: "result-archive",
    )
    monkeypatch.setattr(
        "coding_agent.workspace_archive.extract_workspace_archive_base64",
        failing_extract,
    )

    result = runner.invoke(
        main,
        ["remote", "repl", "dev", "--repo", str(repo_path), "--goal", "hello"],
    )

    assert result.exit_code != 0
    assert "extract failed" in result.output
    assert "Remote session sess-upload left open" in result.output
    assert "python -m coding_agent attach dev --session sess-upload" in result.output
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
    ]


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

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr(
        "coding_agent.remote.client.stream_prompt", failing_stream_prompt
    )
    monkeypatch.setattr(
        "coding_agent.workspace_archive.create_workspace_archive_base64",
        lambda path: "archive-encoded",
    )
    monkeypatch.setattr(
        "coding_agent.remote.client.download_workspace_archive",
        lambda *, base_url, session_id, headers: "result-archive",
    )
    monkeypatch.setattr(
        "coding_agent.workspace_archive.extract_workspace_archive_base64",
        lambda repo_path_arg, archive_base64: (_ for _ in ()).throw(
            OSError("extract failed")
        ),
    )

    result = runner.invoke(
        main,
        ["remote", "repl", "dev", "--repo", str(repo_path), "--goal", "hello"],
    )

    assert result.exit_code != 0
    assert "stream failed" in result.output
    assert "Remote session sess-upload left open" in result.output
    assert "python -m coding_agent attach dev --session sess-upload" in result.output
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


def test_remote_approval_request_prompts_before_submitting_decision(monkeypatch) -> None:
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

    def fake_prompt(text: str, default: str | None = None, show_default: bool = True) -> str:
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
    assert approvals == [
        {"request_id": "req-1", "approved": True, "scope": "session"}
    ]


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

    def fake_prompt(text: str, default: str | None = None, show_default: bool = True) -> str:
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


def test_stream_prompt_reports_non_200_sse_response(monkeypatch) -> None:
    class FakeResponse:
        status_code = 503

        def raise_for_status(self) -> None:
            request = httpx.Request("POST", "http://agent.example/sessions/sess-1/prompt")
            raise httpx.HTTPStatusError(
                "bad status",
                request=request,
                response=httpx.Response(503, request=request, json={"detail": "server busy"}),
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
    monkeypatch.setattr("coding_agent.remote.client.connect_sse", lambda *args, **kwargs: FakeEventSource())

    from coding_agent.remote.client import stream_prompt

    with pytest.raises(click.ClickException, match="Failed to stream remote prompt: server busy"):
        stream_prompt(
            base_url="http://agent.example",
            session_id="sess-1",
            prompt="hello",
            headers={},
        )


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
            yield type("SSE", (), {"event": "StreamDelta", "data": json.dumps({"content": "partial"})})()

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            self.timeout = kwargs.get("timeout")

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    monkeypatch.setattr("coding_agent.remote.client.httpx.Client", FakeClient)
    monkeypatch.setattr("coding_agent.remote.client.connect_sse", lambda *args, **kwargs: FakeEventSource())

    from coding_agent.remote.client import stream_prompt

    with pytest.raises(click.ClickException, match="Remote prompt stream ended without TurnEnd"):
        stream_prompt(
            base_url="http://agent.example",
            session_id="sess-1",
            prompt="hello",
            headers={},
        )


def test_handle_sse_event_reports_invalid_json(monkeypatch) -> None:
    from coding_agent.remote.client import handle_sse_event

    with pytest.raises(click.ClickException, match="Remote SSE event payload must be valid JSON"):
        handle_sse_event(
            base_url="http://agent.example",
            session_id="sess-1",
            headers={},
            event="StreamDelta",
            data="{not-json",
        )
