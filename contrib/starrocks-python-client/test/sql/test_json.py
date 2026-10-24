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

"""JSON index and path access: the SQL it compiles to, and what the server returns for it."""

import decimal

from sqlalchemy import Column, Integer, MetaData, Table, select, testing
from sqlalchemy.testing import eq_, fixtures
from sqlalchemy.testing.assertions import AssertsCompiledSQL

from starrocks.datatype import JSON
from starrocks.dialect import StarRocksDialect


METADATA = MetaData()
DOCUMENTS = Table("documents", METADATA, Column("id", Integer), Column("data", JSON))
DATA = DOCUMENTS.c.data


class JsonAccessCompileTest(fixtures.TestBase, AssertsCompiledSQL):
    __dialect__ = StarRocksDialect()

    @testing.combinations(
        ("index", lambda: DATA["k"], "json_query(documents.data, %s)"),
        ("path", lambda: DATA[("a", "b")], "json_query(documents.data, %s)"),
        ("as_json", lambda: DATA["k"].as_json(), "json_query(documents.data, %s)"),
        ("as_integer", lambda: DATA["k"].as_integer(), "get_json_int(documents.data, %s)"),
        ("as_string", lambda: DATA["k"].as_string(), "get_json_string(documents.data, %s)"),
        ("as_float", lambda: DATA["k"].as_float(), "get_json_double(documents.data, %s)"),
        ("as_boolean", lambda: DATA["k"].as_boolean(), "get_json_bool(documents.data, %s)"),
        (
            "as_numeric_unscaled",
            lambda: DATA["k"].as_numeric(12, 0),
            "CAST(get_json_double(documents.data, %s) AS DECIMAL(12, 0))",
        ),
        (
            "as_numeric",
            lambda: DATA["k"].as_numeric(10, 2),
            "CAST(get_json_double(documents.data, %s) AS DECIMAL(10, 2))",
        ),
        ("path_as_integer", lambda: DATA[("a", 1)].as_integer(), "get_json_int(documents.data, %s)"),
        id_="iaa",
        argnames="expression_factory, expected",
    )
    def test_access_renders_starrocks_function(self, expression_factory, expected):
        self.assert_compile(select(expression_factory()), f"SELECT {expected} AS anon_1 FROM documents")

    def test_comparison_in_where(self):
        self.assert_compile(
            select(DOCUMENTS.c.id).where(DATA["k"].as_integer() == 5),
            "SELECT documents.id FROM documents WHERE get_json_int(documents.data, %s) = %s",
        )


class JsonAccessTest(fixtures.TablesTest):
    __backend__ = True
    __only_on__ = "starrocks"

    DOCUMENT = {
        "text": "x y",
        "unicode": "réve",
        "number": 5,
        "fraction": 1.5,
        "flag": True,
        "nothing": None,
        "nested": {"deep": {"leaf": 7}},
        "items": [10, 20, 30],
    }

    @classmethod
    def define_tables(cls, metadata):
        Table(
            "json_access",
            metadata,
            Column("id", Integer, primary_key=True, autoincrement=False),
            Column("data", JSON),
            starrocks_primary_key="id",
            starrocks_distributed_by="HASH(id)",
        )

    @classmethod
    def insert_data(cls, connection):
        connection.execute(
            cls.tables.json_access.insert(),
            [{"id": 1, "data": cls.DOCUMENT}, {"id": 2, "data": {}}],
        )

    def _scalar(self, connection, expression, row_id=1):
        table = self.tables.json_access
        return connection.scalar(select(expression).where(table.c.id == row_id))

    @testing.combinations(
        ("text", "as_string", "x y"),
        ("unicode", "as_string", "réve"),
        ("number", "as_integer", 5),
        ("fraction", "as_float", 1.5),
        ("flag", "as_boolean", True),
        ("number", "as_string", "5"),
        argnames="key, accessor, expected",
    )
    def test_typed_accessor(self, connection, key, accessor, expected):
        data = self.tables.json_access.c.data
        value = self._scalar(connection, getattr(data[key], accessor)())
        eq_(value, expected)
        eq_(type(value), type(expected))

    def test_numeric_accessor(self, connection):
        data = self.tables.json_access.c.data
        eq_(self._scalar(connection, data["fraction"].as_numeric(10, 2)), decimal.Decimal("1.50"))

    @testing.combinations(
        ("as_string",), ("as_integer",), ("as_float",), ("as_boolean",), ("as_json",), argnames="accessor"
    )
    def test_missing_key_is_null(self, connection, accessor):
        data = self.tables.json_access.c.data
        eq_(self._scalar(connection, getattr(data["absent"], accessor)()), None)
        eq_(self._scalar(connection, getattr(data["text"], accessor)(), row_id=2), None)

    @testing.combinations(
        ("as_string",), ("as_integer",), ("as_float",), ("as_boolean",), ("as_json",), argnames="accessor"
    )
    def test_json_null_reads_as_none(self, connection, accessor):
        data = self.tables.json_access.c.data
        eq_(self._scalar(connection, getattr(data["nothing"], accessor)()), None)

    def test_untyped_index_returns_json_value(self, connection):
        data = self.tables.json_access.c.data
        eq_(self._scalar(connection, data["nested"]), {"deep": {"leaf": 7}})
        eq_(self._scalar(connection, data["items"]), [10, 20, 30])
        eq_(self._scalar(connection, data["text"]), "x y")

    def test_path_access(self, connection):
        data = self.tables.json_access.c.data
        eq_(self._scalar(connection, data[("nested", "deep", "leaf")].as_integer()), 7)
        eq_(self._scalar(connection, data[("items", 1)].as_integer()), 20)
        eq_(self._scalar(connection, data[("nested", "deep")]), {"leaf": 7})

    def test_index_in_where(self, connection):
        table = self.tables.json_access
        rows = connection.execute(select(table.c.id).where(table.c.data["number"].as_integer() == 5)).all()
        eq_(rows, [(1,)])
