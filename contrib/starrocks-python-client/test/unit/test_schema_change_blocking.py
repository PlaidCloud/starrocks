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

"""Which statements the dialect holds back until the schema change running on their table has ended."""

from types import SimpleNamespace

from alembic.ddl import base as alembic_base
from sqlalchemy import Column, Index, MetaData, Table
from sqlalchemy.schema import CreateIndex, CreateTable, DropIndex, SetTableComment
from sqlalchemy.testing import fixtures

from starrocks import INTEGER
from starrocks.dialect import StarRocksDialect
from starrocks.sql.ddl import AlterTableProperties


class TableAlteredByTest(fixtures.TestBase):
    @staticmethod
    def _table():
        return Table("events", MetaData(), Column("id", INTEGER), Column("city", INTEGER), schema="analytics")

    @staticmethod
    def _altered(ddl, statement=None, schema_translate_map=None):
        dialect = StarRocksDialect()
        statement = statement if statement is not None else str(ddl.compile(dialect=dialect))
        context = SimpleNamespace(
            isddl=True,
            compiled=SimpleNamespace(statement=ddl),
            execution_options={"schema_translate_map": schema_translate_map} if schema_translate_map else {},
        )
        return StarRocksDialect._table_altered_by(statement, context)

    def test_create_and_drop_index_wait(self):
        index = Index("ix_city", self._table().c.city)
        assert self._altered(CreateIndex(index)) == ("events", "analytics")
        assert self._altered(DropIndex(index)) == ("events", "analytics")

    def test_the_schema_translate_map_names_the_schema_waited_on(self):
        index = Index("ix_city", self._table().c.city)
        assert self._altered(CreateIndex(index), schema_translate_map={"analytics": "staging"}) == ("events", "staging")
        add = alembic_base.AddColumn("events", Column("note", INTEGER))
        assert self._altered(add, "ALTER TABLE events ADD COLUMN note INTEGER", {None: "staging"}) == ("events", "staging")

    def test_alembic_alter_table_waits(self):
        add = alembic_base.AddColumn("events", Column("note", INTEGER), schema="analytics")
        assert self._altered(add, "ALTER TABLE analytics.events ADD COLUMN note INTEGER") == ("events", "analytics")

    def test_starrocks_alter_table_waits(self):
        alter = AlterTableProperties("events", {"replication_num": "1"}, schema="analytics")
        assert self._altered(alter) == ("events", "analytics")

    def test_alter_table_on_a_table_element_waits(self):
        table = self._table()
        table.comment = "events"
        assert self._altered(SetTableComment(table)) == ("events", "analytics")

    def test_create_table_does_not_wait(self):
        assert self._altered(CreateTable(self._table())) is None

    def test_statements_that_are_not_ddl_do_not_wait(self):
        assert StarRocksDialect._table_altered_by("ALTER TABLE t ADD COLUMN c INT", None) is None
        assert StarRocksDialect._table_altered_by(
            "ALTER TABLE t ADD COLUMN c INT", SimpleNamespace(isddl=False, compiled=None)
        ) is None
