"""Topic-derived memory candidate helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from coding_agent.bee_workspace import BeeWorkspaceRunArtifacts
from coding_agent.topics.context_pack import (
    ContextPack,
    ContextPackItem,
    ContextPackRenderer,
    ContextPackSection,
    EvidenceRef,
)
from coding_agent.topics.provenance import TopicEntryRange, topic_entry_range
from coding_agent.topics.range_index import require_recall_safe_text
from coding_agent.topics.store import JSONObject, JSONValue, TopicRecord

MEMORY_CANDIDATE_STATUS = "candidate"
MEMORY_REFERENCE_MODE = "reference_only"
MEMORY_REVIEW_STATUSES = frozenset({"candidate", "accepted", "rejected", "archived"})

_ALLOWED_KINDS = frozenset(
    {
        "procedure",
        "decision",
        "incident",
        "fact",
        "command_memory",
        "project_convention",
    }
)


@dataclass(frozen=True)
class TopicDerivedMemoryCandidate:
    kind: str
    title: str
    summary: str
    scope: str
    tags: tuple[str, ...]
    confidence: float
    provenance: JSONObject
    candidate_id: str | None = None
    status: str = MEMORY_CANDIDATE_STATUS

    def __post_init__(self) -> None:
        if self.kind not in _ALLOWED_KINDS:
            raise ValueError(f"memory candidate kind is not supported: {self.kind}")
        if self.status != MEMORY_CANDIDATE_STATUS:
            raise ValueError("topic-derived memory starts as candidate")
        require_recall_safe_text("title", self.title)
        require_recall_safe_text("summary", self.summary)
        _require_safe_scope(self.scope)
        for index, tag in enumerate(self.tags):
            require_recall_safe_text(f"tags[{index}]", tag)
            if any(char.isspace() for char in tag):
                raise ValueError(f"tags[{index}] must not contain whitespace")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        _require_provenance(self.provenance)
        object.__setattr__(self, "tags", tuple(sorted(set(self.tags))))
        if self.candidate_id is None:
            object.__setattr__(self, "candidate_id", _candidate_id(self))
        else:
            _require_safe_scope(self.candidate_id)

    def to_dict(self) -> JSONObject:
        return {
            "candidate_id": self.candidate_id,
            "kind": self.kind,
            "title": self.title,
            "summary": self.summary,
            "scope": self.scope,
            "tags": list(self.tags),
            "confidence": self.confidence,
            "status": self.status,
            "reference_mode": MEMORY_REFERENCE_MODE,
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class ReviewedMemoryRecord:
    candidate: TopicDerivedMemoryCandidate
    status: str
    review_reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in MEMORY_REVIEW_STATUSES:
            raise ValueError(f"memory review status is not supported: {self.status}")
        if self.review_reason is not None:
            require_recall_safe_text("review_reason", self.review_reason)

    def to_dict(self) -> JSONObject:
        payload = self.candidate.to_dict()
        payload["status"] = self.status
        if self.review_reason is not None:
            payload["review_reason"] = self.review_reason
        return payload


class MemoryReviewStore:
    """Local deterministic review store for topic-derived memory candidates."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._records: dict[str, ReviewedMemoryRecord] = {}
        self.load()

    @property
    def path(self) -> Path | None:
        return self._path

    def load(self) -> None:
        if self._path is None:
            return
        if not self._path.exists():
            return
        records: dict[str, ReviewedMemoryRecord] = {}
        with self._path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid memory review JSONL at line {line_number}: "
                        f"{self._path}"
                    ) from exc
                if not isinstance(payload, dict):
                    raise ValueError(
                        f"memory review record at line {line_number} must be an object"
                    )
                record = _reviewed_memory_record_from_dict(payload)
                records[_candidate_id_value(record.candidate)] = record
        self._records = records

    def save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self._path.parent,
            prefix=f".{self._path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            for record in self.list_memories():
                temp_file.write(
                    json.dumps(
                        record.to_dict(),
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                )
                temp_file.write("\n")
        temp_path.replace(self._path)

    def add_candidate(
        self,
        candidate: TopicDerivedMemoryCandidate,
    ) -> ReviewedMemoryRecord:
        candidate_id = _candidate_id_value(candidate)
        existing = self._records.get(candidate_id)
        if existing is not None:
            return existing
        record = ReviewedMemoryRecord(candidate=candidate, status="candidate")
        self._records[candidate_id] = record
        self.save()
        return record

    def list_memories(
        self, *, status: str | None = None
    ) -> tuple[ReviewedMemoryRecord, ...]:
        if status is not None and status not in MEMORY_REVIEW_STATUSES:
            raise ValueError(f"memory review status is not supported: {status}")
        records = self._records.values()
        if status is not None:
            records = [record for record in records if record.status == status]
        return tuple(
            sorted(
                records,
                key=lambda record: _candidate_id_value(record.candidate),
            )
        )

    def load_memory(self, candidate_id: str) -> ReviewedMemoryRecord | None:
        _require_safe_scope(candidate_id)
        return self._records.get(candidate_id)

    def accept_candidate(
        self,
        candidate_id: str,
        *,
        reason: str | None = None,
    ) -> ReviewedMemoryRecord:
        return self._transition(candidate_id, "accepted", reason=reason)

    def reject_candidate(
        self,
        candidate_id: str,
        *,
        reason: str | None = None,
    ) -> ReviewedMemoryRecord:
        return self._transition(candidate_id, "rejected", reason=reason)

    def archive_candidate(
        self,
        candidate_id: str,
        *,
        reason: str | None = None,
    ) -> ReviewedMemoryRecord:
        return self._transition(candidate_id, "archived", reason=reason)

    def _transition(
        self,
        candidate_id: str,
        status: str,
        *,
        reason: str | None,
    ) -> ReviewedMemoryRecord:
        _require_safe_scope(candidate_id)
        record = self._records.get(candidate_id)
        if record is None:
            raise KeyError(f"memory candidate not found: {candidate_id}")
        if record.status == status:
            return record
        if record.status != "candidate":
            raise ValueError(
                f"memory candidate {candidate_id} is already {record.status}"
            )
        updated = ReviewedMemoryRecord(
            candidate=record.candidate,
            status=status,
            review_reason=reason,
        )
        self._records[candidate_id] = updated
        self.save()
        return updated

    def accepted_memories(self) -> tuple[ReviewedMemoryRecord, ...]:
        return self.list_memories(status="accepted")


def accepted_memory_context_pack(
    records: tuple[ReviewedMemoryRecord, ...],
) -> ContextPack:
    items = tuple(
        _accepted_memory_item(record)
        for record in records
        if record.status == "accepted"
    )
    if not items:
        return ContextPack(sections=())
    return ContextPack(
        sections=(
            ContextPackSection(
                title="Accepted memory references",
                items=items,
            ),
        )
    )


def accepted_memory_context_messages(
    records: tuple[ReviewedMemoryRecord, ...],
) -> list[dict[str, object]]:
    return ContextPackRenderer().render_messages(accepted_memory_context_pack(records))


def propose_memory_candidate_from_topic(
    topic: TopicRecord,
    *,
    kind: str = "fact",
    tags: tuple[str, ...] = (),
    confidence: float = 0.6,
    source_ranges: tuple[TopicEntryRange, ...] | None = None,
) -> TopicDerivedMemoryCandidate | None:
    if topic.status != "finalized" or topic.summary is None:
        return None
    return TopicDerivedMemoryCandidate(
        kind=kind,
        title=topic.title or topic.topic_id,
        summary=topic.summary,
        scope=f"topic:{topic.topic_id}",
        tags=tags,
        confidence=confidence,
        provenance=_provenance(
            topic=topic,
            source_ranges=source_ranges,
        ),
    )


def propose_memory_candidates_from_bee_artifacts(
    *,
    topic: TopicRecord,
    artifacts: BeeWorkspaceRunArtifacts,
    candidate_kind: str = "procedure",
    confidence: float = 0.65,
    pack_id: str | None = None,
    domain_profile: str | None = None,
    pack_tags: tuple[str, ...] = (),
) -> tuple[TopicDerivedMemoryCandidate, ...]:
    if topic.topic_id != artifacts.topic_id:
        raise ValueError("Bee artifact topic_id must match topic")
    candidates: list[TopicDerivedMemoryCandidate] = []
    report_refs = ("report.md",)
    evidence_refs = tuple(
        executor_run.executor_evidence_path
        for executor_run in artifacts.executor_runs
        if executor_run.executor_evidence_path is not None
    )
    run_id = artifacts.run_ids[0] if artifacts.run_ids else None
    candidates.append(
        TopicDerivedMemoryCandidate(
            kind=candidate_kind,
            title=artifacts.report_title,
            summary=artifacts.report_summary,
            scope=f"bee:{artifacts.template_id}",
            tags=_bee_memory_tags(
                template_id=artifacts.template_id,
                pack_id=pack_id,
                pack_tags=pack_tags,
            ),
            confidence=confidence,
            provenance=_provenance(
                topic=topic,
                task_id=artifacts.task_id,
                run_id=run_id,
                report_refs=report_refs,
                evidence_refs=evidence_refs,
                template_id=artifacts.template_id,
                pack_id=pack_id,
                domain_profile=domain_profile,
                pack_tags=pack_tags,
            ),
        )
    )
    for raw_candidate in artifacts.memory_candidates:
        candidate = _candidate_from_existing(
            raw_candidate,
            topic=topic,
            artifacts=artifacts,
            run_id=run_id,
            report_refs=report_refs,
            evidence_refs=evidence_refs,
            pack_id=pack_id,
            domain_profile=domain_profile,
            pack_tags=pack_tags,
        )
        if candidate is not None:
            candidates.append(candidate)
    return tuple(candidates)


def _reviewed_memory_record_from_dict(payload: dict[str, Any]) -> ReviewedMemoryRecord:
    status = _required_str(payload, "status")
    candidate_status = payload.get("candidate_status")
    if candidate_status is not None and candidate_status != MEMORY_CANDIDATE_STATUS:
        raise ValueError("reviewed memory candidate_status must be candidate")
    candidate = TopicDerivedMemoryCandidate(
        kind=_required_str(payload, "kind"),
        title=_required_str(payload, "title"),
        summary=_required_str(payload, "summary"),
        scope=_required_str(payload, "scope"),
        tags=_required_string_tuple(payload, "tags"),
        confidence=_required_confidence(payload, "confidence"),
        provenance=_required_json_object(payload, "provenance"),
        candidate_id=_required_str(payload, "candidate_id"),
        status=MEMORY_CANDIDATE_STATUS,
    )
    review_reason = payload.get("review_reason")
    if review_reason is not None and not isinstance(review_reason, str):
        raise ValueError("review_reason must be a string")
    return ReviewedMemoryRecord(
        candidate=candidate,
        status=status,
        review_reason=review_reason,
    )


def _candidate_from_existing(
    raw_candidate: JSONObject,
    *,
    topic: TopicRecord,
    artifacts: BeeWorkspaceRunArtifacts,
    run_id: str | None,
    report_refs: tuple[str, ...],
    evidence_refs: tuple[str, ...],
    pack_id: str | None,
    domain_profile: str | None,
    pack_tags: tuple[str, ...],
) -> TopicDerivedMemoryCandidate | None:
    try:
        kind = _optional_str(raw_candidate, "kind") or "fact"
        title = _optional_str(raw_candidate, "title") or artifacts.report_title
        summary = _optional_str(raw_candidate, "summary")
        if summary is None:
            return None
        tags = _string_tuple(raw_candidate.get("tags"))
        confidence = _confidence(raw_candidate.get("confidence"), default=0.5)
        return TopicDerivedMemoryCandidate(
            kind=kind,
            title=title,
            summary=summary,
            scope=f"bee:{artifacts.template_id}",
            tags=(
                *_bee_memory_tags(
                    template_id=artifacts.template_id,
                    pack_id=pack_id,
                    pack_tags=pack_tags,
                ),
                *tags,
            ),
            confidence=confidence,
            provenance=_provenance(
                topic=topic,
                task_id=artifacts.task_id,
                run_id=run_id,
                report_refs=report_refs,
                evidence_refs=evidence_refs,
                template_id=artifacts.template_id,
                pack_id=pack_id,
                domain_profile=domain_profile,
                pack_tags=pack_tags,
            ),
        )
    except (TypeError, ValueError):
        return None


def _provenance(
    *,
    topic: TopicRecord,
    task_id: str | None = None,
    run_id: str | None = None,
    report_refs: tuple[str, ...] = (),
    evidence_refs: tuple[str, ...] = (),
    source_ranges: tuple[TopicEntryRange, ...] | None = None,
    template_id: str | None = None,
    pack_id: str | None = None,
    domain_profile: str | None = None,
    pack_tags: tuple[str, ...] = (),
) -> JSONObject:
    payload: JSONObject = {
        "topic_id": topic.topic_id,
        "topic_status": topic.status,
        "topic_kind": topic.kind,
        "source_entry_ranges": [
            entry_range.to_dict()
            for entry_range in (source_ranges or (topic_entry_range(topic),))
        ],
    }
    if task_id is not None:
        _require_safe_scope(task_id)
        payload["task_id"] = task_id
    if run_id is not None:
        _require_safe_scope(run_id)
        payload["run_id"] = run_id
    if report_refs:
        _require_safe_refs("report_refs", report_refs)
        payload["report_refs"] = list(report_refs)
    if evidence_refs:
        _require_safe_refs("evidence_refs", evidence_refs)
        payload["evidence_refs"] = list(evidence_refs)
    if template_id is not None:
        _require_safe_scope(template_id)
        payload["template_id"] = template_id
    if pack_id is not None:
        _require_safe_scope(pack_id)
        payload["pack_id"] = pack_id
    if domain_profile is not None:
        _require_safe_scope(domain_profile)
        payload["domain_profile"] = domain_profile
    if pack_tags:
        for tag in pack_tags:
            _require_safe_scope(tag)
        payload["pack_tags"] = sorted(set(pack_tags))
    return payload


def _bee_memory_tags(
    *,
    template_id: str,
    pack_id: str | None,
    pack_tags: tuple[str, ...],
) -> tuple[str, ...]:
    tags = [template_id, *pack_tags]
    if pack_id is not None:
        tags.append(pack_id)
    return tuple(tags)


def _candidate_id(candidate: TopicDerivedMemoryCandidate) -> str:
    payload = {
        "kind": candidate.kind,
        "title": candidate.title,
        "summary": candidate.summary,
        "scope": candidate.scope,
        "tags": sorted(candidate.tags),
        "provenance": candidate.provenance,
    }
    encoded = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    return (
        f"memory-candidate-{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:16]}"
    )


def _candidate_id_value(candidate: TopicDerivedMemoryCandidate) -> str:
    if candidate.candidate_id is None:
        raise ValueError("memory candidate is missing candidate_id")
    return candidate.candidate_id


def _accepted_memory_item(record: ReviewedMemoryRecord) -> ContextPackItem:
    candidate = record.candidate
    topic_id = _provenance_topic_id(candidate.provenance)
    return ContextPackItem(
        source_kind="memory",
        source_id=f"accepted-memory:{_candidate_id_value(candidate)}",
        label=candidate.title,
        body=candidate.summary,
        evidence=(
            EvidenceRef(
                kind="topic",
                source_id=topic_id,
                label="accepted memory provenance",
            ),
        ),
        metadata={
            "reference_mode": MEMORY_REFERENCE_MODE,
            "memory_status": "accepted",
            "memory_kind": candidate.kind,
            "provenance": candidate.provenance,
        },
    )


def _provenance_topic_id(provenance: JSONObject) -> str:
    topic_id = provenance.get("topic_id")
    if not isinstance(topic_id, str) or not topic_id:
        raise ValueError("memory candidate provenance requires topic_id")
    return topic_id


def _require_provenance(provenance: JSONObject) -> None:
    topic_id = provenance.get("topic_id")
    if not isinstance(topic_id, str) or not topic_id:
        raise ValueError("memory candidate provenance requires topic_id")
    _require_safe_scope(topic_id)
    ranges = provenance.get("source_entry_ranges")
    if not isinstance(ranges, list) or not ranges:
        raise ValueError("memory candidate provenance requires source_entry_ranges")
    for key, value in provenance.items():
        if isinstance(value, str):
            require_recall_safe_text(f"provenance.{key}", value)
        elif isinstance(value, list):
            _require_json_list(f"provenance.{key}", value)


def _require_json_list(field_name: str, values: list[JSONValue]) -> None:
    for index, value in enumerate(values):
        if isinstance(value, str):
            require_recall_safe_text(f"{field_name}[{index}]", value)
        elif isinstance(value, dict):
            for nested_key, nested_value in value.items():
                if isinstance(nested_value, str):
                    require_recall_safe_text(
                        f"{field_name}[{index}].{nested_key}",
                        nested_value,
                    )


def _require_safe_refs(field_name: str, refs: tuple[str, ...]) -> None:
    for index, ref in enumerate(refs):
        _require_safe_scope(ref)
        if ref.startswith("/") or ".." in ref.split("/"):
            raise ValueError(f"{field_name}[{index}] must be a relative safe ref")


def _require_safe_scope(value: str) -> None:
    require_recall_safe_text("scope", value)
    if any(char.isspace() for char in value):
        raise ValueError("scope-like values must not contain whitespace")


def _optional_str(value: JSONObject, key: str) -> str | None:
    item = value.get(key)
    return item if isinstance(item, str) and item else None


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _confidence(value: object, *, default: float) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return default


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"memory review record requires string {key}")
    return value


def _required_string_tuple(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"memory review record requires string list {key}")
    return tuple(value)


def _required_confidence(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ValueError(f"memory review record requires numeric {key}")
    return float(value)


def _required_json_object(payload: dict[str, Any], key: str) -> JSONObject:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"memory review record requires object {key}")
    return value
