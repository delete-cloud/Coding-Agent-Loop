from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from coding_agent.cli.main import main
from coding_agent.oauth.store import OAuthStore
from coding_agent.oauth.types import OAuthProviderRecord, OAuthTokens


def _store_record(auth_file: Path) -> None:
    store = OAuthStore(auth_file)
    store.set_provider(
        "codex",
        OAuthProviderRecord(
            issuer="https://auth.openai.com",
            client_id="codex-client",
            token_endpoint="https://auth.openai.com/oauth/token",
            base_url="https://chatgpt.com/backend-api/codex",
            tokens=OAuthTokens(
                access_token="secret-access-token",
                refresh_token="secret-refresh-token",
            ),
        ),
    )


def test_oauth_status_reports_missing_provider_without_creating_secrets(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    auth_file = tmp_path / "auth.json"

    result = runner.invoke(
        main,
        ["oauth", "status", "codex", "--auth-file", str(auth_file)],
    )

    assert result.exit_code == 0
    assert "codex: not logged in" in result.output
    assert "secret" not in result.output


def test_oauth_doctor_redacts_tokens(tmp_path: Path) -> None:
    runner = CliRunner()
    auth_file = tmp_path / "auth.json"
    _store_record(auth_file)

    result = runner.invoke(
        main,
        ["oauth", "doctor", "--auth-file", str(auth_file)],
    )

    assert result.exit_code == 0
    assert "Providers: codex" in result.output
    assert "<redacted>" in result.output
    assert "secret-access-token" not in result.output
    assert "secret-refresh-token" not in result.output


def test_oauth_doctor_handles_missing_auth_directory(tmp_path: Path) -> None:
    runner = CliRunner()
    auth_file = tmp_path / "missing" / "auth.json"

    result = runner.invoke(
        main,
        ["oauth", "doctor", "--auth-file", str(auth_file)],
    )

    assert result.exit_code == 0
    assert "Providers: none" in result.output
    assert "Directory mode: 700 (ok)" in result.output
    assert "File mode:" not in result.output
