"""Generic Bee template pack manifest loading (legacy).

Template pack discovery is static metadata loading. It validates manifests and
workspace templates but never executes commands or creates durable Bee runs.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, cast

import yaml

from coding_agent.bee.launch import BeeLaunchRequest, build_bee_launch_plan
from coding_agent.bee.runtime import BeeTaskManifest
from coding_agent.bee.workspace import (
    BeeWorkspaceTemplate,
    build_bee_manifest_from_workspace_template,
    discover_bee_workspace_templates,
    load_bee_workspace_command_intents,
    load_bee_workspace_template,
)
from coding_agent.topics.store import JSONObject

_MANIFEST_CANDIDATES: Final[tuple[tuple[str, str], ...]] = (
    ("bee-pack.yaml", "yaml"),
    ("bee-pack.json", "json"),
    (".bee/pack.yaml", "yaml"),
    (".bee/pack.json", "json"),
)
_SAFE_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_FORBIDDEN_PACK_KEY_PARTS: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "apikey",
        "args",
        "argv",
        "cmd",
        "command",
        "commands",
        "command_output",
        "credential",
        "credentials",
        "content",
        "env",
        "environment",
        "exec",
        "executor",
        "key",
        "message",
        "password",
        "prompt",
        "result",
        "script",
        "secret",
        "shell",
        "stderr",
        "stdout",
        "text",
        "token",
    }
)
_COMPACT_FORBIDDEN_PACK_KEY_PARTS: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "apikey",
        "args",
        "argv",
        "cmd",
        "command",
        "commands",
        "command_output",
        "credential",
        "credentials",
        "env",
        "environment",
        "exec",
        "executor",
        "key",
        "password",
        "prompt",
        "secret",
        "shell",
        "stderr",
        "stdout",
        "token",
    }
)
_ALLOWED_PACK_STATIC_CAPABILITY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "executor_kind",
        "executor_kinds",
        "supported_executor_kinds",
    }
)
_SECRET_VALUE_MARKERS: Final[tuple[str, ...]] = (
    "-----begin ",
    "bearer ",
    "gho_",
    "ghp_",
    "github_pat_",
    "password=",
    "secret=",
    "sk-",
    "token=",
)
_MAX_SAFE_TEXT_CHARS: Final[int] = 256
_SUPPORTED_EXECUTOR_KINDS: Final[frozenset[str]] = frozenset(
    {
        "local",
        "docker",
        "kubernetes_job",
        "argo_workflow",
        "fixture",
    }
)
_COMPATIBILITY_CHECK_ORDER: Final[tuple[str, ...]] = (
    "pack_manifest",
    "template_schema",
    "skill_file",
    "feature_files",
    "commands_yaml_intents",
    "command_ref_references",
    "node_dependencies",
    "acceptance_criteria",
    "risk_profile",
    "report_output_contract",
    "memory_candidate_contract",
    "executor_capability",
    "static_no_raw_keys",
)
_COMPATIBILITY_VALIDATION_EXCEPTIONS: Final[tuple[type[Exception], ...]] = (
    FileNotFoundError,
    json.JSONDecodeError,
    OSError,
    TypeError,
    ValueError,
    yaml.YAMLError,
)


class BeeTemplatePackSource(StrEnum):
    LOCAL_WORKSPACE = "local_workspace"
    FIXTURE = "fixture"
    IMPORTED = "imported"


@dataclass(frozen=True)
class BeePackManifest:
    pack_id: str
    name: str
    version: str
    template_ids: tuple[str, ...]
    description: str | None = None
    domain_profile: str | None = None
    default_workspace_policy: JSONObject | None = None
    default_topic_policy: JSONObject | None = None
    default_memory_policy: JSONObject | None = None
    tags: tuple[str, ...] = ()
    metadata: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", dict(self.metadata or {}))


@dataclass(frozen=True)
class BeeTemplatePack:
    manifest: BeePackManifest
    root: Path
    source: BeeTemplatePackSource
    templates: tuple[BeeWorkspaceTemplate, ...]
    manifest_path: Path | None = None


@dataclass(frozen=True)
class BeePackSummary:
    pack_id: str
    name: str
    version: str
    source: BeeTemplatePackSource
    root: Path
    manifest_path: Path | None
    domain_profile: str | None
    tags: tuple[str, ...]
    template_count: int


@dataclass(frozen=True)
class BeePackTemplateSummary:
    pack_id: str
    template_id: str
    source: BeeTemplatePackSource
    template_kind: str
    template_profile: str
    title: str
    template_dir: Path
    manifest_path: Path | None


@dataclass(frozen=True)
class BeePackTemplateProvenance:
    pack_id: str
    template_id: str
    source: BeeTemplatePackSource
    root: Path
    manifest_path: Path | None
    template_dir: Path


@dataclass(frozen=True)
class BeePackCompatibilityCheck:
    check_id: str
    status: str
    summary: str

    def to_safe_dict(self) -> JSONObject:
        return {
            "check_id": self.check_id,
            "status": self.status,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class BeePackCompatibilityFinding:
    check_id: str
    severity: str
    scope: str
    message: str
    recommended_fix: str

    def to_safe_dict(self) -> JSONObject:
        return {
            "check_id": self.check_id,
            "severity": self.severity,
            "scope": self.scope,
            "message": self.message,
            "recommended_fix": self.recommended_fix,
        }


@dataclass(frozen=True)
class BeePackTemplateCompatibilitySummary:
    template_id: str
    status: str
    feature_count: int
    command_count: int
    finding_count: int

    def to_safe_dict(self) -> JSONObject:
        return {
            "template_id": self.template_id,
            "status": self.status,
            "feature_count": self.feature_count,
            "command_count": self.command_count,
            "finding_count": self.finding_count,
        }


@dataclass(frozen=True)
class BeePackCompatibilityReport:
    status: str
    pack_id: str | None
    source: BeeTemplatePackSource
    checks: tuple[BeePackCompatibilityCheck, ...]
    findings: tuple[BeePackCompatibilityFinding, ...]
    templates: tuple[BeePackTemplateCompatibilitySummary, ...]

    def to_safe_dict(self) -> JSONObject:
        payload: JSONObject = {
            "status": self.status,
            "source": self.source.value,
            "checks": [check.to_safe_dict() for check in self.checks],
            "findings": [finding.to_safe_dict() for finding in self.findings],
            "templates": [template.to_safe_dict() for template in self.templates],
        }
        if self.pack_id is not None:
            payload["pack_id"] = self.pack_id
        return payload


@dataclass(frozen=True)
class BeePackDryRunPlan:
    status: str
    pack_id: str
    template_id: str
    source: BeeTemplatePackSource
    launch_preview: JSONObject
    topic_policy: JSONObject
    workspace_policy: JSONObject
    task_preview: JSONObject
    task_json_path: str
    report_path: str
    evidence_dir: str
    memory_candidates_path: str
    nodes: tuple[JSONObject, ...]
    command_intents: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    def to_safe_dict(self) -> JSONObject:
        return {
            "status": self.status,
            "pack_id": self.pack_id,
            "template_id": self.template_id,
            "source": self.source.value,
            "launch_preview": dict(self.launch_preview),
            "topic_policy": dict(self.topic_policy),
            "workspace_policy": dict(self.workspace_policy),
            "task_preview": dict(self.task_preview),
            "task_json_path": self.task_json_path,
            "report_path": self.report_path,
            "evidence_dir": self.evidence_dir,
            "memory_candidates_path": self.memory_candidates_path,
            "nodes": [dict(node) for node in self.nodes],
            "command_intents": list(self.command_intents),
            "warnings": list(self.warnings),
        }


@dataclass
class BeePackRegistry:
    _packs: dict[str, BeeTemplatePack] = field(default_factory=dict)

    @classmethod
    def discover(
        cls,
        roots: tuple[Path | str, ...] | list[Path | str],
        *,
        source: BeeTemplatePackSource = BeeTemplatePackSource.LOCAL_WORKSPACE,
    ) -> BeePackRegistry:
        registry = cls()
        for root in roots:
            registry.register(load_bee_template_pack(root, source=source))
        return registry

    def register(self, pack: BeeTemplatePack) -> None:
        pack_id = pack.manifest.pack_id
        if pack_id in self._packs:
            raise ValueError(f"Bee pack is already registered: {pack_id}")
        self._packs[pack_id] = pack

    def list_packs(self) -> tuple[BeePackSummary, ...]:
        return tuple(
            _pack_summary(self._packs[pack_id]) for pack_id in sorted(self._packs)
        )

    def list_templates(self, pack_id: str) -> tuple[BeePackTemplateSummary, ...]:
        pack = self._require_pack(pack_id)
        return tuple(
            _template_summary(pack, template)
            for template in sorted(pack.templates, key=lambda item: item.template_id)
        )

    def load_template(
        self,
        pack_id: str,
        template_id: str,
    ) -> BeeWorkspaceTemplate:
        pack = self._require_pack(pack_id)
        for template in pack.templates:
            if template.template_id == template_id:
                return template
        raise KeyError(f"Bee template not found in pack {pack_id}: {template_id}")

    def template_provenance(
        self,
        pack_id: str,
        template_id: str,
    ) -> BeePackTemplateProvenance:
        template = self.load_template(pack_id, template_id)
        pack = self._require_pack(pack_id)
        return BeePackTemplateProvenance(
            pack_id=pack.manifest.pack_id,
            template_id=template.template_id,
            source=pack.source,
            root=pack.root,
            manifest_path=pack.manifest_path,
            template_dir=template.template_dir,
        )

    def _require_pack(self, pack_id: str) -> BeeTemplatePack:
        if pack_id not in self._packs:
            raise KeyError(f"Bee pack not found: {pack_id}")
        return self._packs[pack_id]


def build_bee_pack_dry_run_plan(
    registry: BeePackRegistry,
    *,
    pack_id: str,
    template_id: str,
    inputs: JSONObject,
    topic_policy: JSONObject | None = None,
    workspace_policy: JSONObject | None = None,
    requested_at: datetime | None = None,
) -> BeePackDryRunPlan:
    """Build a non-durable Bee launch preview for a pack template."""

    pack = registry._require_pack(pack_id)
    template = registry.load_template(pack_id, template_id)
    launch_id = f"dry-run-launch-{pack_id}-{template_id}"
    launch_plan = build_bee_launch_plan(
        BeeLaunchRequest(
            launch_id=launch_id,
            source="manual",
            template_id=template.template_id,
            workspace_root=pack.root,
            requested_at=requested_at or datetime.now(UTC),
            inputs=dict(inputs),
            topic_policy=dict(topic_policy or {}),
            workspace_policy=dict(workspace_policy or {}),
            metadata={
                "pack_id": pack.manifest.pack_id,
                "pack_source": pack.source.value,
                "dry_run": True,
            },
        )
    )
    _require_dry_run_command_refs_resolved(launch_plan)
    task_id = f"dry-run-task-{pack_id}-{template_id}"
    run_root = f".bee/runs/{task_id}"
    warnings = _dry_run_warnings(pack)
    return BeePackDryRunPlan(
        status="warning" if warnings else "ready",
        pack_id=pack.manifest.pack_id,
        template_id=template.template_id,
        source=pack.source,
        launch_preview={
            "launch_id": launch_id,
            "source": launch_plan.source,
            "template_kind": launch_plan.resolution.template_kind,
            "template_profile": launch_plan.resolution.template_profile,
        },
        topic_policy=dict(launch_plan.topic_policy),
        workspace_policy=dict(launch_plan.workspace_policy),
        task_preview={
            "task_id": task_id,
            "kind": launch_plan.manifest.kind,
            "profile": launch_plan.manifest.profile,
            "title": launch_plan.manifest.title,
        },
        task_json_path=f"{run_root}/task.json",
        report_path=f"{run_root}/report.md",
        evidence_dir=f"{run_root}/evidence",
        memory_candidates_path=f"{run_root}/memory_candidates.yaml",
        nodes=tuple(_dry_run_node(node) for node in launch_plan.manifest.nodes),
        command_intents=launch_plan.resolution.command_intent_names,
        warnings=warnings,
    )


def validate_bee_pack_compatibility(
    root: Path | str,
    *,
    source: BeeTemplatePackSource = BeeTemplatePackSource.LOCAL_WORKSPACE,
) -> BeePackCompatibilityReport:
    """Validate a Bee template pack using static artifacts only."""

    recorder = _CompatibilityRecorder(source=source)
    try:
        pack = load_bee_template_pack(root, source=source)
    except _COMPATIBILITY_VALIDATION_EXCEPTIONS as exc:
        pack_id = _best_effort_pack_id(Path(root))
        recorder.add_check(
            "pack_manifest", "failed", "Pack manifest or templates failed to load"
        )
        recorder.add_finding(
            check_id="pack_manifest",
            severity="error",
            scope="pack",
            message=_safe_exception_message(exc),
            recommended_fix="Fix the pack manifest and referenced template files.",
        )
        recorder.add_check(
            "static_no_raw_keys", "failed", "Static artifact safety validation failed"
        )
        return recorder.report(pack_id=pack_id, templates=())

    recorder.add_check("pack_manifest", "passed", "Pack manifest loaded")
    recorder.add_check(
        "static_no_raw_keys", "passed", "Static artifacts passed no-raw-key validation"
    )
    _validate_executor_capability(pack, recorder)

    template_summaries = tuple(
        _validate_template_compatibility(template, recorder)
        for template in pack.templates
    )
    return recorder.report(
        pack_id=pack.manifest.pack_id,
        templates=template_summaries,
    )


def _require_dry_run_command_refs_resolved(launch_plan: Any) -> None:
    command_intents = set(launch_plan.resolution.command_intent_names)
    missing_refs = [
        node.command_ref
        for node in launch_plan.manifest.nodes
        if node.command_ref is not None and node.command_ref not in command_intents
    ]
    if missing_refs:
        missing = ", ".join(sorted(str(item) for item in set(missing_refs)))
        raise ValueError(f"unknown Bee command_ref in dry-run plan: {missing}")


def _dry_run_node(node: Any) -> JSONObject:
    payload: JSONObject = {
        "node_id": node.node_id,
        "kind": node.kind,
        "profile": node.profile,
    }
    if node.command_ref is not None:
        payload["command_ref"] = node.command_ref
    if node.depends_on:
        payload["depends_on"] = list(node.depends_on)
    return payload


def _dry_run_warnings(pack: BeeTemplatePack) -> tuple[str, ...]:
    executor_kind = pack.manifest.metadata.get("executor_kind")
    if executor_kind is None:
        return ()
    if (
        isinstance(executor_kind, str)
        and executor_kind not in _SUPPORTED_EXECUTOR_KINDS
    ):
        return (f"Executor kind {executor_kind} is unsupported or deferred",)
    return ()


def load_bee_template_pack(
    root: Path | str,
    *,
    source: BeeTemplatePackSource = BeeTemplatePackSource.LOCAL_WORKSPACE,
) -> BeeTemplatePack:
    """Load and validate a Bee template pack from a workspace root."""

    pack_root = Path(root)
    manifest_path, raw_manifest = _load_manifest_file(pack_root)
    if raw_manifest is None:
        return _load_implicit_local_pack(pack_root, source=source)

    manifest = _parse_manifest(raw_manifest, manifest_path=manifest_path)
    templates = tuple(
        load_bee_workspace_template(pack_root, template_id)
        for template_id in manifest.template_ids
    )
    return BeeTemplatePack(
        manifest=manifest,
        root=pack_root,
        source=source,
        templates=templates,
        manifest_path=manifest_path,
    )


def _pack_summary(pack: BeeTemplatePack) -> BeePackSummary:
    return BeePackSummary(
        pack_id=pack.manifest.pack_id,
        name=pack.manifest.name,
        version=pack.manifest.version,
        source=pack.source,
        root=pack.root,
        manifest_path=pack.manifest_path,
        domain_profile=pack.manifest.domain_profile,
        tags=pack.manifest.tags,
        template_count=len(pack.templates),
    )


def _template_summary(
    pack: BeeTemplatePack,
    template: BeeWorkspaceTemplate,
) -> BeePackTemplateSummary:
    return BeePackTemplateSummary(
        pack_id=pack.manifest.pack_id,
        template_id=template.template_id,
        source=pack.source,
        template_kind=_template_metadata_label(template, "kind"),
        template_profile=_template_metadata_label(template, "profile"),
        title=_template_metadata_label(template, "title"),
        template_dir=template.template_dir,
        manifest_path=pack.manifest_path,
    )


def _template_metadata_label(template: BeeWorkspaceTemplate, key: str) -> str:
    value = template.metadata.get(key)
    if isinstance(value, str) and value:
        return value
    return "unknown"


def _validate_template_compatibility(
    template: BeeWorkspaceTemplate,
    recorder: _CompatibilityRecorder,
) -> BeePackTemplateCompatibilitySummary:
    scope = f"template:{template.template_id}"
    template_findings_before = len(recorder.findings)
    feature_count = 0
    command_count = 0
    manifest: BeeTaskManifest | None = None

    if template.skill_path.is_file():
        recorder.add_check("skill_file", "passed", "SKILL.md exists")
    else:
        recorder.add_check("skill_file", "failed", "SKILL.md missing")
        recorder.add_finding(
            check_id="skill_file",
            severity="error",
            scope=scope,
            message=f"Template {template.template_id} is missing SKILL.md",
            recommended_fix="Add a SKILL.md file to the template directory.",
        )

    feature_count = len(template.feature_paths)
    if feature_count:
        recorder.add_check("feature_files", "passed", "features/*.feature discovered")
    else:
        recorder.add_check("feature_files", "failed", "No feature files discovered")
        recorder.add_finding(
            check_id="feature_files",
            severity="error",
            scope=scope,
            message=f"Template {template.template_id} has no features/*.feature files",
            recommended_fix="Add at least one acceptance feature file.",
        )
    _validate_acceptance_criteria(template, recorder)

    try:
        manifest = build_bee_manifest_from_workspace_template(template)
    except _COMPATIBILITY_VALIDATION_EXCEPTIONS as exc:
        recorder.add_check(
            "template_schema", "failed", "Template manifest schema failed"
        )
        recorder.add_check("node_dependencies", "failed", "Node dependencies failed")
        recorder.add_finding(
            check_id="template_schema",
            severity="error",
            scope=scope,
            message=_safe_exception_message(exc),
            recommended_fix="Fix template metadata so it satisfies the Bee manifest schema.",
        )
    else:
        recorder.add_check(
            "template_schema", "passed", "Template manifest schema valid"
        )
        recorder.add_check("node_dependencies", "passed", "Node dependencies valid")

    try:
        intents = load_bee_workspace_command_intents(template)
    except _COMPATIBILITY_VALIDATION_EXCEPTIONS as exc:
        intents = ()
        recorder.add_check(
            "commands_yaml_intents", "failed", "commands.yaml intent validation failed"
        )
        recorder.add_finding(
            check_id="commands_yaml_intents",
            severity="error",
            scope=scope,
            message=_safe_exception_message(exc),
            recommended_fix="Fix commands.yaml to contain only safe command intent metadata.",
        )
    else:
        recorder.add_check(
            "commands_yaml_intents", "passed", "commands.yaml intents valid"
        )
        command_count = len(intents)

    if manifest is not None:
        _validate_command_refs(template, manifest, intents, recorder)
        _validate_template_contracts(template, recorder)
    else:
        recorder.add_check(
            "command_ref_references", "failed", "Template manifest unavailable"
        )

    finding_count = len(recorder.findings) - template_findings_before
    return BeePackTemplateCompatibilitySummary(
        template_id=template.template_id,
        status=_status_from_findings(recorder.findings[template_findings_before:]),
        feature_count=feature_count,
        command_count=command_count,
        finding_count=finding_count,
    )


def _validate_command_refs(
    template: BeeWorkspaceTemplate,
    manifest: BeeTaskManifest,
    intents: tuple[Any, ...],
    recorder: _CompatibilityRecorder,
) -> None:
    declared_intents = {intent.name for intent in intents}
    missing_refs = [
        node.command_ref
        for node in manifest.nodes
        if node.command_ref is not None and node.command_ref not in declared_intents
    ]
    if not missing_refs:
        recorder.add_check(
            "command_ref_references", "passed", "command_ref references valid"
        )
        return
    recorder.add_check(
        "command_ref_references", "failed", "Unknown command_ref references found"
    )
    for command_ref in sorted(set(missing_refs)):
        recorder.add_finding(
            check_id="command_ref_references",
            severity="error",
            scope=f"template:{template.template_id}",
            message=f"command_ref {command_ref} is not declared in commands.yaml",
            recommended_fix="Declare the command_ref in commands.yaml or remove it from the node.",
        )


def _validate_acceptance_criteria(
    template: BeeWorkspaceTemplate,
    recorder: _CompatibilityRecorder,
) -> None:
    non_empty_features = [
        path
        for path in template.feature_paths
        if path.read_text(encoding="utf-8").strip()
    ]
    if non_empty_features:
        recorder.add_check("acceptance_criteria", "passed", "Acceptance criteria exist")
        return
    recorder.add_check("acceptance_criteria", "failed", "Acceptance criteria missing")
    recorder.add_finding(
        check_id="acceptance_criteria",
        severity="error",
        scope=f"template:{template.template_id}",
        message=f"Template {template.template_id} has no non-empty acceptance criteria",
        recommended_fix="Add non-empty features/*.feature acceptance criteria.",
    )


def _validate_template_contracts(
    template: BeeWorkspaceTemplate,
    recorder: _CompatibilityRecorder,
) -> None:
    metadata = _template_contract_metadata(template)
    if metadata.get("risk_profile") or metadata.get("risk"):
        recorder.add_check("risk_profile", "passed", "Risk profile declared")
    else:
        recorder.add_check("risk_profile", "warning", "Risk profile missing")
        recorder.add_finding(
            check_id="risk_profile",
            severity="warning",
            scope=f"template:{template.template_id}",
            message=f"Template {template.template_id} does not declare risk_profile",
            recommended_fix="Declare metadata.risk_profile with a bounded safe value.",
        )

    if metadata.get("report_output_contract") or metadata.get("report_contract"):
        recorder.add_check(
            "report_output_contract", "passed", "Report output contract declared"
        )
    else:
        recorder.add_check(
            "report_output_contract", "warning", "Report output contract missing"
        )
        recorder.add_finding(
            check_id="report_output_contract",
            severity="warning",
            scope=f"template:{template.template_id}",
            message=f"Template {template.template_id} does not declare a report output contract",
            recommended_fix="Declare metadata.report_output_contract for sanitized reports.",
        )

    memory_contract = metadata.get("memory_candidates")
    if (
        isinstance(memory_contract, dict)
        and memory_contract.get("review_required") is True
    ):
        recorder.add_check(
            "memory_candidate_contract",
            "passed",
            "Memory candidate contract is review-gated",
        )
    elif memory_contract is None:
        recorder.add_check(
            "memory_candidate_contract",
            "warning",
            "Optional memory candidate contract missing",
        )
        recorder.add_finding(
            check_id="memory_candidate_contract",
            severity="warning",
            scope=f"template:{template.template_id}",
            message=f"Template {template.template_id} does not declare memory_candidates",
            recommended_fix="Declare metadata.memory_candidates.review_required: true when the template emits memory candidates.",
        )
    else:
        recorder.add_check(
            "memory_candidate_contract",
            "failed",
            "Memory candidate contract is not review-gated",
        )
        recorder.add_finding(
            check_id="memory_candidate_contract",
            severity="error",
            scope=f"template:{template.template_id}",
            message=f"Template {template.template_id} memory_candidates contract must require review",
            recommended_fix="Set metadata.memory_candidates.review_required to true.",
        )


def _validate_executor_capability(
    pack: BeeTemplatePack,
    recorder: _CompatibilityRecorder,
) -> None:
    executor_kind = pack.manifest.metadata.get("executor_kind")
    if executor_kind is None:
        recorder.add_check(
            "executor_capability", "passed", "No external executor required"
        )
        return
    if not isinstance(executor_kind, str):
        recorder.add_check(
            "executor_capability", "failed", "executor_kind must be a string"
        )
        recorder.add_finding(
            check_id="executor_capability",
            severity="error",
            scope=f"pack:{pack.manifest.pack_id}",
            message="Pack executor_kind must be a string",
            recommended_fix="Use a bounded executor_kind string or omit the field.",
        )
        return
    if executor_kind in _SUPPORTED_EXECUTOR_KINDS:
        recorder.add_check(
            "executor_capability", "passed", "Executor kind is supported"
        )
        return
    recorder.add_check(
        "executor_capability", "warning", "Executor kind is unsupported or deferred"
    )
    recorder.add_finding(
        check_id="executor_capability",
        severity="warning",
        scope=f"pack:{pack.manifest.pack_id}",
        message=f"Executor kind {executor_kind} is unsupported or deferred",
        recommended_fix="Use a supported executor kind or treat this pack as dry-run only.",
    )


def _template_contract_metadata(template: BeeWorkspaceTemplate) -> JSONObject:
    metadata = template.metadata.get("metadata", {})
    if isinstance(metadata, dict):
        return cast(JSONObject, metadata)
    return {}


def _best_effort_pack_id(root: Path) -> str | None:
    try:
        manifest_path, raw_manifest = _load_manifest_file(root)
        if raw_manifest is None:
            return None
        return _parse_manifest(raw_manifest, manifest_path=manifest_path).pack_id
    except _COMPATIBILITY_VALIDATION_EXCEPTIONS:
        return None


def _safe_exception_message(exc: Exception) -> str:
    message = str(exc) or exc.__class__.__name__
    if len(message) > _MAX_SAFE_TEXT_CHARS:
        return message[:_MAX_SAFE_TEXT_CHARS]
    return message


def _status_from_findings(
    findings: tuple[BeePackCompatibilityFinding, ...]
    | list[BeePackCompatibilityFinding],
) -> str:
    if any(finding.severity == "error" for finding in findings):
        return "incompatible"
    if any(finding.severity == "warning" for finding in findings):
        return "warning"
    return "compatible"


@dataclass
class _CompatibilityRecorder:
    source: BeeTemplatePackSource
    checks_by_id: dict[str, BeePackCompatibilityCheck] = field(default_factory=dict)
    findings: list[BeePackCompatibilityFinding] = field(default_factory=list)

    def add_check(self, check_id: str, status: str, summary: str) -> None:
        current = self.checks_by_id.get(check_id)
        next_check = BeePackCompatibilityCheck(
            check_id=check_id,
            status=status,
            summary=summary,
        )
        if current is None or _check_status_rank(status) > _check_status_rank(
            current.status
        ):
            self.checks_by_id[check_id] = next_check

    def add_finding(
        self,
        *,
        check_id: str,
        severity: str,
        scope: str,
        message: str,
        recommended_fix: str,
    ) -> None:
        self.findings.append(
            BeePackCompatibilityFinding(
                check_id=check_id,
                severity=severity,
                scope=scope,
                message=message,
                recommended_fix=recommended_fix,
            )
        )

    def report(
        self,
        *,
        pack_id: str | None,
        templates: tuple[BeePackTemplateCompatibilitySummary, ...],
    ) -> BeePackCompatibilityReport:
        checks = tuple(
            self.checks_by_id[check_id]
            for check_id in _COMPATIBILITY_CHECK_ORDER
            if check_id in self.checks_by_id
        )
        findings = tuple(self.findings)
        return BeePackCompatibilityReport(
            status=_status_from_findings(findings),
            pack_id=pack_id,
            source=self.source,
            checks=checks,
            findings=findings,
            templates=templates,
        )


def _check_status_rank(status: str) -> int:
    if status == "failed":
        return 3
    if status == "warning":
        return 2
    if status == "passed":
        return 1
    return 0


def _load_manifest_file(root: Path) -> tuple[Path | None, JSONObject | None]:
    for relative_path, manifest_format in _MANIFEST_CANDIDATES:
        candidate = root / relative_path
        if candidate.is_symlink():
            raise ValueError(f"Bee pack manifest must not be a symlink: {candidate}")
        if not candidate.exists():
            continue
        if not candidate.is_file():
            raise ValueError(f"Bee pack manifest must be a file: {candidate}")
        loaded = _read_manifest(candidate, manifest_format)
        if not isinstance(loaded, dict):
            raise TypeError(f"Bee pack manifest must be an object: {candidate}")
        return candidate, cast(JSONObject, dict(loaded))
    return None, None


def _read_manifest(path: Path, manifest_format: str) -> object:
    if manifest_format == "json":
        return json.loads(path.read_text(encoding="utf-8"))
    if manifest_format == "yaml":
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    raise ValueError(f"unsupported Bee pack manifest format: {manifest_format}")


def _load_implicit_local_pack(
    root: Path,
    *,
    source: BeeTemplatePackSource,
) -> BeeTemplatePack:
    templates = tuple(discover_bee_workspace_templates(root))
    if not templates:
        raise FileNotFoundError(
            "Bee pack manifest not found and no .bee/templates exist"
        )
    template_ids = tuple(template.template_id for template in templates)
    manifest = BeePackManifest(
        pack_id="local",
        name="Local Bee Template Pack",
        version="0.0.0",
        description="Implicit local pack derived from .bee/templates.",
        template_ids=template_ids,
    )
    return BeeTemplatePack(
        manifest=manifest,
        root=root,
        source=source,
        templates=templates,
        manifest_path=None,
    )


def _parse_manifest(
    raw: Mapping[str, Any], *, manifest_path: Path | None
) -> BeePackManifest:
    _validate_safe_json("bee_pack_manifest", raw)
    pack_id = _required_safe_id(raw, "pack_id", manifest_path)
    name = _required_safe_text(raw, "name", manifest_path)
    version = _required_safe_text(raw, "version", manifest_path)
    template_ids = _template_ids(raw.get("templates"), manifest_path)
    description = _optional_safe_text(raw, "description", manifest_path)
    domain_profile = _optional_safe_id(raw, "domain_profile", manifest_path)
    tags = _tags(raw.get("tags"), manifest_path)
    return BeePackManifest(
        pack_id=pack_id,
        name=name,
        version=version,
        description=description,
        domain_profile=domain_profile,
        template_ids=template_ids,
        default_workspace_policy=_optional_policy(raw, "default_workspace_policy"),
        default_topic_policy=_optional_policy(raw, "default_topic_policy"),
        default_memory_policy=_optional_policy(raw, "default_memory_policy"),
        tags=tags,
        metadata=_optional_metadata(raw),
    )


def _template_ids(value: object, manifest_path: Path | None) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(_manifest_error("templates must be a list", manifest_path))
    ids: list[str] = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            template_id = item
        elif isinstance(item, dict):
            template_id = _required_safe_id(
                item,
                "template_id",
                manifest_path,
                context=f"templates[{index}]",
            )
        else:
            raise TypeError(
                _manifest_error(
                    f"templates[{index}] must be a string or object", manifest_path
                )
            )
        _require_safe_id("template_id", template_id, manifest_path)
        ids.append(template_id)
    if len(set(ids)) != len(ids):
        raise ValueError(_manifest_error("template ids must be unique", manifest_path))
    return tuple(ids)


def _tags(value: object, manifest_path: Path | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise TypeError(_manifest_error("tags must be a list", manifest_path))
    tags = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise TypeError(
                _manifest_error(f"tags[{index}] must be a string", manifest_path)
            )
        _require_safe_id("tag", item, manifest_path)
        tags.append(item)
    if len(set(tags)) != len(tags):
        raise ValueError(_manifest_error("tags must be unique", manifest_path))
    return tuple(tags)


def _optional_policy(raw: Mapping[str, Any], key: str) -> JSONObject | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError(f"Bee pack manifest {key} must be an object")
    return cast(JSONObject, dict(value))


def _optional_metadata(raw: Mapping[str, Any]) -> JSONObject:
    value = raw.get("metadata", {})
    if not isinstance(value, dict):
        raise TypeError("Bee pack manifest metadata must be an object")
    return cast(JSONObject, dict(value))


def _required_safe_id(
    raw: Mapping[str, Any],
    key: str,
    manifest_path: Path | None,
    *,
    context: str = "manifest",
) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise TypeError(
            _manifest_error(f"{context}.{key} must be a string", manifest_path)
        )
    _require_safe_id(key, value, manifest_path)
    return value


def _optional_safe_id(
    raw: Mapping[str, Any],
    key: str,
    manifest_path: Path | None,
) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(_manifest_error(f"{key} must be a string", manifest_path))
    _require_safe_id(key, value, manifest_path)
    return value


def _require_safe_id(name: str, value: str, manifest_path: Path | None) -> None:
    if not _SAFE_ID_RE.fullmatch(value):
        raise ValueError(_manifest_error(f"{name} is not a safe id", manifest_path))


def _required_safe_text(
    raw: Mapping[str, Any],
    key: str,
    manifest_path: Path | None,
) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise TypeError(_manifest_error(f"{key} must be a string", manifest_path))
    _require_safe_text(key, value, manifest_path)
    return value


def _optional_safe_text(
    raw: Mapping[str, Any],
    key: str,
    manifest_path: Path | None,
) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(_manifest_error(f"{key} must be a string", manifest_path))
    _require_safe_text(key, value, manifest_path)
    return value


def _require_safe_text(name: str, value: str, manifest_path: Path | None) -> None:
    if not value:
        raise ValueError(_manifest_error(f"{name} must not be empty", manifest_path))
    if len(value) > _MAX_SAFE_TEXT_CHARS:
        raise ValueError(_manifest_error(f"{name} is too long", manifest_path))
    normalized = value.strip().lower()
    if any(marker in normalized for marker in _SECRET_VALUE_MARKERS):
        raise ValueError(
            _manifest_error(f"{name} contains secret-like value", manifest_path)
        )


def _validate_safe_json(path: str, value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} keys must be strings")
            _reject_forbidden_key(path, key)
            _validate_safe_json(f"{path}.{key}", item)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_safe_json(f"{path}[{index}]", item)
        return
    if isinstance(value, str):
        normalized = value.strip().lower()
        if any(marker in normalized for marker in _SECRET_VALUE_MARKERS):
            raise ValueError(f"{path} contains secret-like value")
        return
    if isinstance(value, int | float | bool) or value is None:
        return
    raise TypeError(f"{path} contains unsupported JSON value")


def _reject_forbidden_key(path: str, key: str) -> None:
    if key in _ALLOWED_PACK_STATIC_CAPABILITY_KEYS:
        return
    normalized = key.strip().replace("-", "_").lower()
    compact = re.sub(r"[^a-z0-9]", "", key.lower())
    for forbidden in _FORBIDDEN_PACK_KEY_PARTS:
        compact_forbidden = re.sub(r"[^a-z0-9]", "", forbidden)
        if (
            normalized == forbidden
            or normalized.startswith(f"{forbidden}_")
            or normalized.endswith(f"_{forbidden}")
            or f"_{forbidden}_" in normalized
            or (
                forbidden in _COMPACT_FORBIDDEN_PACK_KEY_PARTS
                and compact_forbidden in compact
            )
        ):
            raise ValueError(f"{path}.{key} uses forbidden sensitive field")


def _manifest_error(message: str, manifest_path: Path | None) -> str:
    if manifest_path is None:
        return f"Bee pack manifest {message}"
    return f"Bee pack manifest {message}: {manifest_path}"
