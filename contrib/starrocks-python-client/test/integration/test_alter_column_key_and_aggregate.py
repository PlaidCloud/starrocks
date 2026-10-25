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

"""``op.alter_column`` on a key column and on an aggregate value column, which need KEY or the aggregate type."""

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, testing
from sqlalchemy.testing import eq_, fixtures

from starrocks import BIGINT, VARCHAR


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


class _Buffer:
    def __init__(self):
        self.text = ""

    def write(self, text):
        self.text += text

    def flush(self):
        pass
