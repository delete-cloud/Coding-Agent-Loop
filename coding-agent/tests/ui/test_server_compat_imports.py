from __future__ import annotations

import importlib


def test_ui_server_module_aliases_point_to_canonical_modules() -> None:
    module_pairs = [
        ("coding_agent.ui.developer_console", "coding_agent.server.developer_console"),
        ("coding_agent.ui.http_server", "coding_agent.server.http_server"),
        ("coding_agent.ui.rate_limit", "coding_agent.server.rate_limit"),
        ("coding_agent.ui.session_manager", "coding_agent.server.session_manager"),
        (
            "coding_agent.ui.session_owner_store",
            "coding_agent.server.stores.session_owner_store",
        ),
        ("coding_agent.ui.session_store", "coding_agent.server.stores.session_store"),
        (
            "coding_agent.ui.workspace_store",
            "coding_agent.server.stores.workspace_store",
        ),
    ]

    for legacy_name, canonical_name in module_pairs:
        assert importlib.import_module(legacy_name) is importlib.import_module(
            canonical_name
        )


def test_ui_server_compat_exports_point_to_canonical_symbols() -> None:
    auth = importlib.import_module("coding_agent.ui.auth")
    canonical_auth = importlib.import_module("coding_agent.server.auth")
    schemas = importlib.import_module("coding_agent.ui.schemas")
    canonical_schemas = importlib.import_module("coding_agent.server.schemas")

    assert auth.verify_api_key is canonical_auth.verify_api_key
    assert auth.AuthContext is canonical_auth.AuthContext
    assert schemas.CreateSessionRequest is canonical_schemas.CreateSessionRequest
    assert schemas.ApprovalResponseSchema is canonical_schemas.ApprovalResponseSchema
