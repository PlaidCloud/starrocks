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

"""Autogenerate against tables whose metadata differs from the database in ways ALTER TABLE cannot express."""

from alembic.autogenerate import compare_metadata, produce_migrations
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, MetaData, Table, testing
from sqlalchemy.testing import expect_warnings, fixtures

import starrocks.alembic  # noqa: F401
from starrocks.datatype import DATETIME, INTEGER, VARCHAR
from test.util import TIME_SLICE_CEIL_PARTITION_NEEDS_3_4_1


class OmittedAttributesTest(fixtures.TestBase):
    """An attribute the metadata leaves out is left as the database has it, without aborting the run."""

    __backend__ = True
    __only_on__ = "starrocks"

    @staticmethod
    def _diffs(connection, metadata, prefix):
        context = MigrationContext.configure(
            connection,
            opts={"include_name": lambda name, type_, parents: type_ != "table" or name.startswith(prefix)},
        )
        return compare_metadata(context, metadata)

    @testing.requires.sqlalchemy_2
    def test_omitted_distribution_on_a_hash_table(self, connection):
        connection.exec_driver_sql(
            "CREATE TABLE unexpressible_hash (id INT) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1"
        )
        try:
            metadata = MetaData()
            Table("unexpressible_hash", metadata, Column("id", INTEGER), starrocks_duplicate_key="id")
            assert self._diffs(connection, metadata, "unexpressible_") == []
        finally:
            connection.exec_driver_sql("DROP TABLE unexpressible_hash")


class UnalterableChangesTest(fixtures.TestBase):
    """A change StarRocks cannot ALTER is warned about and skipped; the rest of the migration is still generated."""

    __backend__ = True
    __only_on__ = "starrocks"

    @testing.requires.sqlalchemy_2
    def test_partition_change_does_not_stop_other_tables(self, connection, caplog):
        connection.exec_driver_sql(
            "CREATE TABLE unalterable_part (id INT, dt DATETIME) DUPLICATE KEY(id) "
            "PARTITION BY date_trunc('day', dt) DISTRIBUTED BY HASH(id) BUCKETS 1"
        )
        connection.exec_driver_sql(
            "CREATE TABLE unalterable_plain (id INT) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1"
        )
        try:
            metadata = MetaData()
            Table(
                "unalterable_part",
                metadata,
                Column("id", INTEGER),
                Column("dt", DATETIME),
                starrocks_duplicate_key="id",
                starrocks_partition_by="date_trunc('month', dt)",
                starrocks_distributed_by="HASH(id) BUCKETS 1",
            )
            Table(
                "unalterable_plain",
                metadata,
                Column("id", INTEGER),
                Column("added", INTEGER),
                starrocks_duplicate_key="id",
                starrocks_distributed_by="HASH(id) BUCKETS 1",
            )
            diffs = OmittedAttributesTest._diffs(connection, metadata, "unalterable_")
            assert [(diff[0], diff[2], diff[3].name) for diff in diffs] == [("add_column", "unalterable_plain", "added")]
            assert "StarRocks does not support 'ALTER TABLE PARTITION BY'" in caplog.text
        finally:
            connection.exec_driver_sql("DROP TABLE unalterable_part")
            connection.exec_driver_sql("DROP TABLE unalterable_plain")

    @testing.requires.sqlalchemy_2
    def test_the_generated_property_changes_apply(self, connection, caplog):
        """A property StarRocks refuses to SET on this server is warned about and skipped; the others are set."""
        connection.exec_driver_sql(
            "CREATE TABLE unalterable_props (id INT) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
            "PROPERTIES ('replication_num' = '1')"
        )
        try:
            metadata = MetaData()
            Table(
                "unalterable_props",
                metadata,
                Column("id", INTEGER),
                starrocks_duplicate_key="id",
                starrocks_distributed_by="HASH(id) BUCKETS 1",
                starrocks_properties={
                    "replication_num": "1",
                    "compression": "ZSTD",
                    "fast_schema_evolution": "false",
                    "enable_statistic_collect_on_first_load": "false",
                    "write_quorum": "ALL",
                },
            )
            context = MigrationContext.configure(
                connection,
                opts={"include_name": lambda name, type_, parents: type_ != "table" or name == "unalterable_props"},
            )
            operations = Operations(context)
            for op in produce_migrations(context, metadata).upgrade_ops.ops:
                for table_op in getattr(op, "ops", [op]):
                    operations.invoke(table_op)

            ddl = connection.exec_driver_sql("SHOW CREATE TABLE unalterable_props").fetchone()[1]
            assert '"write_quorum" = "ALL"' in ddl, ddl
            assert "would set property 'compression' to 'ZSTD'" in caplog.text
            assert "would set property 'fast_schema_evolution' to 'false'" in caplog.text
        finally:
            connection.exec_driver_sql("DROP TABLE unalterable_props")


class ColumnDefaultChangeTest(fixtures.TestBase):
    """A changed column default is warned about and skipped, since StarRocks cannot change a default."""

    __backend__ = True
    __only_on__ = "starrocks"

    @testing.requires.sqlalchemy_2
    def test_default_change_is_skipped_and_other_changes_are_generated(self, connection, caplog):
        connection.exec_driver_sql(
            "CREATE TABLE default_change (id INT, s VARCHAR(10) DEFAULT 'a') DUPLICATE KEY(id) "
            "DISTRIBUTED BY HASH(id) BUCKETS 1"
        )
        try:
            metadata = MetaData()
            Table(
                "default_change",
                metadata,
                Column("id", INTEGER),
                Column("s", VARCHAR(10), server_default="b"),
                Column("added", INTEGER),
                starrocks_duplicate_key="id",
                starrocks_distributed_by="HASH(id) BUCKETS 1",
            )
            context = MigrationContext.configure(
                connection,
                opts={
                    "compare_server_default": True,
                    "include_name": lambda name, type_, parents: type_ != "table" or name == "default_change",
                },
            )
            diffs = compare_metadata(context, metadata)

            assert [(diff[0], diff[3].name) for diff in diffs] == [("add_column", "added")]
            assert "Detected DEFAULT change on column 'default_change.s'" in caplog.text
        finally:
            connection.exec_driver_sql("DROP TABLE default_change")


class TimeSlicePartitionTest(fixtures.TestBase):
    """A time_slice partition reflects in a different spelling from the one it was created with, and still compares equal."""

    __backend__ = True
    __only_on__ = "starrocks"

    @testing.requires.sqlalchemy_2
    @testing.combinations(
        ("time_slice(dt, INTERVAL 7 DAY)", "time_slice(dt, INTERVAL 7 DAY)"),
        ("time_slice(dt, INTERVAL 7 DAY, floor)", "time_slice(dt, INTERVAL 7 DAY)"),
        (
            "time_slice(dt, INTERVAL 1 MONTH, ceil)",
            "time_slice(dt, INTERVAL 1 MONTH, ceil)",
            TIME_SLICE_CEIL_PARTITION_NEEDS_3_4_1,
        ),
        argnames="partition_by, model_partition_by",
    )
    def test_no_diff_after_create(self, connection, caplog, partition_by, model_partition_by):
        connection.exec_driver_sql(
            f"CREATE TABLE time_slice_part (id INT, dt DATETIME) DUPLICATE KEY(id) "
            f"PARTITION BY {partition_by} DISTRIBUTED BY HASH(id) BUCKETS 1"
        )
        try:
            metadata = MetaData()
            Table(
                "time_slice_part",
                metadata,
                Column("id", INTEGER),
                Column("dt", DATETIME),
                starrocks_duplicate_key="id",
                starrocks_partition_by=model_partition_by,
                starrocks_distributed_by="HASH(id) BUCKETS 1",
            )
            assert OmittedAttributesTest._diffs(connection, metadata, "time_slice_part") == []
            assert "does not support 'ALTER TABLE PARTITION BY'" not in caplog.text
        finally:
            connection.exec_driver_sql("DROP TABLE time_slice_part")


class InferredPrimaryKeyTest(fixtures.TestBase):
    """``primary_key=True`` columns that lead a table with no ``starrocks_*_key`` make it a PRIMARY KEY table.

    Autogenerate compares that key only with a PRIMARY KEY table; any other key model is left as the database has it.
    """

    __backend__ = True
    __only_on__ = "starrocks"

    @staticmethod
    def _model(metadata):
        return Table("inferred_pk", metadata, Column("id", INTEGER, primary_key=True), Column("v", INTEGER))

    @testing.requires.sqlalchemy_2
    def test_created_as_primary_key_table_with_no_diff(self, connection, metadata):
        self._model(metadata)
        metadata.create_all(connection)

        ddl = connection.exec_driver_sql("SHOW CREATE TABLE inferred_pk").fetchone()[1]
        assert "PRIMARY KEY(`id`)" in ddl
        assert OmittedAttributesTest._diffs(connection, metadata, "inferred_pk") == []

    @testing.requires.sqlalchemy_2
    def test_key_columns_that_do_not_lead_make_a_duplicate_key_table(self, connection, metadata, caplog):
        Table("inferred_pk", metadata, Column("v", INTEGER), Column("id", INTEGER, primary_key=True))
        with expect_warnings(r"Table 'inferred_pk' has the primary key \(id\)"):
            metadata.create_all(connection)

        ddl = connection.exec_driver_sql("SHOW CREATE TABLE inferred_pk").fetchone()[1]
        assert "DUPLICATE KEY(`v`" in ddl
        assert OmittedAttributesTest._diffs(connection, metadata, "inferred_pk") == []
        assert "different key" not in caplog.text

    @testing.requires.sqlalchemy_2
    def test_existing_duplicate_key_table_is_left_alone(self, connection, caplog):
        """A table an older dialect created as DUPLICATE KEY from the same model gives no warning and no diff."""
        connection.exec_driver_sql("CREATE TABLE inferred_pk (id INT NOT NULL, v INT) DUPLICATE KEY(id, v)")
        try:
            metadata = MetaData()
            self._model(metadata)
            assert OmittedAttributesTest._diffs(connection, metadata, "inferred_pk") == []
            assert "different key" not in caplog.text
        finally:
            connection.exec_driver_sql("DROP TABLE inferred_pk")

    @testing.requires.sqlalchemy_2
    def test_implied_key_is_compared_with_a_primary_key_table(self, connection, caplog):
        """The model implies the key (id, v) and the table has (id); StarRocks cannot change a key, so this warns."""
        connection.exec_driver_sql(
            "CREATE TABLE inferred_pk (id INT NOT NULL, v INT NOT NULL, w INT) PRIMARY KEY(id) "
            "DISTRIBUTED BY HASH(id) BUCKETS 1"
        )
        try:
            metadata = MetaData()
            Table(
                "inferred_pk",
                metadata,
                Column("id", INTEGER, primary_key=True),
                Column("v", INTEGER, primary_key=True),
                Column("w", INTEGER),
                starrocks_distributed_by="HASH(id) BUCKETS 1",
            )
            assert OmittedAttributesTest._diffs(connection, metadata, "inferred_pk") == []
            assert "has different key columns: (id) to (id, v)" in caplog.text
        finally:
            connection.exec_driver_sql("DROP TABLE inferred_pk")
