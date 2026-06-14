"""Deterministic topic range indexing and search."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from coding_agent.topics.provenance import TopicEntryRange, topic_entry_range
from coding_agent.topics.store import TopicRecord

_FORBIDDEN_TEXT_MARKERS = (
    "-----begin ",
    "command:",
    "command=",
    "command_output",
    "content:",
    "content=",
    "env:",
    "env=",
    "message:",
    "message=",
    "password=",
    "password:",
    "private key",
    "prompt:",
    "prompt=",
    "raw log",
    "raw_log",
    "result:",
    "result=",
    "secret:",
    "secret=",
    "stderr:",
    "stderr=",
    "stdout:",
    "stdout=",
    "text:",
    "text=",
    "token:",
    "token=",
)


@dataclass(frozen=True)
class TopicRangeIndexDocument:
    topic_id: str
    tape_id: str
    session_id: str
    kind: str
    status: str
    title: str | None
    summary: str
    source_range: TopicEntryRange
    created_at: datetime
    finalized_at: datetime | None
    profile: str | None = None
    tags: tuple[str, ...] = ()
    bee_pack_id: str | None = None
    bee_template_id: str | None = None
    domain_profile: str | None = None
    template_kind: str | None = None
    related_task_ids: tuple[str, ...] = ()
    report_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    report_summary: str | None = None
    evidence_summaries: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty("topic_id", self.topic_id)
        _require_non_empty("tape_id", self.tape_id)
        _require_non_empty("session_id", self.session_id)
        _require_non_empty("kind", self.kind)
        _require_non_empty("status", self.status)
        _require_safe_text("summary", self.summary)
        if self.title is not None:
            _require_safe_text("title", self.title)
        if self.profile is not None:
            _require_safe_id("profile", self.profile)
        if self.bee_pack_id is not None:
            _require_safe_id("bee_pack_id", self.bee_pack_id)
        if self.bee_template_id is not None:
            _require_safe_id("bee_template_id", self.bee_template_id)
        if self.domain_profile is not None:
            _require_safe_id("domain_profile", self.domain_profile)
        if self.template_kind is not None:
            _require_safe_id("template_kind", self.template_kind)
        _require_safe_values("tags", self.tags)
        _require_safe_values("related_task_ids", self.related_task_ids)
        _require_safe_refs("report_refs", self.report_refs)
        _require_safe_refs("evidence_refs", self.evidence_refs)
        if self.report_summary is not None:
            _require_safe_text("report_summary", self.report_summary)
        for index, summary in enumerate(self.evidence_summaries):
            _require_safe_text(f"evidence_summaries[{index}]", summary)
        object.__setattr__(self, "tags", tuple(sorted(set(self.tags))))
        object.__setattr__(
            self,
            "related_task_ids",
            tuple(sorted(set(self.related_task_ids))),
        )
        object.__setattr__(self, "report_refs", tuple(sorted(set(self.report_refs))))
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(sorted(set(self.evidence_refs))),
        )


@dataclass(frozen=True)
class TopicRangeSearchQuery:
    text: str | None = None
    kind: str | None = None
    profile: str | None = None
    bee_pack_id: str | None = None
    bee_template_id: str | None = None
    domain_profile: str | None = None
    template_kind: str | None = None
    tags: tuple[str, ...] = ()
    status: str | None = "finalized"
    created_after: datetime | None = None
    created_before: datetime | None = None
    limit: int = 10

    def __post_init__(self) -> None:
        if self.text is not None:
            _require_safe_text("text", self.text)
        if self.kind is not None:
            _require_safe_id("kind", self.kind)
        if self.profile is not None:
            _require_safe_id("profile", self.profile)
        if self.bee_pack_id is not None:
            _require_safe_id("bee_pack_id", self.bee_pack_id)
        if self.bee_template_id is not None:
            _require_safe_id("bee_template_id", self.bee_template_id)
        if self.domain_profile is not None:
            _require_safe_id("domain_profile", self.domain_profile)
        if self.template_kind is not None:
            _require_safe_id("template_kind", self.template_kind)
        if self.status is not None:
            _require_safe_id("status", self.status)
        _require_safe_values("tags", self.tags)
        if self.limit <= 0:
            raise ValueError("limit must be positive")
        object.__setattr__(self, "tags", tuple(sorted(set(self.tags))))


@dataclass(frozen=True)
class TopicRangeSearchResult:
    topic_id: str
    tape_id: str
    session_id: str
    title: str | None
    summary: str
    score: float
    reason: str
    source_ranges: tuple[TopicEntryRange, ...]
    kind: str
    status: str
    created_at: datetime
    finalized_at: datetime | None
    profile: str | None = None
    bee_pack_id: str | None = None
    bee_template_id: str | None = None
    domain_profile: str | None = None
    template_kind: str | None = None
    related_task_ids: tuple[str, ...] = ()
    report_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


@dataclass
class TopicRangeIndex:
    """Small deterministic index for finalized topic range references."""

    _documents: dict[str, TopicRangeIndexDocument] = field(default_factory=dict)

    def index_topic(
        self,
        topic: TopicRecord,
        *,
        profile: str | None = None,
        tags: tuple[str, ...] = (),
        bee_pack_id: str | None = None,
        bee_template_id: str | None = None,
        domain_profile: str | None = None,
        template_kind: str | None = None,
        related_task_ids: tuple[str, ...] = (),
        report_refs: tuple[str, ...] = (),
        evidence_refs: tuple[str, ...] = (),
        report_summary: str | None = None,
        evidence_summaries: tuple[str, ...] = (),
        allow_open: bool = False,
    ) -> TopicRangeIndexDocument | None:
        if topic.status != "finalized" and not allow_open:
            return None
        summary = topic.summary or report_summary
        if summary is None:
            return None
        document = TopicRangeIndexDocument(
            topic_id=topic.topic_id,
            tape_id=topic.tape_id,
            session_id=topic.session_id,
            kind=topic.kind,
            status=topic.status,
            title=topic.title,
            summary=summary,
            source_range=topic_entry_range(topic),
            created_at=topic.created_at,
            finalized_at=topic.finalized_at,
            profile=profile,
            tags=tags,
            bee_pack_id=bee_pack_id,
            bee_template_id=bee_template_id,
            domain_profile=domain_profile,
            template_kind=template_kind,
            related_task_ids=related_task_ids,
            report_refs=report_refs,
            evidence_refs=evidence_refs,
            report_summary=report_summary,
            evidence_summaries=evidence_summaries,
        )
        self._documents[topic.topic_id] = document
        return document

    def search(self, query: TopicRangeSearchQuery) -> list[TopicRangeSearchResult]:
        query_tokens = _tokens(query.text or "")
        results: list[TopicRangeSearchResult] = []
        for document in self._documents.values():
            if not _matches_filters(document, query):
                continue
            score, reason = _score_document(document, query_tokens)
            if query_tokens and score == 0:
                continue
            results.append(
                TopicRangeSearchResult(
                    topic_id=document.topic_id,
                    tape_id=document.tape_id,
                    session_id=document.session_id,
                    title=document.title,
                    summary=document.summary,
                    score=score,
                    reason=reason,
                    source_ranges=(document.source_range,),
                    kind=document.kind,
                    status=document.status,
                    created_at=document.created_at,
                    finalized_at=document.finalized_at,
                    profile=document.profile,
                    bee_pack_id=document.bee_pack_id,
                    bee_template_id=document.bee_template_id,
                    domain_profile=document.domain_profile,
                    template_kind=document.template_kind,
                    related_task_ids=document.related_task_ids,
                    report_refs=document.report_refs,
                    evidence_refs=document.evidence_refs,
                    tags=document.tags,
                )
            )
        results.sort(key=lambda item: (-item.score, item.topic_id))
        return results[: query.limit]


def _matches_filters(
    document: TopicRangeIndexDocument,
    query: TopicRangeSearchQuery,
) -> bool:
    if query.kind is not None and document.kind != query.kind:
        return False
    if query.profile is not None and document.profile != query.profile:
        return False
    if query.bee_pack_id is not None and document.bee_pack_id != query.bee_pack_id:
        return False
    if (
        query.bee_template_id is not None
        and document.bee_template_id != query.bee_template_id
    ):
        return False
    if (
        query.domain_profile is not None
        and document.domain_profile != query.domain_profile
    ):
        return False
    if (
        query.template_kind is not None
        and document.template_kind != query.template_kind
    ):
        return False
    if query.status is not None and document.status != query.status:
        return False
    if query.tags and not set(query.tags).issubset(document.tags):
        return False
    if query.created_after is not None and document.created_at < query.created_after:
        return False
    return not (
        query.created_before is not None and document.created_at > query.created_before
    )


def _score_document(
    document: TopicRangeIndexDocument,
    query_tokens: set[str],
) -> tuple[float, str]:
    if not query_tokens:
        return 1.0, "filtered_match"
    overlap = query_tokens & _tokens(_document_text(document))
    if not overlap:
        return 0.0, "no_token_overlap"
    return round(len(overlap) / len(query_tokens), 4), "deterministic_token_overlap"


def _document_text(document: TopicRangeIndexDocument) -> str:
    return " ".join(
        part
        for part in (
            document.title or "",
            document.summary,
            document.kind,
            document.profile or "",
            document.bee_pack_id or "",
            document.bee_template_id or "",
            document.domain_profile or "",
            document.template_kind or "",
            " ".join(document.tags),
            document.report_summary or "",
            " ".join(document.evidence_summaries),
        )
        if part
    )


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in (
            "".join(char.lower() if char.isalnum() else " " for char in value)
        ).split()
        if len(token) >= 3
    }


def _require_non_empty(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_safe_id(field_name: str, value: str) -> None:
    _require_non_empty(field_name, value)
    if any(char.isspace() for char in value):
        raise ValueError(f"{field_name} must not contain whitespace")
    _require_safe_text(field_name, value)


def _require_safe_values(field_name: str, values: tuple[str, ...]) -> None:
    for index, value in enumerate(values):
        _require_safe_id(f"{field_name}[{index}]", value)


def _require_safe_refs(field_name: str, values: tuple[str, ...]) -> None:
    for index, value in enumerate(values):
        _require_non_empty(f"{field_name}[{index}]", value)
        _require_safe_text(f"{field_name}[{index}]", value)
        if value.startswith("/") or ".." in value.split("/"):
            raise ValueError(f"{field_name}[{index}] must be a relative safe ref")


def _require_safe_text(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    normalized = value.casefold()
    for marker in _FORBIDDEN_TEXT_MARKERS:
        if marker in normalized:
            raise ValueError(f"{field_name} contains forbidden raw content marker")


def require_recall_safe_text(field_name: str, value: str) -> None:
    """Validate bounded recall/memory text against no-leak markers."""

    _require_safe_text(field_name, value)
