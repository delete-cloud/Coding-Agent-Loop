"""PGDurableStore composition."""

from __future__ import annotations

from coding_agent.stores.pg_durable.checkpoint import PgCheckpointMixin
from coding_agent.stores.pg_durable.core import PgCoreMixin
from coding_agent.stores.pg_durable.fact_source import PgFactSourceMixin
from coding_agent.stores.pg_durable.runtime_ops import PgRuntimeMixin
from coding_agent.stores.pg_durable.sql_harness import PgHarnessSqlMixin
from coding_agent.stores.pg_durable.sql_runtime import PgRuntimeSqlMixin
from coding_agent.stores.pg_durable.sql_session import PgSessionSqlMixin
from coding_agent.stores.pg_durable.tape import PgTapeMixin
from coding_agent.stores.pg_durable.topics import PgTopicsMixin
from coding_agent.stores.pg_durable.uow import PgUnitOfWorkMixin


class PGDurableStore(
    PgSessionSqlMixin,
    PgRuntimeSqlMixin,
    PgHarnessSqlMixin,
    PgCoreMixin,
    PgTapeMixin,
    PgRuntimeMixin,
    PgCheckpointMixin,
    PgUnitOfWorkMixin,
    PgFactSourceMixin,
    PgTopicsMixin,
):
    """PostgreSQL protected write facade for durable owner fencing."""
