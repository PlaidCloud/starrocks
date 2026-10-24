#! /usr/bin/python3
# Copyright 2021-present StarRocks, Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https:#www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from datetime import date, datetime, timedelta
from decimal import Decimal
from inspect import isclass
import json
import math
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple, Type, Union

from sqlalchemy import func, literal_column, util
import sqlalchemy.dialects.mysql.types as mysql_types
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql import sqltypes
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.visitors import InternalTraversal
from sqlalchemy.types import UserDefinedType


class BOOLEAN(sqltypes.BOOLEAN):
    __visit_name__ = "BOOLEAN"


class TINYINT(mysql_types.TINYINT):
    __visit_name__ = "TINYINT"


class SMALLINT(mysql_types.SMALLINT):
    __visit_name__ = "SMALLINT"


class INTEGER(mysql_types.INTEGER):
    __visit_name__ = "INTEGER"


class BIGINT(mysql_types.BIGINT):
    __visit_name__ = "BIGINT"


class LARGEINT(sqltypes.Integer):
    __visit_name__ = "LARGEINT"

    def result_processor(self, dialect: Dialect, coltype: object):
        # The server sends LARGEINT as text.
        def process(value):
            if value is None or isinstance(value, int):
                return value
            if isinstance(value, (bytes, bytearray)):
                value = value.decode("ascii")
            return int(value)

        return process


class DECIMAL(mysql_types.DECIMAL):
    __visit_name__ = "DECIMAL"


class DOUBLE(mysql_types.DOUBLE):
    """DOUBLE returning ``float``; MySQL's default ``asdecimal=True`` would round every value to ten places."""

    __visit_name__ = "DOUBLE"

    def __init__(
        self,
        precision: Optional[int] = None,
        scale: Optional[int] = None,
        asdecimal: bool = False,
        **kw: Any,
    ) -> None:
        super().__init__(precision=precision, scale=scale, asdecimal=asdecimal, **kw)


class FLOAT(mysql_types.FLOAT):
    __visit_name__ = "FLOAT"


class CHAR(mysql_types.CHAR):
    __visit_name__ = "CHAR"


class VARCHAR(mysql_types.VARCHAR):
    __visit_name__ = "VARCHAR"


class STRING(mysql_types.TEXT):
    __visit_name__ = "STRING"


class BINARY(sqltypes.BINARY):
    __visit_name__ = "BINARY"


class VARBINARY(sqltypes.VARBINARY):
    __visit_name__ = "VARBINARY"


class DATETIME(mysql_types.DATETIME):
    __visit_name__ = "DATETIME"

    _reg = re.compile(r"(\d+)-(\d+)-(\d+)[ T](\d+):(\d+):(\d+)(?:\.(\d{1,6}))?$")

    def bind_processor(self, dialect: Dialect):
        # DATETIME has no time zone and the driver drops a bound value's UTC
        # offset, so reject an aware value instead of storing it as wall time.
        def process(value):
            if isinstance(value, datetime) and value.utcoffset() is not None:
                raise ValueError(
                    "StarRocks DATETIME has no time zone; convert %r to a naive "
                    "datetime in the intended zone" % (value,)
                )
            return value

        return process

    def literal_processor(self, dialect: Dialect) -> Callable[[datetime], str]:
        """Render ``'YYYY-MM-DD HH:MM:SS[.ffffff]'``; SQLAlchemy 1.4 has no DATETIME literal renderer of its own."""
        reject_aware = self.bind_processor(dialect)

        def process(value: datetime) -> str:
            return "'%s'" % reject_aware(value).isoformat(sep=" ")

        return process

    def result_processor(self, dialect: Dialect, coltype: object):
        def process(value):
            if isinstance(value, (bytes, bytearray)):
                value = value.decode("ascii")
            if isinstance(value, str):
                m = self._reg.match(value.strip())
                if not m:
                    raise ValueError(
                        "could not parse %r as a datetime value" % (value,)
                    )
                *parts, fraction = m.groups()
                # ".5" is 500000 microseconds, not 5.
                return datetime(*(int(x) for x in parts), int((fraction or "").ljust(6, "0")))
            else:
                return value

        return process

class TIME(mysql_types.TIME):
    """StarRocks ``TIME``, a signed duration rather than a time of day.

    StarRocks holds a TIME as a signed number of seconds and renders it as
    ``[-]HH:MM:SS`` with no day part, so the hour field is unbounded and may be
    negative: ``timediff('1000-01-02 01:01:01', '1000-01-01 01:01:01')`` is
    ``24:00:00``, and reversing the arguments gives ``-24:00:00``.

    Values are therefore returned as :class:`datetime.timedelta`, which spans
    that whole range. :class:`sqlalchemy.dialects.mysql.TIME` instead maps the
    driver's ``timedelta`` onto :class:`datetime.time` and drops
    ``timedelta.days`` in the process, turning ``24:00:00`` into ``00:00:00``
    and ``-00:00:01`` into ``23:59:59``. Past 999 hours the value never reaches
    that processor at all -- PyMySQL's parser allows at most three hour digits
    and hands back the undecoded string, which then raises ``AttributeError``.
    """

    __visit_name__ = "TIME"

    _reg = re.compile(r"(-)?(\d+):(\d{1,2}):(\d{1,2})(?:\.(\d{1,6}))?$")

    @property
    def python_type(self) -> Type[timedelta]:
        return timedelta

    def result_processor(self, dialect: Dialect, coltype: object):
        def process(value: Any) -> Optional[timedelta]:
            if value is None or isinstance(value, timedelta):
                return value
            if isinstance(value, (bytes, bytearray)):
                value = value.decode("ascii")
            m = self._reg.match(value)
            if not m:
                raise ValueError("could not parse %r as a time value" % (value,))
            sign, hours, minutes, seconds, fraction = m.groups()
            elapsed = timedelta(
                hours=int(hours),
                minutes=int(minutes),
                seconds=int(seconds),
                microseconds=int((fraction or "").ljust(6, "0")),
            )
            return -elapsed if sign else elapsed

        return process


class DATE(sqltypes.DATE):
    __visit_name__ = "DATE"

    def literal_processor(self, dialect: Dialect) -> Callable[[date], str]:
        def process(value: date) -> str:
            return f"TO_DATE('{value}')"

        return process

    _reg = re.compile(r"(\d+)-(\d+)-(\d+)")

    def result_processor(self, dialect: Dialect, coltype: object):
        def process(value):
            if isinstance(value, str):
                m = self._reg.match(value)
                if not m:
                    raise ValueError("could not parse %r as a date value" % (value,))
                return date(*[int(x or 0) for x in m.groups()])
            else:
                return value

        return process


class HLL(sqltypes.Numeric):
    __visit_name__ = "HLL"


class BITMAP(sqltypes.Numeric):
    __visit_name__ = "BITMAP"


class PERCENTILE(sqltypes.Numeric):
    __visit_name__ = "PERCENTILE"


class StructuredType(UserDefinedType):
    @staticmethod
    def _check_subtype(subtype_obj: Union[sqltypes.TypeEngine, type]) -> sqltypes.TypeEngine:
        """
        Check if the subtype is a valid structured type.
        return: an instance of a type, rather than a type itself
        """
        if isclass(subtype_obj):
            if issubclass(subtype_obj, StructuredType):
                raise TypeError(f"'{subtype_obj.__name__}' should be an instance of StructuredType, not a class")
            return subtype_obj()
        return subtype_obj

    def get_col_spec(self, **kw) -> str:
        return "InvalidStructuredType<>"

    def get_sub_type_col_spec(self, sub_type, **kw) -> str:
        if hasattr(sub_type, 'get_col_spec'):
            return sub_type.get_col_spec(**kw)
        else:
            return str(sub_type)

    def get_sub_item_types(self) -> Set[sqltypes.TypeEngine]:
        """
        Get all the sub item types of this structured type recursively.
        Which is need for sqlacodegen to import correct types.
        """
        raise NotImplementedError("get_sub_item_types is not implemented for this pure Structuredtype")

    def result_processor(self, dialect: Dialect, coltype: object):
        def process(value):
            if value is None:
                return None
            if isinstance(value, (str, bytes, bytearray)):
                value = parse_complex_value(value)
            elif isinstance(value, dict):
                value = list(value.items())
            return _convert_structured(self, value, dialect)

        return process

    def bind_processor(self, dialect: Dialect) -> Callable[[Optional[Any]], Optional[str]]:
        """
        Encode a Python value (list, dict, ...) as one JSON document for binding.

        Every leaf is encoded by its declared element type, as text for everything
        but JSON elements, so the server-side expression built by ``bind_expression``
        casts it back losslessly; see ``StructuredBindExpression``.
        """
        def process(value: Optional[Any]) -> Optional[str]:
            if value is None:
                return None
            return json.dumps(encode_structured_value(self, value, dialect))

        return process

    def literal_processor(self, dialect: Dialect) -> Callable[[Optional[Any]], str]:
        """Render the JSON document ``bind_processor`` builds as an escaped SQL string literal."""
        encode = self.bind_processor(dialect)
        quote = sqltypes.String().literal_processor(dialect)

        def process(value: Optional[Any]) -> str:
            return quote(encode(value))

        return process

    def bind_expression(self, bindvalue: ColumnElement) -> ColumnElement:
        """Turn the bound JSON document into a value of this type, evaluated by the server."""
        return StructuredBindExpression(bindvalue, self)


class ARRAY(StructuredType):
    """
    Usage:
        ARRAY(item_type)

    Examples:
        ARRAY(INTEGER)
        ARRAY(ARRAY(STRING))
        ARRAY(STRUCT(name=STRING, address=MAP(STRING, ARRAY(STRING))))
    """

    __visit_name__ = "ARRAY"
    cache_ok = True

    def __init__(self, item_type: sqltypes.TypeEngine, **kwargs):
        self.item_type = self._check_subtype(item_type)
        super().__init__(**kwargs)

    @property
    def python_type(self) -> Optional[Type[List[Any]]]:
        return list

    def __repr__(self):
        return f"ARRAY({repr(self.item_type)})"

    def get_col_spec(self, **kw) -> str:
        inner_type_sql = self.get_sub_type_col_spec(self.item_type)
        return f"ARRAY<{inner_type_sql}>"

    def get_sub_item_types(self) -> Set[sqltypes.TypeEngine]:
        types = {self.item_type}
        if hasattr(self.item_type, 'get_sub_item_types'):
            types.update(self.item_type.get_sub_item_types())
        return types


class MAP(StructuredType):
    """
    Usage:
        MAP(key_type, value_type)

    Examples:
        MAP(INTEGER, STRING)
        MAP(STRING, MAP(INTEGER, STRING))
        MAP(STRING, STRUCT(name=STRING, age=ARRAY(INTEGER)))
    """

    __visit_name__ = "MAP"
    cache_ok = True

    def __init__(self, key_type: sqltypes.TypeEngine, value_type: sqltypes.TypeEngine, **kwargs: Any):
        self.key_type = self._check_subtype(key_type)
        self.value_type = self._check_subtype(value_type)
        super().__init__()

    @property
    def python_type(self) -> Optional[Type[Dict[Any, Any]]]:
        return dict

    def __repr__(self):
        return f"MAP({repr(self.key_type)}, {repr(self.value_type)})"

    def get_col_spec(self, **kw) -> str:
        key_type_sql = self.get_sub_type_col_spec(self.key_type, **kw)
        value_type_sql = self.get_sub_type_col_spec(self.value_type, **kw)
        return f"MAP<{key_type_sql}, {value_type_sql}>"

    def get_sub_item_types(self) -> Set[sqltypes.TypeEngine]:
        types = set[sqltypes.TypeEngine]({self.key_type, self.value_type})
        if hasattr(self.key_type, 'get_sub_item_types'):
            types.update(self.key_type.get_sub_item_types())
        if hasattr(self.value_type, 'get_sub_item_types'):
            types.update(self.value_type.get_sub_item_types())
        return types


_PLAIN_FIELD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def quote_struct_field_name(name: str) -> str:
    """Backquote a STRUCT field name unless it is a plain identifier."""
    if _PLAIN_FIELD_NAME.fullmatch(name):
        return name
    return "`" + name.replace("`", "``") + "`"


class STRUCT(StructuredType):
    """
    Usage:
        STRUCT((name, type), (name, type), ..., name=type, ...)

    Examples:
        STRUCT((name, STRING), (age, INTEGER))
        STRUCT(name=STRING, info=STRUCT(age=INTEGER, city=STRING))
        STRUCT(name=STRING, address=MAP(STRING, ARRAY(STRING)))
"""

    __visit_name__ = "STRUCT"
    cache_ok = True

    def __init__(self, *fields: Tuple[STRING, sqltypes.TypeEngine], **kwfields: sqltypes.TypeEngine):
        self.field_tuples = tuple(
            (name, self._check_subtype(type_))
            for (name, type_) in (fields + tuple(kwfields.items()))
        )
        self._field_dict = {
            name.lower(): type_ for (name, type_) in self.field_tuples
        }
        super().__init__()

    @util.memoized_property
    def _static_cache_key(self) -> Tuple[Any, ...]:
        """Key on the field names and types; the fields are positional, so the default key would ignore them."""
        return (
            self.__class__,
            tuple((name, type_._static_cache_key) for name, type_ in self.field_tuples),
        )

    @property
    def python_type(self) -> Optional[Type[Any]]:
        return None

    def __repr__(self):
        fields = ", ".join(
            f"{name}={repr(type_)}" for name, type_ in self.field_tuples
        )
        return f"STRUCT({fields})"

    def adapt(self, cls, **kw):
        # The default constructor copy cannot see the fields (they are passed
        # positionally), so a copied or dialect-adapted STRUCT had none.
        if isclass(cls) and issubclass(cls, STRUCT):
            return cls(*self.field_tuples)
        return super().adapt(cls, **kw)

    def get_col_spec(self, **kw) -> str:
        fields_sql = []
        for name, type_ in self.field_tuples:
            type_sql = self.get_sub_type_col_spec(type_, **kw)
            fields_sql.append(f"{quote_struct_field_name(name)} {type_sql}")
        return f"STRUCT<{', '.join(fields_sql)}>"

    def get_sub_item_types(self) -> Set[sqltypes.TypeEngine]:
        types = set[sqltypes.TypeEngine]()
        for _, type_ in self.field_tuples:
            types.add(type_)
            if hasattr(type_, 'get_sub_item_types'):
                types.update(type_.get_sub_item_types())
        return types


def _structured_leaf_kind(type_: sqltypes.TypeEngine, dialect: Dialect) -> str:
    """Classify a non-structured element type by how its values travel inside the bound JSON document."""
    impl = type_.dialect_impl(dialect)
    if isinstance(impl, sqltypes.JSON):
        return "json"
    if isinstance(impl, sqltypes.Boolean):
        return "boolean"
    if isinstance(impl, (sqltypes.Integer, sqltypes.Numeric)):
        return "number"
    if isinstance(impl, sqltypes.DateTime):
        return "datetime"
    if isinstance(impl, sqltypes.Date):
        return "date"
    if isinstance(impl, sqltypes._Binary):
        return "binary"
    if isinstance(impl, sqltypes.String):
        return "string"
    return "other"


def _encode_number(value: Any) -> str:
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("cannot store %r in a numeric element" % (value,))
        return format(value, "f")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("cannot store %r in a numeric element" % (value,))
        return repr(value)
    return str(value)


def _encode_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        if value.utcoffset() is not None:
            raise ValueError(
                "StarRocks DATETIME has no time zone; convert %r to a naive "
                "datetime in the intended zone" % (value,)
            )
        return value.isoformat(sep=" ")
    return value.isoformat() if isinstance(value, date) else str(value)


def _encode_structured_leaf(kind: str, value: Any) -> Any:
    if kind == "json":
        return value
    if kind == "boolean":
        return "true" if value else "false"
    if kind == "number":
        return _encode_number(value)
    if kind in ("datetime", "date"):
        return _encode_datetime(value)
    if kind == "binary":
        return bytes(value).hex()
    return value if isinstance(value, str) else str(value)


def _struct_values_in_field_order(type_: "STRUCT", value: Any) -> List[Any]:
    """Order a STRUCT value by the declared fields; a missing field is NULL and an unknown one is an error."""
    names = [name for name, _ in type_.field_tuples]
    if isinstance(value, (list, tuple)):
        if len(value) != len(names):
            raise ValueError("expected %d STRUCT fields %s, got %r" % (len(names), names, value))
        return list(value)
    if not isinstance(value, Mapping):
        raise ValueError("expected a STRUCT value as a dict, got %r" % (value,))
    by_lower_name = {name.lower(): name for name in names}
    values: Dict[str, Any] = {}
    for key, item in value.items():
        name = key if key in names else by_lower_name.get(str(key).lower())
        if name is None or name in values:
            raise ValueError("%r is not a field of %r, which has %s" % (key, type_, names))
        values[name] = item
    return [values.get(name) for name in names]


def encode_structured_value(type_: sqltypes.TypeEngine, value: Any, dialect: Dialect) -> Any:
    """Encode a Python value as the JSON-ready form the bind expression of ``type_`` decodes.

    An ARRAY is a list of its encoded items, a MAP is ``[keys, values]``, and a STRUCT is the list of its
    fields in declared order, so no name or key is ever looked up in the document. A leaf is a string
    (a number as exact text) unless it is JSON, which is nested as is. ``None`` stays JSON null.
    """
    type_ = StructuredType._check_subtype(type_)
    if value is None:
        return None
    if isinstance(type_, ARRAY):
        if not isinstance(value, (list, tuple)):
            raise ValueError("expected an ARRAY value as a list, got %r" % (value,))
        return [encode_structured_value(type_.item_type, item, dialect) for item in value]
    if isinstance(type_, MAP):
        if not isinstance(value, Mapping):
            raise ValueError("expected a MAP value as a dict, got %r" % (value,))
        return [
            [encode_structured_value(type_.key_type, key, dialect) for key in value],
            [encode_structured_value(type_.value_type, item, dialect) for item in value.values()],
        ]
    if isinstance(type_, STRUCT):
        values = _struct_values_in_field_order(type_, value)
        return [
            encode_structured_value(field_type, item, dialect)
            for (_, field_type), item in zip(type_.field_tuples, values)
        ]
    return _encode_structured_leaf(_structured_leaf_kind(type_, dialect), value)


class StructuredBindExpression(ColumnElement):
    """SQL that builds an ARRAY, MAP or STRUCT value from the one JSON document a bind parameter carries.

    The server's own text-to-ARRAY cast is not a JSON parser and its JSON-to-DECIMAL/DATE/MAP/STRUCT casts
    are missing or version-dependent, so each element is taken out of the parsed document as JSON and cast
    on its own: ``CAST(CAST(element AS VARCHAR) AS type)``, nested through ``array_map``, ``map_from_arrays``
    and ``named_struct``. Only ``CAST(.. AS ARRAY<JSON>)``, ``parse_json`` and these functions are needed,
    which every supported StarRocks version provides. The expression is rendered at compile time because it
    depends on the declared element types.
    """

    inherit_cache = True
    _traverse_internals = [
        ("bindvalue", InternalTraversal.dp_clauseelement),
        ("type", InternalTraversal.dp_type),
    ]

    def __init__(self, bindvalue: ColumnElement, type_: StructuredType) -> None:
        self.bindvalue = bindvalue
        self.type = type_


class _StructuredExpressionRenderer:
    """Render the SQL that ``StructuredBindExpression`` compiles to; lambda variables are numbered to stay unique."""

    def __init__(self, compiler: Any) -> None:
        self.dialect = compiler.dialect
        self.type_compiler = getattr(self.dialect, "type_compiler_instance", None) or self.dialect.type_compiler
        self.quote_string = sqltypes.String().literal_processor(self.dialect)
        self.variable_count = 0

    def render(self, type_: StructuredType, bound_sql: str) -> str:
        return self.convert(type_, f"parse_json({bound_sql})")

    def new_variable(self) -> str:
        self.variable_count += 1
        return f"e{self.variable_count}"

    def convert(self, type_: sqltypes.TypeEngine, json_sql: str) -> str:
        type_ = StructuredType._check_subtype(type_)
        if isinstance(type_, ARRAY):
            return self.convert_array(type_.item_type, f"CAST({json_sql} AS ARRAY<JSON>)")
        if isinstance(type_, MAP):
            return self.build_unless_null(json_sql, lambda name: self.convert_map(type_, name))
        if isinstance(type_, STRUCT):
            return self.build_unless_null(json_sql, lambda name: self.convert_struct(type_, name))
        return self.convert_leaf(type_, json_sql)

    def build_unless_null(self, json_sql: str, build: Callable[[str], str]) -> str:
        """Build a MAP or STRUCT from ``json_sql`` under a lambda variable, or NULL when it is NULL.

        ``named_struct`` of NULL fields is a struct of NULLs, not NULL; ``IF(.., NULL, named_struct(..))`` is
        refused before 3.5; and a NULL ``map_from_arrays`` result crashes the backend of 3.3 and 3.4 when it
        is a constant in ``INSERT .. VALUES``. So the single-element array holding the document is filtered
        down to nothing for a NULL document, and ``element_at`` of the empty array is a typed NULL.
        """
        name = self.new_variable()
        present = self.new_variable()
        present_only = f"array_filter({present} -> {present} IS NOT NULL, [{json_sql}])"
        return f"element_at(array_map({name} -> {build(name)}, {present_only}), 1)"

    def convert_array(self, item_type: sqltypes.TypeEngine, items_sql: str) -> str:
        item_type = StructuredType._check_subtype(item_type)
        if _is_json_leaf(item_type, self.dialect):
            return items_sql
        name = self.new_variable()
        return f"array_map({name} -> {self.convert(item_type, name)}, {items_sql})"

    def convert_map(self, type_: MAP, name: str) -> str:
        pair = f"CAST({name} AS ARRAY<JSON>)"
        keys = self.convert_array(type_.key_type, f"CAST({pair}[1] AS ARRAY<JSON>)")
        values = self.convert_array(type_.value_type, f"CAST({pair}[2] AS ARRAY<JSON>)")
        return f"map_from_arrays({keys}, {values})"

    def convert_struct(self, type_: STRUCT, name: str) -> str:
        fields = ", ".join(
            f"{self.quote_string(field_name)}, {self.convert(field_type, f'CAST({name} AS ARRAY<JSON>)[{position}]')}"
            for position, (field_name, field_type) in enumerate(type_.field_tuples, start=1)
        )
        return f"named_struct({fields})"

    def convert_leaf(self, type_: sqltypes.TypeEngine, json_sql: str) -> str:
        kind = _structured_leaf_kind(type_, self.dialect)
        if kind == "json":
            return json_sql
        text = f"CAST({json_sql} AS VARCHAR)"
        if kind == "string":
            return text
        if kind == "binary":
            return f"to_binary({text}, 'hex')"
        return f"CAST({text} AS {self.type_compiler.process(type_)})"


def _is_json_leaf(type_: sqltypes.TypeEngine, dialect: Dialect) -> bool:
    return not isinstance(type_, StructuredType) and _structured_leaf_kind(type_, dialect) == "json"


@compiles(StructuredBindExpression)
def compile_structured_bind_expression(element: StructuredBindExpression, compiler: Any, **kw: Any) -> str:
    bound_sql = compiler.process(element.bindvalue, **kw)
    return _StructuredExpressionRenderer(compiler).render(element.type, bound_sql)


class JSON(sqltypes.JSON):
    """JSON column type that round-trips scalars as JSON scalars, not JSON strings.

    The server's ``parse_json('1')`` and ``CAST('1' AS JSON)`` yield the JSON *string* ``"1"``, so a bare scalar
    bound as text would read back as ``'1'``. The bound text is therefore wrapped in a one-element array and
    the element is taken out again, ``json_query(parse_json(concat('[', :value, ']')), '$[0]')``, which keeps
    the JSON type of the value. A SQL NULL bind stays SQL NULL, and a JSON null (``'null'``) stays a JSON null.
    """

    __visit_name__ = "JSON"

    def bind_expression(self, bindvalue: Any) -> Any:
        wrapped = func.concat(literal_column("'['"), bindvalue, literal_column("']'"))
        return func.json_query(func.parse_json(wrapped), literal_column("'$[0]'"))


class VARIANT(sqltypes.TypeEngine):
    """Semi-structured type, available from StarRocks 4.1.

    Supported on Iceberg catalog tables (Iceberg format-version 3), where it
    maps to Iceberg's ``variant`` type.

    A bound value is serialised to JSON text and parsed back into a VARIANT
    server-side by ``PARSE_JSON``. That round trip is the reason this does not
    subclass :class:`sqlalchemy.types.JSON`: JSON suppresses ``bind_expression``
    on INSERT, so the text would reach the column as a ``VARCHAR`` bind and
    StarRocks would store the whole document as a VARIANT *string* — indexing
    into it then yields NULL and reading it back returns JSON text rather than
    the original structure.
    """

    __visit_name__ = "VARIANT"

    def bind_expression(self, bindparam):
        return func.parse_json(bindparam)

    def bind_processor(self, dialect: Dialect):
        def process(value: Any) -> Optional[str]:
            return None if value is None else json.dumps(value)

        return process

    def result_processor(self, dialect: Dialect, coltype: object):
        def process(value: Any) -> Any:
            if value is None:
                return None
            if isinstance(value, (bytes, bytearray)):
                value = value.decode("utf-8")
            return json.loads(value)

        return process


_ESCAPES = {'"': '"', "'": "'", "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_NUMBER = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_INTEGER = re.compile(r"-?\d+")
_BAREWORDS = {"null": None, "true": True, "false": False}


class _ComplexValueParser:
    """Parser for the server's text rendering of ARRAY/MAP/STRUCT values.

    That rendering is JSON-like but not JSON: map keys of non-string types are
    unquoted (``{1:"a"}``), JSON items inside an array are single-quoted, and
    strings may contain raw control characters.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def fail(self, message: str) -> ValueError:
        return ValueError(
            "could not parse complex value at offset %d: %s: %r" % (self.pos, message, self.text)
        )

    def skip_ws(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def expect_one_of(self, *tokens: str) -> str:
        self.skip_ws()
        for token in tokens:
            if self.text.startswith(token, self.pos):
                self.pos += len(token)
                return token
        raise self.fail("expected one of %s" % (tokens,))

    def parse(self) -> Any:
        value = self.value()
        self.skip_ws()
        if self.pos != len(self.text):
            raise self.fail("trailing characters")
        return value

    def value(self) -> Any:
        self.skip_ws()
        if self.pos >= len(self.text):
            raise self.fail("unexpected end of input")
        char = self.text[self.pos]
        if char == "[":
            self.pos += 1
            items: List[Any] = []
            self.skip_ws()
            if self.text.startswith("]", self.pos):
                self.pos += 1
                return items
            while True:
                items.append(self.value())
                if self.expect_one_of(",", "]") == "]":
                    return items
        if char == "{":
            # Pairs, not a dict: keys are converted by the declared key type first.
            self.pos += 1
            pairs: List[Tuple[Any, Any]] = []
            self.skip_ws()
            if self.text.startswith("}", self.pos):
                self.pos += 1
                return pairs
            while True:
                key = self.value()
                self.expect_one_of(":")
                pairs.append((key, self.value()))
                if self.expect_one_of(",", "}") == "}":
                    return pairs
        if char in "\"'":
            return self.string(char)
        m = _NUMBER.match(self.text, self.pos)
        if m:
            self.pos = m.end()
            token = m.group()
            return int(token) if _INTEGER.fullmatch(token) else Decimal(token)
        for word, result in _BAREWORDS.items():
            if self.text.startswith(word, self.pos):
                self.pos += len(word)
                return result
        raise self.fail("unexpected character")

    def string(self, quote: str) -> str:
        self.pos += 1
        out: List[str] = []
        while self.pos < len(self.text):
            char = self.text[self.pos]
            if char == quote:
                self.pos += 1
                return "".join(out)
            if char == "\\":
                escape = self.text[self.pos + 1:self.pos + 2]
                if escape == "u":
                    out.append(chr(int(self.text[self.pos + 2:self.pos + 6], 16)))
                    self.pos += 6
                    continue
                if escape not in _ESCAPES:
                    raise self.fail("invalid escape \\%s" % escape)
                out.append(_ESCAPES[escape])
                self.pos += 2
                continue
            out.append(char)
            self.pos += 1
        raise self.fail("unterminated string")


def parse_complex_value(text: Union[str, bytes]) -> Any:
    """Parse an ARRAY/MAP/STRUCT value as rendered by the server.

    Maps and structs are returned as lists of ``(key, value)`` pairs and numbers
    as ``int`` or ``Decimal``, so nothing is lost before the declared type
    converts them. Raises ``ValueError`` rather than returning undecoded text.
    """
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("utf-8")
    return _ComplexValueParser(text).parse()


def _element_converter(type_: Any, dialect: Dialect) -> Callable[[Any], Any]:
    type_ = StructuredType._check_subtype(type_)
    if isinstance(type_, StructuredType):
        return lambda value: _convert_structured(type_, value, dialect)
    impl = type_.dialect_impl(dialect)
    if isinstance(impl, sqltypes.Boolean):
        return lambda value: value if isinstance(value, bool) else bool(value)
    if isinstance(impl, sqltypes.Integer):
        return int
    if isinstance(impl, sqltypes.Float):
        return float
    if isinstance(impl, sqltypes.Numeric):
        return (lambda value: Decimal(str(value))) if impl.asdecimal else float
    if isinstance(impl, sqltypes._Binary):
        # Binary values nested in a complex value are rendered as hex.
        return lambda value: value if isinstance(value, bytes) else bytes.fromhex(value)
    if isinstance(impl, sqltypes.String):
        return str
    processor = impl.result_processor(dialect, None)
    return processor if processor is not None else (lambda value: value)


def _nullable(convert: Callable[[Any], Any]) -> Callable[[Any], Any]:
    return lambda value: None if value is None else convert(value)


def _convert_structured(type_: StructuredType, value: Any, dialect: Dialect) -> Any:
    if value is None:
        return None
    if isinstance(type_, ARRAY):
        if not isinstance(value, list):
            raise ValueError("expected an ARRAY value, got %r" % (value,))
        item = _nullable(_element_converter(type_.item_type, dialect))
        return [item(element) for element in value]
    if not isinstance(value, list) or any(not isinstance(pair, tuple) for pair in value):
        raise ValueError("expected a %s value, got %r" % (type_.__visit_name__, value))
    if isinstance(type_, MAP):
        key = _nullable(_element_converter(type_.key_type, dialect))
        val = _nullable(_element_converter(type_.value_type, dialect))
        return {key(k): val(v) for k, v in value}
    fields = {
        name.lower(): _nullable(_element_converter(field_type, dialect))
        for name, field_type in type_.field_tuples
    }
    return {
        k: fields[k.lower()](v) if isinstance(k, str) and k.lower() in fields else v
        for k, v in value
    }
