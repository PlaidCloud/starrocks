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

import sqlalchemy as sa
from sqlalchemy import TIMESTAMP, Column, Enum, LargeBinary, MetaData, Table, Text, UnicodeText, testing
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateColumn
from sqlalchemy.testing import fixtures
from sqlalchemy.testing.assertions import AssertsCompiledSQL

from starrocks.datatype import (
    ARRAY,
    BIGINT,
    BINARY,
    BITMAP,
    BOOLEAN,
    CHAR,
    DATE,
    DATETIME,
    DECIMAL,
    DOUBLE,
    FLOAT,
    HLL,
    INTEGER,
    JSON,
    LARGEINT,
    MAP,
    PERCENTILE,
    SMALLINT,
    STRING,
    STRUCT,
    TINYINT,
    VARBINARY,
    VARCHAR,
)
from starrocks.dialect import StarRocksDialect


class DataTypeCompileTest(fixtures.TestBase, AssertsCompiledSQL):
    """The DDL each StarRocks type renders, alone and as a column with constraints."""

    __dialect__ = StarRocksDialect()

    @testing.combinations(
        (TINYINT(), "TINYINT"),
        (TINYINT(1), "TINYINT(1)"),
        (SMALLINT(), "SMALLINT"),
        (INTEGER(), "INTEGER"),
        (BIGINT(), "BIGINT"),
        (BIGINT(20), "BIGINT(20)"),
        (LARGEINT(), "LARGEINT"),
        (BOOLEAN(), "BOOLEAN"),
        (DECIMAL(), "DECIMAL"),
        (DECIMAL(10), "DECIMAL(10)"),
        (DECIMAL(10, 2), "DECIMAL(10, 2)"),
        (DECIMAL(38, 18), "DECIMAL(38, 18)"),
        (FLOAT(), "FLOAT"),
        (FLOAT(10), "FLOAT"),
        (DOUBLE(), "DOUBLE"),
        (CHAR(10), "CHAR(10)"),
        (VARCHAR(255), "VARCHAR(255)"),
        (VARCHAR(65533), "STRING"),
        (STRING(), "STRING"),
        (Text(), "STRING"),
        (Text(100), "STRING"),
        (UnicodeText(), "STRING"),
        (Enum("a", "bb"), "VARCHAR(2)"),
        (Enum("réveillé", "b"), "VARCHAR(10)"),
        (Enum("a", native_enum=True), "VARCHAR(1)"),
        (BINARY(), "BINARY"),
        (BINARY(10), "BINARY(10)"),
        (VARBINARY(), "VARBINARY"),
        (VARBINARY(255), "VARBINARY(255)"),
        (LargeBinary(), "BINARY"),
        (LargeBinary(100), "BINARY(100)"),
        (DATE(), "DATE"),
        (DATETIME(), "DATETIME"),
        (DATETIME(fsp=6), "DATETIME"),
        (sa.DateTime(), "DATETIME"),
        (TIMESTAMP(), "DATETIME"),
        (mysql.TIMESTAMP(fsp=3), "DATETIME"),
        (HLL(), "HLL"),
        (BITMAP(), "BITMAP"),
        (PERCENTILE(), "PERCENTILE"),
        (JSON(), "JSON"),
        argnames="type_, expected",
        id_="ra",
    )
    def test_scalar_type(self, type_, expected):
        """Binary types keep their declared length, Enum is a VARCHAR sized in UTF-8 bytes; VARCHAR at StarRocks' maximum length renders as STRING."""
        self.assert_compile(type_, expected)

    @testing.combinations(
        (ARRAY(INTEGER), "ARRAY<INTEGER>"),
        (ARRAY(VARCHAR(50)), "ARRAY<VARCHAR(50)>"),
        (ARRAY(ARRAY(INTEGER)), "ARRAY<ARRAY<INTEGER>>"),
        (MAP(STRING, INTEGER), "MAP<STRING, INTEGER>"),
        (MAP(VARCHAR(50), DOUBLE), "MAP<VARCHAR(50), DOUBLE>"),
        (MAP(STRING, MAP(INTEGER, STRING)), "MAP<STRING, MAP<INTEGER, STRING>>"),
        (STRUCT(name=STRING, age=INTEGER), "STRUCT<name STRING, age INTEGER>"),
        (STRUCT(("a b", INTEGER), ("Caps", VARCHAR(5))), "STRUCT<`a b` INTEGER, Caps VARCHAR(5)>"),
        (STRUCT(("odd`name", INTEGER), ("1st", STRING)), "STRUCT<`odd``name` INTEGER, `1st` STRING>"),
        (
            STRUCT(id=INTEGER, name=VARCHAR(100), active=BOOLEAN),
            "STRUCT<id INTEGER, name VARCHAR(100), active BOOLEAN>",
        ),
        (
            STRUCT(user=STRUCT(id=INTEGER, name=STRING), metadata=MAP(STRING, STRING)),
            "STRUCT<user STRUCT<id INTEGER, name STRING>, metadata MAP<STRING, STRING>>",
        ),
        (
            ARRAY(
                MAP(STRING, STRUCT(level1=ARRAY(MAP(INTEGER, STRUCT(level2=STRING, level2_array=ARRAY(INTEGER))))))
            ),
            "ARRAY<MAP<STRING, STRUCT<level1 ARRAY<MAP<INTEGER, "
            "STRUCT<level2 STRING, level2_array ARRAY<INTEGER>>>>>>>",
        ),
        argnames="type_, expected",
        id_="ra",
    )
    def test_complex_type(self, type_, expected):
        """Compiling must not warn that the type cannot produce a cache key."""
        self.assert_compile(type_, expected)

    @testing.combinations(
        (ARRAY(INTEGER), ARRAY(INTEGER), True),
        (ARRAY(INTEGER), ARRAY(STRING), False),
        (MAP(STRING, INTEGER), MAP(STRING, INTEGER), True),
        (MAP(STRING, INTEGER), MAP(INTEGER, STRING), False),
        (STRUCT(a=INTEGER), STRUCT(a=INTEGER), True),
        (STRUCT(a=INTEGER), STRUCT(b=INTEGER), False),
        (STRUCT(a=INTEGER), STRUCT(a=STRING), False),
        (STRUCT(a=INTEGER, b=STRING), STRUCT(a=INTEGER), False),
        (ARRAY(STRUCT(a=INTEGER)), ARRAY(STRUCT(a=STRING)), False),
        argnames="left, right, same_key",
        id_="aaa",
    )
    def test_complex_types_have_distinguishing_cache_keys(self, left, right, same_key):
        """Statements are cached by type, so types that render differently must not share a cache key."""
        left_key = left._static_cache_key
        right_key = right._static_cache_key
        assert (left_key == right_key) is same_key

    @testing.combinations(
        (sa.Float(), "CAST(t.x AS FLOAT)"),
        (FLOAT(), "CAST(t.x AS FLOAT)"),
        (DOUBLE(), "CAST(t.x AS DOUBLE)"),
        argnames="target_type, expected",
        id_="ra",
    )
    def test_cast_to_floating_point_is_not_dropped(self, target_type, expected):
        """MySQL's version gate on CAST to FLOAT fails for StarRocks' 4.x version, silently dropping the CAST."""
        x = sa.column("x", sa.Integer, _selectable=sa.table("t"))
        self.assert_compile(sa.cast(x, target_type), expected)

    @testing.combinations(
        ("not_null", INTEGER(), {"nullable": False}, "col INTEGER NOT NULL"),
        ("comment", VARCHAR(100), {"comment": "A test column"}, "col VARCHAR(100) COMMENT 'A test column'"),
        ("default", INTEGER(), {"server_default": "0"}, "col INTEGER DEFAULT '0'"),
        (
            "all",
            VARCHAR(50),
            {"nullable": False, "comment": "Full test column", "server_default": "test"},
            "col VARCHAR(50) NOT NULL DEFAULT 'test' COMMENT 'Full test column'",
        ),
        ("empty_varchar_default", VARCHAR(50), {"server_default": ""}, "col VARCHAR(50) DEFAULT ''"),
        ("empty_string_default", STRING(), {"server_default": ""}, "col STRING DEFAULT ''"),
        argnames="type_, column_kwargs, expected",
        id_="iaaa",
    )
    def test_column_constraints(self, type_, column_kwargs, expected):
        column = Column("col", type_, **column_kwargs)
        Table("t", MetaData(), column)
        self.assert_compile(CreateColumn(column), expected)
