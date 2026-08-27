"""Contract tests for the ADR-0076 harness control plane, P3 first cut.

These tests pin the frozen IDL, not a runtime: the eleven D14 verbs must be
declared in ``protocol/harness/openrpc.yaml`` with zero handlers, the unix
socket must report ``unavailable`` with nothing listening, and the wire types
must carry ``EffectRef`` while never carrying ``ExecutionHandle``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_DIR = REPO_ROOT / "protocol" / "harness"
OPENRPC_PATH = PROTOCOL_DIR / "openrpc.yaml"
SCHEMA_TYPES_PATH = PROTOCOL_DIR / "schema" / "types.json"
SCHEMA_ERRORS_PATH = PROTOCOL_DIR / "schema" / "errors.json"
ADR_0076_PATH = REPO_ROOT / "docs" / "adr" / "0076-harness-control-plane.md"

HARNESS_METHODS = (
    "initialize",
    "session/subscribe",
    "turn/start",
    "turn/steer",
    "turn/interrupt",
    "turn/resume-wait",
    "session/resume",
    "approval/decide",
    "checkpoint/restore",
    "effect/reconcile",
    "effect/compensate",
)

MAINTENANCE_METHODS = frozenset(
    {"checkpoint/restore", "effect/reconcile", "effect/compensate"}
)

FENCED_PARAMS = (
    "SessionSubscribeParams",
    "TurnStartParams",
    "TurnSteerParams",
    "TurnInterruptParams",
    "TurnResumeWaitParams",
    "SessionResumeParams",
    "ApprovalDecideParams",
    "CheckpointRestoreParams",
    "EffectReconcileParams",
    "EffectCompensateParams",
)

METHOD_ERRORS = {
    "initialize": (),
    "session/subscribe": ("NotInitialized", "StaleFence", "CursorInvalid"),
    "turn/start": ("NotInitialized", "StaleFence"),
    "turn/steer": ("NotInitialized", "StaleFence"),
    "turn/interrupt": ("NotInitialized", "StaleFence"),
    "turn/resume-wait": ("NotInitialized", "StaleFence"),
    "session/resume": ("NotInitialized", "StaleFence"),
    "approval/decide": ("NotInitialized", "StaleFence"),
    "checkpoint/restore": ("NotInitialized", "StaleFence"),
    "effect/reconcile": ("NotInitialized", "StaleFence"),
    "effect/compensate": ("NotInitialized", "StaleFence", "QuiescentGate"),
}

ERROR_DATA_SCHEMAS = {
    "StaleFence": "StaleFenceData",
    "NotInitialized": "NotInitializedData",
    "QuiescentGate": "QuiescentGateData",
    "CursorInvalid": "CursorInvalidData",
}

U64_MAX = "18446744073709551615"
U64_MAX_PLUS_ONE = "18446744073709551616"


def _load_openrpc() -> dict:
    if not OPENRPC_PATH.exists():
        raise AssertionError(f"missing harness OpenRPC document: {OPENRPC_PATH}")
    document = yaml.safe_load(OPENRPC_PATH.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise AssertionError("openrpc.yaml must parse to a mapping")
    return document


def _load_schema(path: Path) -> dict:
    if not path.exists():
        raise AssertionError(f"missing harness JSON Schema document: {path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise AssertionError(f"{path.name} must parse to a mapping")
    return document


def _collect_strings(node: object) -> list[str]:
    found: list[str] = []
    if isinstance(node, str):
        found.append(node)
    elif isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                found.append(key)
            found.extend(_collect_strings(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_collect_strings(item))
    return found


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _collect_schema_refs(node: object) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and (
            ref.startswith("#/") or "/schema/" in ref or ref.startswith("./")
        ):
            found.append(_ref_name(ref))
        for value in node.values():
            found.extend(_collect_schema_refs(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_collect_schema_refs(item))
    return found


def _result_ref(method: dict) -> str:
    schema = method["result"]["schema"]
    ref = schema.get("$ref", "")
    assert ref, f"{method['name']} result must be a $ref"
    return _ref_name(ref)


def test_idl_openrpc_declares_exactly_the_eleven_d14_methods() -> None:
    document = _load_openrpc()
    methods = document.get("methods")
    assert isinstance(methods, list), "openrpc.yaml must declare a methods list"
    names = [method.get("name") for method in methods]
    assert sorted(names) == sorted(HARNESS_METHODS)
    assert len(names) == len(set(names))
    for method in methods:
        assert method.get("params"), f"{method['name']} must declare params"
        assert method.get("result"), f"{method['name']} must declare a result"


def test_idl_maintenance_verbs_are_tagged() -> None:
    document = _load_openrpc()
    by_name = {method["name"]: method for method in document["methods"]}
    for name in MAINTENANCE_METHODS:
        tags = by_name[name].get("tags") or []
        tag_names = {tag.get("name") for tag in tags}
        assert "maintenance" in tag_names, f"{name} must be tagged maintenance"


def test_idl_schemas_are_json_schema_draft_7() -> None:
    for path in (SCHEMA_TYPES_PATH, SCHEMA_ERRORS_PATH):
        document = _load_schema(path)
        draft = document.get("$schema", "")
        assert "draft-07" in draft, f"{path.name} must declare JSON Schema Draft 7"


def test_idl_u64_travels_as_decimal_string() -> None:
    types = _load_schema(SCHEMA_TYPES_PATH)
    u64 = types["definitions"]["U64"]
    assert u64["type"] == "string"
    assert u64.get("maxLength") == 20
    pattern = u64["pattern"]
    matcher = re.compile(pattern)
    for accepted in ("0", "1", "9", "10", U64_MAX):
        assert matcher.fullmatch(accepted), f"U64 must accept {accepted}"
    for rejected in ("", "00", "01", "-1", U64_MAX_PLUS_ONE, U64_MAX + "0"):
        assert matcher.fullmatch(rejected) is None, f"U64 must reject {rejected}"
    openrpc_text = OPENRPC_PATH.read_text(encoding="utf-8")
    assert "ExecutionHandle" not in openrpc_text


def test_idl_schema_refs_resolve_only_from_schema_bearing_locations() -> None:
    types = _load_schema(SCHEMA_TYPES_PATH)
    errors = _load_schema(SCHEMA_ERRORS_PATH)
    openrpc = _load_openrpc()
    known = set(types["definitions"]) | set(errors["definitions"])
    schema_nodes: list[object] = [types, errors]
    schema_nodes.extend(openrpc["components"]["schemas"].values())
    schema_nodes.extend(
        error["x-data-schema"]
        for error in openrpc["components"]["errors"].values()
    )
    for method in openrpc["methods"]:
        schema_nodes.extend(param["schema"] for param in method["params"])
        schema_nodes.append(method["result"]["schema"])
    referenced = {
        ref
        for schema_node in schema_nodes
        for ref in _collect_schema_refs(schema_node)
    }
    missing = sorted(referenced - known)
    assert not missing, f"unresolved schema $ref targets: {missing}"
    for method in openrpc["methods"]:
        params = method["params"][0]["schema"]["$ref"]
        result = method["result"]["schema"]["$ref"]
        assert _ref_name(params) in types["definitions"]
        assert _ref_name(result) in types["definitions"]


def test_idl_methods_reference_only_their_semantic_errors() -> None:
    openrpc = _load_openrpc()
    components = openrpc["components"]["errors"]
    assert set(components) == set(ERROR_DATA_SCHEMAS)
    by_name = {method["name"]: method for method in openrpc["methods"]}
    for method_name, expected_errors in METHOD_ERRORS.items():
        refs = by_name[method_name].get("errors", [])
        assert refs == [
            {"$ref": f"#/components/errors/{error_name}"}
            for error_name in expected_errors
        ]


def test_idl_error_data_schemas_use_an_openrpc_extension() -> None:
    openrpc = _load_openrpc()
    components = openrpc["components"]["errors"]
    for error_name, schema_name in ERROR_DATA_SCHEMAS.items():
        error = components[error_name]
        assert "data" not in error, (
            f"{error_name}.data is a literal Error Object value, not a schema"
        )
        assert error["x-data-schema"] == {
            "$ref": f"./schema/errors.json#/definitions/{schema_name}"
        }


def test_p3_server_is_explicitly_non_routable_and_unavailable_until_p4() -> None:
    openrpc = _load_openrpc()
    assert openrpc["servers"] == [
        {
            "name": "p3-unavailable",
            "url": "https://harness.invalid/p3-unavailable",
            "summary": "Non-routable placeholder; P3 declares no live server.",
            "x-availability": "unavailable-until-p4",
        }
    ]
    adr_text = ADR_0076_PATH.read_text(encoding="utf-8")
    assert "`https://harness.invalid/p3-unavailable`" in adr_text
    assert "`x-availability: unavailable-until-p4`" in adr_text


def test_idl_has_no_http_route_for_any_harness_verb() -> None:
    from coding_agent.server.http_server import app

    routes = list(app.routes)
    assert routes, "the FastAPI app must expose routes to scan"
    paths = [getattr(route, "path", "") or "" for route in routes]
    names = [getattr(route, "name", "") or "" for route in routes]
    for verb in HARNESS_METHODS:
        for path in paths:
            assert verb not in path, f"harness verb {verb!r} leaked into {path}"
        underscored = verb.replace("/", "_").replace("-", "_")
        for name in names:
            assert verb not in name and underscored not in name, (
                f"harness verb {verb!r} leaked into HTTP route name {name}"
            )


def test_unix_socket_status_is_unavailable() -> None:
    from coding_agent.harness import UNIX_SOCKET_STATUS, unix_socket_status

    assert UNIX_SOCKET_STATUS == "unavailable"
    assert unix_socket_status() == "unavailable"


def test_unix_socket_is_not_bound_or_configured() -> None:
    import coding_agent.harness as harness_pkg

    assert harness_pkg.protocol_dir() == PROTOCOL_DIR
    serve_source = (
        REPO_ROOT / "src" / "coding_agent" / "cli" / "serve_command.py"
    ).read_text(encoding="utf-8")
    assert "--socket" not in serve_source
    assert "AF_UNIX" not in serve_source
    harness_source = (
        REPO_ROOT / "src" / "coding_agent" / "harness" / "__init__.py"
    ).read_text(encoding="utf-8")
    assert "AF_UNIX" not in harness_source
    assert ".bind(" not in harness_source
    assert ".listen(" not in harness_source


def test_unix_socket_status_is_in_the_idl() -> None:
    types = _load_schema(SCHEMA_TYPES_PATH)
    status = types["definitions"]["UnixSocketStatus"]
    assert status["const"] == "unavailable"
    initialize_result = types["definitions"]["InitializeResult"]
    assert "unix_socket_status" in initialize_result["required"]


def test_effect_ref_is_a_wire_type() -> None:
    types = _load_schema(SCHEMA_TYPES_PATH)
    definitions = types["definitions"]
    assert "EffectRef" in definitions
    effect_ref = definitions["EffectRef"]
    assert "effect_id" in effect_ref["required"]
    assert effect_ref["properties"]["effect_id"] == {"$ref": "#/definitions/U64"}
    for params_name in (
        "ApprovalDecideParams",
        "TurnResumeWaitParams",
        "EffectReconcileParams",
        "EffectCompensateParams",
    ):
        params = definitions[params_name]
        assert params["properties"]["effect_ref"] == {
            "$ref": "#/definitions/EffectRef"
        }


def test_effect_ref_execution_handle_is_not_a_wire_type() -> None:
    for path in (OPENRPC_PATH, SCHEMA_TYPES_PATH, SCHEMA_ERRORS_PATH):
        text = path.read_text(encoding="utf-8")
        assert "ExecutionHandle" not in text
    types = _load_schema(SCHEMA_TYPES_PATH)
    for definition_name in types["definitions"]:
        assert "ExecutionHandle" not in definition_name
    openrpc_strings = _collect_strings(_load_openrpc())
    assert not any("ExecutionHandle" in value for value in openrpc_strings)


def test_effect_ref_adr_0076_documents_the_method_table() -> None:
    if not ADR_0076_PATH.exists():
        raise AssertionError(f"missing ADR: {ADR_0076_PATH}")
    text = ADR_0076_PATH.read_text(encoding="utf-8")
    assert "**Status**: Proposed" in text
    for verb in HARNESS_METHODS:
        assert verb in text, f"ADR-0076 method table must name {verb}"
    assert "every verb except `initialize`" in text
    assert "These tests do not exist yet" not in text
    assert "does not implement protocol, storage, daemon, or frontend code" not in text


def test_idl_initialize_is_the_only_unfenced_verb() -> None:
    types = _load_schema(SCHEMA_TYPES_PATH)
    initialize = types["definitions"]["InitializeParams"]
    assert "fence" not in initialize.get("properties", {})
    assert initialize["required"] == ["client_name", "protocol_version"]
    for name in FENCED_PARAMS:
        params = types["definitions"][name]
        assert "fence" in params["required"], f"{name} must require SessionFence"
        assert params["properties"]["fence"] == {"$ref": "#/definitions/SessionFence"}


def test_idl_maintenance_results_are_not_empty_acks() -> None:
    document = _load_openrpc()
    by_name = {method["name"]: method for method in document["methods"]}
    types = _load_schema(SCHEMA_TYPES_PATH)
    assert _result_ref(by_name["effect/reconcile"]) == "EffectReconcileResult"
    assert _result_ref(by_name["effect/compensate"]) == "EffectCompensateResult"
    reconcile = types["definitions"]["EffectReconcileResult"]
    assert set(reconcile["required"]) == {
        "effect_ref",
        "classification",
        "attempt_state",
        "settlement",
    }
    compensate = types["definitions"]["EffectCompensateResult"]
    assert set(compensate["required"]) == {
        "generation",
        "compensation_effect_id",
        "attempt_state",
        "settlement",
    }
    compensate_params = types["definitions"]["EffectCompensateParams"]
    assert "generation" in compensate_params["required"]
    initialize = _load_openrpc()["methods"][0]
    assert initialize["name"] == "initialize"
    assert "capabilities" not in initialize.get("summary", "")
