# Copyright 2021-present StarRocks, Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from enum import Enum
import logging
import time
from typing import Any, Collection, Mapping, Optional, Sequence, Tuple, Union

from alembic.ddl import base as alembic_base
from alembic.ddl.mysql import MySQLImpl
from sqlalchemy import Column, ForeignKeyConstraint, Index, MetaData, Table, UniqueConstraint, text, util
from sqlalchemy.engine.cursor import CursorResult
from sqlalchemy.sql import Executable
from sqlalchemy.sql.elements import TextClause

from starrocks import datatype
from starrocks.alembic import compare
from starrocks.datatype import BIGINT, VARCHAR
from starrocks.dialect import StarRocksDialect, column_default_change_error
from starrocks.sql.ddl import AlterTableColumns, AlterTableDistribution, AlterTableOrder, AlterTableProperties


logger = logging.getLogger(__name__)

# DDL constructs that submit an asynchronous StarRocks column schema-change job.
# StarRocks allows only one in-flight schema-change job per table, so when the
# wait feature is enabled we poll for a terminal state after each of these.
# A property set may start one too (bloom_filter_columns does), and most start none.
# So does a sort key change, ORDER BY: on 4.1.6 it is listed by SHOW ALTER TABLE COLUMN.
_COLUMN_JOB_CONSTRUCTS = (
    AlterTableColumns,
    AlterTableOrder,
    AlterTableProperties,
    alembic_base.AddColumn,
    alembic_base.DropColumn,
    alembic_base.AlterColumn,  # covers Column{Nullable,Type,Default,Comment} + MySQL modify/change
)

# Changing the distribution submits an OPTIMIZE job, which SHOW ALTER TABLE COLUMN does not list.
_OPTIMIZE_JOB_CONSTRUCTS = (AlterTableDistribution,)

_SCHEMA_CHANGE_CONSTRUCTS = _COLUMN_JOB_CONSTRUCTS + _OPTIMIZE_JOB_CONSTRUCTS

class SchemaChangeState(str, Enum):
    """Job states reported by the ``State`` column of ``SHOW ALTER TABLE COLUMN``."""

    PENDING = "PENDING"
    WAITING_TXN = "WAITING_TXN"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    CANCELLED = "CANCELLED"

    @classmethod
    def parse(cls, value: Any) -> Optional["SchemaChangeState"]:
        """Return the matching member, or ``None`` for a state we don't know.

        An unknown state is treated as "still in progress" by the caller, so a
        state added by a future StarRocks version keeps the migration waiting
        instead of failing it.
        """
        try:
            return cls(str(value).upper())
        except ValueError:
            return None


class StarRocksImpl(MySQLImpl):
    """Alembic DDL implementation for StarRocks."""

    __dialect__ = "starrocks"

    def version_table_impl(
        self,
        *,
        version_table: str,
        version_table_schema: Optional[str],
        version_table_pk: bool,  # ignored as StarRocks requires a primary key
        **kw,
    ) -> Table:
        version_table_kwargs = self.context_opts.get("version_table_kwargs", {}) if self.context_opts else {}
        if version_table_kwargs:
            logger.info(f"There are extra kwargs for version_table: {version_table_kwargs}")
        return Table(
            version_table,
            MetaData(),
            Column("id", BIGINT, autoincrement=True, primary_key=True),
            Column("version_num", VARCHAR(32), primary_key=False),
            schema=version_table_schema,
            starrocks_primary_key="id",
            **version_table_kwargs,
            **kw,
        )

    def _exec(
        self,
        construct: Union[Executable, str],
        execution_options: Optional[Mapping[str, Any]] = None,
        multiparams: Optional[Sequence[Mapping[str, Any]]] = None,
        params: Mapping[str, Any] = util.immutabledict(),
    ) -> Optional[CursorResult]:
        """Execute DDL, then optionally block until the schema change it starts finishes.

        StarRocks runs schema changes asynchronously and rejects a new
        job while a prior one on the same table is still running. When the user
        opts in via ``context.configure(starrocks_wait_for_schema_change=True)``,
        we poll ``SHOW ALTER TABLE COLUMN`` (or ``OPTIMIZE`` after a distribution or sort key change)
        after each such statement until the job it started reaches a terminal state, keeping the
        migration in lock-step with the cluster.

        Only a job newer than the table's latest one before the statement counts, so a statement that
        starts no job, and an old cancelled job of the table or of a dropped table of the same name,
        are not mistaken for it.
        """
        target = self._schema_change_target(construct)
        waiting = target is not None and self._should_wait_for_schema_change()
        alter_type = self._schema_change_job_kind(construct)
        previous_job_id = self._latest_job_id(*target, alter_type) if waiting and target is not None else None

        result = super()._exec(
            construct,
            execution_options=execution_options,
            multiparams=multiparams,
            params=params,
        )

        if waiting and target is not None:
            self._wait_for_schema_change(*target, alter_type=alter_type, after_job_id=previous_job_id)

        return result

    def _should_wait_for_schema_change(self) -> bool:
        """Whether to block on column schema changes (opt-in, online mode only)."""
        if self.as_sql or self.connection is None:
            # Offline (--sql) mode has no live connection to poll.
            return False
        opts = self.context_opts or {}
        return bool(opts.get("starrocks_wait_for_schema_change", False))

    @staticmethod
    def _schema_change_target(construct: Any) -> Optional[Tuple[str, Optional[str]]]:
        """Return (table_name, schema) if the construct triggers a column schema change."""
        if not isinstance(construct, _SCHEMA_CHANGE_CONSTRUCTS):
            return None
        table_name: Optional[str] = getattr(construct, "table_name", None)
        if not table_name:
            # Every construct listed above carries a table name; guard anyway so
            # the poll is never issued without one.
            return None
        return table_name, getattr(construct, "schema", None)

    def drop_index(self, index: Index, **kw: Any) -> None:
        """Drop a secondary index, unless StarRocks already dropped it together with its column.

        Dropping a column drops its index too, so a migration that drops an indexed column and then its index
        would otherwise fail on the second statement. Once the column's job has ended, a ``drop_index`` that
        finds its index gone does nothing.
        """
        table = index.table
        if self.connection is not None and not self.as_sql:
            dbapi_cursor = self.connection.connection.cursor()
            try:
                self.dialect.wait_while_schema_change_runs(dbapi_cursor, table.name, table.schema)
            finally:
                dbapi_cursor.close()
            if not self._index_exists(index.name, table.name, table.schema):
                logger.info("Index %r of table %r was already dropped with its column.", index.name, table.name)
                return
        super().drop_index(index, **kw)

    def _index_exists(self, index_name: str, table_name: str, schema: Optional[str]) -> bool:
        full_name = ".".join(self.dialect.identifier_preparer.quote(name) for name in (schema, table_name) if name)
        rows = self.connection.exec_driver_sql(f"SHOW INDEX FROM {full_name}").mappings().all()
        return any(row["Key_name"] == index_name for row in rows)

    @staticmethod
    def _schema_change_job_kind(construct: Any) -> str:
        """Return the SHOW ALTER TABLE kind that lists the job the construct starts: ``OPTIMIZE`` or ``COLUMN``."""
        return "OPTIMIZE" if isinstance(construct, _OPTIMIZE_JOB_CONSTRUCTS) else "COLUMN"

    @staticmethod
    def _latest_job_query(table_name: str, schema: Optional[str], alter_type: str) -> TextClause:
        """Build the query for the table's newest job of the kind, whatever state it is in.

        Order by JobId (a monotonic, unique FE counter), NOT CreateTime: CreateTime has second
        granularity, so several jobs submitted back-to-back on the same table share a timestamp and
        CreateTime-ordering returns an arbitrary (observed: the oldest) tied job.
        """
        return text(
            StarRocksDialect.gen_show_alter_table_statement(
                table_name,
                alter_type,
                schema=schema,
                state=None,
                order_by="JobId DESC",
                limit=1,
                bind_table_name=True,
            )
        )

    def _latest_job_id(self, table_name: str, schema: Optional[str], alter_type: str) -> int:
        """Return the JobId of the table's newest job of the kind, or 0 when it has none."""
        if self.connection is None:
            return 0
        row = self.connection.execute(
            self._latest_job_query(table_name, schema, alter_type), {"table_name": table_name}
        ).mappings().first()
        return int(row["JobId"]) if row and row.get("JobId") is not None else 0

    def _wait_for_schema_change(
        self,
        table_name: str,
        schema: Optional[str],
        alter_type: str = "COLUMN",
        after_job_id: Optional[int] = None,
    ) -> None:
        """Poll ``SHOW ALTER TABLE <alter_type>`` until the newest job reaches a terminal state.

        When ``after_job_id`` is given, only a job with a higher JobId counts, and the wait ends at once
        if the table has none.

        Raises:
            RuntimeError: if the schema change is CANCELLED or the timeout elapses.
        """
        connection = self.connection
        if connection is None:
            # Guarded by _should_wait_for_schema_change; keeps the poll below
            # honest for a direct caller (and for type checkers).
            return

        opts = self.context_opts or {}
        poll_interval = float(opts.get("starrocks_schema_change_poll_interval", 2.0))
        # None / 0 means wait indefinitely.
        timeout = opts.get("starrocks_schema_change_timeout", None)

        query = self._latest_job_query(table_name, schema, alter_type)

        deadline = None if not timeout else time.monotonic() + float(timeout)
        while True:
            row = connection.execute(
                query, {"table_name": table_name}
            ).mappings().first()

            # Defensive: no job row at all for this table (never had a column
            # change), or a result set without a State column on some version.
            # Note: fast-schema-evolution add/drop DOES create a row here, but it
            # comes back already FINISHED, so it exits via the FINISHED branch.
            raw_state = (row.get("State") if row else None)
            if raw_state is None:
                return
            if after_job_id is not None and int(row["JobId"]) <= after_job_id:
                logger.debug("The statement on %s started no %s job.", table_name, alter_type)
                return

            state = SchemaChangeState.parse(raw_state)
            if state is SchemaChangeState.FINISHED:
                logger.debug("Schema change for %s finished.", table_name)
                return
            if state is SchemaChangeState.CANCELLED:
                msg = row.get("Msg") if row else ""
                raise RuntimeError(
                    f"StarRocks schema change for table '{table_name}' was CANCELLED: {msg}"
                )

            if deadline is not None and time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Timed out after {timeout}s waiting for the schema change on "
                    f"table '{table_name}' to finish (last state: {raw_state})."
                )

            logger.info(
                "Waiting for StarRocks schema change on table '%s' (state: %s)...",
                table_name, raw_state,
            )
            time.sleep(poll_interval)

    @staticmethod
    def _unquoted_default(server_default: Any) -> Optional[str]:
        """Return the text of a server default without the quotes around a string, or ``None`` for none."""
        text_of_default = compare.server_default_text(server_default)
        return None if text_of_default is None else text_of_default.strip("'\"")

    def alter_column(
        self,
        table_name: str,
        column_name: str,
        *,
        server_default: Any = False,
        existing_server_default: Any = None,
        **kw: Any,
    ) -> None:
        """Alter a column, refusing a change of its default value, which StarRocks cannot make.

        A ``server_default`` that equals ``existing_server_default`` is no change, so it is passed on to the
        MODIFY COLUMN that keeps the default; any other one raises CompileError.
        """
        if server_default is not False and self._unquoted_default(server_default) != self._unquoted_default(
            existing_server_default
        ):
            raise column_default_change_error(table_name, column_name)
        super().alter_column(
            table_name,
            column_name,
            server_default=server_default,
            existing_server_default=existing_server_default,
            **kw,
        )

    def correct_for_autogen_constraints(
        self,
        conn_unique_constraints: Collection[UniqueConstraint],
        conn_indexes: Collection[Index],
        metadata_unique_constraints: Collection[UniqueConstraint],
        metadata_indexes: Collection[Index],
    ) -> None:
        """Leave unique constraints out of the comparison: StarRocks tables cannot hold them.

        CREATE TABLE drops a model's UniqueConstraint and reflection reports none, so comparing them proposed the same
        ``create_unique_constraint`` on every run, which StarRocks then rejects. Uniqueness comes from a PRIMARY KEY or
        UNIQUE KEY table instead. MySQL's correction for its implicit foreign key indexes does not apply.
        """
        self._empty(conn_unique_constraints, metadata_unique_constraints)

    def correct_for_autogen_foreignkeys(
        self, conn_fks: Collection[ForeignKeyConstraint], metadata_fks: Collection[ForeignKeyConstraint]
    ) -> None:
        """Leave foreign keys out of the comparison: StarRocks tables cannot hold them.

        As with unique constraints, a model's ForeignKey never reaches the table, so comparing them proposed the same
        ``create_foreign_key`` on every run.
        """
        self._empty(conn_fks, metadata_fks)

    @staticmethod
    def _empty(*collections: Collection[Any]) -> None:
        """Empty each collection; for a table that does not exist yet Alembic passes an already empty frozenset."""
        for collection in collections:
            if isinstance(collection, set):
                collection.clear()

    def compare_server_default(
        self,
        inspector_column: Column[Any],
        metadata_column: Column[Any],
        rendered_metadata_default: Optional[str],
        rendered_inspector_default: Optional[str],
    ) -> bool:
        """
        Compare column defaults, treating a default that only differs in how it is written as the same.

        MySQL's comparison reads ``uuid()`` and the ``(uuid())`` that StarRocks reports as different defaults, and
        compares function names case sensitively, so a default such as that diffed on every run. Defaults that
        still differ fall through to MySQL's comparison.

        Returns:
            True if the defaults are different, False if they are the same.
        """
        if compare.server_defaults_match(
            self.dialect, metadata_column, rendered_metadata_default, rendered_inspector_default
        ):
            return False
        return super().compare_server_default(
            inspector_column, metadata_column, rendered_metadata_default, rendered_inspector_default
        )

    def compare_type(self, inspector_column: Column[Any], metadata_column: Column[Any]) -> bool:
        """
        Set StarRocks' specific type comparison logic for some special cases.

        For some special cases:
            - complex type comparison: ARRAY, MAP, STRUCT
            - simple type comparison:
                - meta.BOOLEAN equals to conn.TINYINT(1)
                - meta.STRING equals to conn.VARCHAR(65533)

        Args:
            inspector_column: The column from the inspector.
            metadata_column: The column from the metadata.

        Returns:
            True if the types are different, False if the types are the same.
        """
        inspector_type = inspector_column.type
        metadata_type = metadata_column.type

        # Handle complex type comparison.
        if isinstance(metadata_type, datatype.StructuredType):
            # If the inspector found a different base type, they are different.
            is_different = False
            if not isinstance(inspector_type, type(metadata_type)):
                is_different = True
            else:
                # Perform deep, recursive comparison.
                # Returns True if different, False if same.
                is_different = compare.compare_complex_type(self, inspector_type, metadata_type)
            if is_different:
                table: Optional[Table] = metadata_column.table
                table_info_msg = f" of table: {table.name}, schema: {table.schema}," if table is not None else ""
                logger.warning(f"Detected type change{table_info_msg} from inspector_type: {inspector_type!r} to metadata_type: {metadata_type!r}. "
                            f"But, StarRocks does not support schema change for complex type columns. "
                            f"You should check and change the metadata carefully to make sure there is no type difference for this column. "
                            )
            return is_different

        return compare.compare_simple_type(self, inspector_column, metadata_column)
