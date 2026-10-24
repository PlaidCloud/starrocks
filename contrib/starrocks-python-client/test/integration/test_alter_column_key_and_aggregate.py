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

"""``op.alter_column`` where StarRocks needs the MODIFY COLUMN to restate KEY, an aggregate type or the default."""

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import exc, inspect, testing, text
from sqlalchemy.testing import eq_, expect_raises_message, fixtures

from starrocks import BIGINT, INTEGER, VARCHAR


class AlterKeyAndAggregateColumnTest(fixtures.TestBase):
    __backend__ = True
    __only_on__ = "starrocks"

    def _operations(self, connection):
        return Operations(MigrationContext.configure(connection, opts={"starrocks_wait_for_schema_change": True}))

    @testing.combinations(("DUPLICATE",), ("UNIQUE",), argnames="model")
    def test_a_key_column_that_is_not_the_distribution_column_widens(self, connection, model):
        """Without KEY, StarRocks takes the column for a value column; an AGGREGATE table keeps its keys anyway."""
        name = f"alter_key_{model.lower()}"
        connection.exec_driver_sql(f"DROP TABLE IF EXISTS {name}")
        connection.exec_driver_sql(
            f"CREATE TABLE {name} (k1 INT, k2 VARCHAR(10), v INT) {model} KEY(k1, k2) "
            "DISTRIBUTED BY HASH(k1) BUCKETS 1 PROPERTIES ('replication_num' = '1')"
        )
        try:
            self._operations(connection).alter_column(name, "k2", type_=VARCHAR(30), existing_nullable=True)
            columns = {c["name"]: c for c in inspect(connection).get_columns(name)}
            eq_(columns["k2"]["type"].length, 30)
        finally:
            connection.exec_driver_sql(f"DROP TABLE IF EXISTS {name}")

    def test_an_aggregate_value_column_keeps_its_aggregate_type(self, connection):
        connection.exec_driver_sql("DROP TABLE IF EXISTS alter_agg_value")
        connection.exec_driver_sql(
            "CREATE TABLE alter_agg_value (k INT, v BIGINT SUM) AGGREGATE KEY(k) "
            "DISTRIBUTED BY HASH(k) BUCKETS 1 PROPERTIES ('replication_num' = '1')"
        )
        try:
            self._operations(connection).alter_column(
                "alter_agg_value", "v", comment="total", existing_type=BIGINT(), existing_nullable=True
            )
            rows = connection.exec_driver_sql("SHOW FULL COLUMNS FROM alter_agg_value").mappings().all()
            column = next(row for row in rows if row["Field"] == "v")
            eq_((column["Extra"].upper(), column["Comment"]), ("SUM", "total"))
        finally:
            connection.exec_driver_sql("DROP TABLE IF EXISTS alter_agg_value")

    def test_a_key_column_named_in_another_case_keeps_its_key(self, connection):
        connection.exec_driver_sql("DROP TABLE IF EXISTS alter_key_case")
        connection.exec_driver_sql(
            "CREATE TABLE alter_key_case (k1 INT, Name VARCHAR(10), v INT) DUPLICATE KEY(k1, Name) "
            "DISTRIBUTED BY HASH(k1) BUCKETS 1 PROPERTIES ('replication_num' = '1')"
        )
        try:
            self._operations(connection).alter_column("alter_key_case", "name", type_=VARCHAR(30), existing_nullable=True)
            columns = {c["name"].lower(): c for c in inspect(connection).get_columns("alter_key_case")}
            eq_(columns["name"]["type"].length, 30)
        finally:
            connection.exec_driver_sql("DROP TABLE IF EXISTS alter_key_case")

    def test_an_aggregate_value_column_named_in_another_case_keeps_its_aggregate_type(self, connection):
        connection.exec_driver_sql("DROP TABLE IF EXISTS alter_agg_case")
        connection.exec_driver_sql(
            "CREATE TABLE alter_agg_case (k INT, `My Total` BIGINT SUM) AGGREGATE KEY(k) "
            "DISTRIBUTED BY HASH(k) BUCKETS 1 PROPERTIES ('replication_num' = '1')"
        )
        try:
            self._operations(connection).alter_column(
                "alter_agg_case", "my total", comment="total", existing_type=BIGINT(), existing_nullable=True
            )
            rows = connection.exec_driver_sql("SHOW FULL COLUMNS FROM alter_agg_case").mappings().all()
            eq_([row["Extra"].upper() for row in rows if row["Field"].lower() == "my total"], ["SUM"])
        finally:
            connection.exec_driver_sql("DROP TABLE IF EXISTS alter_agg_case")

    def test_offline_mode_leaves_the_statement_as_it_is(self):
        context = MigrationContext.configure(dialect_name="starrocks", opts={"as_sql": True})
        context.impl.output_buffer = buffer = _Buffer()
        Operations(context).alter_column("t", "k", type_=VARCHAR(30), existing_nullable=True)
        eq_(buffer.text.strip().rstrip(";"), "ALTER TABLE t MODIFY COLUMN k VARCHAR(30) NULL")


class AlterColumnRestatingItsDefaultTest(fixtures.TestBase):
    """StarRocks needs a type change to restate the column's default, which the database tells when a script doesn't.

    A MODIFY COLUMN without the default fails with "Can not change default value", so hand-written migrations pass
    the unchanged default as ``server_default`` and leave ``existing_server_default`` out.
    """

    __backend__ = True
    __only_on__ = "starrocks"

    @testing.fixture
    def default_table(self, connection):
        connection.exec_driver_sql("DROP TABLE IF EXISTS alter_default")
        connection.exec_driver_sql(
            "CREATE TABLE alter_default (k INT, n INT DEFAULT '5', s VARCHAR(10) DEFAULT 'it''s') DUPLICATE KEY(k) "
            "DISTRIBUTED BY HASH(k) BUCKETS 1 PROPERTIES ('replication_num' = '1')"
        )
        yield "alter_default"
        connection.exec_driver_sql("DROP TABLE IF EXISTS alter_default")

    @staticmethod
    def _column(connection, name):
        return next(c for c in inspect(connection).get_columns("alter_default") if c["name"] == name)

    @testing.combinations(
        ("integer", "n", INTEGER(), BIGINT(), "5", ("BIGINT", None, "'5'")),
        ("quoted_string", "s", VARCHAR(10), VARCHAR(20), "it's", ("VARCHAR", 20, "'it''s'")),
        ("text_of_the_reflected_default", "s", VARCHAR(10), VARCHAR(20), text("'it''s'"), ("VARCHAR", 20, "'it''s'")),
        argnames="column, existing_type, new_type, server_default, reflected",
        id_="iaaaaa",
    )
    def test_the_unchanged_default_is_restated(
        self, connection, default_table, column, existing_type, new_type, server_default, reflected
    ):
        Operations(MigrationContext.configure(connection, opts={"starrocks_wait_for_schema_change": True})).alter_column(
            default_table, column, type_=new_type, existing_type=existing_type, server_default=server_default
        )
        column_after = self._column(connection, column)
        column_type = column_after["type"]
        eq_((type(column_type).__name__, getattr(column_type, "length", None), column_after["default"]), reflected)

    def test_a_new_default_is_refused_before_anything_is_sent(self, connection, default_table):
        with expect_raises_message(exc.CompileError, "cannot change a column's default value"):
            Operations(MigrationContext.configure(connection)).alter_column(
                default_table, "n", type_=BIGINT(), existing_type=INTEGER(), server_default="6"
            )
        eq_(self._column(connection, "n")["default"], "'5'")

    def test_offline_the_default_is_taken_as_it_is(self):
        """With no database to read, a ``server_default`` without ``existing_server_default`` is not refused."""
        context = MigrationContext.configure(dialect_name="starrocks", opts={"as_sql": True})
        context.impl.output_buffer = buffer = _Buffer()
        Operations(context).alter_column("t", "n", type_=BIGINT(), existing_type=INTEGER(), server_default="5")
        eq_(buffer.text.strip().rstrip(";"), "ALTER TABLE t MODIFY COLUMN n BIGINT NULL DEFAULT '5'")


class _Buffer:
    def __init__(self):
        self.text = ""

    def write(self, text):
        self.text += text

    def flush(self):
        pass
