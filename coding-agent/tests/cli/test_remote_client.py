from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from coding_agent.__main__ import main


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
        ["remote", "repl", "dev", "--repo", str(tmp_path), "--goal", "hello"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "Created remote session sess-123" in result.output
    assert calls == [
        (
            "post",
            "/sessions",
            {
                "repo_path": str(tmp_path),
                "workspace_source": {"kind": "docker"},
                "approval_policy": "auto",
            },
            {"Authorization": "Bearer secret-token"},
        ),
        (
            "stream",
            "/sessions/sess-123/prompt",
            {"prompt": "hello", "base_url": "http://agent.example"},
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
        ["remote", "repl", "dev", "--repo", str(tmp_path), "--goal", "hello"],
    )

    assert result.exit_code != 0
    assert "Failed to create remote session" in result.output
    assert "cloud workspace disabled" in result.output
