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

"""``MetaData.create_all()`` and ``drop_all()`` with views and materialized views next to tables."""

from sqlalchemy import Column, MetaData, Table, inspect, testing
from sqlalchemy.schema import CreateTable
from sqlalchemy.testing import fixtures

from starrocks.datatype import INTEGER
from starrocks.sql.schema import MaterializedView, View


class MetaDataCreateDropTest(fixtures.TestBase):
    __backend__ = True
    __only_on__ = "starrocks"

    @staticmethod
    def _names(connection):
        inspector = inspect(connection)
        return (
            set(inspector.get_table_names())
            | set(inspector.get_view_names())
            | set(inspector.get_materialized_view_names())
        )

    @testing.requires.sqlalchemy_2
    def test_create_all_and_drop_all(self, connection):
        metadata = MetaData()
        Table(
            "cd_base",
            metadata,
            Column("id", INTEGER),
            starrocks_duplicate_key="id",
            starrocks_distributed_by="HASH(id) BUCKETS 1",
        )
        View("cd_view", metadata, definition="SELECT id FROM cd_base")
        MaterializedView("cd_mv", metadata, definition="SELECT id FROM cd_base", starrocks_refresh="MANUAL")

        metadata.create_all(connection)
        assert {"cd_base", "cd_view", "cd_mv"} <= self._names(connection)

        metadata.drop_all(connection)
        assert not {"cd_base", "cd_view", "cd_mv"} & self._names(connection)

    @testing.requires.sqlalchemy_2
    def test_views_are_created_after_the_tables_they_read(self, connection):
        """The view and MV are defined before their base table, and ``create_all()`` keeps definition order."""
        metadata = MetaData()
        View("aa_view", metadata, definition="SELECT id FROM zz_base")
        MaterializedView("ab_mv", metadata, definition="SELECT id FROM zz_base", starrocks_refresh="MANUAL")
        Table(
            "zz_base",
            metadata,
            Column("id", INTEGER),
            starrocks_duplicate_key="id",
            starrocks_distributed_by="HASH(id) BUCKETS 1",
        )

        metadata.create_all(connection)
        assert {"zz_base", "aa_view", "ab_mv"} <= self._names(connection)

        metadata.drop_all(connection)
        assert not {"zz_base", "aa_view", "ab_mv"} & self._names(connection)


class ReflectedTableRecreateTest(fixtures.TestBase):
    """A reflected table's CREATE TABLE, with its partition, distribution and sort key, runs again."""

    __backend__ = True
    __only_on__ = "starrocks"

    def test_a_reflected_table_is_created_again(self, connection):
        connection.exec_driver_sql(
            "CREATE TABLE reflected_to_recreate (k INT, d DATE) DUPLICATE KEY(k) PARTITION BY date_trunc('day', d) "
            "DISTRIBUTED BY HASH(k) BUCKETS 2 ORDER BY (k) PROPERTIES('replication_num' = '1')"
        )
        try:
            reflected = Table("reflected_to_recreate", MetaData(), autoload_with=connection)
            connection.exec_driver_sql(
                str(CreateTable(reflected).compile(connection)).replace("reflected_to_recreate", "recreated", 1)
            )
            options = inspect(connection).get_table_options("recreated")
            assert "date_trunc" in str(options["starrocks_partition_by"]), options
        finally:
            connection.exec_driver_sql("DROP TABLE IF EXISTS recreated")
            connection.exec_driver_sql("DROP TABLE IF EXISTS reflected_to_recreate")
