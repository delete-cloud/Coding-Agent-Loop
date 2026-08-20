"""Semantic memory maintenance and topic-store selection."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import (
    UTC,
    datetime,
)
from typing import cast
from agentkit.tape.models import Anchor
from agentkit.tape.store import ForkTapeStore
from agentkit.tape.tape import Tape
from coding_agent.stores.local import (
    durable_storage_backend_values,
    storage_uses_local_sqlite_bundle,
)
from coding_agent.stores.durable_local import FencedSQLiteTopicStore
from coding_agent.stores.durable_pg import FencedPGTopicStore
from coding_agent.topics.store import (
    PGTopicStore,
    SQLiteTopicStore,
    TopicAnchorRecord,
    TopicRecord,
)
from coding_agent.topics.semantic_maintenance import (
    SemanticMemoryMaintainer,
    SemanticMemoryStatus,
)
from coding_agent.topics.semantic_sync import SemanticSyncReport
from coding_agent.topics.lifecycle import (
    TOPIC_FINALIZED,
    TOPIC_INITIAL,
)
from coding_agent.topics.memory import MemoryReviewStore
from coding_agent.server.session import _bindings
from coding_agent.server.session.models import _runtime_memory_write_enabled

logger = logging.getLogger("coding_agent.server.session_manager")

_SEMANTIC_MEMORY_REBUILD_MAX_BATCH_SIZE = 1000


@dataclass(frozen=True, slots=True)
class SemanticDogfoodTopicSeedResult:
    topic_id: str
    candidate_id: str | None
    warnings: tuple[str, ...] = ()


class SemanticOps:
    def selected_topic_store(self) -> SQLiteTopicStore | PGTopicStore | None:
        if self._local_durable_store is not None:
            if not storage_uses_local_sqlite_bundle(self._storage_config):
                return None
            return FencedSQLiteTopicStore(
                durable_store=self._local_durable_store,
                path=self._local_sqlite_bundle_path,
                authority_for_session=self._owner_authority_for_session,
            )
        backend_values = durable_storage_backend_values(self._storage_config)
        if all(value == "pg" for value in backend_values.values()):
            if self._custom_store_names:
                return None
            if self._pg_durable_store is None:
                return PGTopicStore(pool=self._get_pg_pool())
            return FencedPGTopicStore(
                durable_store=self._pg_durable_store,
                pool=self._get_pg_pool(),
                authority_for_session=self._owner_authority_for_session,
            )
        return None

    async def semantic_memory_maintainer(
        self,
        session_id: str,
    ) -> SemanticMemoryMaintainer:
        runtime_ctx = await self.ensure_session_runtime(session_id)
        config = getattr(runtime_ctx, "config", None)
        if not isinstance(config, dict):
            raise RuntimeError("semantic memory is disabled")
        backend = config.get("semantic_memory_backend")
        syncer = config.get("semantic_memory_syncer")
        review_store = config.get("memory_review_store")
        if backend is None or syncer is None or review_store is None:
            raise RuntimeError("semantic memory is disabled")
        return SemanticMemoryMaintainer(
            syncer=syncer,
            backend=backend,
            review_store=review_store,
            topic_store=self.selected_topic_store(),
        )

    async def semantic_memory_status(self, session_id: str) -> SemanticMemoryStatus:
        maintainer = await self.semantic_memory_maintainer(session_id)
        return await maintainer.status()

    async def rebuild_semantic_memory(
        self,
        session_id: str,
        *,
        batch_size: int,
        allow_rebuild: bool,
    ) -> SemanticSyncReport:
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or batch_size <= 0
            or batch_size > _SEMANTIC_MEMORY_REBUILD_MAX_BATCH_SIZE
        ):
            raise ValueError("batch_size must be between 1 and 1000")
        if not isinstance(allow_rebuild, bool):
            raise ValueError("allow_rebuild must be a boolean")

        async def rebuild_admitted_semantic_memory(
            session: object,
        ) -> SemanticSyncReport:
            del session
            maintainer = await self.semantic_memory_maintainer(session_id)
            return await maintainer.rebuild(
                batch_size=batch_size,
                allow_rebuild=allow_rebuild,
            )

        return cast(
            SemanticSyncReport,
            await self._runtime_maintenance_admission.run_exclusive(
                session_id,
                rebuild_admitted_semantic_memory,
            ),
        )

    async def seed_semantic_dogfood_topic(
        self,
        session_id: str,
        *,
        title: str,
        summary: str,
        kind: str = "coding",
    ) -> SemanticDogfoodTopicSeedResult:
        if not title.strip():
            raise ValueError("title must not be blank")
        if not summary.strip():
            raise ValueError("summary must not be blank")
        if not kind.strip():
            raise ValueError("kind must not be blank")

        async def seed_admitted_semantic_dogfood_topic(
            session: object,
        ) -> SemanticDogfoodTopicSeedResult:
            del session
            runtime_ctx = await self.ensure_session_runtime(session_id)
            runtime_tape = getattr(runtime_ctx, "tape", None)
            if not isinstance(runtime_tape, Tape):
                raise RuntimeError("Session runtime context is missing tape")
            config = getattr(runtime_ctx, "config", None)
            if not isinstance(config, Mapping):
                raise RuntimeError("Session runtime context is missing config")
            topic_store = self.selected_topic_store()
            if topic_store is None:
                raise RuntimeError(
                    "topic_store is required for semantic dogfood topic seed"
                )
            review_store = config.get("memory_review_store")
            if not isinstance(review_store, MemoryReviewStore):
                raise RuntimeError("memory_review_store is required for dogfood topic")
            semantic_syncer = config.get("semantic_memory_syncer")
            if semantic_syncer is not None and not callable(
                getattr(semantic_syncer, "sync_topic", None)
            ):
                raise RuntimeError("semantic_memory_syncer is configured incorrectly")
            memory_write_enabled = _runtime_memory_write_enabled(
                config,
                review_store=review_store,
            )
            fork_store = ForkTapeStore(self._tape_store)
            fork = fork_store.begin(runtime_tape)
            base_len = len(runtime_tape)
            stable_tape_id = runtime_tape.tape_id
            topic_id = f"topic-{uuid.uuid4().hex}"
            title_value = title.strip()
            summary_value = summary.strip()
            kind_value = kind.strip()
            initial_anchor = Anchor(
                anchor_type="topic_start",
                payload={"label": title_value},
                meta={
                    "topic_id": topic_id,
                    "product_anchor_type": TOPIC_INITIAL,
                    "skip": True,
                },
            )
            initial_seq = len(fork)
            fork.append(initial_anchor)
            finalized_anchor = Anchor(
                anchor_type="topic_end",
                payload={"label": summary_value},
                meta={
                    "topic_id": topic_id,
                    "product_anchor_type": TOPIC_FINALIZED,
                    "skip": True,
                },
            )
            finalized_seq = len(fork)
            fork.append(finalized_anchor)
            try:
                stable_tape_id = await fork_store.commit(fork)
            except Exception:
                fork_store.rollback(fork)
                raise
            created_at = datetime.now(UTC)
            finalized_at = created_at
            finalized: TopicRecord | None = None
            stored_candidate_id: str | None = None
            warnings: list[str] = []
            topic_created = False
            try:
                topic = await topic_store.create_topic(
                    TopicRecord(
                        topic_id=topic_id,
                        tape_id=stable_tape_id,
                        session_id=session_id,
                        kind=kind_value,
                        status="open",
                        title=title_value,
                        summary=None,
                        owner="semantic-dogfood",
                        topic_initial_seq=initial_seq,
                        topic_finalized_seq=None,
                        created_at=created_at,
                        finalized_at=None,
                        metadata={"source": "semantic-dogfood-topic"},
                    )
                )
                topic_created = True
                await topic_store.record_topic_anchor(
                    TopicAnchorRecord(
                        topic_id=topic.topic_id,
                        tape_id=stable_tape_id,
                        seq=initial_seq,
                        anchor_type=TOPIC_INITIAL,
                        entry_id=initial_anchor.id,
                        metadata={
                            "encoded_anchor_type": "topic_start",
                            "product_anchor_type": TOPIC_INITIAL,
                        },
                    )
                )
                finalized = await topic_store.finalize_topic(
                    topic.topic_id,
                    summary=summary_value,
                    topic_finalized_seq=finalized_seq,
                    finalized_at=finalized_at,
                    metadata={"source": "semantic-dogfood-topic"},
                )
                await topic_store.record_topic_anchor(
                    TopicAnchorRecord(
                        topic_id=finalized.topic_id,
                        tape_id=stable_tape_id,
                        seq=finalized_seq,
                        anchor_type=TOPIC_FINALIZED,
                        entry_id=finalized_anchor.id,
                        metadata={
                            "encoded_anchor_type": "topic_end",
                            "product_anchor_type": TOPIC_FINALIZED,
                        },
                    )
                )
            except Exception as exc:
                delete_topic = getattr(topic_store, "delete_topic", None)
                if topic_created:
                    if delete_topic is None or not callable(delete_topic):
                        raise RuntimeError(
                            "semantic dogfood topic seed failed after tape commit "
                            "and topic compensation is unavailable"
                        ) from exc
                    try:
                        await delete_topic(topic_id)
                    except Exception as compensation_exc:
                        raise RuntimeError(
                            "semantic dogfood topic seed failed after tape commit "
                            "and topic compensation failed: "
                            f"{exc}; compensation error: {compensation_exc}"
                        ) from exc
                try:
                    await self._tape_store.truncate(stable_tape_id, base_len)
                except Exception as compensation_exc:
                    raise RuntimeError(
                        "semantic dogfood topic seed failed after tape commit "
                        "and tape compensation failed: "
                        f"{exc}; compensation error: {compensation_exc}"
                    ) from exc
                raise
            if finalized is None:
                raise RuntimeError("semantic dogfood topic seed did not finalize topic")
            if memory_write_enabled and review_store.candidate_writes_enabled:
                try:
                    candidate = _bindings.module().propose_memory_candidate_from_topic(
                        finalized
                    )
                    if candidate is not None:
                        stored_candidate = review_store.add_candidate(candidate)
                        stored_candidate_id = stored_candidate.candidate.candidate_id
                except Exception as exc:
                    warning = f"memory review candidate write failed: {exc}"
                    logger.warning(
                        "Semantic dogfood topic review candidate write failed",
                        exc_info=True,
                    )
                    warnings.append(warning)
            if semantic_syncer is not None:
                try:
                    await semantic_syncer.sync_topic(finalized)
                except Exception as exc:
                    warning = f"semantic topic sync failed: {exc}"
                    logger.warning(
                        "Semantic dogfood topic sync failed",
                        exc_info=True,
                    )
                    warnings.append(warning)
            fork.tape_id = stable_tape_id
            runtime_ctx.tape = fork
            return SemanticDogfoodTopicSeedResult(
                topic_id=finalized.topic_id,
                candidate_id=stored_candidate_id,
                warnings=tuple(warnings),
            )

        return cast(
            SemanticDogfoodTopicSeedResult,
            await self._runtime_maintenance_admission.run_exclusive(
                session_id,
                seed_admitted_semantic_dogfood_topic,
            ),
        )
