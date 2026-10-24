#! /usr/bin/python3
# Copyright 2021-present StarRocks, Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from datetime import date, datetime, timezone
from decimal import Decimal
import enum
import json
import math
import warnings

from sqlalchemy import Column, Enum, MetaData, Table, TypeDecorator, insert, testing
from sqlalchemy.testing import AssertsCompiledSQL, fixtures

from starrocks import datatype
from starrocks.dialect import StarRocksDialect


_DIALECT = StarRocksDialect()


def _sub_type_reprs(structured_type):
    return {repr(sub_type) for sub_type in structured_type.get_sub_item_types()}


def _col_spec(structured_type):
    return structured_type.get_col_spec()


class StructuredTypeSubItemsTest(fixtures.TestBase):
    def test_array_with_scalar_items(self):
        array_type = datatype.ARRAY(datatype.INTEGER)

        assert _sub_type_reprs(array_type) == {repr(datatype.INTEGER())}

    def test_map_with_struct_and_json(self):
        map_type = datatype.MAP(
            datatype.STRING,
            datatype.ARRAY(
                datatype.STRUCT(
                    ("flag", datatype.BOOLEAN),
                    ("payload", datatype.JSON()),
                )
            ),
        )

        assert _sub_type_reprs(map_type) == {
            repr(datatype.STRING()),
            repr(
                datatype.ARRAY(
                    datatype.STRUCT(
                        ("flag", datatype.BOOLEAN),
                        ("payload", datatype.JSON()),
                    )
                )
            ),
            repr(
                datatype.STRUCT(
                    ("flag", datatype.BOOLEAN),
                    ("payload", datatype.JSON()),
                )
            ),
            repr(datatype.BOOLEAN()),
            repr(datatype.JSON()),
        }

    def test_struct_with_mixed_nested_types(self):
        struct_type = datatype.STRUCT(
            ("id", datatype.BIGINT),
            ("tags", datatype.ARRAY(datatype.STRING)),
            (
                "props",
                datatype.MAP(
                    datatype.VARCHAR(5),
                    datatype.STRUCT(
                        ("k", datatype.CHAR(3)),
                        ("v", datatype.DECIMAL(9, 3)),
                        ("extra", datatype.ARRAY(datatype.JSON())),
                    ),
                ),
            ),
        )

        assert _sub_type_reprs(struct_type) == {
            repr(datatype.BIGINT()),
            repr(datatype.ARRAY(datatype.STRING)),
            repr(datatype.STRING()),
            repr(
                datatype.MAP(
                    datatype.VARCHAR(5),
                    datatype.STRUCT(
                        ("k", datatype.CHAR(3)),
                        ("v", datatype.DECIMAL(9, 3)),
                        ("extra", datatype.ARRAY(datatype.JSON())),
                    ),
                )
            ),
            repr(datatype.VARCHAR(5)),
            repr(
                datatype.STRUCT(
                    ("k", datatype.CHAR(3)),
                    ("v", datatype.DECIMAL(9, 3)),
                    ("extra", datatype.ARRAY(datatype.JSON())),
                )
            ),
            repr(datatype.CHAR(3)),
            repr(datatype.DECIMAL(9, 3)),
            repr(datatype.ARRAY(datatype.JSON())),
            repr(datatype.JSON()),
        }


class StructuredTypeColSpecTest(fixtures.TestBase):
    def test_array_simple_col_spec(self):
        array_type = datatype.ARRAY(datatype.INTEGER)

        assert _col_spec(array_type) == "ARRAY<INTEGER>"

    def test_array_struct_col_spec(self):
        array_type = datatype.ARRAY(
            datatype.STRUCT(
                ("flag", datatype.BOOLEAN),
                ("payload", datatype.JSON()),
            )
        )

        assert _col_spec(array_type) == "ARRAY<STRUCT<flag BOOLEAN, payload JSON>>"

    def test_map_nested_col_spec(self):
        map_type = datatype.MAP(
            datatype.STRING,
            datatype.ARRAY(
                datatype.STRUCT(
                    ("flag", datatype.BOOLEAN),
                    ("payload", datatype.JSON()),
                )
            ),
        )

        assert (
            _col_spec(map_type)
            == "MAP<STRING, ARRAY<STRUCT<flag BOOLEAN, payload JSON>>>"
        )

    def test_struct_mixed_col_spec(self):
        struct_type = datatype.STRUCT(
            ("id", datatype.BIGINT),
            ("tags", datatype.ARRAY(datatype.STRING)),
            (
                "props",
                datatype.MAP(
                    datatype.VARCHAR(5),
                    datatype.STRUCT(
                        ("k", datatype.CHAR(3)),
                        ("v", datatype.DECIMAL(9, 3)),
                        ("extra", datatype.ARRAY(datatype.JSON())),
                    ),
                ),
            ),
        )

        assert (
            _col_spec(struct_type)
            == "STRUCT<id BIGINT, tags ARRAY<STRING>, props MAP<VARCHAR(5), STRUCT<k CHAR(3), v DECIMAL(9, 3), extra ARRAY<JSON>>>>"
        )


def _process(type_obj, value):
    """Call result_processor and apply it to value."""
    proc = type_obj.result_processor(dialect=_DIALECT, coltype=None)
    if proc is None:
        return value
    return proc(value)


class ArrayResultProcessorTest(fixtures.TestBase):
    def test_array_of_ints(self):
        assert _process(datatype.ARRAY(datatype.INTEGER), "[1,2,3]") == [1, 2, 3]

    def test_array_of_strings(self):
        assert _process(datatype.ARRAY(datatype.VARCHAR(50)), '["a","b"]') == ["a", "b"]

    def test_array_null_returns_none(self):
        assert _process(datatype.ARRAY(datatype.INTEGER), None) is None

    def test_array_already_list(self):
        assert _process(datatype.ARRAY(datatype.INTEGER), [4, 5]) == [4, 5]

    def test_nested_array(self):
        assert _process(datatype.ARRAY(datatype.ARRAY(datatype.INTEGER)), "[[1,2],[3]]") == [[1, 2], [3]]

    def test_null_element_preserved(self):
        assert _process(datatype.ARRAY(datatype.INTEGER), "[1,null,3]") == [1, None, 3]

    def test_array_of_dates_applies_element_processor(self):
        result = _process(datatype.ARRAY(datatype.DATE()), '["2024-01-15","2024-06-30"]')
        assert result == [date(2024, 1, 15), date(2024, 6, 30)]

    def test_array_of_datetimes_applies_element_processor(self):
        result = _process(datatype.ARRAY(datatype.DATETIME()), '["2024-01-15 10:30:00"]')
        assert result == [datetime(2024, 1, 15, 10, 30, 0)]

    @testing.combinations(
        ("unknown", None, '["616263"]'),
        ("hex_4_1", (4, 1, 6), '["616263"]'),
        ("hex_4_0", (4, 0, 10), '["616263"]'),
        ("raw_4_1_0", (4, 1, 0), '["abc"]'),
        ("raw_4_0_9", (4, 0, 9), '["abc"]'),
        ("raw_3_5", (3, 5, 21), '["abc"]'),
        id_="iaa",
        argnames="version,text",
    )
    def test_nested_binary_is_read_as_the_server_writes_it(self, version, text):
        dialect = StarRocksDialect()
        dialect.server_version_info = version
        processor = datatype.ARRAY(datatype.VARBINARY()).result_processor(dialect, None)
        assert processor(text) == [b"abc"]

    def test_non_finite_doubles_are_read(self):
        """StarRocks writes a non-finite DOUBLE inside a collection as a bare inf, -inf or nan."""
        values = _process(datatype.ARRAY(datatype.DOUBLE()), "[inf,-inf,nan,1.5]")
        assert values[:2] == [math.inf, -math.inf] and math.isnan(values[2]) and values[3] == 1.5
        assert _process(datatype.MAP(datatype.VARCHAR(5), datatype.DOUBLE()), '{"k":inf}') == {"k": math.inf}

    def test_array_of_decimal_preserves_precision(self):
        result = _process(datatype.ARRAY(datatype.DECIMAL(18, 18)), '[0.123456789012345678]')
        assert isinstance(result[0], Decimal)
        assert result[0] == Decimal('0.123456789012345678')

    def test_array_of_json_decodes_single_quoted_items(self):
        # StarRocks sends ARRAY<JSON> using single-quoted elements (not valid JSON).
        raw = """['{"a":1}','{"b":2}']"""
        assert _process(datatype.ARRAY(datatype.JSON()), raw) == [{"a": 1}, {"b": 2}]

    @testing.combinations(
        ("[1]", [1]),
        ("[]", []),
        ('["x","y","z"]', ["x", "y", "z"]),
        argnames="json_str,expected",
    )
    def test_parametrized_cases(self, json_str, expected):
        item_type = datatype.INTEGER if expected and isinstance(expected[0], int) else datatype.VARCHAR(10)
        assert _process(datatype.ARRAY(item_type), json_str) == expected


class MapResultProcessorTest(fixtures.TestBase):
    def test_simple_map(self):
        assert _process(datatype.MAP(datatype.VARCHAR(10), datatype.INTEGER), '{"a":1,"b":2}') == {"a": 1, "b": 2}

    def test_map_null_returns_none(self):
        assert _process(datatype.MAP(datatype.VARCHAR(10), datatype.INTEGER), None) is None

    def test_map_already_dict(self):
        assert _process(datatype.MAP(datatype.VARCHAR(10), datatype.INTEGER), {"x": 9}) == {"x": 9}

    def test_null_value_in_map_preserved(self):
        result = _process(datatype.MAP(datatype.VARCHAR(10), datatype.INTEGER), '{"a":null}')
        assert result == {"a": None}

    def test_map_with_array_values(self):
        t = datatype.MAP(datatype.VARCHAR(10), datatype.ARRAY(datatype.INTEGER))
        assert _process(t, '{"x":[1,2],"y":[3]}') == {"x": [1, 2], "y": [3]}

    def test_nested_map(self):
        t = datatype.MAP(datatype.VARCHAR(10), datatype.MAP(datatype.VARCHAR(10), datatype.INTEGER))
        assert _process(t, '{"outer":{"inner":42}}') == {"outer": {"inner": 42}}

    def test_map_decimal_value_preserves_precision(self):
        result = _process(
            datatype.MAP(datatype.VARCHAR(10), datatype.DECIMAL(18, 18)),
            '{"k":0.123456789012345678}'
        )
        assert isinstance(result["k"], Decimal)
        assert result["k"] == Decimal('0.123456789012345678')


class StructResultProcessorTest(fixtures.TestBase):
    def test_simple_struct(self):
        t = datatype.STRUCT(name=datatype.VARCHAR(50), age=datatype.INTEGER)
        assert _process(t, '{"name":"Alice","age":30}') == {"name": "Alice", "age": 30}

    def test_struct_null_returns_none(self):
        t = datatype.STRUCT(x=datatype.INTEGER)
        assert _process(t, None) is None

    def test_struct_already_dict(self):
        t = datatype.STRUCT(x=datatype.INTEGER)
        assert _process(t, {"x": 7}) == {"x": 7}

    def test_null_field_preserved(self):
        t = datatype.STRUCT(name=datatype.VARCHAR(50), age=datatype.INTEGER)
        assert _process(t, '{"name":null,"age":30}') == {"name": None, "age": 30}

    def test_struct_with_array_field(self):
        t = datatype.STRUCT(tags=datatype.ARRAY(datatype.VARCHAR(20)), score=datatype.INTEGER)
        result = _process(t, '{"tags":["c++","python"],"score":9}')
        assert result == {"tags": ["c++", "python"], "score": 9}
        assert isinstance(result["tags"], list)

    def test_struct_applies_field_processor_for_dates(self):
        t = datatype.STRUCT(created=datatype.DATE())
        result = _process(t, '{"created":"2024-03-01"}')
        assert result == {"created": date(2024, 3, 1)}

    def test_struct_unknown_field_passed_through(self):
        """Extra keys from DB not in the type definition are passed through unchanged."""
        t = datatype.STRUCT(name=datatype.VARCHAR(50))
        result = _process(t, '{"name":"Bob","extra":"ignored"}')
        assert result["name"] == "Bob"
        assert result["extra"] == "ignored"

    def test_struct_decimal_field_preserves_precision(self):
        result = _process(
            datatype.STRUCT(price=datatype.DECIMAL(18, 18)),
            '{"price":0.123456789012345678}'
        )
        assert isinstance(result["price"], Decimal)
        assert result["price"] == Decimal('0.123456789012345678')


class MapNonStringKeysTest(fixtures.TestBase):
    """MAP keys that are non-string types must be cast from JSON string keys."""

    def test_integer_keys(self):
        result = _process(datatype.MAP(datatype.INTEGER, datatype.VARCHAR(10)), '{"1":"a","2":"b"}')
        assert list(result.keys()) == [1, 2]
        assert all(isinstance(k, int) for k in result.keys())

    def test_bigint_keys(self):
        result = _process(datatype.MAP(datatype.BIGINT, datatype.INTEGER), '{"100":1}')
        assert list(result.keys()) == [100]
        assert isinstance(list(result.keys())[0], int)

    def test_string_keys_unchanged(self):
        result = _process(datatype.MAP(datatype.VARCHAR(10), datatype.INTEGER), '{"a":1}')
        assert list(result.keys()) == ["a"]
        assert isinstance(list(result.keys())[0], str)


def _bind(type_obj, value):
    """Call bind_processor and apply it to value."""
    proc = type_obj.bind_processor(dialect=_DIALECT)
    if proc is None:
        return value
    return proc(value)


def _insert_sql(type_obj, **compile_kwargs):
    table = Table("t", MetaData(), Column("v", type_obj))
    return str(insert(table).compile(dialect=_DIALECT, **compile_kwargs))


class StructuredTypeBindProcessorTest(fixtures.TestBase):
    """bind_processor must encode a value as one JSON document the bind expression can decode exactly.

    Without it, the DBAPI driver falls back to escaping lists/dicts as SQL
    tuples (e.g. ``('a', 'b')``), which is invalid syntax for structured
    literals. Leaves travel as text, so no number or date is quoted into NULL.
    """

    def test_array_encodes_list_as_json(self):
        assert _bind(datatype.ARRAY(datatype.VARCHAR(10)), ["a", "b"]) == '["a", "b"]'

    def test_array_null_returns_none(self):
        assert _bind(datatype.ARRAY(datatype.INTEGER), None) is None

    def test_nested_array_encodes_as_json(self):
        assert _bind(datatype.ARRAY(datatype.ARRAY(datatype.INTEGER)), [[1, 2], [3]]) == '[["1", "2"], ["3"]]'

    def test_map_encodes_keys_and_values_as_two_lists(self):
        assert _bind(datatype.MAP(datatype.VARCHAR(10), datatype.INTEGER), {"a": 1}) == '[["a"], ["1"]]'

    def test_map_null_returns_none(self):
        assert _bind(datatype.MAP(datatype.VARCHAR(10), datatype.INTEGER), None) is None

    def test_struct_encodes_fields_in_declared_order(self):
        t = datatype.STRUCT(name=datatype.VARCHAR(10), age=datatype.INTEGER)
        assert _bind(t, {"age": 30, "name": "Alice"}) == '["Alice", "30"]'

    def test_struct_missing_field_is_null(self):
        t = datatype.STRUCT(name=datatype.VARCHAR(10), age=datatype.INTEGER)
        assert _bind(t, {"name": "Alice"}) == '["Alice", null]'

    def test_struct_field_names_match_case_insensitively(self):
        t = datatype.STRUCT(Name=datatype.VARCHAR(10))
        assert _bind(t, {"name": "Alice"}) == '["Alice"]'

    def test_struct_unknown_field_is_rejected(self):
        t = datatype.STRUCT(name=datatype.VARCHAR(10))
        with testing.expect_raises_message(ValueError, "'nmae' is not a field"):
            _bind(t, {"nmae": "Alice"})

    def test_struct_null_returns_none(self):
        t = datatype.STRUCT(name=datatype.VARCHAR(10))
        assert _bind(t, None) is None

    @testing.combinations(
        (datatype.ARRAY(datatype.DECIMAL(9, 3)), [Decimal("1.500"), Decimal("1E+2"), None], ["1.500", "100", None]),
        (datatype.ARRAY(datatype.DATE()), [date(2024, 2, 29)], ["2024-02-29"]),
        (
            datatype.ARRAY(datatype.DATETIME()),
            [datetime(2024, 2, 29, 10, 11, 12, 123456), datetime(2024, 1, 1)],
            ["2024-02-29 10:11:12.123456", "2024-01-01 00:00:00"],
        ),
        (datatype.ARRAY(datatype.BOOLEAN()), [True, False], ["true", "false"]),
        (datatype.ARRAY(datatype.LARGEINT()), [2**127 - 1], [str(2**127 - 1)]),
        (datatype.ARRAY(datatype.DOUBLE()), [0.1, 1e-07], ["0.1", "1e-07"]),
        (datatype.ARRAY(datatype.VARBINARY()), [b"\x00\xff"], ["00ff"]),
        (datatype.ARRAY(datatype.JSON()), [{"a": [1, None]}, 1, "1"], [{"a": [1, None]}, 1, "1"]),
        (datatype.ARRAY(datatype.VARCHAR(20)), ["b'c", 'q"r', "s\\t", "\u00e9\u4e2d"], ["b'c", 'q"r', "s\\t", "\u00e9\u4e2d"]),
        argnames="type_obj,value,encoded",
    )
    def test_leaves_are_encoded_by_declared_type(self, type_obj, value, encoded):
        assert json.loads(_bind(type_obj, value)) == encoded

    def test_aware_datetime_is_rejected(self):
        with testing.expect_raises_message(ValueError, "has no time zone"):
            _bind(datatype.ARRAY(datatype.DATETIME()), [datetime(2024, 1, 1, tzinfo=timezone.utc)])

    @testing.combinations(
        (datatype.ARRAY(datatype.DOUBLE()), [float("nan")]),
        (datatype.ARRAY(datatype.DECIMAL(9, 3)), [Decimal("Infinity")]),
        argnames="type_obj,value",
    )
    def test_non_finite_number_is_rejected(self, type_obj, value):
        with testing.expect_raises_message(ValueError, "cannot store"):
            _bind(type_obj, value)

    @testing.combinations(
        (datatype.ARRAY(datatype.INTEGER()), {"a": 1}),
        (datatype.MAP(datatype.INTEGER(), datatype.INTEGER()), [1, 2]),
        (datatype.STRUCT(a=datatype.INTEGER()), 5),
        argnames="type_obj,value",
    )
    def test_value_of_the_wrong_shape_is_rejected(self, type_obj, value):
        with testing.expect_raises_message(ValueError, "expected"):
            _bind(type_obj, value)


class _Reversed(TypeDecorator):
    impl = datatype.VARCHAR(10)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return value[::-1]


class _Cents(TypeDecorator):
    impl = datatype.INTEGER()
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return round(value * 100)


class _Color(enum.Enum):
    RED = 1
    GREEN = 2


class _Float(float):
    """A float subclass whose repr is not a float literal, as numpy's float64 is under numpy 2."""

    def __repr__(self):
        return f"_Float({float(self)!r})"


class StructuredLeafBindProcessingTest(fixtures.TestBase):
    """An element goes through its type's own bind processing, as a plain column value does."""

    @testing.combinations(
        ("whole_float", datatype.ARRAY(datatype.INTEGER()), [2.0], ["2"]),
        ("whole_decimal", datatype.ARRAY(datatype.BIGINT()), [Decimal("2.0"), Decimal("2E+1")], ["2", "20"]),
        ("float_subclass", datatype.ARRAY(datatype.DOUBLE()), [_Float(0.5)], ["0.5"]),
        ("type_decorator", datatype.ARRAY(_Reversed()), ["abc"], ["cba"]),
        ("type_decorator_number", datatype.STRUCT(m=_Cents()), {"m": 5.5}, ["550"]),
        ("enum", datatype.ARRAY(Enum(_Color)), [_Color.GREEN], ["GREEN"]),
        id_="iaaa",
        argnames="type_obj,value,encoded",
    )
    def test_encoded(self, type_obj, value, encoded):
        assert json.loads(_bind(type_obj, value)) == encoded

    def test_a_fraction_in_an_integer_element_is_refused(self):
        with testing.expect_raises_message(ValueError, "cannot store 1.7 in an integer element exactly"):
            _bind(datatype.ARRAY(datatype.INTEGER()), [1.7])

    @testing.combinations(("false",), ("0",), (2,), argnames="value")
    def test_a_boolean_element_is_as_strict_as_a_boolean_column(self, value):
        with testing.expect_raises((TypeError, ValueError)):
            _bind(datatype.ARRAY(datatype.BOOLEAN()), [value])


class StructuredTypeBindExpressionTest(fixtures.TestBase, AssertsCompiledSQL):
    """The bind parameter must become the declared type through casts the server evaluates correctly.

    The server's text-to-ARRAY cast is not a JSON parser, and its JSON casts to ARRAY<DECIMAL/DATE/DATETIME>,
    MAP and STRUCT are refused or depend on the version, so every element is cast on its own from the parsed
    document.
    """

    __dialect__ = _DIALECT

    def test_array_casts_each_element(self):
        assert _insert_sql(datatype.ARRAY(datatype.VARCHAR(50))) == (
            "INSERT INTO t (v) VALUES (array_map(e1 -> CAST(e1 AS VARCHAR), CAST(parse_json(%s) AS ARRAY<JSON>)))"
        )

    def test_array_of_decimal_casts_through_text(self):
        assert _insert_sql(datatype.ARRAY(datatype.DECIMAL(10, 2))) == (
            "INSERT INTO t (v) VALUES (array_map(e1 -> CAST(CAST(e1 AS VARCHAR) AS DECIMAL(10, 2)), "
            "CAST(parse_json(%s) AS ARRAY<JSON>)))"
        )

    def test_array_of_json_keeps_the_parsed_elements(self):
        assert _insert_sql(datatype.ARRAY(datatype.JSON())) == (
            "INSERT INTO t (v) VALUES (CAST(parse_json(%s) AS ARRAY<JSON>))"
        )

    def test_array_of_binary_decodes_hex(self):
        assert _insert_sql(datatype.ARRAY(datatype.VARBINARY())) == (
            "INSERT INTO t (v) VALUES (array_map(e1 -> to_binary(CAST(e1 AS VARCHAR), 'hex'), "
            "CAST(parse_json(%s) AS ARRAY<JSON>)))"
        )

    def test_map_builds_from_key_and_value_arrays(self):
        assert _insert_sql(datatype.MAP(datatype.INTEGER(), datatype.DECIMAL(10, 2))) == (
            "INSERT INTO t (v) VALUES (element_at(array_map(e1 -> map_from_arrays("
            "array_map(e3 -> CAST(CAST(e3 AS VARCHAR) AS INTEGER), CAST(CAST(e1 AS ARRAY<JSON>)[1] AS ARRAY<JSON>)), "
            "array_map(e4 -> CAST(CAST(e4 AS VARCHAR) AS DECIMAL(10, 2)), CAST(CAST(e1 AS ARRAY<JSON>)[2] AS ARRAY<JSON>))), "
            "array_filter(e2 -> e2 IS NOT NULL, [parse_json(%s)])), 1))"
        )

    def test_struct_builds_with_named_struct_and_null_stays_null(self):
        assert _insert_sql(datatype.STRUCT(name=datatype.VARCHAR(10), age=datatype.INTEGER())) == (
            "INSERT INTO t (v) VALUES (element_at(array_map(e1 -> named_struct("
            "'name', CAST(CAST(e1 AS ARRAY<JSON>)[1] AS VARCHAR), "
            "'age', CAST(CAST(CAST(e1 AS ARRAY<JSON>)[2] AS VARCHAR) AS INTEGER)), "
            "array_filter(e2 -> e2 IS NOT NULL, [parse_json(%s)])), 1))"
        )

    def test_struct_field_name_is_an_escaped_literal(self):
        sql = _insert_sql(datatype.STRUCT(**{"it's": datatype.INTEGER()}))
        assert "named_struct('it''s', " in sql

    def test_array_of_struct_nests_lambdas_without_reusing_variables(self):
        sql = _insert_sql(datatype.ARRAY(datatype.STRUCT(a=datatype.INTEGER(), b=datatype.ARRAY(datatype.DATE()))))
        assert sql == (
            "INSERT INTO t (v) VALUES (array_map(e1 -> element_at(array_map(e2 -> named_struct("
            "'a', CAST(CAST(CAST(e2 AS ARRAY<JSON>)[1] AS VARCHAR) AS INTEGER), "
            "'b', array_map(e4 -> CAST(CAST(e4 AS VARCHAR) AS DATE), CAST(CAST(e2 AS ARRAY<JSON>)[2] AS ARRAY<JSON>))), "
            "array_filter(e3 -> e3 IS NOT NULL, [e1])), 1), CAST(parse_json(%s) AS ARRAY<JSON>)))"
        )

    @testing.combinations(
        (datatype.ARRAY(datatype.VARCHAR(5)), ["it's", "100%", "back\\slash"]),
        (datatype.MAP(datatype.VARCHAR(5), datatype.INTEGER()), {"it's": 1}),
        (datatype.STRUCT(a=datatype.VARCHAR(5)), {"a": "it's"}),
        argnames="type_obj,value",
    )
    def test_literal_binds_renders_the_escaped_document(self, type_obj, value):
        table = Table("t", MetaData(), Column("v", type_obj))
        sql = str(insert(table).values(v=value).compile(dialect=_DIALECT, compile_kwargs={"literal_binds": True}))
        document = json.dumps(datatype.encode_structured_value(type_obj, value, _DIALECT))
        escaped = document.replace("\\", "\\\\").replace("'", "''").replace("%", "%%")
        assert "parse_json('%s')" % escaped in sql
        assert "%s" not in sql.replace("%%", "")

    def test_literal_binds_renders_null(self):
        table = Table("t", MetaData(), Column("v", datatype.ARRAY(datatype.INTEGER())))
        sql = str(insert(table).values(v=None).compile(dialect=_DIALECT, compile_kwargs={"literal_binds": True}))
        assert "CAST(parse_json(NULL) AS ARRAY<JSON>)" in sql

    def test_no_cast_is_skipped_warning(self):
        """Regression guard: casting a structured type must not warn/skip."""
        table = Table("t", MetaData(), Column("fruits", datatype.ARRAY(datatype.INTEGER)))

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            str(insert(table).compile(dialect=_DIALECT))

        assert not any("does not support CAST" in str(w.message) for w in caught)
