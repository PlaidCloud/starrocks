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

import datetime
import decimal

import sqlalchemy as sa
from sqlalchemy import TIMESTAMP, Column, Enum, LargeBinary, MetaData, Table, Text, UnicodeText, testing
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateColumn
from sqlalchemy.testing import eq_, fixtures
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
from test.util import JSON_TO_MAP_CAST_NEEDS_3_4_3


class _Float(sa.TypeDecorator):
    impl = sa.Float()
    cache_ok = True


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
        (sa.REAL(), "FLOAT"),
        (mysql.REAL(10, 2), "FLOAT"),
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
        (Enum("a", "bb", length=50, native_enum=False), "VARCHAR(50)"),
        (sa.NCHAR(5), "CHAR(5)"),
        (mysql.NCHAR(5), "CHAR(5)"),
        (mysql.CHAR(5, national=True), "CHAR(5)"),
        (mysql.CHAR(5, collation="utf8_bin"), "CHAR(5)"),
        (mysql.CHAR(5, binary=True), "CHAR(5)"),
        (sa.CHAR(), "CHAR"),
        (sa.NVARCHAR(5), "VARCHAR(5)"),
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
        (sa.ARRAY(sa.Integer, dimensions=2), "ARRAY<ARRAY<INTEGER>>"),
        (sa.ARRAY(sa.String(20), dimensions=3), "ARRAY<ARRAY<ARRAY<VARCHAR(20)>>>"),
        (MAP(STRING, INTEGER), "MAP<STRING, INTEGER>"),
        (MAP(VARCHAR(50), DOUBLE), "MAP<VARCHAR(50), DOUBLE>"),
        (MAP(STRING, MAP(INTEGER, STRING)), "MAP<STRING, MAP<INTEGER, STRING>>"),
        (STRUCT(name=STRING, age=INTEGER), "STRUCT<name STRING, age INTEGER>"),
        (STRUCT(("a b", INTEGER), ("Caps", VARCHAR(5))), "STRUCT<`a b` INTEGER, `Caps` VARCHAR(5)>"),
        (STRUCT(("1st", STRING)), "STRUCT<`1st` STRING>"),
        (STRUCT(("p%ct", INTEGER)), "STRUCT<`p%%ct` INTEGER>"),
        (STRUCT(key=INTEGER, select=STRING, files=INTEGER), "STRUCT<`key` INTEGER, `select` STRING, `files` INTEGER>"),
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
        ("column", lambda type_: CreateColumn(Column("s", type_))),
        ("cast", lambda type_: sa.cast(sa.column("x", STRUCT(a=INTEGER())), type_)),
        ("nested", lambda type_: CreateColumn(Column("s", ARRAY(type_)))),
        argnames="construct",
        id_="ia",
    )
    def test_a_backquote_in_a_struct_field_name_is_refused(self, construct):
        """StarRocks drops the backquote from a field name such as ``a`b``, so the field would be named ``ab``."""
        with testing.expect_raises_message(sa.exc.CompileError, "drops the backquote"):
            construct(STRUCT(("a`b", INTEGER))).compile(dialect=self.__dialect__)

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
        ("generic_float", sa.Float(), "CAST(t.x AS DOUBLE)"),
        ("generic_float_53", sa.Float(53), "CAST(t.x AS DOUBLE)"),
        ("generic_float_25", sa.Float(25), "CAST(t.x AS DOUBLE)"),
        ("generic_float_24", sa.Float(24), "CAST(t.x AS FLOAT)"),
        ("generic_float_as_decimal", sa.Float(asdecimal=True), "CAST(t.x AS DOUBLE)"),
        ("decorated_generic_float", _Float(), "CAST(t.x AS DOUBLE)"),
        ("float", FLOAT(), "CAST(t.x AS FLOAT)"),
        ("float_24", FLOAT(24), "CAST(t.x AS FLOAT)"),
        ("float_53", FLOAT(53), "CAST(t.x AS DOUBLE)"),
        ("sqla_float", sa.FLOAT(), "CAST(t.x AS FLOAT)"),
        ("sqla_float_24", sa.FLOAT(24), "CAST(t.x AS FLOAT)"),
        ("sqla_float_53", sa.FLOAT(53), "CAST(t.x AS DOUBLE)"),
        ("mysql_float", mysql.FLOAT(), "CAST(t.x AS FLOAT)"),
        ("mysql_float_53", mysql.FLOAT(53), "CAST(t.x AS DOUBLE)"),
        ("mysql_float_10_2", mysql.FLOAT(10, 2), "CAST(t.x AS FLOAT)"),
        ("mysql_float_30_2", mysql.FLOAT(30, 2), "CAST(t.x AS FLOAT)"),
        ("double", DOUBLE(), "CAST(t.x AS DOUBLE)"),
        ("real", sa.REAL(), "CAST(t.x AS FLOAT)"),
        ("mysql_real", mysql.REAL(), "CAST(t.x AS FLOAT)"),
        argnames="target_type, expected",
        id_="iaa",
    )
    def test_cast_to_floating_point_is_not_dropped(self, target_type, expected):
        """MySQL's version gate on CAST to FLOAT fails for StarRocks' 4.x version, silently dropping the CAST.

        A float type with a precision above 24, and the generic ``Float`` with none, is DOUBLE, as MySQL reads
        ``FLOAT(p)``. MySQL stores ``FLOAT(M, D)`` in 4 bytes whatever its M.
        """
        x = sa.column("x", sa.Integer, _selectable=sa.table("t"))
        self.assert_compile(sa.cast(x, target_type), expected)

    @testing.combinations(
        (sa.Integer(), "CAST(t.x AS BIGINT)"),
        (sa.INTEGER(), "CAST(t.x AS INT)"),
        (sa.BigInteger(), "CAST(t.x AS BIGINT)"),
        (sa.SmallInteger(), "CAST(t.x AS SMALLINT)"),
        (LARGEINT(), "CAST(t.x AS LARGEINT)"),
        (TINYINT(4), "CAST(t.x AS TINYINT)"),
        (INTEGER(11), "CAST(t.x AS INT)"),
        (BIGINT(20), "CAST(t.x AS BIGINT)"),
        argnames="target_type, expected",
        id_="ra",
    )
    def test_cast_to_an_integer_names_its_width(self, target_type, expected):
        """MySQL's ``SIGNED INTEGER`` is INT on StarRocks 3.3 and 3.5 and BIGINT on 4.1, so a cast names the type itself."""
        x = sa.column("x", sa.String, _selectable=sa.table("t"))
        self.assert_compile(sa.cast(x, target_type), expected)

    @testing.combinations(
        ("tinyint_unsigned", mysql.TINYINT(unsigned=True), "CAST(t.x AS SMALLINT)"),
        ("smallint_unsigned", mysql.SMALLINT(unsigned=True), "CAST(t.x AS INT)"),
        ("integer_unsigned", mysql.INTEGER(unsigned=True), "CAST(t.x AS BIGINT)"),
        ("bigint_unsigned", mysql.BIGINT(unsigned=True), "CAST(t.x AS LARGEINT)"),
        ("integer_zerofill", mysql.INTEGER(display_width=5, zerofill=True), "CAST(t.x AS INT)"),
        ("mediumint", mysql.MEDIUMINT(), "CAST(t.x AS INT)"),
        ("mediumint_unsigned", mysql.MEDIUMINT(unsigned=True), "CAST(t.x AS INT)"),
        argnames="target_type, expected",
        id_="iaa",
    )
    def test_cast_to_a_mysql_integer_names_a_starrocks_type(self, target_type, expected):
        """StarRocks refuses UNSIGNED, ZEROFILL and MEDIUMINT in a CAST; an unsigned type widens to hold its range."""
        x = sa.column("x", sa.String, _selectable=sa.table("t"))
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


_PLUS_FIVE = datetime.timezone(datetime.timedelta(hours=5))


class DateTimeLiteralTest(fixtures.TestBase, AssertsCompiledSQL):
    """DATETIME values render as literals on SQLAlchemy 1.4 as well as 2.0, an aware one as its wall time."""

    __dialect__ = StarRocksDialect()

    @testing.combinations(
        ("naive", datetime.datetime(2012, 10, 15, 12, 57, 18), "'2012-10-15 12:57:18'"),
        ("microseconds", datetime.datetime(1, 1, 1, 0, 0, 0, 5), "'0001-01-01 00:00:00.000005'"),
        ("aware", datetime.datetime(2012, 10, 15, 12, 57, 18, tzinfo=_PLUS_FIVE), "'2012-10-15 12:57:18'"),
        ("date", datetime.date(2012, 10, 15), "'2012-10-15'"),
        argnames="value, expected",
        id_="iaa",
    )
    def test_literal(self, value, expected):
        self.assert_compile(
            sa.literal(value, DATETIME()), expected, literal_binds=True
        )

    def test_a_date_in_a_datetime_column_renders_in_an_insert(self):
        table = sa.table("t", sa.column("d", sa.DateTime()))
        self.assert_compile(
            table.insert().values(d=datetime.date(2024, 1, 2)),
            "INSERT INTO t (d) VALUES ('2024-01-02')",
            literal_binds=True,
        )


class DateTimeWallTimeExecutionTest(fixtures.TestBase):
    """An aware datetime is stored and compared as its wall time, bound or rendered, as PyMySQL does with MySQL."""

    __backend__ = True
    __only_on__ = "starrocks"

    @testing.combinations(("bound", False), ("literal", True), argnames="literal_binds", id_="ia")
    def test_an_aware_value_is_stored_as_its_wall_time(self, connection, metadata, literal_binds):
        table = Table(
            "datetime_wall_time",
            metadata,
            Column("id", sa.Integer),
            Column("at", sa.DateTime(timezone=True)),
            starrocks_duplicate_key="id",
        )
        table.create(connection)
        statement = table.insert().values(id=1, at=datetime.datetime(2024, 1, 2, 3, 4, 5, tzinfo=_PLUS_FIVE))
        if literal_binds:
            connection.exec_driver_sql(str(statement.compile(connection, compile_kwargs={"literal_binds": True})))
        else:
            connection.execute(statement)

        eq_(connection.scalar(sa.select(table.c.at)), datetime.datetime(2024, 1, 2, 3, 4, 5))
        a_second_later = datetime.datetime(2024, 1, 2, 3, 4, 6, tzinfo=_PLUS_FIVE)
        eq_(connection.scalar(sa.select(table.c.id).where(table.c.at < a_second_later)), 1)


class SysdateCompileTest(fixtures.TestBase, AssertsCompiledSQL):
    """``func.sysdate()`` compiles to a function StarRocks has."""

    __dialect__ = StarRocksDialect()

    def test_sysdate_compiles_to_now(self):
        self.assert_compile(sa.func.sysdate(), "now()")

    def test_sysdate_keeps_its_precision(self):
        self.assert_compile(sa.func.sysdate(6), "now(6)", literal_binds=True)


class SysdateExecutionTest(fixtures.TestBase):
    """``func.sysdate()`` runs on the server and returns a datetime."""

    __backend__ = True
    __only_on__ = "starrocks"

    def test_sysdate_returns_a_datetime(self, connection):
        assert isinstance(connection.scalar(sa.select(sa.func.sysdate())), datetime.datetime)

    def test_sysdate_with_a_precision_returns_microseconds(self, connection):
        """``now()`` has no fraction, so reads that all lack microseconds mean the precision was dropped."""
        readings = [connection.scalar(sa.select(sa.func.sysdate(6))) for _ in range(3)]
        assert all(isinstance(reading, datetime.datetime) for reading in readings)
        assert any(reading.microsecond for reading in readings), readings


class IntegerCastExecutionTest(fixtures.TestBase):
    """An integer cast keeps the whole range of its target type on every supported StarRocks version."""

    __backend__ = True
    __only_on__ = "starrocks"

    @testing.combinations(
        (sa.Integer(), 5000000000),
        (sa.BigInteger(), 5000000000),
        (LARGEINT(), 2**100),
        argnames="target_type, value",
    )
    def test_cast_keeps_a_wide_value(self, connection, target_type, value):
        """The generic ``Integer`` names no width, so its cast keeps 64 bits, as ``SIGNED INTEGER`` did on 4.1."""
        read = connection.scalar(sa.select(sa.cast(sa.literal(str(value)), target_type)))
        eq_(read, value)
        eq_(type(read), int)

    @testing.combinations(
        ("tinyint_unsigned", mysql.TINYINT(unsigned=True), 255),
        ("smallint_unsigned", mysql.SMALLINT(unsigned=True), 65535),
        ("integer_unsigned", mysql.INTEGER(unsigned=True), 4294967295),
        ("bigint_unsigned", mysql.BIGINT(unsigned=True), 18446744073709551615),
        ("integer_zerofill", mysql.INTEGER(display_width=5, zerofill=True), 7),
        ("mediumint", mysql.MEDIUMINT(), -8388608),
        ("mediumint_unsigned", mysql.MEDIUMINT(unsigned=True), 16777215),
        argnames="target_type, value",
        id_="iaa",
    )
    def test_cast_to_a_mysql_integer_keeps_its_whole_range(self, connection, target_type, value):
        """An unsigned BIGINT is cast to LARGEINT, which the server sends as text; it still reads as ``int``."""
        read = connection.scalar(sa.select(sa.cast(sa.literal(str(value)), target_type)))
        eq_(read, value)
        eq_(type(read), int)


class FloatingPointCastExecutionTest(fixtures.TestBase):
    """REAL, which StarRocks does not have, is its FLOAT, in a CAST and in a column.

    StarRocks' FLOAT holds about 7 digits, so a CAST to the generic ``Float``, or to a float type with a
    precision above 24, keeps every digit as DOUBLE.
    """

    __backend__ = True
    __only_on__ = "starrocks"

    @testing.combinations(
        ("generic_float", sa.Float(), 123456789.0),
        ("generic_float_53", sa.Float(53), 123456789.0),
        ("generic_float_as_decimal", sa.Float(asdecimal=True), decimal.Decimal("123456789.0000000000")),
        ("sqla_float_53", sa.FLOAT(53), 123456789.0),
        ("mysql_float_53", mysql.FLOAT(53), 123456789.0),
        ("float", FLOAT(), 123456790.0),
        ("mysql_float", mysql.FLOAT(), 123456790.0),
        argnames="target_type, expected",
        id_="iaa",
    )
    def test_cast_of_a_wide_value(self, connection, target_type, expected):
        """``FLOAT()`` and ``mysql.FLOAT`` name StarRocks' 4-byte FLOAT, which rounds 123456789."""
        eq_(connection.scalar(sa.select(sa.cast(sa.literal(123456789), target_type))), expected)

    @testing.combinations((sa.REAL(),), (mysql.REAL(),), argnames="real_type")
    def test_cast_to_real_runs(self, connection, real_type):
        eq_(connection.scalar(sa.select(sa.cast(sa.literal("1.5"), real_type))), 1.5)

    def test_real_column_is_created_as_float(self, connection, metadata):
        table = Table("real_column", metadata, Column("id", sa.Integer), Column("r", sa.REAL()), starrocks_duplicate_key="id")
        table.create(connection)
        connection.execute(table.insert().values(id=1, r=0.25))
        eq_(connection.scalar(sa.select(table.c.r)), 0.25)
        reflected = sa.inspect(connection).get_columns("real_column")
        eq_(str(reflected[1]["type"].compile(dialect=connection.dialect)), "FLOAT")


class _Text(sa.TypeDecorator):
    impl = sa.String(50)
    cache_ok = True


def _dialect_of_version(version):
    dialect = StarRocksDialect()
    dialect.server_version_info = version
    return dialect


_FIELD_NAMED_A_DOT_B = STRUCT(("a.b", INTEGER()))


class StructuredCastCompileTest(fixtures.TestBase, AssertsCompiledSQL):
    """A CAST to ARRAY, MAP or STRUCT is left out where StarRocks can't cast the operand, and kept otherwise.

    StarRocks can't cast text to MAP or STRUCT, casts text to ARRAY as no JSON parser would, and casts JSON to MAP
    from 3.4.3 and to STRUCT from 3.5.0, but never to DATE or DECIMAL elements. An operand of no type may be text.
    """

    __dialect__ = StarRocksDialect()

    @testing.combinations(
        ("string_to_map", sa.String(), MAP(VARCHAR(5), INTEGER()), "SELECT t.x AS x FROM t"),
        ("text_to_struct", sa.Text(), STRUCT(a=INTEGER()), "SELECT t.x AS x FROM t"),
        ("decorated_text_to_map", _Text(), MAP(VARCHAR(5), INTEGER()), "SELECT t.x AS x FROM t"),
        ("string_to_array", sa.String(), ARRAY(INTEGER()), "SELECT t.x AS x FROM t"),
        ("string_to_generic_array", sa.String(), sa.ARRAY(sa.Integer()), "SELECT t.x AS x FROM t"),
        ("untyped_to_map", None, MAP(VARCHAR(5), INTEGER()), "SELECT t.x AS x FROM t"),
        ("untyped_to_struct", None, STRUCT(a=INTEGER()), "SELECT t.x AS x FROM t"),
        ("untyped_to_array", None, ARRAY(INTEGER()), "SELECT t.x AS x FROM t"),
        ("map_to_map", MAP(INTEGER(), INTEGER()), MAP(BIGINT(), VARCHAR(10)), "SELECT CAST(t.x AS MAP<BIGINT, VARCHAR(10)>) AS x FROM t"),
        ("struct_to_struct", STRUCT(a=INTEGER()), STRUCT(a=BIGINT()), "SELECT CAST(t.x AS STRUCT<a BIGINT>) AS x FROM t"),
        ("array_to_array", ARRAY(INTEGER()), ARRAY(BIGINT()), "SELECT CAST(t.x AS ARRAY<BIGINT>) AS x FROM t"),
        argnames="operand_type, target_type, expected",
        id_="iaaa",
    )
    def test_cast(self, operand_type, target_type, expected):
        x = sa.column("x", operand_type, _selectable=sa.table("t"))
        self.assert_compile(sa.select(sa.cast(x, target_type)), expected)

    @testing.combinations(
        ("array", ARRAY(INTEGER()), "CAST(NULL AS ARRAY<INTEGER>)"),
        ("map", MAP(VARCHAR(5), INTEGER()), "CAST(NULL AS MAP<VARCHAR(5), INTEGER>)"),
        ("struct", STRUCT(a=INTEGER()), "CAST(NULL AS STRUCT<a INTEGER>)"),
        argnames="target_type, expected",
        id_="iaa",
    )
    def test_cast_of_null_is_kept(self, target_type, expected):
        self.assert_compile(sa.cast(sa.null(), target_type), expected)

    @testing.combinations(
        ("map_offline", None, MAP(VARCHAR(5), INTEGER()), False),
        ("map_on_3_3", (3, 3, 22), MAP(VARCHAR(5), INTEGER()), False),
        ("map_on_3_4_2", (3, 4, 2), MAP(VARCHAR(5), INTEGER()), False),
        ("map_on_3_4_3", (3, 4, 3), MAP(VARCHAR(5), INTEGER()), True),
        ("map_on_4_1", (4, 1, 6), MAP(VARCHAR(5), INTEGER()), True),
        ("struct_offline", None, STRUCT(a=INTEGER()), False),
        ("struct_on_3_4", (3, 4, 10), STRUCT(a=INTEGER()), False),
        ("struct_on_3_5", (3, 5, 0), STRUCT(a=INTEGER()), True),
        ("map_of_structs_on_3_4", (3, 4, 10), MAP(VARCHAR(5), STRUCT(a=INTEGER())), False),
        ("map_of_structs_on_3_5", (3, 5, 0), MAP(VARCHAR(5), STRUCT(a=INTEGER())), True),
        ("array_offline", None, ARRAY(INTEGER()), True),
        ("array_on_3_3", (3, 3, 22), ARRAY(VARCHAR(5)), True),
        ("array_of_structs_on_3_4", (3, 4, 10), ARRAY(STRUCT(a=INTEGER())), False),
        ("array_of_structs_on_3_5", (3, 5, 0), ARRAY(STRUCT(a=INTEGER())), True),
        ("array_of_arrays", (4, 1, 6), ARRAY(ARRAY(INTEGER())), False),
        ("array_of_maps", (4, 1, 6), ARRAY(MAP(VARCHAR(5), INTEGER())), False),
        ("array_of_dates", (4, 1, 6), ARRAY(DATE()), False),
        ("map_of_decimals", (4, 1, 6), MAP(VARCHAR(5), DECIMAL(10, 2)), False),
        ("struct_of_datetimes", (4, 1, 6), STRUCT(a=DATETIME()), False),
        ("struct_of_binary", (4, 1, 6), STRUCT(a=VARBINARY()), False),
        ("struct_field_a_dot_b", (4, 1, 6), _FIELD_NAMED_A_DOT_B, False),
        ("struct_field_bracket", (4, 1, 6), STRUCT(("a[", INTEGER())), False),
        ("struct_field_dollar", (4, 1, 6), STRUCT(("$", INTEGER())), False),
        ("struct_field_with_a_space", (4, 1, 6), STRUCT(("a b", INTEGER())), True),
        ("nested_struct_field_a_dot_b", (4, 1, 6), STRUCT(s=_FIELD_NAMED_A_DOT_B), False),
        argnames="version, target_type, kept",
        id_="iaaa",
    )
    def test_cast_of_json(self, version, target_type, kept):
        """With no version known, offline, only the casts every supported StarRocks has are kept."""
        x = sa.column("x", JSON(), _selectable=sa.table("t"))
        compiled = str(sa.select(sa.cast(x, target_type)).compile(dialect=_dialect_of_version(version)))
        eq_(compiled.startswith("SELECT CAST(t.x AS "), kept, compiled)


class StructuredCastExecutionTest(fixtures.TestBase):
    """A CAST to ARRAY, MAP or STRUCT reads the value on every version, cast by the server where it can cast the
    operand, and read from the operand's text where it can't.

    ``type_coerce`` reads text as a MAP or STRUCT too, without a CAST.
    """

    __backend__ = True
    __only_on__ = "starrocks"

    @testing.combinations(
        ("map", "map{1:2}", MAP(TINYINT(), TINYINT()), MAP(BIGINT(), VARCHAR(10)), "CAST(map{1:2} AS MAP<BIGINT, VARCHAR(10)>)", {1: "2"}),
        ("struct", "named_struct('a', 1)", STRUCT(a=TINYINT()), STRUCT(a=BIGINT()), "CAST(named_struct('a', 1) AS STRUCT<a BIGINT>)", {"a": 1}),
        ("array", "[1, 2]", ARRAY(TINYINT()), ARRAY(VARCHAR(5)), "CAST([1, 2] AS ARRAY<VARCHAR(5)>)", ["1", "2"]),
        argnames="sql, operand_type, target_type, cast_sql, expected",
        id_="iaaaaa",
    )
    def test_cast_of_a_collection_runs(self, connection, sql, operand_type, target_type, cast_sql, expected):
        statement = sa.select(sa.cast(sa.literal_column(sql, operand_type), target_type))
        assert cast_sql in str(statement.compile(connection))
        eq_(connection.scalar(statement), expected)

    @testing.combinations(
        ("map", '{"a":1,"b":null}', MAP(VARCHAR(5), INTEGER()), {"a": 1, "b": None}),
        ("map_of_integer_keys", '{1:"x"}', MAP(INTEGER(), VARCHAR(5)), {1: "x"}),
        ("struct", '{"a":1,"d":"2024-01-02"}', STRUCT(a=INTEGER(), d=DATE()), {"a": 1, "d": datetime.date(2024, 1, 2)}),
        ("array_of_dates", '["2024-01-02"]', ARRAY(DATE()), [datetime.date(2024, 1, 2)]),
        ("array_of_datetimes", '["2024-01-02 03:04:05"]', ARRAY(DATETIME()), [datetime.datetime(2024, 1, 2, 3, 4, 5)]),
        ("array_of_escaped_quotes", '["a\\"b","c,d"]', ARRAY(VARCHAR(10)), ['a"b', "c,d"]),
        ("array_of_decimals", "[1.5, 2]", ARRAY(DECIMAL(10, 2)), [decimal.Decimal("1.5"), decimal.Decimal("2")]),
        ("generic_array", "[1, 2]", sa.ARRAY(sa.Integer()), [1, 2]),
        argnames="text, target_type, expected",
        id_="iaaa",
    )
    def test_cast_of_text_reads_the_parsed_value(self, connection, text, target_type, expected):
        eq_(connection.scalar(sa.select(sa.cast(sa.literal(text), target_type))), expected)

    @testing.combinations(
        ("map", """'{"a":1}'""", MAP(VARCHAR(5), INTEGER()), {"a": 1}),
        ("struct", """'{"a":1}'""", STRUCT(a=INTEGER()), {"a": 1}),
        ("array", """'["2024-01-02"]'""", ARRAY(DATE()), [datetime.date(2024, 1, 2)]),
        argnames="sql, target_type, expected",
        id_="iaaa",
    )
    def test_cast_of_untyped_text_reads_the_parsed_value(self, connection, sql, target_type, expected):
        untyped = sa.select(sa.literal_column(sql).label("s")).subquery()
        eq_(connection.scalar(sa.select(sa.cast(sa.literal_column(sql), target_type))), expected)
        eq_(connection.scalar(sa.select(sa.cast(untyped.c.s, target_type))), expected)

    @testing.combinations(
        ("map", '{"a": 1}', MAP(VARCHAR(5), INTEGER()), {"a": 1}),
        ("map_of_integer_keys", '{"1": 2}', MAP(INTEGER(), INTEGER()), {1: 2}),
        ("map_of_dates", '{"a": "2024-01-02"}', MAP(VARCHAR(5), DATE()), {"a": datetime.date(2024, 1, 2)}),
        ("struct", '{"a": 1}', STRUCT(a=INTEGER()), {"a": 1}),
        ("struct_of_decimals", '{"a": 1.25}', STRUCT(a=DECIMAL(10, 2)), {"a": decimal.Decimal("1.25")}),
        ("struct_field_a_dot_b", '{"a.b": 7}', _FIELD_NAMED_A_DOT_B, {"a.b": 7}),
        ("array", "[1, 2]", ARRAY(INTEGER()), [1, 2]),
        ("array_of_dates", '["2024-01-02"]', ARRAY(DATE()), [datetime.date(2024, 1, 2)]),
        ("array_of_arrays", "[[1], [2]]", ARRAY(ARRAY(INTEGER())), [[1], [2]]),
        ("array_of_structs", '[{"a": 1}]', ARRAY(STRUCT(a=INTEGER())), [{"a": 1}]),
        argnames="document, target_type, expected",
        id_="iaaa",
    )
    def test_cast_of_json_reads_the_value(self, connection, document, target_type, expected):
        json_value = sa.func.parse_json(document, type_=JSON())
        eq_(connection.scalar(sa.select(sa.cast(json_value, target_type))), expected)

    @JSON_TO_MAP_CAST_NEEDS_3_4_3
    def test_cast_of_json_to_a_map_is_a_map_on_the_server(self, connection):
        json_value = sa.func.parse_json('{"a": 1}', type_=JSON())
        keys = sa.func.map_keys(sa.cast(json_value, MAP(VARCHAR(5), INTEGER())), type_=ARRAY(VARCHAR(5)))
        eq_(connection.scalar(sa.select(keys)), ["a"])

    def test_text_is_read_as_a_map_through_type_coerce(self, connection):
        text = sa.literal_column("""'{"a":1}'""")
        eq_(connection.scalar(sa.select(sa.type_coerce(text, MAP(VARCHAR(5), INTEGER())))), {"a": 1})


class GenericArrayTest(fixtures.TablesTest):
    """SQLAlchemy's generic ``ARRAY`` binds and reads as the dialect's ARRAY does."""

    __backend__ = True
    __only_on__ = "starrocks"

    @classmethod
    def define_tables(cls, metadata):
        sa.Table(
            "generic_array",
            metadata,
            sa.Column("id", INTEGER),
            sa.Column("numbers", sa.ARRAY(sa.Integer)),
            sa.Column("words", sa.ARRAY(sa.String(10))),
            sa.Column("number_grid", sa.ARRAY(sa.Integer, dimensions=2)),
            sa.Column("word_grid", sa.ARRAY(sa.String(10), dimensions=2)),
            starrocks_duplicate_key="id",
        )

    def test_round_trip(self, connection):
        table = self.tables.generic_array
        connection.execute(table.insert(), {"id": 1, "numbers": [1, 2, 3], "words": ["x", None]})
        eq_(connection.execute(sa.select(table.c.numbers, table.c.words)).one(), ([1, 2, 3], ["x", None]))

    def test_dimensions_round_trip(self, connection):
        """``dimensions`` declares an array of arrays, which the generic ARRAY cannot nest."""
        table = self.tables.generic_array
        grids = {"number_grid": [[1, 2], [3], None], "word_grid": [["a", "b"], [None]]}
        connection.execute(table.insert(), {"id": 2, **grids})
        eq_(connection.execute(sa.select(table.c.number_grid, table.c.word_grid)).one(), tuple(grids.values()))
