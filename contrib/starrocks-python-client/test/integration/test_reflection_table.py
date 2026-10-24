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

from sqlalchemy import Column, Integer, MetaData, String, Table, event, inspect, testing
from sqlalchemy.testing import fixtures

from starrocks.common.params import TableInfoKeyWithPrefix
from starrocks.common.types import SystemRunMode
from starrocks.engine.interfaces import ReflectedPartitionInfo
from starrocks.sql.ddl import AlterTableProperties
from test.util import normalize_sql


class ReflectionTableOptionsTest(fixtures.TestBase):
    """``get_table_options`` reflects every StarRocks table option from information_schema."""

    __backend__ = True
    __only_on__ = "starrocks"

    def test_reflect_table_options(self, connection, metadata):
        Table(
            "test_reflect_table_options",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("name", String(32)),
            comment="Test table with all StarRocks options",
            starrocks_engine="OLAP",
            starrocks_primary_key="id",
            starrocks_partition_by="""RANGE(id) (
                PARTITION p1 VALUES LESS THAN ("10"),
                PARTITION p2 VALUES LESS THAN ("20"),
                PARTITION p3 VALUES LESS THAN (MAXVALUE)
            )""",
            starrocks_distributed_by="HASH(id) BUCKETS 8",
            starrocks_order_by="id, name",
            starrocks_properties={"replication_num": "1", "storage_medium": "SSD"},
        )
        metadata.create_all(connection)

        table_options = inspect(connection).get_table_options("test_reflect_table_options")

        assert table_options[TableInfoKeyWithPrefix.ENGINE] == "OLAP"
        assert normalize_sql(table_options[TableInfoKeyWithPrefix.PRIMARY_KEY]) == "id"
        partition_info = table_options[TableInfoKeyWithPrefix.PARTITION_BY]
        assert isinstance(partition_info, ReflectedPartitionInfo)
        assert normalize_sql(partition_info.partition_method) == normalize_sql("RANGE(id)")
        assert "(PARTITION p1 VALUES" in normalize_sql(partition_info.pre_created_partitions)
        assert normalize_sql(table_options[TableInfoKeyWithPrefix.DISTRIBUTED_BY]) == normalize_sql(
            "HASH(id) BUCKETS 8"
        )
        assert normalize_sql(table_options[TableInfoKeyWithPrefix.ORDER_BY]) == normalize_sql("id, name")
        properties = table_options[TableInfoKeyWithPrefix.PROPERTIES]
        assert properties["replication_num"] == "1"
        if connection.dialect.run_mode == SystemRunMode.SHARED_DATA:
            assert properties.get("storage_medium", "HDD") == "HDD"
        else:
            assert properties["storage_medium"] == "SSD"
        assert table_options[TableInfoKeyWithPrefix.COMMENT] == "Test table with all StarRocks options"


class PropertyValueRoundTripTest(fixtures.TestBase):
    """A PROPERTIES value with backslashes and double quotes is stored, and reflected, exactly as given."""

    __backend__ = True
    __only_on__ = "starrocks"

    @staticmethod
    def _reflected_colocate_with(connection, table_name):
        options = inspect(connection).get_table_options(table_name)
        return options[TableInfoKeyWithPrefix.PROPERTIES]["colocate_with"]

    @testing.combinations(
        ("backslash_letter", r"grp\d+"),
        ("double_quote", 'grp"1'),
        ("escaped_quote", r'grp\"1'),
        argnames="value",
        id_="ia",
    )
    def test_create_and_alter(self, connection, metadata, value):
        Table(
            "prop_round_trip",
            metadata,
            Column("id", Integer),
            starrocks_duplicate_key="id",
            starrocks_distributed_by="HASH(id) BUCKETS 1",
            starrocks_properties={"colocate_with": value},
        )
        metadata.create_all(connection)
        assert self._reflected_colocate_with(connection, "prop_round_trip") == value

        altered = value + "_2"
        connection.execute(AlterTableProperties("prop_round_trip", {"colocate_with": altered}))
        assert self._reflected_colocate_with(connection, "prop_round_trip") == altered


class ReflectedPrimaryKeyTest(fixtures.TestBase):
    """Only tables with one row per key report their key columns as the primary key."""

    __backend__ = True
    __only_on__ = "starrocks"

    @testing.combinations(
        ("primary_key", "starrocks_primary_key", ["id", "tenant"]),
        ("unique_key", "starrocks_unique_key", ["id", "tenant"]),
        ("aggregate_key", "starrocks_aggregate_key", ["id", "tenant"]),
        ("duplicate_key", "starrocks_duplicate_key", []),
        argnames="key_option, expected_pk_columns",
        id_="iaa",
    )
    def test_primary_key_reported_by_table_model(self, connection, metadata, key_option, expected_pk_columns):
        value_kwargs = {"starrocks_agg_type": "SUM"} if key_option == "starrocks_aggregate_key" else {}
        Table(
            "reflected_pk_model",
            metadata,
            Column("id", Integer),
            Column("tenant", Integer),
            Column("val", Integer, **value_kwargs),
            starrocks_distributed_by="HASH(id) BUCKETS 1",
            starrocks_properties={"replication_num": "1"},
            **{key_option: "id, tenant"},
        )
        metadata.create_all(connection)

        inspector = inspect(connection)
        assert inspector.get_pk_constraint("reflected_pk_model")["constrained_columns"] == expected_pk_columns
        assert inspector.get_table_options("reflected_pk_model")[f"{key_option}"] == "id, tenant"
        assert inspector.get_indexes("reflected_pk_model") == []


class ReflectionStatementCountTest(fixtures.TestBase):
    """A table is read from information_schema once, however it is reflected."""

    __backend__ = True
    __only_on__ = "starrocks"

    @staticmethod
    def _create_tables(connection, metadata, count):
        for number in range(count):
            Table(
                f"stmt_count_{number}",
                metadata,
                Column("id", Integer),
                Column("name", String(32)),
                starrocks_duplicate_key="id",
                starrocks_distributed_by="HASH(id) BUCKETS 1",
                starrocks_properties={"replication_num": "1"},
            )
        metadata.create_all(connection)

    @staticmethod
    def _config_reads_while(connection, reflect):
        statements = []

        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(" ".join(statement.split()).lower())

        event.listen(connection, "before_cursor_execute", record)
        try:
            reflect()
        finally:
            event.remove(connection, "before_cursor_execute", record)
        return [s for s in statements if "information_schema.tables_config" in s]

    def test_autoload_reads_each_table_once(self, connection, metadata):
        self._create_tables(connection, metadata, 1)

        reads = self._config_reads_while(
            connection, lambda: Table("stmt_count_0", MetaData(), autoload_with=connection)
        )

        assert len(reads) == 1

    def test_metadata_reflect_reads_each_table_once(self, connection, metadata):
        self._create_tables(connection, metadata, 3)
        names = [f"stmt_count_{number}" for number in range(3)]

        reads = self._config_reads_while(connection, lambda: MetaData().reflect(connection, only=names))

        assert len(reads) == 3
