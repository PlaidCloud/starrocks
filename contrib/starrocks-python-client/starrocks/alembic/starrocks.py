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
from alembic.ddl.mysql import MySQLChangeColumn, MySQLImpl, MySQLModifyColumn
from sqlalchemy import Column, ForeignKeyConstraint, Index, MetaData, Table, UniqueConstraint, exc, text, util
from sqlalchemy.engine import Connection
from sqlalchemy.engine.cursor import CursorResult
from sqlalchemy.sql import Executable
from sqlalchemy.sql.elements import TextClause

from starrocks import datatype
from starrocks.alembic import compare
from starrocks.common.types import TableModel
from starrocks.datatype import BIGINT, VARCHAR
from starrocks.dialect import KEY_OR_AGGREGATE_ATTRIBUTE, StarRocksDialect, column_default_change_error
from starrocks.reflection import column_auto_increments
from starrocks.sql.ddl import AlterTableColumns, AlterTableDistribution, AlterTableOrder, AlterTableProperties


logger = logging.getLogger(__name__)

_COLUMN_JOB_CONSTRUCTS = (
    AlterTableColumns,
    AlterTableOrder,
    AlterTableProperties,
    alembic_base.AddColumn,
    alembic_base.DropColumn,
    alembic_base.AlterColumn,
)
"""DDL that can start a column schema-change job, listed by SHOW ALTER TABLE COLUMN.

StarRocks runs one schema-change job per table at a time, so with the wait on, the impl polls after each of these
for the job to end. A sort key change (ORDER BY) starts such a job, a property set may (bloom_filter_columns does),
and ``AlterColumn`` covers Alembic's nullable, type, default and comment changes and MySQL's MODIFY and CHANGE.
"""

_OPTIMIZE_JOB_CONSTRUCTS = (AlterTableDistribution,)
"""DDL that starts an OPTIMIZE job, which SHOW ALTER TABLE COLUMN does not list: a distribution change."""

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
        we poll ``SHOW ALTER TABLE COLUMN`` (or ``OPTIMIZE`` after a distribution change)
        after each such statement until the job it started reaches a terminal state, keeping the
        migration in lock-step with the cluster.

        Only a job newer than the table's latest one before the statement counts, so a statement that
        starts no job, and an old cancelled job of the table or of a dropped table of the same name,
        are not mistaken for it.
        """
        if isinstance(construct, (MySQLModifyColumn, MySQLChangeColumn)):
            self._mark_key_or_aggregate(construct)
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

    _KEY_COLUMN_KINDS = frozenset({"DUP", "UNI", "AGG"})
    _AGGREGATE_MODELS = frozenset({TableModel.AGG_KEYS, TableModel.AGG_KEYS2})

    def _mark_key_or_aggregate(self, construct: Union[MySQLModifyColumn, MySQLChangeColumn]) -> None:
        """Give a column change the ``KEY`` or aggregate type that StarRocks wants repeated in MODIFY COLUMN.

        Without ``KEY``, StarRocks takes a modified key column of a DUPLICATE or UNIQUE KEY table for a value
        column and fails with "No key column left"; without its aggregate type, a value column of an AGGREGATE KEY
        table fails with "Can not change aggregation type". Neither shows in the migration, so it is read from
        the database. Offline (``--sql``), or on anything but a live connection, there is no database to read, and
        the statement stays as it is.
        """
        if self.as_sql or not isinstance(self.connection, Connection) or construct.type_ is None:
            return
        schema_condition = "TABLE_SCHEMA = :schema" if construct.schema else "TABLE_SCHEMA = DATABASE()"
        names = {"table": construct.table_name, "column": construct.column_name, "schema": construct.schema}
        column_key = self._scalar(
            f"SELECT COLUMN_KEY FROM information_schema.columns WHERE {schema_condition} "
            "AND TABLE_NAME = :table AND COLUMN_NAME = :column",
            names,
        )
        if column_key in self._KEY_COLUMN_KINDS:
            setattr(construct, KEY_OR_AGGREGATE_ATTRIBUTE, "KEY")
            return
        table_model = self._scalar(
            f"SELECT TABLE_MODEL FROM information_schema.tables_config WHERE {schema_condition} AND TABLE_NAME = :table",
            names,
        )
        if table_model in self._AGGREGATE_MODELS:
            full_name = ".".join(
                self.dialect.identifier_preparer.quote(name) for name in (construct.schema, construct.table_name) if name
            )
            for row in self.connection.exec_driver_sql(f"SHOW FULL COLUMNS FROM {full_name}").mappings():
                if row["Field"] == construct.column_name and row["Extra"]:
                    setattr(construct, KEY_OR_AGGREGATE_ATTRIBUTE, row["Extra"].upper())

    def _scalar(self, query: str, parameters: Mapping[str, Any]) -> Any:
        """Return the first value of the first row a query returns, or None."""
        return self.connection.execute(text(query), parameters).scalar()

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
        name: Optional[str] = None,
        schema: Optional[str] = None,
        **kw: Any,
    ) -> None:
        """Alter a column, refusing a change of its default value, which StarRocks cannot make.

        A ``server_default`` that equals ``existing_server_default`` is no change, so it is passed on to the
        MODIFY COLUMN that keeps the default; any other one raises CompileError.

        StarRocks takes a RENAME COLUMN only in an ALTER TABLE of its own, so a new ``name`` is applied after the
        column's other changes, which keep the old name.

        StarRocks refuses AUTO_INCREMENT in MODIFY COLUMN and can't add or remove it, so an unchanged
        ``autoincrement`` is left out and a changed one raises CompileError; see :meth:`_refuse_autoincrement_change`.
        StarRocks 4.1 keeps a column's AUTO_INCREMENT through a MODIFY COLUMN that leaves it out, while 3.3 and 3.5
        refuse any MODIFY COLUMN of an AUTO_INCREMENT column.
        """
        if server_default is not False and self._unquoted_default(server_default) != self._unquoted_default(
            existing_server_default
        ):
            raise column_default_change_error(table_name, column_name)
        self._refuse_autoincrement_change(
            table_name, column_name, schema, kw.pop("autoincrement", None), kw.pop("existing_autoincrement", None)
        )
        renamed = name is not None and name != column_name
        if not renamed or self._changes_besides_the_name(server_default, kw):
            super().alter_column(
                table_name,
                column_name,
                server_default=server_default,
                existing_server_default=existing_server_default,
                schema=schema,
                **kw,
            )
        if renamed:
            self._exec(alembic_base.ColumnName(table_name, column_name, name, schema=schema))

    def _refuse_autoincrement_change(
        self,
        table_name: str,
        column_name: str,
        schema: Optional[str],
        autoincrement: Optional[bool],
        existing_autoincrement: Optional[bool],
    ) -> None:
        """Raise CompileError for an ``autoincrement`` that differs from the column's AUTO_INCREMENT.

        Without ``existing_autoincrement``, which Alembic never writes into a script, the column's AUTO_INCREMENT
        is read from the database. Offline (``--sql``) there is none to read, so an ``autoincrement`` without
        ``existing_autoincrement`` raises there too.
        """
        if autoincrement is None:
            return
        if existing_autoincrement is None:
            if self.as_sql or not isinstance(self.connection, Connection):
                raise exc.CompileError(
                    f"Without a database to read, the dialect can't tell whether AUTO_INCREMENT on column "
                    f"{column_name!r} of table {table_name!r} changes; pass existing_autoincrement"
                )
            existing_autoincrement = self._column_auto_increments(table_name, column_name, schema)
        if bool(autoincrement) != bool(existing_autoincrement):
            raise exc.CompileError(
                f"StarRocks cannot {'add' if autoincrement else 'remove'} AUTO_INCREMENT on column {column_name!r} "
                f"of table {table_name!r}; recreate the table to change it"
            )

    def _column_auto_increments(self, table_name: str, column_name: str, schema: Optional[str]) -> bool:
        """Return whether SHOW CREATE TABLE gives the column AUTO_INCREMENT; StarRocks column names ignore case."""
        full_name = ".".join(self.dialect.identifier_preparer.quote(name) for name in (schema, table_name) if name)
        create_table = self.connection.exec_driver_sql(f"SHOW CREATE TABLE {full_name}").fetchone()[1]
        return any(
            name.lower() == column_name.lower() and auto_increments
            for name, auto_increments in column_auto_increments(create_table).items()
        )

    @staticmethod
    def _changes_besides_the_name(server_default: Any, kw: Mapping[str, Any]) -> bool:
        """Whether an alter_column call changes more than the column's name."""
        return (
            server_default is not False
            or kw.get("nullable") is not None
            or kw.get("type_") is not None
            or kw.get("autoincrement") is not None
            or kw.get("comment", False) is not False
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
