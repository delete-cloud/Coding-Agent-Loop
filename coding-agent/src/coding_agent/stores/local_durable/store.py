"""SQLiteLocalDurableStore composition."""

from __future__ import annotations

from coding_agent.stores.local_durable.core import LocalCoreMixin
from coding_agent.stores.local_durable.tape import LocalTapeMixin
from coding_agent.stores.local_durable.topics import LocalTopicsMixin
from coding_agent.stores.local_durable.runtime_ops import LocalRuntimeMixin
from coding_agent.stores.local_durable.checkpoint import LocalCheckpointMixin
from coding_agent.stores.local_durable.uow import LocalUnitOfWorkMixin
from coding_agent.stores.local_durable.fact_source import LocalFactSourceMixin


class SQLiteLocalDurableStore(
    LocalCoreMixin,
    LocalTapeMixin,
    LocalTopicsMixin,
    LocalRuntimeMixin,
    LocalCheckpointMixin,
    LocalUnitOfWorkMixin,
    LocalFactSourceMixin,
):
    """SQLite-local protected mutation facade.

    This facade is intentionally local to the SQLite bundle path. It does not replace
    the generic store protocols; it provides the transaction shape required by local
    durable fencing: owner epoch check, target ownership check, and mutation in the
    same SQLite transaction.
    """
