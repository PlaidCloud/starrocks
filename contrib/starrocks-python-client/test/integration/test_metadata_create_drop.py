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

from sqlalchemy import Column, MetaData, Table, inspect, literal, select, testing, text
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
    @testing.requires.sqlalchemy_2
    def test_a_view_of_a_select_reads_its_columns(self, connection):
        """A ``select()`` definition names a mixed-case and a reserved column as StarRocks reads them, and keeps a
        literal's ``%``."""
        metadata = MetaData()
        base = Table(
            "cd_select_base",
            metadata,
            Column("Id", INTEGER),
            Column("select", VARCHAR(10)),
            starrocks_duplicate_key="Id",
            starrocks_distributed_by="HASH(Id) BUCKETS 1",
        )
        View("cd_select_view", metadata, definition=select(base.c.Id, base.c.select, literal("50%").label("pct")))
        metadata.create_all(connection)
        try:
            connection.execute(base.insert(), [{"Id": 1, "select": "s"}])
            assert connection.execute(text("SELECT * FROM cd_select_view")).all() == [(1, "s", "50%")]
        finally:
            metadata.drop_all(connection)

