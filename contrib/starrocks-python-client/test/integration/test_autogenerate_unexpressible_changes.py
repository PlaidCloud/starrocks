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

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Column, MetaData, Table, testing
from sqlalchemy.testing import fixtures

import starrocks.alembic  # noqa: F401
from starrocks.datatype import DATETIME, INTEGER


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
