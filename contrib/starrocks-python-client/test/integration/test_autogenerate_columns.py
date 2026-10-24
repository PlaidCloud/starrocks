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

"""Column comparisons in autogenerate against tables reflected from StarRocks."""

from unittest.mock import Mock

from alembic.autogenerate.api import AutogenContext
from alembic.operations import Operations
from alembic.operations.ops import AlterColumnOp
from alembic.runtime.migration import MigrationContext
from sqlalchemy import BIGINT, Column, Integer, MetaData, Table, exc, inspect, testing
from sqlalchemy.testing import fixtures
from sqlalchemy.testing.assertions import expect_raises_message

from starrocks.alembic.compare import compare_starrocks_column_agg_type, compare_starrocks_column_autoincrement
from starrocks.common.types import ColumnAggType


AGGREGATE_TABLE_DDL = (
    "CREATE TABLE {name} (id BIGINT, v BIGINT SUM) AGGREGATE KEY(id) DISTRIBUTED BY HASH(id) "
    'PROPERTIES("replication_num"="1")'
)


class _ReflectedTableFixture:
    """Creates a table with plain DDL, reflects it, and drops only that table afterwards."""

    @testing.fixture
    def reflected_table(self, connection):
        created = []

        def create_and_reflect(name: str, ddl: str) -> Table:
            connection.exec_driver_sql(f"DROP TABLE IF EXISTS {name}")
            connection.exec_driver_sql(ddl.format(name=name))
            created.append(name)
            return Table(name, MetaData(), autoload_with=connection)

        yield create_and_reflect
        for name in created:
            connection.exec_driver_sql(f"DROP TABLE IF EXISTS {name}")

    def _autogen_context(self, connection) -> Mock:
        autogen_context = Mock(spec=AutogenContext)
        autogen_context.dialect = connection.dialect
        return autogen_context


class CompareColumnAggTypeTest(_ReflectedTableFixture, fixtures.TestBase):
    __backend__ = True
    __only_on__ = "starrocks"

    def _metadata_table(self, name: str, agg_type: ColumnAggType) -> Table:
        return Table(
            name,
            MetaData(),
            Column("id", BIGINT),
            Column("v", BIGINT, starrocks_agg_type=agg_type),
            starrocks_distributed_by="HASH(id)",
        )

    def test_compare_agg_type_no_changes(self, connection, reflected_table, caplog):
        conn_table = reflected_table("compare_agg_type_no_changes", AGGREGATE_TABLE_DDL)
        meta_table = self._metadata_table("compare_agg_type_no_changes", ColumnAggType.SUM)
        caplog.set_level("INFO")

        compare_starrocks_column_agg_type(
            self._autogen_context(connection), None, None, conn_table.name, "v", conn_table.c.v, meta_table.c.v
        )
        assert "aggregation type" not in caplog.text

    def test_compare_agg_type_changes_warns(self, connection, reflected_table, caplog):
        conn_table = reflected_table("compare_agg_type_changes_warns", AGGREGATE_TABLE_DDL)
        meta_table = self._metadata_table("compare_agg_type_changes_warns", ColumnAggType.REPLACE)

        compare_starrocks_column_agg_type(
            self._autogen_context(connection), None, None, conn_table.name, "v", conn_table.c.v, meta_table.c.v
        )
        assert "does not support changing the aggregation type" in caplog.text


class CompareColumnAutoIncrementTest(_ReflectedTableFixture, fixtures.TestBase):
    __backend__ = True
    __only_on__ = "starrocks"

    def _compare_with_autoincrement_metadata(self, connection, conn_table: Table) -> object:
        meta_table = Table(
            conn_table.name,
            MetaData(),
            Column("id", BIGINT, autoincrement=True),
            starrocks_properties={"replication_num": "1"},
        )
        return compare_starrocks_column_autoincrement(
            self._autogen_context(connection), None, None, conn_table.name, "id", conn_table.c.id, meta_table.c.id
        )

    def test_compare_autoincrement_no_changes(self, connection, reflected_table, caplog):
        conn_table = reflected_table(
            "compare_autoincrement_no_changes",
            "CREATE TABLE {name} (id BIGINT NOT NULL AUTO_INCREMENT) DISTRIBUTED BY RANDOM "
            'PROPERTIES("replication_num"="1")',
        )
        caplog.set_level("INFO")

        assert not self._compare_with_autoincrement_metadata(connection, conn_table)
        assert "cannot add or remove AUTO_INCREMENT" not in caplog.text

    def test_the_alter_of_an_integer_primary_key_carries_no_autoincrement(self, connection, reflected_table):
        conn_table = reflected_table(
            "compare_integer_primary_key",
            "CREATE TABLE {name} (id INT NOT NULL COMMENT 'old', v INT) PRIMARY KEY(id) DISTRIBUTED BY HASH(id) "
            'PROPERTIES("replication_num"="1")',
        )
        meta_table = Table(
            conn_table.name, MetaData(), Column("id", Integer, primary_key=True, comment="new"), Column("v", Integer)
        )
        alter_column_op = AlterColumnOp(conn_table.name, "id", modify_comment="new", autoincrement=True)
        compare_starrocks_column_autoincrement(
            self._autogen_context(connection), alter_column_op, None, conn_table.name, "id",
            conn_table.c.id, meta_table.c.id,
        )
        assert "autoincrement" not in alter_column_op.kw

    @testing.skip_if("starrocks<4.1", "StarRocks 3.3 and 3.5 refuse any MODIFY COLUMN of an AUTO_INCREMENT column")
    def test_an_alter_without_existing_autoincrement_reads_it_from_the_database(self, connection, reflected_table):
        conn_table = reflected_table(
            "alter_reads_autoincrement",
            "CREATE TABLE {name} (k INT, id BIGINT NOT NULL AUTO_INCREMENT COMMENT 'old') DUPLICATE KEY(k) "
            'DISTRIBUTED BY RANDOM PROPERTIES("replication_num"="1")',
        )
        Operations(MigrationContext.configure(connection)).alter_column(
            conn_table.name, "id", comment="new", existing_type=BIGINT(), existing_nullable=False, autoincrement=True
        )
        columns = {column["name"]: column for column in inspect(connection).get_columns(conn_table.name)}
        assert (columns["id"]["comment"], columns["id"]["autoincrement"]) == ("new", True)

    def test_an_alter_adding_autoincrement_is_refused(self, connection, reflected_table):
        conn_table = reflected_table(
            "alter_adds_autoincrement",
            "CREATE TABLE {name} (k INT, id BIGINT NOT NULL COMMENT 'old') DUPLICATE KEY(k) "
            'DISTRIBUTED BY RANDOM PROPERTIES("replication_num"="1")',
        )
        with expect_raises_message(exc.CompileError, "cannot add AUTO_INCREMENT"):
            Operations(MigrationContext.configure(connection)).alter_column(
                conn_table.name, "id", comment="new", existing_type=BIGINT(), existing_nullable=False,
                autoincrement=True,
            )

    def test_compare_autoincrement_changes_warns(self, connection, reflected_table, caplog):
        """A column without AUTO_INCREMENT against metadata asking for it: no op, but a warning."""
        conn_table = reflected_table(
            "compare_autoincrement_changes_warns",
            'CREATE TABLE {name} (id BIGINT NOT NULL) DISTRIBUTED BY RANDOM PROPERTIES("replication_num"="1")',
        )
        caplog.set_level("WARNING")

        assert not self._compare_with_autoincrement_metadata(connection, conn_table)
        assert any(
            "cannot add or remove AUTO_INCREMENT" in r.getMessage() and r.levelname == "WARNING" for r in caplog.records
        )
